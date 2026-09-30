"""Lo que esta en curso: las cotizaciones posibles y cuanto pueden dejar.

Cada consulta abierta vale su precio por la probabilidad de terminar en un
flete hecho, y esa probabilidad sale de tu historia EN ESA ETAPA: de las
consultas que llegaron a "presupuestado" y ya se resolvieron (hechas o
perdidas), cuantas se hicieron. Las abiertas no entran en el denominador:
todavia no se sabe como terminan.

El precio, de lo mas firme a lo menos: lo que le cotizaste, lo que calcula
Estiba con la ruta, o tu precio base (un supuesto). Cada uno dice de donde
salio.

Tambien marca los fletes con fecha pasada que siguen abiertos: si se
hicieron y no se cerraron, los tableros no los ven.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from estiba.core.models import Stage
from estiba.crm.leads import AVANCE, LeadInfo
from estiba.economia.suficiencia import Suficiencia, proporcion

ABIERTAS = (Stage.NEW, Stage.QUALIFIED, Stage.QUOTED, Stage.CONFIRMED)
COTIZADO, ESTIMADO, BASE = "le cotizaste", "lo calcula Estiba", "tu precio base (supuesto)"
PARCIAL = "lo calcula Estiba (faltan datos)"


def probabilidades(leads: list[LeadInfo], dias_observados: int = 0) -> dict[Stage, Suficiencia]:
    """Para cada etapa abierta: de las resueltas que llegaron ahi, cuantas se hicieron."""
    resueltas = [l for l in leads if l.etapa in (Stage.DONE, Stage.LOST)]
    salida = {}
    for etapa in ABIERTAS:
        llegaron = [l for l in resueltas if AVANCE.index(l.alcanzo) >= AVANCE.index(etapa)]
        hechas = sum(1 for l in llegaron if l.etapa is Stage.DONE)
        salida[etapa] = proporcion(f"cierre desde {etapa.value}", hechas, len(llegaron),
                                   dias_observados=dias_observados,
                                   falta="Cerrar o dar por perdidas las consultas viejas")
    return salida


@dataclass(frozen=True)
class Posible:
    job_id: str
    etapa: Stage
    monto: Decimal
    fuente: str
    p: Suficiencia
    cuando: date | None
    vencido: bool

    @property
    def esperado(self) -> Decimal:
        return self.monto * Decimal(str(self.p.valor or 0))


def posibles(abiertos: list[tuple], probs: dict[Stage, Suficiencia],
             precio_base: Decimal, hoy: date) -> list[Posible]:
    """`abiertos`: (lead, lo que calcula Estiba o None, fecha del flete o None[, si el calculo
    esta completo]). Un calculo incompleto (falta que se lleva, o la fecha) vale mas que el
    precio base: al menos conoce la ruta."""
    salida = []
    for lead, calculado, cuando, *resto in abiertos:
        completo = resto[0] if resto else True
        if lead.etapa not in ABIERTAS:
            continue
        if lead.cotizado:
            monto, fuente = lead.cotizado, COTIZADO
        elif calculado:
            monto, fuente = calculado, ESTIMADO if completo else PARCIAL
        else:
            monto, fuente = precio_base, BASE
        salida.append(Posible(lead.job_id, lead.etapa, monto, fuente, probs[lead.etapa], cuando,
                              vencido=cuando is not None and cuando < hoy))
    # Primero lo que hay que mirar: vencidos, despues lo mas avanzado.
    return sorted(salida, key=lambda x: (not x.vencido, -AVANCE.index(x.etapa), -(x.monto or 0)))


def rango(ps: list[Posible]) -> tuple[Decimal, Decimal, Decimal]:
    """Lo esperado y su rango con los intervalos de cada etapa (lo, medio, hi)."""
    d = lambda v: Decimal(str(v or 0))
    return (sum((x.monto * d(x.p.lo) for x in ps), Decimal(0)),
            sum((x.esperado for x in ps), Decimal(0)),
            sum((x.monto * d(x.p.hi) for x in ps), Decimal(0)))
