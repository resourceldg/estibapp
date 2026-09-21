"""El embudo: de cada 10 consultas, cuantas terminan en un flete hecho.

Se calcula sobre los leads creados en un periodo. Un lead cuenta en una
etapa si llego a ella alguna vez, aunque despues se haya perdido: asi se ve
en que paso se cae la gente.

El gasto en publicidad (y con el, el costo por cliente) queda afuera hasta
que se conecte el adaptador de Meta: no se estima.
"""

from __future__ import annotations

import statistics
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from estiba.core.models import Stage
from estiba.crm.leads import AVANCE, LeadInfo

ETIQUETA_ETAPA = {
    Stage.NEW: "Consultas",
    Stage.QUALIFIED: "Con datos",
    Stage.QUOTED: "Presupuestados",
    Stage.CONFIRMED: "Confirmados",
    Stage.DONE: "Realizados",
    Stage.LOST: "Perdidos",
}


@dataclass(frozen=True)
class PasoEmbudo:
    etapa: Stage
    etiqueta: str
    cantidad: int
    del_total: float  # 0..1, sobre las consultas
    del_anterior: float | None  # 0..1, sobre la etapa anterior


@dataclass
class Embudo:
    desde: datetime | None
    hasta: datetime
    pasos: list[PasoEmbudo] = field(default_factory=list)
    perdidos: int = 0
    motivos: list[tuple[str, int]] = field(default_factory=list)
    facturado: Decimal = Decimal(0)       # realizados
    comprometido: Decimal = Decimal(0)    # confirmados que todavia no se hicieron
    ticket_promedio: Decimal | None = None
    respuesta_mediana: timedelta | None = None
    clientes: int = 0
    recurrentes: int = 0
    desvio_precio: float | None = None    # cotizado vs sugerido, promedio


def motivo_corto(motivo: str | None) -> str:
    m = (motivo or "").lower()
    if "sin respuesta" in m:
        return "Dejó de responder"
    if "canceló" in m:
        return "Canceló"
    if "perdido" in m:
        return "Lo diste por perdido"
    return "Otro"


def calcular(leads: list[LeadInfo], hasta: datetime, desde: datetime | None = None) -> Embudo:
    del_periodo = [l for l in leads if (desde is None or l.creado >= desde) and l.creado < hasta]
    e = Embudo(desde=desde, hasta=hasta)
    total = len(del_periodo)

    anterior = None
    for etapa in AVANCE:
        n = sum(1 for l in del_periodo if AVANCE.index(l.alcanzo) >= AVANCE.index(etapa))
        e.pasos.append(PasoEmbudo(
            etapa, ETIQUETA_ETAPA[etapa], n,
            n / total if total else 0.0,
            (n / anterior if anterior else 0.0) if anterior is not None else None,
        ))
        anterior = n

    perdidos = [l for l in del_periodo if l.etapa is Stage.LOST]
    e.perdidos = len(perdidos)
    e.motivos = Counter(motivo_corto(l.motivo_perdida) for l in perdidos).most_common()

    hechos = [l for l in del_periodo if l.etapa is Stage.DONE and l.cotizado]
    en_agenda = [l for l in del_periodo if l.etapa is Stage.CONFIRMED and l.cotizado]
    e.facturado = sum((l.cotizado for l in hechos), Decimal(0))
    e.comprometido = sum((l.cotizado for l in en_agenda), Decimal(0))
    vendidos = hechos + en_agenda
    if vendidos:
        e.ticket_promedio = (sum((l.cotizado for l in vendidos), Decimal(0)) / len(vendidos)).quantize(Decimal(1))

    respuestas = [l.primera_respuesta for l in del_periodo if l.primera_respuesta is not None]
    if respuestas:
        e.respuesta_mediana = timedelta(seconds=statistics.median(r.total_seconds() for r in respuestas))

    por_cliente = Counter(l.chat_id for l in leads)  # recurrencia: con toda la historia
    e.clientes = len({l.chat_id for l in del_periodo})
    e.recurrentes = sum(1 for c in {l.chat_id for l in del_periodo} if por_cliente[c] > 1)

    desvios = [float((l.cotizado - l.sugerido) / l.sugerido) for l in del_periodo if l.cotizado and l.sugerido]
    if desvios:
        e.desvio_precio = sum(desvios) / len(desvios)
    return e
