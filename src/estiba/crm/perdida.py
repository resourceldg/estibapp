"""Por que se perdio una oportunidad, de la fuente mas firme a la menos.

1. Lo que anoto una persona (la accion `perdido` o una correccion `motivo`).
2. Lo que dijo el chat: el cliente cancelo.
3. El silencio: cuatro dias sin respuesta. Nadie lo decidio, y se dice asi.

Es de la oportunidad, no de la persona: perder un flete no hace mala a nadie.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation

from estiba.core.models import JobState
from estiba.jobs.engine import MOTIVO, PERDIDO, Evaluacion, motivo_de_nota, sin_anuladas


@dataclass(frozen=True)
class Perdida:
    codigo: str
    etiqueta: str
    nota: str | None
    competencia: Decimal | None
    at: datetime | None
    #: True si lo anoto una persona; False si se dedujo.
    decidida: bool
    por_silencio: bool


def perdida_de(ev: Evaluacion, acciones) -> Perdida | None:
    if ev.job.state is not JobState.CANCELLED:
        return None
    at = next((e.at for e in reversed(ev.eventos) if e.hacia is JobState.CANCELLED), None)
    anotadas = [a for a in sin_anuladas(acciones) if a.tipo in (PERDIDO, MOTIVO) and a.nota]
    if anotadas:
        codigo, etiqueta, nota, competencia = motivo_de_nota(anotadas[-1].nota)
        try:
            monto = Decimal(competencia) if competencia else None
        except InvalidOperation:
            monto = None
        if codigo or etiqueta or nota:
            return Perdida(codigo or "otro", etiqueta or "Otro", nota, monto, at, True, ev.perdido_por_silencio)
    if ev.perdido_por_silencio:
        return Perdida("no-respondio", "Dejó de responder", None, None, at, False, True)
    causa = next((e.causa for e in reversed(ev.eventos) if e.hacia is JobState.CANCELLED), "")
    if "canceló" in causa:
        return Perdida("cancelo", "Canceló", None, None, at, False, False)
    return Perdida("otro", "Sin motivo anotado", None, None, at, False, False)
