"""Cuatro cuentas del mismo mes que no se suman entre si.

- Caja: la plata que entro y salio. La cuota entera sale, y los aportes a
  los fondos tambien salen (a otra cuenta, pero salen de la disponible).
- Resultado economico: lo que se gano de verdad. Solo cuentan los costos
  economicos: el uso de la camioneta, la publicidad pagada, los directos,
  la estructura, el *interes* del credito (no el capital) y la
  depreciacion si se cargo como supuesto. Si falta el interes o la
  depreciacion, se dice; no se inventa.
- Deuda y patrimonio: la parte de capital de la cuota baja la deuda y sube
  lo que es propio de la camioneta. No es liquidez.
- Reserva liquida: lo aportado a los fondos. Es la unica plata que sirve
  para arreglar o reponer la camioneta. Existe aunque se este pagando el
  credito: pagar capital no la reemplaza.

Doble conteo que se evita: la cuota nunca es costo economico entera, y la
depreciacion nunca se suma a la cuota en la caja.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from estiba.economia.config import CONSERVACION, PERSONAL, Economia
from estiba.economia.estructura import costo_uso_km, cuota_mes, estructura_mes, fondos_mes
from estiba.economia.modelo import CERO, Parte, Tipo, suma


@dataclass(frozen=True)
class Actividad:
    """Lo que paso en el periodo (o lo que se simula)."""

    ingresos: Decimal
    km: Decimal
    publicidad_pagada: Decimal = CERO
    directos: Decimal = CERO
    horas: Decimal | None = None


@dataclass(frozen=True)
class Cuentas:
    caja: list[Parte]
    economico: list[Parte]
    #: Capital del credito pagado este mes. None: no se sabe (falta el interes).
    capital_amortizado: Decimal | None
    reserva: list[Parte]
    #: Lo que el resultado economico no pudo contar, en palabras.
    sin_contar: list[str] = field(default_factory=list)

    @property
    def saldo_caja(self) -> Decimal:
        return suma(self.caja)

    @property
    def resultado_economico(self) -> Decimal:
        return suma(self.economico)

    @property
    def aportado(self) -> Decimal:
        return suma(self.reserva)


def cuentas(e: Economia, mes: date, a: Actividad, fraccion: Decimal = Decimal(1)) -> Cuentas:
    """`fraccion`: que parte del mes se mira (los fijos, la cuota y los aportes se
    prorratean; lo que paso en los fletes no)."""
    uso = costo_uso_km(e)
    ingreso = Parte("Cobrado", a.ingresos, Tipo.MEDIDO)
    usar = Parte(f"Uso de la camioneta ({a.km:.0f} km)", -a.km * uso.monto, uso.tipo)
    publi = Parte("Publicidad pagada", -a.publicidad_pagada, Tipo.MEDIDO)
    directos = Parte("Ayudantes y gastos de los fletes", -a.directos, Tipo.MEDIDO)
    estructura = [Parte(p.nombre, -p.monto * fraccion, p.tipo, p.detalle) for p in estructura_mes(e)]
    comunes = [ingreso, usar, publi, directos, *estructura]

    reserva = [Parte(p.nombre, p.monto * fraccion, p.tipo, p.detalle)
               for p in fondos_mes(e, CONSERVACION) + fondos_mes(e, PERSONAL)]
    caja = list(comunes)
    cuota = cuota_mes(e, mes)
    if cuota is not None:
        caja.append(Parte(cuota.nombre, -cuota.monto * fraccion, cuota.tipo, cuota.detalle))
    caja += [Parte(f"Apartado para {p.nombre.lower()}", -p.monto, p.tipo) for p in reserva]

    economico = list(comunes)
    sin_contar = []
    capital = None
    if cuota is not None:
        interes = e.credito.interes_mes
        if interes is None:
            sin_contar.append("el interés del crédito: no cargaste qué parte de la cuota es interés")
        else:
            economico.append(Parte("Interés del crédito", -interes * fraccion, Tipo.DECLARADO))
            capital = (cuota.monto - interes) * fraccion
    if e.camioneta.depreciacion_mes is not None:
        economico.append(Parte("Depreciación (supuesto)", -e.camioneta.depreciacion_mes * fraccion, Tipo.SUPUESTO,
                               "la cargaste como estimación"))
    else:
        sin_contar.append("la depreciación de la camioneta: no hay un criterio defendible todavía")
    return Cuentas(caja, economico, capital, reserva, sin_contar)
