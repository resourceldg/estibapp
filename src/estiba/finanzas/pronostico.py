"""Como va a terminar el mes, y que hacer esta semana para llegar a la meta.

Lo de `costos.py` responde "si las cosas fueran asi, ¿que hace falta?". Esto
responde con lo que paso: cuantas conversaciones trajo la publicidad, cuantas
terminaron en un flete, cuanto se cobro y cuantos fletes vinieron sin
publicidad (el cliente que vuelve, la recomendacion).

Tres reglas para que el numero sea honesto:

- Se usa lo medido cuando alcanza para decir algo, y lo configurado cuando
  no. Siempre se dice cual de los dos.
- Con pocos casos el numero puede estar muy lejos: 5 de 28 consultas es un
  18 %, pero con tan pocos casos el cierre de verdad puede estar entre 8 % y
  36 %. Eso no se esconde: el pronostico es un rango, y el rango sale de la
  cuenta (intervalo de Wilson), no de un "mas o menos" a ojo.
- Lo que se recomienda subir se sube de a poco. La zona es chica: duplicar
  la publicidad no duplica las consultas, y eso no se sabe hasta probarlo.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from decimal import Decimal

from estiba.finanzas.costos import Costos, plan_a_capacidad, plan_con_precio, redondear

#: Con menos que esto, lo medido todavia es ruido: se usa lo configurado.
MINIMO_CONSULTAS = 15
MINIMO_FLETES = 3
#: Cuanto se puede subir la publicidad de una semana a la otra sin perder de
#: vista si el costo por conversacion se dispara.
SUBA_MAXIMA = Decimal("1.5")


def wilson(exitos: int, casos: int, z: float = 1.96) -> tuple[float, float]:
    """Entre que valores esta la proporcion real, con 95 % de confianza.

    Mejor que el "18 % ± algo" de siempre cuando hay pocos casos o una
    proporcion chica: nunca da negativos ni mas de 100 %.
    """
    if casos == 0:
        return 0.0, 1.0
    p = exitos / casos
    centro = (p + z * z / (2 * casos)) / (1 + z * z / casos)
    margen = z * math.sqrt(p * (1 - p) / casos + z * z / (4 * casos * casos)) / (1 + z * z / casos)
    return max(0.0, centro - margen), min(1.0, centro + margen)


@dataclass(frozen=True)
class Historia:
    """Lo que paso en la ventana que se mira (lo arma la app con sus datos)."""

    dias: int
    consultas_publicidad: int     # conversaciones que llegaron por un anuncio
    hechos_publicidad: int        # de esas, cuantas terminaron en un flete hecho
    hechos_sin_publicidad: int    # el que vuelve, el recomendado
    gasto: Decimal                # lo gastado en Meta en la ventana, sin impuestos
    cobrado: list[Decimal]        # lo cobrado en cada flete hecho
    #: Las conversaciones que cuenta Meta, si se cargaron. Mandan sobre las
    #: que reconoce Estiba para el costo por conversacion y el cierre.
    consultas_meta: int | None = None


@dataclass(frozen=True)
class Dato:
    valor: Decimal
    medido: bool
    detalle: str


@dataclass(frozen=True)
class Escenario:
    nombre: str
    cierre: float
    fletes: int
    ingresos: Decimal
    ganancia: Decimal


@dataclass(frozen=True)
class Pronostico:
    cierre: Dato
    costo_consulta: Dato
    ticket: Dato
    sin_publicidad_mes: Decimal       # fletes por mes que llegan solos
    gasto_dia: Decimal                # lo que se esta poniendo por dia, sin impuestos
    escenarios: list[Escenario]       # pesimista, probable, optimista
    pocos_datos: bool
    #: Lo que hay que hacer esta semana, en orden.
    semana: list[str]


def _consultas(h: Historia) -> tuple[int, str]:
    if h.consultas_meta:
        return h.consultas_meta, "según Meta"
    return h.consultas_publicidad, "que reconoció Estiba"


def _cierre(c: Costos, h: Historia) -> tuple[Dato, tuple[float, float]]:
    consultas, segun = _consultas(h)
    if consultas >= MINIMO_CONSULTAS:
        hechos = min(h.hechos_publicidad, consultas)
        p = hechos / consultas
        lo, hi = wilson(hechos, consultas)
        return (Dato(Decimal(str(round(p * 100, 1))), True,
                     f"{hechos} fletes de {consultas} conversaciones de la publicidad ({segun})"),
                (lo, hi))
    p = float(c.cierre) / 100
    # Sin datos propios el rango es el de no saber: la mitad y una vez y media.
    return Dato(c.cierre, False, "el que cargaste: todavía no hay consultas suficientes"), (p / 2, min(1.0, p * 1.5))


def pronosticar(c: Costos, h: Historia) -> Pronostico:
    cierre, (lo, hi) = _cierre(c, h)
    consultas, segun = _consultas(h)
    if consultas >= MINIMO_CONSULTAS and h.gasto > 0:
        costo_consulta = Dato((h.gasto / consultas).quantize(Decimal(1)), True,
                              f"lo gastado en Meta dividido {consultas} conversaciones ({segun})")
    else:
        costo_consulta = Dato(c.costo_por_consulta, False, "el que cargaste")
    if len(h.cobrado) >= MINIMO_FLETES:
        ticket = Dato(sum(h.cobrado, Decimal(0)) / len(h.cobrado), True,
                      f"promedio de {len(h.cobrado)} fletes cobrados")
    else:
        ticket = Dato(c.tarifa(c.km_por_flete), False, "tu tarifa para un flete típico")

    dias = max(h.dias, 1)
    gasto_dia = h.gasto / dias
    sin_publicidad_mes = Decimal(h.hechos_sin_publicidad) * 30 / dias
    consultas_mes = gasto_dia * 30 / costo_consulta.valor if costo_consulta.valor else Decimal(0)

    escenarios = []
    for nombre, p in (("Si viene flojo", lo), ("Lo más probable", float(cierre.valor) / 100), ("Si viene bien", hi)):
        fletes = consultas_mes * Decimal(str(p)) + sin_publicidad_mes
        fletes = min(fletes, c.fletes_por_mes_max)
        n = int(fletes.to_integral_value())
        ingresos = ticket.valor * n
        costos = c.camioneta_por_flete * n + gasto_dia * 30 * c.con_impuestos + c.fijos_mes
        escenarios.append(Escenario(nombre, p, n, ingresos, ingresos - costos))

    pocos = not (cierre.medido and ticket.medido)
    medido = replace(c, cierre=Decimal(str(float(cierre.valor))), costo_por_consulta=costo_consulta.valor)
    return Pronostico(cierre, costo_consulta, ticket, sin_publicidad_mes, gasto_dia, escenarios, pocos,
                      _semana(medido, ticket.valor, gasto_dia, sin_publicidad_mes, pocos))


def _semana(c: Costos, ticket: Decimal, gasto_dia: Decimal, solos_mes: Decimal, pocos: bool) -> list[str]:
    """Tres o cuatro cosas concretas, con numeros, en el orden en que pesan."""
    from estiba.formato import pesos

    pasos = []
    tipico = c.tarifa(c.km_por_flete)
    precio = max(ticket, tipico)
    if ticket < tipico * Decimal("0.9"):
        pasos.append(
            f"Cobrá como tu tarifa: un flete típico son {pesos(tipico)} y venís cobrando "
            f"{pesos(redondear(ticket))}. Es lo que más mueve la cuenta, y no cuesta publicidad.")
    plan = plan_con_precio(c, precio)
    if plan is None or not plan.entra:
        # A ese precio los fletes no entran en el mes: la meta sale de
        # trabajar a pleno y cobrar lo que haga falta, no de un numero de
        # fletes que no se puede hacer.
        plan = plan_a_capacidad(c)
        if plan is None:
            return pasos
        precio = plan.precio
        pasos.append(
            f"Con {int(c.fletes_por_mes_max)} fletes por mes como máximo, para ganar {pesos(c.ganancia_objetivo)} "
            f"cada flete tiene que dejar {pesos(redondear(precio))} en promedio. Si eso es más de lo que "
            "paga la zona, la meta pide otra cosa: un ayudante fijo para hacer dos por turno, o una "
            "meta intermedia primero.")
    semana = math.ceil(plan.fletes / Decimal("4.3"))
    pasos.append(f"Meta de la semana: {semana} fletes a {pesos(redondear(precio))} en promedio "
                 f"({plan.fletes} en el mes).")
    # Lo que ya llega solo no hay que pagarlo con publicidad.
    con_publicidad = max(Decimal(0), Decimal(plan.fletes) - solos_mes)
    necesario_dia = c.costo_por_consulta * con_publicidad / (c.cierre / 100) / 30 if c.cierre > 0 else None
    if necesario_dia is not None and gasto_dia > 0:
        if necesario_dia > gasto_dia * SUBA_MAXIMA:
            siguiente = redondear(gasto_dia * SUBA_MAXIMA, Decimal(500))
            pasos.append(
                f"Publicidad: para la meta harían falta {pesos(redondear(necesario_dia, Decimal(500)))} por día. "
                f"Subí de {pesos(redondear(gasto_dia, Decimal(500)))} a {pesos(siguiente)} esta semana, no más: "
                "si el costo por conversación no se dispara, la semana que viene otro escalón.")
        elif necesario_dia < gasto_dia * Decimal("0.8"):
            pasos.append(
                f"Publicidad: con {pesos(redondear(necesario_dia, Decimal(500)))} por día alcanza; "
                f"hoy ponés {pesos(redondear(gasto_dia, Decimal(500)))}.")
        else:
            pasos.append(f"Publicidad: seguí con {pesos(redondear(gasto_dia, Decimal(500)))} por día.")
    elif necesario_dia is not None:
        pasos.append(f"Publicidad: para la meta, unos {pesos(redondear(necesario_dia, Decimal(500)))} por día. "
                     "Cargá lo que gastás para que Estiba lo mida.")
    if solos_mes >= 1:
        pasos.append(f"Te llegan unos {int(solos_mes)} fletes por mes sin publicidad (clientes que vuelven o "
                     "recomendados): escribile a los que ya te contrataron, es el cliente más barato.")
    if pocos:
        pasos.append("Con tan pocos fletes estos números se mueven mucho. Marcá cada flete hecho con lo que "
                     "cobraste: el pronóstico se afina solo.")
    return pasos
