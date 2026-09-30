"""La historia del negocio semana a semana, con los hitos que la explican.

Responde "¿cómo viene?" mirando para atras: cuanto dejo cada semana, cuanto
se lleva acumulado y cuanto rindio cada hora de trabajo. Los hitos (el
primer flete, cuando arranco una pauta, un cambio de precio, una reparacion,
una decision aplicada) van en la misma linea de tiempo para poder leer
causa y efecto sin adivinar.

El resultado es el mismo "resultado real" de ¿Cómo estoy?: cobrado menos
uso de la camioneta, ayudantes, publicidad pagada y la estructura de esos
dias (mas el interes del credito, si se cargo). La cuota entera no entra:
es capital.

Con pocos fletes esto es exploratorio: se muestra igual, porque la forma
de la curva ya dice algo, pero la pantalla lo marca.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from estiba.economia.mes import FleteDelMes, margen

CERO = Decimal(0)


@dataclass(frozen=True)
class Semana:
    inicio: date          # lunes
    fletes: int
    cobrado: Decimal
    margen: Decimal       # cobrado − uso de la camioneta − ayudantes
    publicidad: Decimal   # lo pagado de verdad, con impuestos
    fijos: Decimal        # estructura (+ interes) de los dias de la semana
    horas: Decimal

    @property
    def resultado(self) -> Decimal:
        return self.margen - self.publicidad - self.fijos

    @property
    def por_hora(self) -> Decimal | None:
        """Lo que dejo cada hora trabajada, ya pagada la publicidad. Sin fijos:
        se compara con lo que cada hora tiene que aportar para cubrirlos."""
        return (self.margen - self.publicidad) / self.horas if self.horas else None


@dataclass(frozen=True)
class Hito:
    dia: date
    texto: str
    tipo: str   # flete, publicidad, precio, reparacion, decision


def lunes(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _fijos_del_dia(d: date, fijos_mes: Decimal) -> Decimal:
    return fijos_mes / calendar.monthrange(d.year, d.month)[1]


def semanas(fletes: list[FleteDelMes], publicidad_por_dia: dict[date, Decimal], uso_km: Decimal,
            fijos_mes: Decimal, hasta: date, desde: date | None = None) -> list[Semana]:
    """Una fila por semana, desde la primera actividad (flete o pauta) hasta `hasta`.

    La semana en curso cuenta solo los dias que ya pasaron.
    """
    dias_con_algo = [f.dia for f in fletes if f.dia <= hasta] + [d for d, m in publicidad_por_dia.items()
                                                                 if m and d <= hasta]
    if desde is None:
        if not dias_con_algo:
            return []
        desde = min(dias_con_algo)
    salida = []
    s = lunes(desde)
    while s <= hasta:
        fin = min(s + timedelta(days=6), hasta)
        dentro = [f for f in fletes if s <= f.dia <= fin]
        dias = [s + timedelta(days=i) for i in range((fin - s).days + 1)]
        # Antes de la primera actividad no se cargan fijos: no habia negocio.
        dias_fijos = [d for d in dias if d >= desde]
        salida.append(Semana(
            inicio=s, fletes=len(dentro),
            cobrado=sum((f.cobrado for f in dentro), CERO),
            margen=sum((margen(f, uso_km) for f in dentro), CERO),
            publicidad=sum((publicidad_por_dia.get(d, CERO) for d in dias), CERO),
            fijos=sum((_fijos_del_dia(d, fijos_mes) for d in dias_fijos), CERO),
            horas=sum((f.horas for f in dentro), CERO)))
        s += timedelta(days=7)
    return salida


def acumulado(ss: list[Semana]) -> list[Decimal]:
    total, salida = CERO, []
    for s in ss:
        total += s.resultado
        salida.append(total)
    return salida


def hitos_de_publicidad(publicidad_por_dia: dict[date, Decimal]) -> list[Hito]:
    """Cada vez que la pauta arranca despues de estar parada, y cuando se corta."""
    dias = sorted(d for d, m in publicidad_por_dia.items() if m)
    salida = []
    for i, d in enumerate(dias):
        if i == 0 or (d - dias[i - 1]).days > 1:
            salida.append(Hito(d, "Arranca la publicidad" if i == 0 else "Vuelve la publicidad", "publicidad"))
        if i > 0 and (d - dias[i - 1]).days > 1:
            salida.append(Hito(dias[i - 1] + timedelta(days=1), "Se corta la publicidad", "publicidad"))
    return salida


def ordenar(hitos: list[Hito], desde: date | None, hasta: date) -> list[Hito]:
    """Los del periodo, en orden, sin repetir el mismo texto el mismo dia."""
    vistos, salida = set(), []
    for h in sorted(hitos, key=lambda h: h.dia):
        if (desde is not None and h.dia < desde) or h.dia > hasta or (h.dia, h.texto) in vistos:
            continue
        vistos.add((h.dia, h.texto))
        salida.append(h)
    return salida
