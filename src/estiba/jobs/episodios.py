"""Un cliente, varios fletes: partir una conversacion en episodios.

WhatsApp es un solo hilo por persona, pero el que se mudo en marzo y vuelve
en octubre por un sillon pide otro flete. Si todo fuera un solo flete, el
de octubre heredaria la direccion de marzo y el CRM no sabria que es un
cliente que volvio.

Un episodio nuevo empieza cuando el cliente vuelve a consultar y, en ese
momento, el flete anterior ya estaba cerrado (realizado o perdido) o hacia
mas de un mes que nadie escribia. Volver a escribir despues de un silencio
corto no abre nada: es el mismo flete que retoma.
"""

from __future__ import annotations

import re

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from estiba.core.models import Conversation, FreightJob, JobState
from estiba.extraction.intencion import Intencion, intencion
from estiba.extraction.texto import plano
from estiba.jobs.engine import Accion, JobEngine

#: Sin mensajes durante esto, una consulta nueva es otro flete aunque el anterior no se haya cerrado.
PAUSA_LARGA = timedelta(days=30)


def _termino(job: FreightJob, cuando: datetime) -> bool:
    """Si ese flete ya es cosa juzgada para cuando llega la consulta nueva.

    No alcanza con mirar si esta marcado realizado: en la practica nadie
    marca nada. Un flete agendado cuya fecha ya paso tambien termino, y el
    cliente que vuelve despues esta pidiendo otro, no retomando aquel.
    """
    if job.state in (JobState.DONE, JobState.CANCELLED):
        return True
    return bool(
        job.state in (JobState.CONFIRMED, JobState.SCHEDULED)
        and job.when
        and job.when.value < cuando.date()
    )


#: El cliente habla de un viaje anterior como algo que ya paso: "como ayer",
#: "igual que la otra vez". Es la pista mas clara de que hubo otro flete.
_OTRA_VEZ = re.compile(
    r"\b(?:como\s+(?:ayer|la\s+otra\s+vez|el\s+otro\s+dia|antes)"
    r"|igual\s+que\s+(?:ayer|la\s+otra|el\s+otro)"
    r"|(?:otro|otra)\s+(?:flete|viaje|vuelta)"
    r"|de\s+nuevo\s+(?:te|el|la)\b)"
)


@dataclass(frozen=True)
class Sospecha:
    """Un flete que parece haber pasado sin que nadie lo separara."""

    message_id: str
    cuando: datetime
    porque: str


def sospechas(conv: Conversation, engine: JobEngine, ahora: datetime) -> list[Sospecha]:
    """Señales de que hubo otro flete que el chat no dice con todas las letras.

    Dos pistas, las dos de conversaciones reales:

    - El cliente nombra un viaje anterior: "como ayer 19 hs".
    - Se vuelve a acordar dia y hora despues de que paso la fecha que ya
      estaba acordada. Nadie negocia dos veces el mismo viaje una vez que
      ya se hizo.
    """
    mensajes = [m for m in conv.messages if m.sent_at <= ahora]
    if not mensajes:
        return []
    halladas: list[Sospecha] = []
    for m in mensajes:
        texto = conv.text_of(m)
        if m.from_customer and texto and _OTRA_VEZ.search(plano(texto)):
            halladas.append(Sospecha(m.id, m.sent_at, "habla de un viaje anterior"))

    # La fecha acordada ya paso y despues se siguio hablando de horarios.
    ev = engine.evaluar(conv, ahora)
    acordada = ev.job.when.value if ev.job.when else None
    if acordada:
        for m in mensajes:
            if not m.from_customer or m.sent_at.date() <= acordada:
                continue
            texto = plano(conv.text_of(m) or "")
            if re.search(r"\b(?:\d{1,2}\s?(?:hs|hrs|horas)|a\s+las\s+\d{1,2})\b", texto):
                halladas.append(
                    Sospecha(m.id, m.sent_at, "se volvió a acordar un horario después de esa fecha")
                )
                break
    return halladas


@dataclass(frozen=True)
class Episodio:
    job_id: str
    conversacion: Conversation


def job_id(chat_id: str, numero: int) -> str:
    """El primero es `<chat>:job`, como siempre; los siguientes `<chat>:job2`..."""
    return f"{chat_id}:job" if numero == 1 else f"{chat_id}:job{numero}"


def episodios(
    conv: Conversation,
    engine: JobEngine,
    acciones: Callable[[str], list[Accion]],
    a_mano: set[str] | None = None,
) -> list[Episodio]:
    """Parte la conversacion en fletes.

    `a_mano` son los mensajes donde el fletero dijo que empieza otro flete.
    Mandan sobre cualquier regla: lo que se arreglo en persona no deja
    rastro en el chat y no hay forma de deducirlo.
    """
    a_mano = a_mano or set()
    mensajes = conv.messages
    cortes = [0]
    for i, m in enumerate(mensajes):
        if i == cortes[-1]:
            continue
        if m.id in a_mano:
            cortes.append(i)
            continue
        if not m.from_customer:
            continue
        texto = conv.text_of(m)
        hallada = intencion(texto) if texto else None
        if hallada is None or hallada[0] is not Intencion.CONSULTA:
            continue
        pausa = m.sent_at - mensajes[i - 1].sent_at
        if pausa < PAUSA_LARGA:
            anterior = Conversation(conv.chat_id, mensajes[cortes[-1] : i], conv.transcripts)
            previo = job_id(conv.chat_id, len(cortes))
            job = engine.evaluar(anterior, _justo_antes(m.sent_at), acciones(previo)).job
            if not _termino(job, m.sent_at):
                continue
        cortes.append(i)

    limites = [*cortes, len(mensajes)]
    return [
        Episodio(
            job_id(conv.chat_id, n),
            Conversation(conv.chat_id, mensajes[desde:hasta], conv.transcripts),
        )
        for n, (desde, hasta) in enumerate(zip(limites, limites[1:]), start=1)
    ]


def _justo_antes(t: datetime) -> datetime:
    return t - timedelta(microseconds=1)
