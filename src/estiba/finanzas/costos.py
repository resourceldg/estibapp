"""Cuanto cuesta de verdad un flete, cuanto cobrar y cuanto hace falta vender.

Tres preguntas que salieron de mirar la primera campaña con los numeros en
la mano: cinco fletes, $175.000 facturados, y una ganancia que parecia de
$93.000 hasta que se contaron el seguro, el monotributo, el aceite y las
cubiertas. Con todo adentro, el flete promedio de $35.000 apenas cubria.

Todo lo que se calcula aca sale de dos lugares y nada mas:

- `Costos`: lo que el fletero dice que paga. Ningun monto es de fabrica en
  serio: los que vienen por defecto son una primera estimacion para que la
  pantalla no arranque vacia, y la pantalla lo dice.
- Lo que paso: los fletes hechos con lo que se cobro, y el gasto en
  publicidad que se cargo. Eso no se estima.

El gasto en publicidad se prorratea por dias cuando el periodo que se mira
corta una carga por la mitad. No se reparte entre fletes ni clientes: el
costo de publicidad por flete es un promedio del periodo, y se llama asi.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, fields
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

CERO = Decimal(0)
#: Los costos fijos se piensan por mes; un periodo se prorratea por dias.
DIAS_MES = Decimal(30)


@dataclass(frozen=True)
class Costos:
    """Lo que el fletero configura. Montos en pesos; porcentajes de 0 a 100."""

    # Fijos por mes: se pagan aunque no salga ningun flete.
    #: La cuota del credito de la camioneta. Es el fijo que mas pesa: hay
    #: que pagarlo haga los fletes que haga.
    credito: Decimal = Decimal(0)
    seguro: Decimal = Decimal(60000)
    monotributo: Decimal = Decimal(50000)
    #: VTV y la prueba del equipo de GNC, que son anuales, divididas por 12.
    vtv_gnc: Decimal = Decimal(8000)
    patente: Decimal = Decimal(5000)
    celular: Decimal = Decimal(20000)
    otros_fijos: Decimal = Decimal(0)

    # La camioneta, por kilometro.
    gnc_cada_100km: Decimal = Decimal(8700)
    aceite_km: Decimal = Decimal(20)
    cubiertas_km: Decimal = Decimal(15)
    #: Frenos, embrague, tren delantero: lo que se rompe tarde o temprano.
    arreglos_km: Decimal = Decimal(40)
    #: Ida, vuelta y lo que se anda buscando la direccion. Se usa cuando el
    #: mapa no encuentra alguna de las dos direcciones.
    km_por_flete: Decimal = Decimal(64)
    #: Las calles no van en linea recta: km por manejar por cada km de mapa.
    factor_calles: Decimal = Decimal("1.3")
    velocidad_kmh: Decimal = Decimal(35)
    #: Cargar, atar, descargar: lo minimo de cualquier flete, en horas.
    horas_carga: Decimal = Decimal("0.75")
    #: Lo que dura un flete promedio, de salir de casa a volver.
    horas_flete: Decimal = Decimal(3)
    #: Lo que se le cobra al cliente por cada ayudante.
    ayudante: Decimal = Decimal(15000)

    #: IVA y percepciones que Meta cobra encima de lo que muestra como gastado.
    impuestos_publicidad: Decimal = Decimal(25)

    # Objetivo.
    ganancia_objetivo: Decimal = Decimal(2000000)
    #: Cuantos fletes entran en un mes con una camioneta y una persona.
    fletes_por_mes_max: Decimal = Decimal(48)
    #: Lo que cuesta cada conversacion que trae la publicidad, sin impuestos.
    costo_por_consulta: Decimal = Decimal(1849)
    #: De cada 100 consultas, cuantas terminan en un flete hecho.
    cierre: Decimal = Decimal(17)

    # Tarifa: lo que se le cobra al cliente.
    tarifa_base: Decimal = Decimal(30000)
    tarifa_km: Decimal = Decimal(450)

    @classmethod
    def desde_dict(cls, datos: dict | None) -> Costos:
        """Lo guardado manda; lo que falta (o no es un numero) queda por defecto."""
        validos = {f.name for f in fields(cls)}
        limpios = {}
        for clave, valor in (datos or {}).items():
            if clave not in validos:
                continue
            try:
                limpios[clave] = Decimal(str(valor))
            except ArithmeticError:
                continue
        return cls(**limpios)

    def a_dict(self) -> dict[str, str]:
        return {k: str(v) for k, v in asdict(self).items()}

    @property
    def fijos_mes(self) -> Decimal:
        return (self.credito + self.seguro + self.monotributo + self.vtv_gnc + self.patente
                + self.celular + self.otros_fijos)

    @property
    def costo_km(self) -> Decimal:
        return self.gnc_cada_100km / 100 + self.aceite_km + self.cubiertas_km + self.arreglos_km

    @property
    def camioneta_por_flete(self) -> Decimal:
        return self.costo_km * self.km_por_flete

    @property
    def con_impuestos(self) -> Decimal:
        return 1 + self.impuestos_publicidad / 100

    @property
    def publicidad_por_flete(self) -> Decimal | None:
        """Lo que cuesta en publicidad conseguir un flete hecho, con impuestos.

        None si no se sabe cuantas consultas cierran: dividir por cero no
        es un costo infinito, es que falta el dato.
        """
        if self.cierre <= 0:
            return None
        return self.costo_por_consulta / (self.cierre / 100) * self.con_impuestos

    @property
    def valor_hora(self) -> Decimal | None:
        """Cuanto tiene que valer cada hora de trabajo para llegar a la meta.

        La meta y los fijos se pagan con las horas que hay en un mes: si
        entran 48 fletes de 3 horas, son 144 horas, y cada una tiene que
        traer su parte. Un flete largo usa mas de esas horas, asi que paga
        mas; uno corto, menos. Es lo que hace que el precio sea justo para
        los dos lados.
        """
        horas = self.fletes_por_mes_max * self.horas_flete
        return (self.ganancia_objetivo + self.fijos_mes) / horas if horas > 0 else None

    def tarifa(self, km: Decimal) -> Decimal:
        return redondear(self.tarifa_base + self.tarifa_km * km)


def redondear(monto: Decimal, a: Decimal = Decimal(1000)) -> Decimal:
    return (monto / a).quantize(Decimal(1), ROUND_HALF_UP) * a


# --------------------------------------------------------------------------
# Lo que paso
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class FleteHecho:
    job_id: str
    dia: date
    #: Lo cobrado si se anoto; si no, lo cotizado en el chat. None si nada.
    cobrado: Decimal | None


@dataclass(frozen=True)
class GastoPublicidad:
    desde: date
    hasta: date  # inclusive
    monto: Decimal  # tal como lo muestra Meta, sin impuestos
    #: Conversaciones que dice Meta que trajo. None si no se cargo.
    resultados: int | None = None

    def parte(self, desde: date | None, hasta: date) -> Decimal:
        """Que fraccion de los dias de este gasto cae entre `desde` y `hasta`."""
        dias = (self.hasta - self.desde).days + 1
        inicio = max(self.desde, desde) if desde else self.desde
        fin = min(self.hasta, hasta)
        if fin < inicio or dias <= 0:
            return CERO
        return Decimal((fin - inicio).days + 1) / Decimal(dias)

    def dentro(self, desde: date | None, hasta: date) -> Decimal:
        """La parte de este gasto que cae entre `desde` y `hasta` (inclusive)."""
        return self.monto * self.parte(desde, hasta)


@dataclass(frozen=True)
class Balance:
    """Lo que dejo un periodo, con todos los costos adentro."""

    dias: int
    fletes: int
    sin_monto: int  # hechos sin nada cobrado ni cotizado: no suman
    ingresos: Decimal
    camioneta: Decimal
    publicidad: Decimal  # con impuestos
    fijos: Decimal  # prorrateados al periodo

    @property
    def costos(self) -> Decimal:
        return self.camioneta + self.publicidad + self.fijos

    @property
    def ganancia(self) -> Decimal:
        return self.ingresos - self.costos

    @property
    def margen(self) -> float | None:
        return float(self.ganancia / self.ingresos) if self.ingresos else None

    @property
    def ticket(self) -> Decimal | None:
        con_monto = self.fletes - self.sin_monto
        return self.ingresos / con_monto if con_monto else None

    @property
    def retorno_publicidad(self) -> float | None:
        """Cuantos pesos facturados volvieron por cada peso de publicidad."""
        return float(self.ingresos / self.publicidad) if self.publicidad else None

    def por_flete(self, monto: Decimal) -> Decimal | None:
        return monto / self.fletes if self.fletes else None


def balance(c: Costos, fletes: list[FleteHecho], gastos: list[GastoPublicidad],
            desde: date | None, hasta: date) -> Balance:
    """`desde` None es desde siempre: arranca con el primer flete o gasto."""
    del_periodo = [f for f in fletes if (desde is None or f.dia >= desde) and f.dia <= hasta]
    if desde is None:
        primeros = [f.dia for f in fletes] + [g.desde for g in gastos]
        inicio = min(primeros, default=hasta)
    else:
        inicio = desde
    dias = (hasta - inicio).days + 1
    publicidad = sum((g.dentro(desde, hasta) for g in gastos), CERO) * c.con_impuestos
    return Balance(
        dias=dias,
        fletes=len(del_periodo),
        sin_monto=sum(1 for f in del_periodo if f.cobrado is None),
        ingresos=sum((f.cobrado for f in del_periodo if f.cobrado is not None), CERO),
        camioneta=c.camioneta_por_flete * len(del_periodo),
        publicidad=publicidad,
        fijos=c.fijos_mes * Decimal(dias) / DIAS_MES,
    )


# --------------------------------------------------------------------------
# Lo que conviene cobrar
# --------------------------------------------------------------------------


def precio_minimo(c: Costos, fletes_mes: int) -> Decimal | None:
    """Lo que tiene que cobrar un flete para no perder plata, sin ganar nada.

    Depende del volumen: los fijos se reparten entre los fletes del mes.
    Sin volumen o sin saber cuanto cierra la publicidad, no hay minimo.
    """
    pub = c.publicidad_por_flete
    if fletes_mes <= 0 or pub is None:
        return None
    return c.camioneta_por_flete + pub + c.fijos_mes / fletes_mes


# --------------------------------------------------------------------------
# El objetivo
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Plan:
    """Una forma de llegar a la ganancia objetivo."""

    fletes: int
    precio: Decimal
    consultas: int
    publicidad_mes: Decimal  # con impuestos
    publicidad_dia: Decimal  # lo que se pone en Meta por dia, sin impuestos
    entra: bool  # si esos fletes entran en un mes


def plan_a_capacidad(c: Costos) -> Plan | None:
    """Trabajando a pleno: cuanto tiene que valer cada flete."""
    n = int(c.fletes_por_mes_max)
    pub = c.publicidad_por_flete
    if n <= 0 or pub is None:
        return None
    precio = (c.ganancia_objetivo + c.fijos_mes) / n + c.camioneta_por_flete + pub
    return _plan(c, n, precio)


def plan_con_precio(c: Costos, precio: Decimal) -> Plan | None:
    """Cobrando `precio`: cuantos fletes hacen falta. None si cada uno pierde."""
    pub = c.publicidad_por_flete
    if pub is None:
        return None
    margen = precio - c.camioneta_por_flete - pub
    if margen <= 0:
        return None
    n = math.ceil((c.ganancia_objetivo + c.fijos_mes) / margen)
    return _plan(c, n, precio)


def _plan(c: Costos, n: int, precio: Decimal) -> Plan:
    consultas = math.ceil(Decimal(n) / (c.cierre / 100))
    sin_impuestos = c.costo_por_consulta * consultas
    return Plan(
        fletes=n,
        precio=precio,
        consultas=consultas,
        publicidad_mes=sin_impuestos * c.con_impuestos,
        publicidad_dia=sin_impuestos / DIAS_MES,
        entra=n <= c.fletes_por_mes_max,
    )


@dataclass(frozen=True)
class Ritmo:
    """Como viene el mes contra el objetivo."""

    ganancia: Decimal
    esperado_a_hoy: Decimal
    proyectado: Decimal  # si sigue igual hasta fin de mes

    @property
    def avance(self) -> float:
        return max(0.0, float(self.ganancia / self.esperado_a_hoy)) if self.esperado_a_hoy else 0.0


def ritmo_del_mes(c: Costos, b: Balance, hoy: date) -> Ritmo:
    """`b` es el balance del 1 del mes a hoy."""
    dias_mes = ((hoy.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)).day
    transcurridos = Decimal(hoy.day)
    esperado = c.ganancia_objetivo * transcurridos / Decimal(dias_mes)
    proyectado = b.ganancia * Decimal(dias_mes) / transcurridos
    return Ritmo(b.ganancia, esperado, proyectado)
