"""Herramientas de decision: optimizar, medir la sensibilidad y simular el mes.

Investigacion de operaciones aplicada a una camioneta. Cuatro preguntas, cada
una con su herramienta:

- ¿Que combinacion de precio y publicidad deja mas plata? Optimizacion sobre
  una grilla, con la capacidad como restriccion: mas de los fletes que
  entran en un mes no se pueden hacer, por mas que la publicidad los traiga.
- ¿Que palanca pesa mas? Analisis de sensibilidad (tornado): cada variable
  se mueve un 20 % para cada lado, de a una, y se mide cuanto cambia la
  ganancia.
- ¿Cuanto hace falta para no perder? Punto de equilibrio.
- ¿Que tan seguro es el resultado? Simulacion Monte Carlo: miles de meses
  posibles, sacados de la incertidumbre de los datos medidos.

El modelo de demanda tiene dos supuestos que no se pueden medir con cinco
fletes, y por eso se muestran y se ajustan:

- Elasticidad al precio: si el precio sube un 10 %, el cierre baja cerca de
  un 10 % × elasticidad. Con 1,0, cobrar el doble cierra la mitad.
- Saturacion de la publicidad: duplicar el gasto multiplica las consultas
  por 2^b. Con b = 0,8, duplicar trae 1,74 veces, no 2: en una zona chica
  cada peso extra llega a gente que ya vio el anuncio.

Todo es determinista salvo el Monte Carlo, que usa una semilla fija: el
mismo dato da siempre el mismo numero, y un test lo puede comprobar.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, replace
from decimal import Decimal

from estiba.finanzas.costos import Costos

TREINTA = 30.0


@dataclass(frozen=True)
class Modelo:
    """El punto de partida (lo medido) y como responde la demanda."""

    precio: float          # lo que se cobra hoy en promedio
    gasto_dia: float       # lo que se pone hoy en Meta por dia, sin impuestos
    costo_consulta: float  # lo que cuesta hoy cada conversacion
    cierre: float          # 0..1, de las conversaciones de la publicidad
    solos_mes: float       # fletes por mes que llegan sin publicidad
    elasticidad: float = 1.0
    saturacion: float = 0.8

    def consultas(self, gasto_dia: float) -> float:
        if gasto_dia <= 0 or self.gasto_dia <= 0 or self.costo_consulta <= 0:
            return 0.0
        base = self.gasto_dia * TREINTA / self.costo_consulta
        return base * (gasto_dia / self.gasto_dia) ** self.saturacion

    def factor_precio(self, precio: float) -> float:
        """Cuanto de la demanda queda a este precio, contra el de hoy."""
        if precio <= 0 or self.precio <= 0:
            return 1.0
        return (precio / self.precio) ** (-self.elasticidad)

    def cierre_a(self, precio: float) -> float:
        return min(0.9, self.cierre * self.factor_precio(precio))


@dataclass(frozen=True)
class Resultado:
    precio: float
    gasto_dia: float
    consultas: float
    pedidos: float
    fletes: float
    ingresos: float
    costos: float
    ganancia: float

    @property
    def sobran(self) -> float:
        return max(0.0, self.pedidos - self.fletes)


def evaluar(c: Costos, m: Modelo, precio: float, gasto_dia: float) -> Resultado:
    """El mes esperado con este precio y esta publicidad."""
    consultas = m.consultas(gasto_dia)
    pedidos = consultas * m.cierre_a(precio) + m.solos_mes * m.factor_precio(precio)
    fletes = min(pedidos, float(c.fletes_por_mes_max))
    ingresos = precio * fletes
    costos = (float(c.camioneta_por_flete) * fletes + gasto_dia * TREINTA * float(c.con_impuestos)
              + float(c.fijos_mes))
    return Resultado(precio, gasto_dia, consultas, pedidos, fletes, ingresos, costos, ingresos - costos)


# --------------------------------------------------------------------------
# Optimizar
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Optimo:
    mejor: Resultado
    actual: Resultado
    #: La grilla entera, para dibujar el mapa de calor: [(precio, gasto, ganancia)].
    grilla: list[tuple[float, float, float]]
    precios: list[float]
    gastos: list[float]
    #: La combinacion mas barata en publicidad que llega a la meta, si existe.
    meta: Resultado | None


def optimizar(c: Costos, m: Modelo, pasos: int = 24, gasto_max: float | None = None) -> Optimo:
    """Recorre precios de 0,6× a 2,5× el actual y publicidad de 0 a 6× la actual."""
    p0 = m.precio or float(c.tarifa(c.km_por_flete))
    g0 = m.gasto_dia or 4000.0
    precios = [round(p0 * (0.6 + 1.9 * i / (pasos - 1)), -3) for i in range(pasos)]
    tope = gasto_max if gasto_max is not None else g0 * 6
    gastos = [round(tope * j / (pasos - 1), -2) for j in range(pasos)]
    grilla, mejor, meta = [], None, None
    for g in gastos:
        for p in precios:
            r = evaluar(c, m, p, g)
            grilla.append((p, g, r.ganancia))
            if mejor is None or r.ganancia > mejor.ganancia:
                mejor = r
            if r.ganancia >= float(c.ganancia_objetivo) and (meta is None or g < meta.gasto_dia
                                                              or (g == meta.gasto_dia and p < meta.precio)):
                meta = r
    return Optimo(mejor, evaluar(c, m, p0, g0), grilla, precios, gastos, meta)


# --------------------------------------------------------------------------
# Sensibilidad
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Palanca:
    nombre: str
    baja: float   # ganancia con la variable 20 % abajo
    sube: float   # ganancia con la variable 20 % arriba

    @property
    def rango(self) -> float:
        return abs(self.sube - self.baja)


def sensibilidad(c: Costos, m: Modelo, precio: float, gasto_dia: float, paso: float = 0.2) -> tuple[float, list[Palanca]]:
    """Cada variable ±`paso`, de a una. Devuelve la ganancia base y las palancas, de mayor a menor."""
    base = evaluar(c, m, precio, gasto_dia).ganancia
    d = Decimal(str(paso))

    def con(fn) -> tuple[float, float]:
        return fn(1 - paso, 1 - d).ganancia, fn(1 + paso, 1 + d).ganancia

    palancas = [
        Palanca("Precio", *con(lambda f, _: evaluar(c, m, precio * f, gasto_dia))),
        Palanca("Cierre", *con(lambda f, _: evaluar(c, replace(m, cierre=m.cierre * f), precio, gasto_dia))),
        Palanca("Publicidad por día", *con(lambda f, _: evaluar(c, m, precio, gasto_dia * f))),
        Palanca("Costo por conversación",
                *con(lambda f, _: evaluar(c, replace(m, costo_consulta=m.costo_consulta * f), precio, gasto_dia))),
        Palanca("Costos fijos", *con(lambda _, fd: evaluar(replace(c, credito=c.credito * fd, seguro=c.seguro * fd,
                                                                    monotributo=c.monotributo * fd,
                                                                    vtv_gnc=c.vtv_gnc * fd, patente=c.patente * fd,
                                                                    celular=c.celular * fd, otros_fijos=c.otros_fijos * fd),
                                                            m, precio, gasto_dia))),
        Palanca("Costo por km", *con(lambda _, fd: evaluar(replace(c, gnc_cada_100km=c.gnc_cada_100km * fd,
                                                                    aceite_km=c.aceite_km * fd,
                                                                    cubiertas_km=c.cubiertas_km * fd,
                                                                    arreglos_km=c.arreglos_km * fd),
                                                            m, precio, gasto_dia))),
        Palanca("Clientes que vuelven",
                *con(lambda f, _: evaluar(c, replace(m, solos_mes=m.solos_mes * f), precio, gasto_dia))),
    ]
    return base, sorted(palancas, key=lambda x: -x.rango)


def equilibrio(c: Costos, precio: float, gasto_dia: float) -> float | None:
    """Fletes por mes para no perder, a este precio y con esta publicidad. None si no hay."""
    margen = precio - float(c.camioneta_por_flete)
    if margen <= 0:
        return None
    return (float(c.fijos_mes) + gasto_dia * TREINTA * float(c.con_impuestos)) / margen


# --------------------------------------------------------------------------
# Monte Carlo
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Simulado:
    ganancias: list[float]
    p_ganar: float        # probabilidad de no perder plata
    p_meta: float         # probabilidad de llegar a la meta
    p10: float
    p50: float
    p90: float
    fletes_p50: float


def _poisson(r: random.Random, lam: float) -> int:
    """Knuth para lambda chica; normal para grande (mas de 60 consultas por mes)."""
    if lam <= 0:
        return 0
    if lam > 60:
        return max(0, round(r.gauss(lam, math.sqrt(lam))))
    limite, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= r.random()
        if p <= limite:
            return k
        k += 1


def montecarlo(c: Costos, m: Modelo, precio: float, gasto_dia: float, *,
               exitos: int, casos: int, cobrados: list[float], meses: int = 4000, semilla: int = 7) -> Simulado:
    """Miles de meses posibles, con la incertidumbre que tienen los datos.

    - El cierre no es "10 %": con 3 de 29 puede ser 4 % o 25 %. Se sortea de
      una Beta(exitos + 1, fracasos + 1), que es lo que dicen esos datos.
    - Las consultas del mes varian como una Poisson alrededor de lo esperado.
    - Cada flete se cobra como alguno de los que ya se cobraron, escalado al
      precio que se esta probando (si no hay historia, el precio justo).
    """
    r = random.Random(semilla)
    factor = m.factor_precio(precio)
    escala = [x / m.precio for x in cobrados] if cobrados and m.precio else [1.0]
    tope = int(c.fletes_por_mes_max)
    camioneta, imp, fijos = float(c.camioneta_por_flete), float(c.con_impuestos), float(c.fijos_mes)
    esperadas = m.consultas(gasto_dia)
    ganancias, fletes_mes = [], []
    for _ in range(meses):
        cierre = min(0.9, r.betavariate(exitos + 1, max(casos - exitos, 0) + 1) * factor) if casos else m.cierre_a(precio)
        consultas = _poisson(r, esperadas)
        fletes = r.binomialvariate(consultas, cierre) if consultas else 0
        fletes += _poisson(r, m.solos_mes * factor)
        fletes = min(fletes, tope)
        ingresos = sum(precio * r.choice(escala) for _ in range(fletes))
        ganancia = ingresos - camioneta * fletes - gasto_dia * TREINTA * imp - fijos
        ganancias.append(ganancia)
        fletes_mes.append(fletes)
    orden = sorted(ganancias)
    en = lambda q: orden[min(len(orden) - 1, int(q * len(orden)))]
    return Simulado(
        ganancias=ganancias,
        p_ganar=sum(1 for g in ganancias if g >= 0) / meses,
        p_meta=sum(1 for g in ganancias if g >= float(c.ganancia_objetivo)) / meses,
        p10=en(0.10), p50=en(0.50), p90=en(0.90),
        fletes_p50=sorted(fletes_mes)[meses // 2],
    )
