"""Que corresponde hacer ahora en esta conversacion.

Primero se decide la accion, despues se escribe. Al reves salian mensajes
absurdos: a alguien que se despedia con "mañana hablamos" se le pedia el
barrio, y a alguien que preguntaba por un repuesto de auto se le ofrecia
reservarle un dia para el flete.

La decision mira toda la conversacion pero le da mucho peso al ultimo turno:
lo ultimo que dijo el cliente es lo que manda sobre que toca ahora.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import Enum

from estiba.core.models import Conversation, JobState
from estiba.extraction.intencion import Intencion
from estiba.jobs.engine import Evaluacion
from estiba.jobs.preguntas import PARA_COTIZAR


class Accion(Enum):
    #: Hay algo para mandarle y Estiba lo puede escribir.
    PEDIR_DATOS = "preguntas"
    COTIZAR = "cotizacion"
    CONFIRMAR = "confirmacion"
    SEGUIR = "seguimiento"
    #: Te pregunto algo que las reglas no saben contestar: te toca a vos.
    RESPONDER = "respuesta"
    #: Dijo que despues sigue: sacarla de lo urgente hasta entonces.
    POSPONER = "posponer"
    #: No hay nada que hacer. Decirlo es mejor que inventar un mensaje.
    ESPERAR = "esperar"


@dataclass(frozen=True)
class Decision:
    accion: Accion
    #: Por que, en criollo, para que la pantalla lo pueda explicar.
    razon: str
    #: Para POSPONER: hasta cuando conviene no molestar.
    hasta: date | None = None


def _intencion_del_ultimo(ev: Evaluacion, message_id: str) -> Intencion | None:
    return next(
        (i.intencion for i in ev.extraccion.intenciones if i.message_id == message_id), None
    )


def _hay_algo_concreto(ev: Evaluacion) -> bool:
    """Si alguna vez se hablo de un flete de verdad.

    Sin esto, a cualquiera que escribio una vez se le mandaba "¿pudiste ver
    lo del flete?", incluso si nunca se hablo de ningun flete.
    """
    job = ev.job
    return bool(job.origin or job.destination or job.items or job.quoted_price)


def decidir(ev: Evaluacion, conv: Conversation, ahora: datetime) -> Decision:
    """Que toca ahora. El borrador es consecuencia de esto, no al reves."""
    mensajes = [m for m in conv.messages if m.sent_at <= ahora]
    if not mensajes:
        return Decision(Accion.ESPERAR, "todavía no escribió nadie")
    ultimo = mensajes[-1]
    job = ev.job
    estado = job.state

    if estado in (JobState.CANCELLED, JobState.DONE):
        return Decision(Accion.ESPERAR, "el flete ya está cerrado")

    intencion = _intencion_del_ultimo(ev, ultimo.id)

    # Se despidio o dijo que sigue despues: no se le pregunta nada ahora.
    if ultimo.from_customer and intencion is Intencion.POSPONE:
        manana = (ahora + timedelta(days=1)).date()
        return Decision(Accion.POSPONER, "dijo que seguían después", hasta=manana)

    if not ultimo.from_customer or estado is JobState.WAITING_CUSTOMER:
        # Le toca contestar a el. Solo el seguimiento rompe el silencio, y
        # solo si hay un flete concreto del que hablar.
        if not ev.requiere_seguimiento:
            return Decision(Accion.ESPERAR, "le toca contestar a él")
        if not _hay_algo_concreto(ev):
            return Decision(Accion.ESPERAR, "nunca se habló de un flete concreto")
        razon = (
            "Dijo que confirmaba y no volvió a escribir"
            if estado is JobState.WAITING_CUSTOMER
            else "Hace dos días que no responde"
        )
        return Decision(Accion.SEGUIR, razon)

    if estado in (JobState.CONFIRMED, JobState.SCHEDULED):
        if job.when and job.when.value < ahora.date():
            return Decision(Accion.ESPERAR, "el flete ya pasó")
        return Decision(Accion.CONFIRMAR, "Aceptó: falta confirmarle los detalles")

    # Escribio el cliente, y el flete sigue abierto.
    #
    # Cotizar depende de tener los datos, no de que Estiba sepa el precio.
    # La tarifa puede no estar definida —pasa cuando el negocio no es donde
    # el sistema supone— y aun asi el trabajo esta listo para cotizarse: los
    # datos estan, el precio lo pone una persona.
    if estado is JobState.DRAFT and not [c for c in PARA_COTIZAR if c not in ev.extraccion.ganadores]:
        return Decision(Accion.COTIZAR, "Tiene todos los datos para cotizar")

    texto_ultimo = (conv.text_of(ultimo) or "").rstrip()
    if texto_ultimo.endswith("?"):
        # "¿Cuanto sale?" si se puede contestar: no con un precio, pero si
        # pidiendo lo que hace falta para calcularlo. Mejor eso que dejar el
        # compositor en blanco.
        if intencion is Intencion.CONSULTA and ev.preguntas:
            return Decision(Accion.PEDIR_DATOS, "Preguntó el precio y faltan datos")
        if intencion is None:
            # Algo que las reglas no saben contestar, como "¿incluye subirla?".
            # Contestarle eso importa mas que seguir juntando datos.
            return Decision(Accion.RESPONDER, "Te hizo una pregunta")

    if ev.preguntas:
        return Decision(Accion.PEDIR_DATOS, "Faltan datos para avanzar")
    return Decision(Accion.RESPONDER, "Te escribió y espera respuesta")
