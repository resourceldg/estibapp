"""La forma mas simple de cumplir el mes, escenarios y simulacion.

Funcion objetivo lexicografica, sin sumas magicas. Entre los planes que
entran en la capacidad deseada, gana el que:

    1. cumple las prioridades activas del mes (en su orden);
    2. con menos horas;
    3. con menos km vacios;
    4. con menos desgaste (km totales);
    5. con menos complejidad (dias pico, despues dias trabajados);
    6. y recien ahi, con mas ganancia.

Disponibilidad no es obligacion: si el objetivo se cumple con 3 de los 4
dias, el cuarto queda libre y se dice.

Rendimientos decrecientes: los fletes que llegan solos (clientes que
vuelven, recomendados) son pocos; los demas se compran con publicidad, y
cada conversacion extra sale mas cara (saturacion: duplicar el gasto
multiplica las conversaciones por 2^b, b = 0,8 supuesto). Por eso la
ganancia por hora no sube para siempre con mas fletes.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field, replace
from decimal import Decimal

from estiba.economia.config import CONSERVACION, PERSONAL, Economia
from estiba.economia.estructura import costo_uso_km, cuota_mes, estructura_mes, fondos_mes, ganancia_libre
from estiba.economia.mes import PRIORIDADES
from estiba.economia.modelo import CERO, suma
from estiba.economia.suficiencia import Estado

SATURACION = 0.8


@dataclass(frozen=True)
class Base:
    """Un flete tipico y la demanda, con la calidad de cada dato."""

    ticket: float
    km: float
    km_vacios: float
    horas: float
    directos: float
    #: Fletes por mes que llegan sin publicidad (clientes que vuelven, recomendados).
    organicos: float
    cierre: float                 # 0..1, de las conversaciones de la publicidad
    costo_conversacion: float     # pagado, con impuestos
    conversaciones_base: float    # las que trae hoy la publicidad por mes
    calidad: dict[str, Estado] = field(default_factory=dict)

    @property
    def exploratoria(self) -> bool:
        """Si algun insumo critico es debil, todo el resultado es exploratorio."""
        criticos = ("margen", "horas", "conversión")
        return any(self.calidad.get(c, Estado.EXPLORATORIO) is Estado.EXPLORATORIO for c in criticos)


def publicidad_para(b: Base, pagados: float) -> float:
    """Lo que cuesta por mes conseguir `pagados` fletes por publicidad, con saturacion."""
    if pagados <= 0:
        return 0.0
    if b.cierre <= 0:
        return math.inf
    conversaciones = pagados / b.cierre
    if b.conversaciones_base <= 0:
        return conversaciones * b.costo_conversacion
    gasto_base = b.conversaciones_base * b.costo_conversacion
    return gasto_base * (conversaciones / b.conversaciones_base) ** (1 / SATURACION)


def _obligaciones(e: Economia, mes, hasta_nivel: int) -> float:
    """Lo que tiene que dejar el mes (sin el uso por km) para llegar a ese escalon."""
    partes = [suma(estructura_mes(e))]
    if (c := cuota_mes(e, mes)) is not None:
        partes.append(c.monto)
    if hasta_nivel >= 4:
        partes.append(suma(fondos_mes(e, CONSERVACION)))
    if hasta_nivel >= 5:
        partes.append(suma(fondos_mes(e, PERSONAL)))
    if hasta_nivel >= 6 and (g := ganancia_libre(e)) is not None:
        partes.append(g.monto)
    return float(sum(partes, CERO))


@dataclass(frozen=True)
class Plan:
    fletes: int
    dias: int
    dias_pico: int
    horas: float
    km: float
    km_vacios: float
    publicidad: float
    #: Lo que deja el mes en caja despues de uso, publicidad, estructura y cuota.
    deja: float

    @property
    def por_hora(self) -> float | None:
        return self.deja / self.horas if self.horas else None


def plan(e: Economia, b: Base, mes, n: int, dias_disponibles: float) -> Plan | None:
    """n fletes en el mes, concentrados en la menor cantidad de dias comodos. None si no entran."""
    d = e.disponibilidad
    comodo, pico = int(d.fletes_dia_comodo), int(d.fletes_dia_pico)
    if n <= 0:
        dias, dias_pico = 0, 0
    else:
        dias = math.ceil(n / max(1, comodo))
        dias_pico = 0
        if dias > dias_disponibles:
            # Los dias comodos no alcanzan: algunos pasan a ser dias pico.
            if pico <= comodo:
                return None
            dias = int(dias_disponibles)
            dias_pico = math.ceil((n - dias * comodo) / (pico - comodo))
            if dias_pico > dias:
                return None
    uso = float(costo_uso_km(e).monto)
    pagados = max(0.0, n - b.organicos)
    pub = publicidad_para(b, pagados)
    if math.isinf(pub):
        return None
    deja = n * (b.ticket - b.km * uso - b.directos) - pub - _obligaciones(e, mes, 3)
    return Plan(n, dias, int(dias_pico), n * b.horas, n * b.km, n * b.km_vacios, pub, deja)


@dataclass(frozen=True)
class Recomendacion:
    plan: Plan | None
    #: Las prioridades que el plan cumple y las que quedan afuera.
    cumple: list[str]
    afuera: list[str]
    dias_libres: int
    frase: str
    exploratoria: bool


def lo_mas_simple(e: Economia, b: Base, mes, prioridades: tuple[str, ...], ya_dejado: float = 0.0,
                  dias_restantes: float | None = None, horas_hechas: float = 0.0) -> Recomendacion:
    """La forma mas simple de cumplir lo que falta del mes (orden lexicografico)."""
    """`ya_dejado`: lo que dejaron los fletes del mes hasta hoy, despues del uso
    de la camioneta y la publicidad, antes de estructura y cuota."""
    from estiba.economia.estructura import horas_deseadas

    d = e.disponibilidad
    semanas = dias_restantes / 7 if dias_restantes is not None else 52 / 12
    dias_disp = float(d.dias_semana) * semanas
    h_des = float(horas_deseadas(e).monto or 0)
    nivel_meta = max(((PRIORIDADES[p][1] or 3) for p in prioridades), default=3)
    # `plan.deja` ya descuenta estructura y cuota: la meta es lo que va por encima.
    meta = _obligaciones(e, mes, nivel_meta) - _obligaciones(e, mes, 3)
    tope_horas = "tope_horas" in prioridades
    candidatos = []
    n_max = math.ceil(float(d.fletes_dia_pico) * dias_disp)
    for n in range(0, n_max + 1):
        p = plan(e, b, mes, n, dias_disp)
        if p is None:
            continue
        cumple_meta = ya_dejado + p.deja >= meta
        dentro_horas = horas_hechas + p.horas <= h_des or not tope_horas
        candidatos.append((p, cumple_meta, dentro_horas))
    # 1: cumplir; 2: menos horas; 3: menos km vacios; 4: menos km; 5: menos dias pico, menos dias; 6: mas ganancia
    orden = lambda x: (not (x[1] and x[2]), x[0].horas, x[0].km_vacios, x[0].km, x[0].dias_pico, x[0].dias,
                       -x[0].deja)
    candidatos.sort(key=orden)
    textos = {k: v[0] for k, v in PRIORIDADES.items()}
    if not candidatos:
        return Recomendacion(None, [], [textos[p] for p in prioridades], 0,
                             "Con tus días y tu ritmo no entra ningún plan.", b.exploratoria)
    mejor, cumple, dentro = candidatos[0]
    metas = [p for p in prioridades if PRIORIDADES[p][1] is not None]
    si = [textos[p] for p in metas] if cumple else []
    no = [] if cumple else [textos[p] for p in metas]
    if tope_horas:
        (si if dentro else no).append(textos["tope_horas"])
    libres = max(0, int(dias_disp) - mejor.dias)
    if mejor.fletes == 0 and cumple:
        frase = "No necesitás más fletes este mes para cumplir tus prioridades."
    elif cumple and dentro:
        frase = (f"Con unos {mejor.fletes} fletes más cumplís tus prioridades: entran en {mejor.dias} "
                 f"{'día' if mejor.dias == 1 else 'días'}"
                 + (f" y te quedan {libres} de tus días disponibles libres." if libres else "."))
        if mejor.publicidad <= 0:
            frase += " No hace falta publicidad: alcanza con los que llegan solos."
    else:
        frase = (f"Con tu ritmo cómodo no se llega a todo: queda afuera {', '.join(no).lower()}. "
                 "Es un dato para revisar precio, costos o prioridades, no para trabajar más.")
    if b.exploratoria:
        frase += " (Escenario exploratorio: los datos todavía son pocos.)"
    return Recomendacion(mejor, si, no, libres, frase, b.exploratoria)


def curva(e: Economia, b: Base, mes, n_max: int | None = None) -> list[Plan]:
    """Ganancia contra horas y ganancia por hora contra fletes, para ver donde deja de convenir."""
    tope = n_max or int(float(e.costos.fletes_por_mes_max))
    # La curva mira toda la capacidad tecnica (todos los dias del mes), para
    # mostrar tambien lo que pasa mas alla de lo que se quiere trabajar.
    salida = []
    for n in range(0, tope + 1, max(1, tope // 16)):
        if (p := plan(e, b, mes, n, 31)) is not None:
            salida.append(p)
    return salida


def mejor_por_hora(puntos: list[Plan]) -> Plan | None:
    # Un "mejor" negativo no es un mejor: es el menos malo, y no se marca.
    con = [p for p in puntos if p.por_hora is not None and p.fletes > 0 and p.por_hora > 0]
    return max(con, key=lambda p: p.por_hora) if con else None


def marginal_por_hora(puntos: list[Plan]) -> list[float | None]:
    """Lo que agrega cada hora extra entre un punto de la curva y el anterior.

    El promedio por hora puede seguir subiendo (los fijos se diluyen) aunque
    las horas que se agregan valgan cada vez menos: ganar $300.000 en 30 h o
    $450.000 en 80 h es sumar 50 h a $3.000 cada una.
    """
    salida: list[float | None] = [None]
    for a, b in zip(puntos, puntos[1:]):
        dh = b.horas - a.horas
        salida.append((b.deja - a.deja) / dh if dh else None)
    return salida


def hasta_donde_conviene(puntos: list[Plan], valor_hora_libre: float | None) -> Plan | None:
    """El ultimo punto en que la hora extra vale mas que tu hora libre. None si no se sabe."""
    if valor_hora_libre is None:
        return None
    marg = marginal_por_hora(puntos)
    ultimo = None
    for p, m in zip(puntos, marg):
        if m is None:
            continue
        if m < valor_hora_libre:
            break
        ultimo = p
    return ultimo


# --------------------------------------------------------------------------
# ¿Que pasa si...?
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Resultado:
    nombre: str
    fletes: int
    horas: float
    km: float
    deja: float
    cuota_cubierta: float | None
    viajes_cubiertos: float | None
    por_hora: float | None


def _resultado(e: Economia, b: Base, mes, n: int, nombre: str) -> Resultado:
    p = plan(e, b, mes, n, 31)
    if p is None:
        return Resultado(nombre, n, 0, 0, 0, None, None, None)
    uso = float(costo_uso_km(e).monto)
    disponible = n * (b.ticket - b.directos) - p.publicidad
    base_cuota = _obligaciones(e, mes, 3) + p.km * uso
    cuota_ok = min(1.0, max(0.0, disponible / base_cuota)) if base_cuota else None
    viajes = float(suma(fondos_mes(e, PERSONAL)))
    sobra = disponible - p.km * uso - _obligaciones(e, mes, 4)
    viajes_ok = min(1.0, max(0.0, sobra / viajes)) if viajes else None
    return Resultado(nombre, n, p.horas, p.km, p.deja, cuota_ok, viajes_ok, p.por_hora)


def escenarios(e: Economia, b: Base, mes, n_hoy: int, base: str = "Como vengo") -> list[Resultado]:
    d = e.disponibilidad
    por_dia = float(d.fletes_dia_comodo) * 52 / 12
    fondos_mas = tuple(replace(f, aporte_mes=f.aporte_mes + Decimal(50000)) if f.clave == "mantenimiento" else f
                       for f in e.fondos)
    c = e.costos
    return [
        _resultado(e, b, mes, n_hoy, base),
        _resultado(e, b, mes, max(0, n_hoy - 4), "4 fletes menos"),
        _resultado(e, b, mes, max(0, int(n_hoy - por_dia)), "Un día menos por semana"),
        _resultado(e, replace(b, ticket=b.ticket * 1.10), mes, n_hoy, "Precio base +10 %"),
        _resultado(replace(e, costos=replace(c, gnc_cada_100km=c.gnc_cada_100km * Decimal("1.2"))), b, mes, n_hoy,
                   "GNC +20 %"),
        _resultado(e, replace(b, cierre=b.cierre * 0.7), mes, n_hoy, "La publicidad cierra 30 % menos"),
        _resultado(e, replace(b, organicos=b.organicos * 1.5 + 1), mes, n_hoy, "Más clientes que vuelven"),
        _resultado(e, replace(b, km=b.km - b.km_vacios * 0.2, km_vacios=b.km_vacios * 0.8), mes, n_hoy,
                   "20 % menos km vacíos"),
        _resultado(e, replace(b, organicos=b.organicos + 4), mes, n_hoy + 4, "Un convenio (+4 fletes)"),
        _resultado(e, replace(b, km=b.km * 0.8, km_vacios=b.km_vacios * 0.8, horas=b.horas * 0.85), mes, n_hoy,
                   "Dejar los viajes largos"),
        _resultado(replace(e, fondos=fondos_mas), b, mes, n_hoy, "Fondo de mantenimiento +$50.000"),
    ]


# --------------------------------------------------------------------------
# Monte Carlo: exploracion, nunca oraculo
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Simulacion:
    p10: float
    p50: float
    p90: float
    #: Probabilidad de que la camioneta se pague sola (escalon 3).
    p_se_paga: float
    #: Meses con una rotura grande, sobre el total simulado.
    con_rotura: float
    exploratoria: bool
    entradas: list[tuple[str, str]]


def simular_mes(e: Economia, b: Base, mes, cobrados: list[float], kms: list[float], *,
                prob_rotura_mes: float = 0.04, costo_rotura: float = 600_000, meses: int = 4000,
                semilla: int = 7) -> Simulacion:
    """Cada mes simulado: fletes que llegan solos (Poisson) + los de la publicidad
    (conversaciones Poisson, cierre sorteado de lo medido), ticket y km sacados
    de los fletes reales, y una rotura grande con baja probabilidad y alto
    impacto (un evento, no una normal). Todo con semilla fija."""
    r = random.Random(semilla)
    uso = float(costo_uso_km(e).monto)
    oblig = _obligaciones(e, mes, 3)
    gasto_base = b.conversaciones_base * b.costo_conversacion
    tope = int(float(e.disponibilidad.fletes_mes(pico=True)))
    tickets = cobrados or [b.ticket]
    km_obs = kms or [b.km]
    resultados, pagas, roturas = [], 0, 0
    for _ in range(meses):
        n = _poisson(r, b.organicos)
        conv = _poisson(r, b.conversaciones_base)
        cierre = min(0.9, max(0.0, r.gauss(b.cierre, max(0.02, b.cierre * 0.5))))
        n += sum(1 for _ in range(conv) if r.random() < cierre)
        n = min(n, tope)
        ingresos = sum(r.choice(tickets) for _ in range(n))
        km = sum(r.choice(km_obs) for _ in range(n))
        rotura = r.random() < prob_rotura_mes
        roturas += rotura
        deja = ingresos - n * b.directos - km * uso - gasto_base - oblig - (
            costo_rotura * r.lognormvariate(0, 0.5) if rotura else 0)
        resultados.append(deja)
        pagas += deja >= 0
    orden = sorted(resultados)
    q = lambda x: orden[min(len(orden) - 1, int(x * len(orden)))]
    entradas = [(nombre, estado.etiqueta) for nombre, estado in b.calidad.items()]
    entradas += [("roturas", "escenario (supuesto)"), ("saturación de la publicidad", "supuesto")]
    return Simulacion(q(0.1), q(0.5), q(0.9), pagas / meses, roturas / meses, b.exploratoria, entradas)


def _poisson(r: random.Random, lam: float) -> int:
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
