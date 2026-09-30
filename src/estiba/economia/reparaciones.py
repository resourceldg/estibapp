"""Reparaciones, dias parada y saldos de los fondos.

Dos clases de reparacion:

- ordinaria: frenos, aceite, cubiertas. Ya esta estimada en el desgaste
  por km: pagarla es caja, no un costo nuevo.
- extraordinaria: caja, motor, embrague, diferencial, direccion, tren
  delantero, electricidad. Poco probable y cara: es lo que tiene que cubrir
  el fondo. Con el tiempo, su historia alimenta el riesgo (suficiencia:
  eventos raros, por km y meses de exposicion).

Dias parada: capacidad que se perdio. Se muestra en fletes que no se
pudieron hacer al ritmo comodo; no se convierte en plata sin datos.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from estiba.economia.config import Disponibilidad

ORDINARIA, EXTRAORDINARIA = "ordinaria", "extraordinaria"
COMPONENTES_EXTRAORDINARIOS = ("caja", "motor", "embrague", "diferencial", "dirección", "tren delantero",
                               "electricidad")


@dataclass(frozen=True)
class Reparacion:
    fecha: date
    componente: str
    tipo: str
    costo: Decimal
    km: Decimal | None = None
    dias_parada: int = 0
    fondo: str | None = None


def tipo_sugerido(componente: str) -> str:
    c = componente.lower()
    return EXTRAORDINARIA if any(x in c for x in COMPONENTES_EXTRAORDINARIOS) else ORDINARIA


@dataclass(frozen=True)
class Parada:
    dias: int
    fletes_perdidos: Decimal
    costo_caja: Decimal
    costo_fondos: Decimal


def del_periodo(reps: list[Reparacion], desde: date, hasta: date, d: Disponibilidad) -> Parada:
    """Dias parada y lo que costaron las reparaciones entre dos fechas."""
    en = [r for r in reps if desde <= r.fecha <= hasta]
    dias = sum(r.dias_parada for r in en)
    # Solo los dias en que se habria trabajado: dias parada × (dias/semana ÷ 7).
    perdidos = Decimal(dias) * d.dias_semana / 7 * d.fletes_dia_comodo
    return Parada(dias, perdidos, sum((r.costo for r in en if r.fondo is None), Decimal(0)),
                  sum((r.costo for r in en if r.fondo is not None), Decimal(0)))


def extraordinarias(reps: list[Reparacion]) -> int:
    return sum(1 for r in reps if r.tipo == EXTRAORDINARIA)


def saldo(movimientos: list[tuple[date, Decimal]], hasta: date | None = None) -> Decimal:
    return sum((m for d, m in movimientos if hasta is None or d <= hasta), Decimal(0))
