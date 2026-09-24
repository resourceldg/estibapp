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
    DraftRepository,
    ExclusionRepository,
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
    excluidos = ExclusionRepository(conn).excluidos()
    clases = ClaseRepository(conn)
    guardadas = clases.todas()

    for chat_id in store.chat_ids():
        if chat_id in excluidos:
            continue
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
            cl = clasificar(conv, engine.extractor.extract(conv))
            clases.guardar_inferida(chat_id, cl.clase.value, cl.porque, ahora)
        clase = cl.clase
        r.clases[chat_id] = cl
        # Lo que Estiba sabe que no es negocio no entra al embudo ni a las
        # metricas. La duda si entra: perder un cliente en silencio es peor.
        if clase in (Clase.PERSONAL, Clase.SPAM):
            r.fuera[chat_id] = clase.value
            continue
        todos = episodios(conv, engine, acciones.for_job)
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
            if ev.job.state in (JobState.CONFIRMED, JobState.SCHEDULED, JobState.DONE):
                evento, cambio = agenda.programar(ev.job)
                ev.job.calendar_event_id = evento.uid
                if cambio != "igual":
                    r.agenda.append((ep.job_id, cambio))
            elif ev.job.state is JobState.CANCELLED and agenda.cancelar_de(ep.job_id):
                r.agenda.append((ep.job_id, "cancelado"))

            jobs.save(ev.job, ev.eventos)
            # Solo el ultimo flete de la conversacion puede necesitar respuesta.
            propuesta = proponer(ev, ep.conversacion, ahora) if n == len(todos) else None
            borradores.sync(ep.job_id, chat_id, propuesta, ahora)
            lead = lead_de(ep.job_id, ev, ep.conversacion)
            crm.save(lead)
            r.fletes[ep.job_id] = (ev, ep.conversacion)
            r.leads.append(lead)

    if espejo is not None:
        try:
            for evento, cancelado in agenda.sin_reflejar():
                espejo.reflejar(evento, cancelado, ahora)
                agenda.marcar_reflejado(evento.uid)
        except Exception as e:  # el CalDAV caido no frena nada: se reintenta la proxima vez
            r.espejo_error = str(e) or type(e).__name__
    return r
