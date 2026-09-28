"""Sincronizar: poner todo al dia a partir de las conversaciones.

Para cada conversacion: partirla en fletes (un cliente puede pedir varios),
evaluar cada uno con lo que se marco a mano, agendarlo si se confirmo,
guardarlo, dejar su borrador en la bandeja y actualizar el CRM. Al final,
copiar la agenda al CalDAV si hay uno.

Es lo que corre al abrir el dashboard o al tocar "actualizar". Idempotente:
correrlo dos veces seguidas no cambia nada.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime

from estiba.agenda.local import LocalCalendar
from estiba.bandeja.redactor import proponer
from estiba.conversation.clasificacion import Clase, Clasificacion, clasificar
from estiba.conversation.store import ConversationStore
from estiba.core.models import Conversation, JobState
from estiba.crm.leads import LeadInfo, lead_de
from estiba.jobs.engine import AGENDADO, Evaluacion, JobEngine
from estiba.jobs.episodios import episodios
from estiba.storage.repositories import (
    ActionRepository,
    CRMRepository,
    ClaseRepository,
    CorteRepository,
    DraftRepository,
    EstimacionRepository,
    JobRepository,
)


@dataclass
class Resultado:
    #: job_id -> (evaluacion, conversacion del episodio)
    fletes: dict[str, tuple[Evaluacion, Conversation]] = field(default_factory=dict)
    leads: list[LeadInfo] = field(default_factory=list)
    #: Que paso con la agenda: (job_id, "creado" | "movido" | "cancelado" | ...)
    agenda: list[tuple[str, str]] = field(default_factory=list)
    #: chat_id -> por que quedo afuera del embudo ("personal", "spam").
    fuera: dict[str, str] = field(default_factory=dict)
    #: chat_id -> como quedo clasificada la conversacion que si entro.
    clases: dict[str, Clasificacion] = field(default_factory=dict)
    espejo_error: str | None = None


def sincronizar(conn: sqlite3.Connection, engine: JobEngine, ahora: datetime, espejo=None) -> Resultado:
    store = ConversationStore(conn)
    jobs = JobRepository(conn)
    borradores = DraftRepository(conn)
    acciones = ActionRepository(conn)
    crm = CRMRepository(conn)
    agenda = LocalCalendar(conn, reloj=lambda: ahora)
    r = Resultado()
    clases = ClaseRepository(conn)
    estimaciones = EstimacionRepository(conn)
    cortes = CorteRepository(conn)
    guardadas = clases.todas()

    for chat_id in store.chat_ids():
        conv = store.get(chat_id)
        if not any(m.sent_at <= ahora for m in conv.messages):
            continue

        # Que es esta conversacion. Lo que decidio una persona no se toca;
        # lo demas se vuelve a mirar cada vez, porque un "hola" de ayer
        # puede ser un pedido de flete hoy.
        guardada = guardadas.get(chat_id)
        if guardada is not None and guardada["quien"] == "humano":
            # Lo que decidio una persona no vuelve a preguntarse nunca.
            cl = Clasificacion(Clase(guardada["clase"]), "lo dijiste vos", hay_con_que=True)
        else:
            cl = clasificar(conv, engine.extractor.extract(conv), crm.name(chat_id))
            clases.guardar_inferida(chat_id, cl.clase.value, cl.porque, ahora)
        clase = cl.clase
        r.clases[chat_id] = cl
        decidida_por_humano = guardada is not None and guardada["quien"] == "humano"
        # Lo que Estiba sabe que no es negocio no entra al embudo ni a las
        # metricas. La duda si entra: perder un cliente en silencio es peor.
        if clase in (Clase.PRIVADO, Clase.SPAM):
            r.fuera[chat_id] = clase.value
            # Y lo que hubiera quedado escrito para esta conversacion sale
            # de la bandeja. De aca en adelante no se la vuelve a mirar, asi
            # que si no se limpia ahora no se limpia nunca.
            borradores.cancelar_del_chat(chat_id, ahora)
            continue
        todos = episodios(conv, engine, acciones.for_job, cortes.de(chat_id))
        for n, ep in enumerate(todos, start=1):
            if not any(m.sent_at <= ahora for m in ep.conversacion.messages):
                continue
            ev = engine.evaluar(ep.conversacion, ahora, acciones.for_job(ep.job_id))
            ev.job.id = ep.job_id

            # Confirmado y con fecha: va a la agenda, y eso lo pasa a agendado.
            if ev.job.state is JobState.CONFIRMED and not acciones.has(ep.job_id, AGENDADO):
                acciones.add(ep.job_id, AGENDADO, ahora, actor="calendario")
                ev = engine.evaluar(ep.conversacion, ahora, acciones.for_job(ep.job_id))
                ev.job.id = ep.job_id
            # Sin fecha no hay nada que poner en el calendario. Un flete que
            # se arreglo por telefono y se hizo el mismo dia se cierra igual:
            # el calendario es una consecuencia, no un requisito.
            # Una fecha que alguien nombro y no esta en ningun lado es una
            # fecha que se pasa. Entran todas las que todavia no pasaron; las
            # que no cerro el cliente van marcadas como tentativas y no
            # suenan. Antes solo entraban las confirmadas, y una fecha dicha
            # en un audio sobre un flete sin cerrar no aparecia en ninguna
            # pantalla salvo que abrieras esa conversacion.
            cerrado_con_fecha = ev.job.state in (
                JobState.CONFIRMED, JobState.SCHEDULED, JobState.DONE
            )
            se_viene = bool(ev.job.when) and ev.job.when.value >= ahora.date()
            if ev.job.when and (cerrado_con_fecha or (se_viene and ev.job.state is not JobState.CANCELLED)):
                evento, cambio = agenda.programar(ev.job, tentativo=not cerrado_con_fecha)
                ev.job.calendar_event_id = evento.uid
                if cambio != "igual":
                    r.agenda.append((ep.job_id, cambio))
            elif agenda.cancelar_de(ep.job_id):
                # Se cae del calendario si el flete se perdio, y tambien si
                # se quedo sin fecha. Esto ultimo pasa: una fecha sale de un
                # audio, se vuelve a transcribir o se corta el episodio, y
                # el flete deja de tenerla. El evento quedaba en el celular
                # con el titulo viejo y un dia que ya no existia en ningun
                # lado. En la base real habia uno.
                r.agenda.append((ep.job_id, "cancelado"))

            jobs.save(ev.job, ev.eventos)
            # Lo que Estiba estima queda guardado antes de saber como salio:
            # es contra esto que se mide despues, y sin el primer eslabon la
            # cadena "estimacion -> decision humana -> resultado" no existe.
            if ev.presupuesto is not None:
                estimaciones.guardar_si_cambio(
                    ep.job_id, "precio", ev.presupuesto.total, ahora,
                    supuestos={
                        "desglose": [[c, str(m)] for c, m in ev.presupuesto.desglose],
                        "sin_contar": ev.presupuesto.sin_contar,
                    },
                    completa=ev.presupuesto.completo,
                )
            # Solo el ultimo flete de la conversacion puede necesitar respuesta.
            propuesta = proponer(ev, ep.conversacion, ahora) if n == len(todos) else None
            borradores.sync(ep.job_id, chat_id, propuesta, ahora)
            lead = lead_de(ep.job_id, ev, ep.conversacion)
            crm.save(lead)
            r.fletes[ep.job_id] = (ev, ep.conversacion)
            r.leads.append(lead)
            # Dejo de ser un lead: ya hizo un flete. Lo deduce del historial,
            # no hay que marcarlo a mano.
            if ev.job.state in (JobState.CONFIRMED, JobState.SCHEDULED, JobState.DONE):
                clase = Clase.CLIENTE

        if clase is not cl.clase and not decidida_por_humano:
            clases.guardar_inferida(chat_id, clase.value, "ya hizo un flete", ahora)
            r.clases[chat_id] = Clasificacion(clase, "ya hizo un flete")

    if espejo is not None:
        try:
            for evento, cancelado in agenda.sin_reflejar():
                espejo.reflejar(evento, cancelado, ahora)
                agenda.marcar_reflejado(evento.uid)
        except Exception as e:  # el CalDAV caido no frena nada: se reintenta la proxima vez
            r.espejo_error = str(e) or type(e).__name__
    return r
