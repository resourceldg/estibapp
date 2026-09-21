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

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from estiba.core.models import Conversation, JobState
from estiba.extraction.intencion import Intencion, intencion
from estiba.jobs.engine import Accion, JobEngine

#: Sin mensajes durante esto, una consulta nueva es otro flete aunque el anterior no se haya cerrado.
PAUSA_LARGA = timedelta(days=30)


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
) -> list[Episodio]:
    mensajes = conv.messages
    cortes = [0]
    for i, m in enumerate(mensajes):
        if i == cortes[-1] or not m.from_customer:
            continue
        texto = conv.text_of(m)
        hallada = intencion(texto) if texto else None
        if hallada is None or hallada[0] is not Intencion.CONSULTA:
            continue
        pausa = m.sent_at - mensajes[i - 1].sent_at
        if pausa < PAUSA_LARGA:
            anterior = Conversation(conv.chat_id, mensajes[cortes[-1] : i], conv.transcripts)
            previo = job_id(conv.chat_id, len(cortes))
            estado = engine.evaluar(anterior, _justo_antes(m.sent_at), acciones(previo)).job.state
            if estado not in (JobState.DONE, JobState.CANCELLED):
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
