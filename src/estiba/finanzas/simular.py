"""¿Y si...? Cobrar otro precio, poner otra plata en Meta, cerrar mas consultas.

La calculadora del tablero. Se mueve una perilla y se ve el mes entero:
cuantas consultas, cuantos fletes, cuanto se factura, en que se va y cuanto
queda contra la meta. Misma cuenta que el pronostico, pero con los numeros
que uno quiere probar en vez de los que vinieron pasando.

Dos cosas que una planilla no avisa y esto si:

- Los fletes no pueden pasar de los que entran en un mes. Si la publicidad
  trae mas pedidos de los que se pueden hacer, sobran: se paga por
  consultas que no se atienden.
- El precio minimo cambia con el volumen. Se dice a que volumen se esta.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from estiba.finanzas.costos import Costos

TREINTA = Decimal(30)


@dataclass(frozen=True)
class Simulacion:
    consultas: int
    pedidos: Decimal        # los fletes que traeria, sin tope
    fletes: int             # los que se pueden hacer
    sobran: int             # pedidos que no entran en el mes
    ingresos: Decimal
    camioneta: Decimal
    publicidad: Decimal     # con impuestos
    fijos: Decimal
    ganancia: Decimal
    falta: Decimal          # para la meta; negativo si se pasa
    minimo: Decimal | None  # el precio minimo a ese volumen

    @property
    def costos(self) -> Decimal:
        return self.camioneta + self.publicidad + self.fijos

    @property
    def margen(self) -> float | None:
        return float(self.ganancia / self.ingresos) if self.ingresos else None


def simular(c: Costos, *, precio: Decimal, gasto_dia: Decimal, costo_consulta: Decimal,
            cierre: Decimal, solos_mes: Decimal) -> Simulacion:
    """`cierre` de 0 a 100; `solos_mes`: fletes por mes que llegan sin publicidad."""
    consultas = gasto_dia * TREINTA / costo_consulta if costo_consulta > 0 else Decimal(0)
    pedidos = consultas * cierre / 100 + solos_mes
    tope = c.fletes_por_mes_max
    # La plata se cuenta sobre lo esperado (5,7 fletes), no sobre el numero
    # redondeado: redondear a 6 inflaba la ganancia en un flete entero y la
    # calculadora no coincidia con la optimizacion.
    esperados = min(pedidos, tope)
    fletes = int(esperados.to_integral_value())
    ingresos = precio * esperados
    camioneta = c.camioneta_por_flete * esperados
    publicidad = gasto_dia * TREINTA * c.con_impuestos
    ganancia = ingresos - camioneta - publicidad - c.fijos_mes
    return Simulacion(
        consultas=int(consultas.to_integral_value()),
        pedidos=pedidos,
        fletes=fletes,
        sobran=max(0, int((pedidos - tope).to_integral_value())),
        ingresos=ingresos,
        camioneta=camioneta,
        publicidad=publicidad,
        fijos=c.fijos_mes,
        ganancia=ganancia,
        falta=c.ganancia_objetivo - ganancia,
        # Salir hecho a ese volumen y con esa publicidad: todo lo que cuesta
        # el mes, repartido entre los fletes que se hacen.
        minimo=(camioneta + publicidad + c.fijos_mes) / esperados if esperados > 0 else None,
    )
