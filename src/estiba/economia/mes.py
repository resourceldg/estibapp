"""¿Como estoy este mes? La cascada de la camioneta.

Lo que dejan los fletes (cobrado − ayudantes − publicidad pagada) baja por
escalones, en este orden, y cada uno se llena o no:

    1 Cubre el uso      GNC, aceite, cubiertas y desgaste de los km del mes
    2 Paga su estructura seguro, patente, VTV/GNC, monotributo, celular
    3 Se paga sola      + la cuota del credito
    4 Se conserva       + los fondos de mantenimiento, reparaciones y reposicion
    5 Te da vida        + los fondos personales (viajes)
    6 Deja libre        + la ganancia libre que se quiere

Es una cuenta de caja: la cuota entra entera. El resultado economico
(cuentas.py) es otra pregunta.

    Autonomia = disponible / (uso + estructura + cuota + fondos de conservacion)

118 % quiere decir: se pago y se conservo, y sobro un 18 % de eso.

Dia de autofinanciamiento: el primer dia en que lo acumulado cubre el
escalon 3. Horas para autofinanciar: las horas de los fletes hasta ese dia.

Prioridades: de 1 a 3, ordenadas. Cuando se cumplen, el mes esta cumplido
y Estiba lo dice: no hace falta tomar mas trabajo.
"""

from __future__ import annotations

import calendar
import math
import statistics
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from estiba.economia.config import CONSERVACION, PERSONAL, Economia
from estiba.economia.estructura import costo_uso_km, cuota_mes, estructura_mes, fondos_mes, ganancia_libre, horas_deseadas
from estiba.economia.modelo import CERO, Tipo, suma


@dataclass(frozen=True)
class FleteDelMes:
    job_id: str
    dia: date
    cobrado: Decimal
    km: Decimal
    km_vacios: Decimal | None
    horas: Decimal
    directos: Decimal = CERO
    recurrente: bool = False
    #: True si km y horas salen de un cierre confirmado o corregido.
    medido: bool = False


@dataclass(frozen=True)
class Escalon:
    nivel: int
    nombre: str
    #: Lo que cuesta este escalon solo.
    monto: Decimal
    #: Lo que cuesta llegar hasta aca (este y los de abajo).
    acumulado: Decimal
    #: 0..1: cuanto de este escalon se lleno.
    lleno: float
    detalle: str = ""


NOMBRES = {1: "Cubre el uso", 2: "Paga su estructura", 3: "Se paga sola", 4: "Se conserva",
           5: "Te da vida", 6: "Deja libre"}

PRIORIDADES = {
    "se_paga_sola": ("Que la camioneta se pague sola (cuota incluida)", 3),
    "se_conserva": ("Apartar los fondos de conservación", 4),
    "te_da_vida": ("Juntar los fondos personales (viajes)", 5),
    "deja_libre": ("Llegar a la ganancia libre que querés", 6),
    "tope_horas": ("No pasar tus horas deseadas", None),
}
PRIORIDADES_INICIALES = ("se_paga_sola", "se_conserva", "tope_horas")


def prioridades_desde(lista) -> tuple[str, ...]:
    """De 1 a 3, sin repetir y solo de las conocidas: pocas prioridades a la vez."""
    salida = []
    for p in lista or ():
        if p in PRIORIDADES and p not in salida:
            salida.append(p)
    return tuple(salida[:3]) or PRIORIDADES_INICIALES


@dataclass(frozen=True)
class Mes:
    desde: date
    hasta: date  # hoy, o el ultimo dia si el mes termino
    dias_mes: int
    fletes: list[FleteDelMes]
    ingresos: Decimal
    publicidad: Decimal
    directos: Decimal
    disponible: Decimal
    km: Decimal
    km_vacios: Decimal | None
    horas: Decimal
    horas_deseadas: Decimal | None
    escalones: list[Escalon]
    nivel: int
    autonomia: float | None
    dia_autofinanciado: date | None
    horas_para_autofinanciar: Decimal | None
    #: Cuanto falta para el escalon 3 y cuantos fletes como los ultimos.
    falta_para_pagarse: Decimal
    fletes_que_faltan: int | None
    margen_tipico: Decimal | None
    margen_tipico_casos: int
    prioridades: tuple[str, ...]
    cumplidas: dict[str, bool]
    objetivo_cumplido: bool
    #: Horas y km salen de cierres medidos, o de estimaciones.
    medidos: int
    #: Cuantos fletes entran en lo que queda del mes, al ritmo comodo (no al pico).
    capacidad_restante: Decimal = CERO
    avisos: list[str] = field(default_factory=list)

    @property
    def deadhead(self) -> float | None:
        return float(self.km_vacios / self.km) if self.km_vacios is not None and self.km else None

    @property
    def recurrentes(self) -> float | None:
        return sum(1 for f in self.fletes if f.recurrente) / len(self.fletes) if self.fletes else None

    @property
    def ticket(self) -> Decimal | None:
        return self.ingresos / len(self.fletes) if self.fletes else None


def _escalones(e: Economia, mes: date, uso: Decimal) -> list[tuple[int, Decimal, str]]:
    cuota = cuota_mes(e, mes)
    libre = ganancia_libre(e)
    return [
        (1, uso, "GNC, aceite, cubiertas y desgaste de los km del mes"),
        (2, suma(estructura_mes(e)), "seguro, patente, VTV/GNC, monotributo, celular"),
        (3, cuota.monto if cuota else CERO, "cuota del crédito" if cuota else "sin cuota este mes"),
        (4, suma(fondos_mes(e, CONSERVACION)), "fondos de mantenimiento y reposición"),
        (5, suma(fondos_mes(e, PERSONAL)), "fondos personales"),
        (6, libre.monto if libre else CERO, "la ganancia libre que querés"),
    ]


def _alcanzado(esc: Escalon, disponible: Decimal) -> bool:
    if esc.nivel > 3 and esc.monto == 0:
        return False
    return disponible >= esc.acumulado


def margen(f: FleteDelMes, uso_km: Decimal) -> Decimal:
    return f.cobrado - f.km * uso_km - f.directos


def calcular(e: Economia, hoy: date, fletes: list[FleteDelMes], publicidad_por_dia: dict[date, Decimal],
             historia: list[FleteDelMes] = (), prioridades: tuple[str, ...] = PRIORIDADES_INICIALES) -> Mes:
    """`fletes`: los hechos del mes de `hoy`. `historia`: los hechos antes, para el margen tipico.

    `publicidad_por_dia`: lo pagado en publicidad cada dia del mes (prorrateado).
    """
    desde = hoy.replace(day=1)
    dias_mes = calendar.monthrange(hoy.year, hoy.month)[1]
    del_mes = sorted((f for f in fletes if desde <= f.dia <= hoy), key=lambda f: f.dia)
    uso_km = costo_uso_km(e).monto
    ingresos = sum((f.cobrado for f in del_mes), CERO)
    directos = sum((f.directos for f in del_mes), CERO)
    publicidad = sum((m for d, m in publicidad_por_dia.items() if desde <= d <= hoy), CERO)
    disponible = ingresos - directos - publicidad
    km = sum((f.km for f in del_mes), CERO)
    vacios = [f.km_vacios for f in del_mes]
    km_vacios = sum(vacios, CERO) if del_mes and all(v is not None for v in vacios) else None
    horas = sum((f.horas for f in del_mes), CERO)

    escalones, acumulado = [], CERO
    for n, monto, detalle in _escalones(e, desde, km * uso_km):
        antes = acumulado
        acumulado += monto
        if monto > 0:
            lleno = float(max(CERO, min(monto, disponible - antes)) / monto)
        else:
            lleno = 1.0 if disponible >= antes else 0.0
        escalones.append(Escalon(n, NOMBRES[n], monto, acumulado, lleno, detalle))
    nivel = 0
    # Sin fletes no se cubrio nada, aunque cero de uso "cubra" cero.
    for esc in (escalones if del_mes else []):
        # Un fondo sin monto no cuenta como alcanzado: no hay nada que
        # festejar por apartar cero.
        if not _alcanzado(esc, disponible):
            break
        nivel = esc.nivel
    base_autonomia = escalones[3].acumulado
    autonomia = float(disponible / base_autonomia) if base_autonomia > 0 else None

    # Dia y horas de autofinanciamiento: dia a dia, con la publicidad del dia.
    obligacion = escalones[1].monto + escalones[2].monto
    dia_ok, horas_ok = None, None
    acum_d, acum_h = CERO, CERO
    d = desde
    while d <= hoy:
        hoy_fletes = [f for f in del_mes if f.dia == d]
        acum_d += sum((margen(f, uso_km) for f in hoy_fletes), CERO) - publicidad_por_dia.get(d, CERO)
        acum_h += sum((f.horas for f in hoy_fletes), CERO)
        if dia_ok is None and hoy_fletes and acum_d >= obligacion:
            dia_ok, horas_ok = d, acum_h
        d += timedelta(days=1)

    # Cuanto falta, en fletes como los ultimos (la mediana: uno regalado no arrastra).
    muestra = [margen(f, uso_km) for f in (list(historia) + del_mes)[-12:]]
    tipico = Decimal(str(statistics.median(muestra))) if muestra else None
    if tipico is None:
        tipico = e.precio_base - e.costos.km_por_flete * uso_km
        casos = 0
    else:
        casos = len(muestra)
    falta = max(CERO, escalones[2].acumulado - disponible)
    fletes_faltan = math.ceil(falta / tipico) if falta > 0 and tipico and tipico > 0 else (0 if falta == 0 else None)

    h_des = horas_deseadas(e).monto
    cumplidas = {}
    for p in prioridades:
        _, nivel_p = PRIORIDADES[p]
        if nivel_p is None:
            cumplidas[p] = h_des is None or horas <= h_des
        else:
            cumplidas[p] = _alcanzado(escalones[nivel_p - 1], disponible)
    metas = [p for p in prioridades if PRIORIDADES[p][1] is not None]
    objetivo_cumplido = bool(metas) and all(cumplidas[p] for p in metas)

    avisos = []
    if any(PRIORIDADES[p][1] in (4, 5, 6) and escalones[PRIORIDADES[p][1] - 1].monto == 0 for p in prioridades):
        avisos.append("Una de tus prioridades es un fondo sin monto: definilo en Camioneta.")
    if h_des is not None and horas > h_des:
        avisos.append("Este mes ya pasaste tus horas deseadas.")
    d_ = e.disponibilidad
    restantes = Decimal(dias_mes - hoy.day) / 7 * d_.dias_semana * d_.fletes_dia_comodo
    return Mes(
        capacidad_restante=restantes,
        desde=desde, hasta=hoy, dias_mes=dias_mes, fletes=del_mes, ingresos=ingresos, publicidad=publicidad,
        directos=directos, disponible=disponible, km=km, km_vacios=km_vacios, horas=horas,
        horas_deseadas=h_des, escalones=escalones, nivel=nivel, autonomia=autonomia,
        dia_autofinanciado=dia_ok, horas_para_autofinanciar=horas_ok,
        falta_para_pagarse=falta, fletes_que_faltan=fletes_faltan, margen_tipico=tipico,
        margen_tipico_casos=casos, prioridades=prioridades, cumplidas=cumplidas,
        objetivo_cumplido=objetivo_cumplido, medidos=sum(1 for f in del_mes if f.medido), avisos=avisos,
    )


def publicidad_diaria(gastos, tasa: Decimal, desde: date, hasta: date) -> dict[date, Decimal]:
    """Lo pagado en publicidad, repartido por dia (gastos = GastoPublicidad)."""
    salida: dict[date, Decimal] = {}
    d = desde
    while d <= hasta:
        m = sum((g.pagado_dentro(d, d, tasa) for g in gastos), CERO)
        if m:
            salida[d] = m
        d += timedelta(days=1)
    return salida


def tipo_de_horas(m: Mes) -> Tipo:
    if not m.fletes:
        return Tipo.FALTA
    return Tipo.MEDIDO if m.medidos == len(m.fletes) else Tipo.ESTIMADO
