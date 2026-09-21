"""Del flete al CRM: el contacto, el lead y su recorrido por el embudo.

Nada de esto se carga a mano: se deriva de la conversacion y de la historia
del flete, igual que el estado. Lo unico que pone una persona es el nombre
del contacto (WhatsApp no siempre lo trae).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from estiba.core.models import Conversation, JobState, Stage
from estiba.jobs.engine import Evaluacion

ETAPA_DE = {
    JobState.DRAFT: Stage.NEW,
    JobState.QUOTED: Stage.QUOTED,
    JobState.WAITING_CUSTOMER: Stage.QUOTED,
    JobState.CONFIRMED: Stage.CONFIRMED,
    JobState.SCHEDULED: Stage.CONFIRMED,
    JobState.DONE: Stage.DONE,
    JobState.CANCELLED: Stage.LOST,
}

#: Orden del embudo. LOST no esta: se puede perder desde cualquier etapa.
AVANCE = [Stage.NEW, Stage.QUALIFIED, Stage.QUOTED, Stage.CONFIRMED, Stage.DONE]

_PARA_CALIFICAR = ("origin", "destination", "items")


@dataclass(frozen=True)
class Paso:
    etapa: Stage
    at: datetime
    motivo: str


@dataclass(frozen=True)
class LeadInfo:
    job_id: str
    chat_id: str
    creado: datetime
    etapa: Stage
    historia: tuple[Paso, ...]
    motivo_perdida: str | None
    cotizado: Decimal | None
    sugerido: Decimal | None
    primera_respuesta: timedelta | None

    @property
    def alcanzo(self) -> Stage:
        """La etapa mas avanzada a la que llego, aunque despues se haya perdido."""
        return max((p.etapa for p in self.historia if p.etapa in AVANCE), key=AVANCE.index)


def lead_de(job_id: str, ev: Evaluacion, conv: Conversation) -> LeadInfo:
    mensajes = conv.messages
    tiempos = {m.id: m.sent_at for m in mensajes}
    historia = [Paso(Stage.NEW, mensajes[0].sent_at, "primera consulta")]

    # Calificado: cuando se supo de donde, a donde y que. Es la primera vez
    # que cada uno de esos datos aparecio, no la ultima.
    primeros = {}
    for c in ev.extraccion.candidatos:
        if c.campo in _PARA_CALIFICAR and c.campo not in primeros:
            primeros[c.campo] = tiempos[c.slot.evidence.message_id]
    calificado = max(primeros.values()) if len(primeros) == len(_PARA_CALIFICAR) else None

    for e in ev.eventos:
        if calificado is not None and calificado <= e.at:
            historia.append(Paso(Stage.QUALIFIED, calificado, "sabemos de donde, a donde y que"))
            calificado = None
        etapa = ETAPA_DE[e.hacia]
        if etapa is not historia[-1].etapa:
            historia.append(Paso(etapa, e.at, e.causa))
    if calificado is not None:
        historia.append(Paso(Stage.QUALIFIED, calificado, "sabemos de donde, a donde y que"))

    etapa = historia[-1].etapa
    job = ev.job
    return LeadInfo(
        job_id=job_id,
        chat_id=conv.chat_id,
        creado=mensajes[0].sent_at,
        etapa=etapa,
        historia=tuple(historia),
        motivo_perdida=historia[-1].motivo if etapa is Stage.LOST else None,
        cotizado=job.quoted_price.value if job.quoted_price else None,
        sugerido=ev.presupuesto.total if ev.presupuesto else None,
        primera_respuesta=_primera_respuesta(conv),
    )


def _primera_respuesta(conv: Conversation) -> timedelta | None:
    """Cuanto tardo el fletero en contestar el primer mensaje del cliente."""
    primero = next((m for m in conv.messages if m.from_customer), None)
    if primero is None:
        return None
    respuesta = next((m for m in conv.messages if not m.from_customer and m.sent_at >= primero.sent_at), None)
    return respuesta.sent_at - primero.sent_at if respuesta else None
