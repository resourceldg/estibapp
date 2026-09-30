"""Un numero con su procedencia.

Todo lo que el motor devuelve lleva de donde sale, para que la pantalla
nunca muestre mas precision de la que tiene el dato:

- declarado: lo cargo el fletero (la cuota, el seguro). Es un hecho.
- medido: sale de lo que paso (lo cobrado, los km de un flete cerrado).
- calculado: una cuenta sobre declarados y medidos, sin supuestos.
- estimado: una cuenta en la que entro al menos un supuesto.
- supuesto: un valor puesto para poder calcular, sin dato detras
  (el valor por defecto de un costo, la velocidad promedio).
- falta: no hay con que calcularlo.

La confianza *estadistica* (cuanto creerle a un promedio de pocos casos) es
otra cosa y la decide el motor de suficiencia, no esta etiqueta.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal
from enum import Enum

CERO = Decimal(0)


class Tipo(Enum):
    DECLARADO = "declarado"
    MEDIDO = "medido"
    CALCULADO = "calculado"
    ESTIMADO = "estimado"
    SUPUESTO = "supuesto"
    FALTA = "falta"


#: De mas firme a mas debil. Una cuenta vale lo que su insumo mas debil.
_ORDEN = [Tipo.DECLARADO, Tipo.MEDIDO, Tipo.CALCULADO, Tipo.ESTIMADO, Tipo.SUPUESTO, Tipo.FALTA]


def combinar(*tipos: Tipo) -> Tipo:
    """El tipo de una cuenta hecha con estos insumos."""
    if not tipos:
        return Tipo.CALCULADO
    peor = max(tipos, key=_ORDEN.index)
    if peor is Tipo.FALTA:
        return Tipo.FALTA
    if peor is Tipo.SUPUESTO or peor is Tipo.ESTIMADO:
        return Tipo.ESTIMADO
    return Tipo.CALCULADO


@dataclass(frozen=True)
class Valor:
    monto: Decimal | None
    tipo: Tipo
    detalle: str = ""

    @classmethod
    def falta(cls, detalle: str) -> Valor:
        return cls(None, Tipo.FALTA, detalle)


@dataclass(frozen=True)
class Parte:
    """Un renglon de un desglose."""

    nombre: str
    monto: Decimal
    tipo: Tipo
    detalle: str = ""


def suma(partes: list[Parte]) -> Decimal:
    return sum((p.monto for p in partes), CERO)


def tipo_de(partes: list[Parte]) -> Tipo:
    return combinar(*(p.tipo for p in partes))


def redondear(monto: Decimal, a: Decimal = Decimal(1000)) -> Decimal:
    return (monto / a).quantize(Decimal(1), ROUND_HALF_UP) * a


def redondear_arriba(monto: Decimal, a: Decimal = Decimal(1000)) -> Decimal:
    """Para precios: redondear para abajo regala plata sin decirlo."""
    return (monto / a).quantize(Decimal(1), ROUND_CEILING) * a
