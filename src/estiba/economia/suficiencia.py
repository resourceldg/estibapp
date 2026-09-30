"""¿Tenemos suficientes datos para confiar en esta conclusion puntual?

La suficiencia no es "hay mas de N casos". Depende de la pregunta, de la
variable, del segmento y de la incertidumbre real. Cada metrica se evalua
por separado, con su propia tecnica:

- Medias (ticket, margen, horas, km): bootstrap con semilla fija sobre
  observaciones pesadas; intervalo por percentiles.
- Proporciones (conversion, recurrencia): intervalo de Wilson. La
  recurrencia solo cuenta clientes observados el tiempo suficiente
  (cohorte): uno que llego la semana pasada todavia no tuvo tiempo de volver.
- Elasticidad de precio: sin variacion real de precio entre fletes
  comparables no hay nada que medir. Es un supuesto, y se dice.
- Eventos raros (roturas grandes): hace falta exposicion (km y meses), no
  cantidad de fletes. Con cero eventos, la regla de tres da una cota.
- Segmentos: se evaluan con SUS datos. Muchos fletes urbanos no dicen nada
  de Miramar.

Cada observacion pesa segun:

    peso = antiguedad (vida media 120 dias) × calidad (medido 1, estimado 0,6,
           supuesto 0,3) × vigencia (0,5 si es anterior a un cambio de condiciones)

y los pesos se agrupan por cliente antes de calcular el tamano efectivo de
muestra (Kish): diez fletes de un mismo cliente no son diez clientes.

Estado (el mas bajo que permita cualquiera de las dimensiones):

    ROBUSTO      precision <= 10 %, n_ef >= 20, un cliente <= 25 %, faltantes <= 20 %, >= 60 dias
    UTILIZABLE   precision <= 20 %, n_ef >= 8,  un cliente <= 40 %, faltantes <= 50 %
    EMERGENTE    precision <= 40 %, n_ef >= 3
    EXPLORATORIO lo demas
    y si el dato mas nuevo tiene mas de 120 dias, no pasa de EMERGENTE.

`precision` es el semiancho relativo del intervalo del 95 %. Los umbrales
estan todos aca, a la vista, y un test fija cada uno.

La regla central: la precision visual nunca es mayor que la de los datos
(`mostrar`), y si el umbral de una decision cae dentro del intervalo, no se
da una recomendacion fuerte (`cambia_la_decision`).
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum

from estiba.finanzas.pronostico import wilson

VIDA_MEDIA_DIAS = 120
CALIDAD = {"medido": 1.0, "estimado": 0.6, "supuesto": 0.3}
PESO_ANTES_DEL_CAMBIO = 0.5
VIEJO_DIAS = 120
REMUESTREOS = 2000
SEMILLA = 11


class Estado(Enum):
    EXPLORATORIO = 0
    EMERGENTE = 1
    UTILIZABLE = 2
    ROBUSTO = 3

    @property
    def etiqueta(self) -> str:
        return {0: "exploratorio", 1: "emergente", 2: "utilizable", 3: "robusto"}[self.value]


#: (estado, precision maxima, n efectivo minimo, concentracion maxima, faltantes maximos, dias minimos)
UMBRALES = (
    (Estado.ROBUSTO, 0.10, 20.0, 0.25, 0.20, 60),
    (Estado.UTILIZABLE, 0.20, 8.0, 0.40, 0.50, 0),
    (Estado.EMERGENTE, 0.40, 3.0, 1.00, 1.00, 0),
)


@dataclass(frozen=True)
class Obs:
    valor: float
    dia: date
    cliente: str | None = None
    calidad: str = "medido"
    segmento: str | None = None


@dataclass(frozen=True)
class Suficiencia:
    metrica: str
    estado: Estado
    valor: float | None
    lo: float | None
    hi: float | None
    n: int
    n_efectivo: float
    razones: list[str] = field(default_factory=list)
    #: Lo que mas haria falta aprender para mejorar esta metrica.
    falta: str = ""

    @property
    def precision(self) -> float | None:
        if self.valor in (None, 0) or self.lo is None or self.hi is None:
            return None
        return (self.hi - self.lo) / 2 / abs(self.valor)

    @property
    def fuerte(self) -> bool:
        """Si alcanza para una recomendacion firme."""
        return self.estado.value >= Estado.UTILIZABLE.value


# --------------------------------------------------------------------------
# Pesos y tamano efectivo
# --------------------------------------------------------------------------


def peso(o: Obs, hoy: date, cambio: date | None = None) -> float:
    edad = max(0, (hoy - o.dia).days)
    w = 0.5 ** (edad / VIDA_MEDIA_DIAS) * CALIDAD.get(o.calidad, 0.3)
    if cambio is not None and o.dia < cambio:
        w *= PESO_ANTES_DEL_CAMBIO
    return w


def n_efectivo(obs: list[Obs], pesos: list[float]) -> float:
    """Kish sobre los pesos agrupados por cliente (sin cliente: cada uno aparte)."""
    grupos: dict[str, float] = {}
    for i, (o, w) in enumerate(zip(obs, pesos)):
        grupos[o.cliente or f"_{i}"] = grupos.get(o.cliente or f"_{i}", 0.0) + w
    s, s2 = sum(grupos.values()), sum(w * w for w in grupos.values())
    return s * s / s2 if s2 > 0 else 0.0


def concentracion(obs: list[Obs], pesos: list[float]) -> float:
    """La parte del peso que se lleva el cliente mas grande."""
    grupos: dict[str, float] = {}
    for i, (o, w) in enumerate(zip(obs, pesos)):
        grupos[o.cliente or f"_{i}"] = grupos.get(o.cliente or f"_{i}", 0.0) + w
    total = sum(grupos.values())
    return max(grupos.values()) / total if total else 1.0


def _estado(precision: float | None, n_ef: float, conc: float, faltantes: float, dias: int,
            mas_nuevo_dias: int | None) -> tuple[Estado, list[str]]:
    razones = []
    estado = Estado.EXPLORATORIO
    for est, p_max, n_min, c_max, f_max, d_min in UMBRALES:
        if (precision is not None and precision <= p_max and n_ef >= n_min and conc <= c_max
                and faltantes <= f_max and dias >= d_min):
            estado = est
            break
    # Lo que le falta para el escalon de arriba, en palabras.
    siguiente = next((u for u in reversed(UMBRALES) if u[0].value == estado.value + 1), None)
    if precision is None:
        razones.append("no hay con qué calcular un intervalo")
    elif siguiente and precision > siguiente[1]:
        razones.append(f"el intervalo es ancho (±{precision:.0%})")
    if siguiente and n_ef < siguiente[2]:
        razones.append(f"muestra efectiva de {n_ef:.1f}".replace(".", ","))
    if siguiente and conc > siguiente[3]:
        razones.append(f"un solo cliente pesa {conc:.0%}")
    if siguiente and faltantes > siguiente[4]:
        razones.append(f"{faltantes:.0%} de los datos son estimaciones, no mediciones")
    if siguiente and dias < siguiente[5]:
        razones.append(f"solo {dias} días de observación")
    if mas_nuevo_dias is not None and mas_nuevo_dias > VIEJO_DIAS:
        razones.append(f"el dato más nuevo tiene {mas_nuevo_dias} días")
        if estado.value > Estado.EMERGENTE.value:
            estado = Estado.EMERGENTE
    return estado, razones


# --------------------------------------------------------------------------
# Medias con bootstrap
# --------------------------------------------------------------------------


def media(metrica: str, obs: list[Obs], hoy: date, *, cambio: date | None = None,
          segmento: str | None = None, falta: str = "") -> Suficiencia:
    if segmento is not None:
        obs = [o for o in obs if o.segmento == segmento]
    if not obs:
        que = f"de {segmento}" if segmento else ""
        return Suficiencia(metrica, Estado.EXPLORATORIO, None, None, None, 0, 0.0,
                           [f"no hay ningún dato {que}".strip()], falta or f"Registrar fletes {que}".strip())
    pesos = [peso(o, hoy, cambio) for o in obs]
    total = sum(pesos)
    valor = sum(o.valor * w for o, w in zip(obs, pesos)) / total if total else None
    lo = hi = None
    if len(obs) >= 2 and total > 0:
        r = random.Random(SEMILLA)
        valores = [o.valor for o in obs]
        medias = []
        for _ in range(REMUESTREOS):
            idx = r.choices(range(len(obs)), weights=pesos, k=len(obs))
            medias.append(sum(valores[i] for i in idx) / len(idx))
        medias.sort()
        lo, hi = medias[int(0.025 * REMUESTREOS)], medias[int(0.975 * REMUESTREOS) - 1]
        # Todos iguales: el bootstrap no ve incertidumbre, pero con pocos casos la hay.
        if hi == lo and len(obs) < 8:
            lo = hi = None
    n_ef = n_efectivo(obs, pesos)
    conc = concentracion(obs, pesos)
    faltantes = sum(1 for o in obs if o.calidad != "medido") / len(obs)
    dias = (max(o.dia for o in obs) - min(o.dia for o in obs)).days
    s = Suficiencia(metrica, Estado.EXPLORATORIO, valor, lo, hi, len(obs), n_ef)
    estado, razones = _estado(s.precision, n_ef, conc, faltantes, dias, (hoy - max(o.dia for o in obs)).days)
    return Suficiencia(metrica, estado, valor, lo, hi, len(obs), n_ef, razones,
                       falta or f"Más casos de {metrica}")


# --------------------------------------------------------------------------
# Proporciones con Wilson, y cohortes
# --------------------------------------------------------------------------


def proporcion(metrica: str, exitos: int, casos: int, *, dias_observados: int = 0,
               falta: str = "") -> Suficiencia:
    if casos <= 0:
        return Suficiencia(metrica, Estado.EXPLORATORIO, None, None, None, 0, 0.0,
                           ["no hay casos"], falta or f"Casos de {metrica}")
    p = exitos / casos
    lo, hi = wilson(exitos, casos)
    # Para proporciones la precision se mide en puntos, contra el valor o
    # contra 25 puntos si el valor es chico: 20 % ± 2,5 puntos es solido,
    # aunque sea ±12 % relativo.
    semi = (hi - lo) / 2
    precision = semi / max(p, 0.25)
    estado, razones = _estado(precision, float(casos), 0.0, 0.0, dias_observados, None)
    return Suficiencia(metrica, estado, p, lo, hi, casos, float(casos), razones,
                       falta or f"Más casos de {metrica}")


@dataclass(frozen=True)
class Cliente:
    id: str
    primer_flete: date
    fletes: int


def recurrencia(clientes: list[Cliente], hoy: date, madurez_dias: int = 60) -> Suficiencia:
    """Que parte de los clientes vuelve. Solo cuentan los que ya tuvieron tiempo."""
    maduros = [c for c in clientes if (hoy - c.primer_flete).days >= madurez_dias]
    jovenes = len(clientes) - len(maduros)
    s = proporcion("recurrencia", sum(1 for c in maduros if c.fletes > 1), len(maduros),
                   dias_observados=max(((hoy - c.primer_flete).days for c in maduros), default=0),
                   falta="Tiempo: que los clientes de este mes tengan tiempo de volver")
    razones = list(s.razones)
    if jovenes:
        razones.append(f"{jovenes} {'cliente es' if jovenes == 1 else 'clientes son'} muy "
                       f"{'nuevo' if jovenes == 1 else 'nuevos'} para saber si vuelve")
    return Suficiencia(s.metrica, s.estado, s.valor, s.lo, s.hi, s.n, s.n_efectivo, razones, s.falta)


# --------------------------------------------------------------------------
# Casos especiales
# --------------------------------------------------------------------------


def elasticidad(precios: list[float], minimo_variacion: float = 0.10, minimo_casos: int = 20) -> Suficiencia:
    """Sin variacion real de precio entre fletes comparables, la elasticidad es un supuesto."""
    if len(precios) < 2:
        return Suficiencia("elasticidad", Estado.EXPLORATORIO, None, None, None, len(precios), 0.0,
                           ["es un supuesto: no hay precios distintos para comparar"],
                           "Probar precios distintos en fletes parecidos")
    m = sum(precios) / len(precios)
    cv = math.sqrt(sum((p - m) ** 2 for p in precios) / (len(precios) - 1)) / m if m else 0.0
    if cv < minimo_variacion or len(precios) < minimo_casos:
        return Suficiencia("elasticidad", Estado.EXPLORATORIO, None, None, None, len(precios), float(len(precios)),
                           [f"es un supuesto: los precios varían {cv:.0%} en {len(precios)} fletes"],
                           "Probar precios distintos en fletes parecidos")
    return Suficiencia("elasticidad", Estado.EMERGENTE, None, None, None, len(precios), float(len(precios)),
                       ["hay variación de precio, pero todavía no un modelo"], "Más fletes con precios distintos")


def eventos_raros(metrica: str, eventos: int, km_expuestos: float, meses: float,
                  km_necesarios: float = 100_000, meses_necesarios: float = 24) -> Suficiencia:
    """Roturas grandes: la exposicion manda. Con 0 eventos, regla de tres: tasa < 3/km."""
    tasa = eventos / km_expuestos if km_expuestos > 0 else None
    cota = 3 / km_expuestos if eventos == 0 and km_expuestos > 0 else None
    razones = [f"{km_expuestos:,.0f} km y {meses:.0f} meses observados".replace(",", ".")]
    if km_expuestos < 5_000:
        razones = ["muy poca exposición todavía: hacen falta años y decenas de miles de km"]
    elif cota is not None:
        razones.append(f"sin eventos todavía: la tasa podría ser de hasta 1 cada {1 / cota:,.0f} km".replace(",", "."))
    if km_expuestos >= km_necesarios and meses >= meses_necesarios and eventos >= 3:
        estado = Estado.UTILIZABLE
    elif km_expuestos >= km_necesarios / 4:
        estado = Estado.EMERGENTE
    else:
        estado = Estado.EXPLORATORIO
    return Suficiencia(metrica, estado, tasa, None, cota, eventos, float(eventos), razones,
                       "Registrar reparaciones con km y fecha, durante años")


# --------------------------------------------------------------------------
# Decisiones y presentacion
# --------------------------------------------------------------------------


def cambia_la_decision(s: Suficiencia, umbral: float) -> bool:
    """True si el umbral cae dentro del intervalo: el dato no alcanza para decidir."""
    if s.lo is None or s.hi is None:
        return True
    return s.lo <= umbral <= s.hi


def lo_que_mas_falta(metricas: list[tuple[Suficiencia, float]]) -> Suficiencia | None:
    """El dato que mas conviene aprender: el mas debil, pesado por cuanto importa."""
    candidatas = [(s, imp) for s, imp in metricas if s.estado is not Estado.ROBUSTO]
    if not candidatas:
        return None
    return min(candidatas, key=lambda x: (x[0].estado.value, -x[1]))[0]


def mostrar(valor: float | Decimal | None, s: Suficiencia, unidad: str = "$") -> str:
    """Un numero con la precision que tienen sus datos, nunca mas.

    exploratorio -> el rango ("entre $30.000 y $55.000"), o "sin datos"
    emergente    -> redondeado a $5.000 y "aprox."
    utilizable   -> a $1.000
    robusto      -> a $1.000
    """
    if valor is None:
        return "sin datos"

    def fmt(x, paso):
        x = (Decimal(str(x)) / paso).quantize(Decimal(1), ROUND_HALF_UP) * paso
        if unidad == "$":
            return f"${int(x):,}".replace(",", ".")
        return f"{x:g} {unidad}".strip()

    paso_fino = Decimal(1000) if unidad == "$" else Decimal(1)
    if s.estado is Estado.EXPLORATORIO:
        if s.lo is not None and s.hi is not None and fmt(s.lo, paso_fino) != fmt(s.hi, paso_fino):
            return f"entre {fmt(s.lo, paso_fino)} y {fmt(s.hi, paso_fino)}"
        if s.lo is not None:
            return f"~{fmt(valor, paso_fino)} (pocos datos)"
        return f"~{fmt(valor, paso_fino * 5)} (supuesto)"
    if s.estado is Estado.EMERGENTE:
        return f"~{fmt(valor, paso_fino * 5)}"
    return fmt(valor, paso_fino)
