"""Que tan firme es cada cosa que Estiba cree saber, y la validacion del negocio.

Junta las observaciones reales (fletes hechos, cierres medidos, consultas,
clientes) y le pregunta al motor de suficiencia por cada metrica. Despues
responde, sin apurarse a un si o un no:

- ¿Hay evidencia suficiente para seguir invirtiendo tiempo y capital?
- ¿Puede el negocio reemplazar ~$1.000.000 por mes sin pedir mas horas de
  las que se quieren dar?
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from estiba.economia.config import CONSERVACION, Economia
from estiba.economia.estructura import costo_uso_km, cuota_mes, estructura_mes, fondos_mes, horas_deseadas
from estiba.economia.mes import FleteDelMes, margen
from estiba.economia.modelo import suma
from estiba.economia.suficiencia import (
    Cliente,
    Estado,
    Obs,
    Suficiencia,
    elasticidad,
    eventos_raros,
    lo_que_mas_falta,
    media,
    mostrar,
    proporcion,
    recurrencia,
)


@dataclass(frozen=True)
class Datos:
    """Lo observado, tal cual. Lo arma la app."""

    fletes: list[FleteDelMes]
    #: chat de cada flete (para agrupar por cliente)
    cliente_de: dict[str, str]
    consultas_publicidad: int
    hechos_publicidad: int
    dias_publicidad: int
    #: cuando cambio por ultima vez el precio base (los datos de antes valen menos)
    cambio_precio: date | None = None
    reparaciones_grandes: int = 0


@dataclass(frozen=True)
class Catalogo:
    ticket: Suficiencia
    margen: Suficiencia
    horas: Suficiencia
    km: Suficiencia
    conversion: Suficiencia
    recurrencia: Suficiencia
    elasticidad: Suficiencia
    roturas: Suficiencia

    def todas(self) -> list[Suficiencia]:
        return [self.ticket, self.margen, self.horas, self.km, self.conversion, self.recurrencia,
                self.elasticidad, self.roturas]


#: Cuanto pesa cada metrica en la pregunta "¿el negocio se sostiene?" (para
#: elegir el dato que mas conviene aprender). El margen y las horas mandan.
IMPORTANCIA = {"margen por flete": 5, "horas por flete": 4, "conversión de la publicidad": 3,
               "recurrencia": 3, "ticket": 2, "km por flete": 2, "elasticidad": 1, "roturas grandes": 1}


def catalogo(e: Economia, d: Datos, hoy: date) -> Catalogo:
    uso = costo_uso_km(e).monto
    calidad = lambda f: "medido" if f.medido else "estimado"
    cli = lambda f: d.cliente_de.get(f.job_id, f.job_id)
    obs_ticket = [Obs(float(f.cobrado), f.dia, cli(f), "medido") for f in d.fletes if f.cobrado > 0]
    obs_margen = [Obs(float(margen(f, uso)), f.dia, cli(f), calidad(f)) for f in d.fletes if f.cobrado > 0]
    obs_horas = [Obs(float(f.horas), f.dia, cli(f), calidad(f)) for f in d.fletes]
    obs_km = [Obs(float(f.km), f.dia, cli(f), calidad(f)) for f in d.fletes]

    primeros: dict[str, date] = {}
    cuantos: dict[str, int] = {}
    for f in d.fletes:
        c = cli(f)
        primeros[c] = min(primeros.get(c, f.dia), f.dia)
        cuantos[c] = cuantos.get(c, 0) + 1
    clientes = [Cliente(c, primeros[c], cuantos[c]) for c in primeros]
    km_total = float(sum((f.km for f in d.fletes), Decimal(0)))
    meses = ((hoy - min(primeros.values())).days / 30) if primeros else 0.0

    return Catalogo(
        ticket=media("ticket", obs_ticket, hoy, cambio=d.cambio_precio,
                     falta="Anotar lo cobrado en cada flete"),
        margen=media("margen por flete", obs_margen, hoy, cambio=d.cambio_precio,
                     falta="Cerrar los fletes confirmando km y horas"),
        horas=media("horas por flete", obs_horas, hoy,
                    falta="Confirmar o corregir las horas al cerrar cada flete"),
        km=media("km por flete", obs_km, hoy, falta="Que el mapa ubique las direcciones"),
        conversion=proporcion("conversión de la publicidad", min(d.hechos_publicidad, d.consultas_publicidad),
                              d.consultas_publicidad, dias_observados=d.dias_publicidad,
                              falta="Más conversaciones de la publicidad, con su resultado"),
        recurrencia=recurrencia(clientes, hoy),
        elasticidad=elasticidad([float(f.cobrado) for f in d.fletes if f.cobrado > 0]),
        roturas=eventos_raros("roturas grandes", d.reparaciones_grandes, km_total, meses),
    )


@dataclass(frozen=True)
class Compatibilidad:
    dimension: str
    frase: str
    estado: Estado


@dataclass(frozen=True)
class Validacion:
    a_favor: list[str]
    en_contra: list[str]
    no_sabemos: list[str]
    proximo_dato: str | None
    #: "¿Puede reemplazar ~$1.000.000 sin mas horas de las que queres?"
    meta: Decimal
    compatibilidad: list[Compatibilidad] = field(default_factory=list)
    conclusion: str = ""


def _rango_fletes(necesario: Decimal, s: Suficiencia) -> tuple[int | None, int | None, int | None]:
    """Fletes por mes para cubrir `necesario`, con el margen y su intervalo."""
    if s.valor is None or s.valor <= 0:
        return None, None, None
    medio = math.ceil(float(necesario) / s.valor)
    lo = math.ceil(float(necesario) / s.hi) if s.hi and s.hi > 0 else None
    hi = math.ceil(float(necesario) / s.lo) if s.lo and s.lo > 0 else None
    return lo, medio, hi


def _lado(lo: float | None, hi: float | None, umbral: float) -> int | None:
    """+1 si todo el intervalo queda arriba del umbral, -1 si todo abajo, None si lo cruza."""
    if lo is None or hi is None:
        return None
    if lo > umbral:
        return 1
    if hi < umbral:
        return -1
    return None


def validar(e: Economia, cat: Catalogo, hoy: date, meta: Decimal = Decimal(1_000_000)) -> Validacion:
    """Evidencia es lo que queda de un lado del umbral con todo su intervalo.
    Lo que cruza el umbral va a "no sabemos", aunque el valor central parezca bueno."""
    a_favor, en_contra, no_sabemos = [], [], []
    m = cat.margen
    lado = _lado(m.lo, m.hi, 0)
    rango_m = mostrar(m.valor, m)
    if lado == 1:
        a_favor.append(f"Cada flete deja algo después de la camioneta y los ayudantes (margen {rango_m}).")
    elif lado == -1:
        en_contra.append(f"Los fletes no cubren ni el uso de la camioneta (margen {rango_m}).")
    r = cat.recurrencia
    if (lr := _lado(r.lo, r.hi, 0.0)) == 1 and r.fuerte:
        a_favor.append(f"Hay clientes que vuelven ({r.valor:.0%} de los que tuvieron tiempo).")
    c = cat.conversion
    if c.valor is not None:
        frase = (f"De cada 100 conversaciones de la publicidad terminan en flete entre "
                 f"{c.lo * 100:.0f} y {c.hi * 100:.0f}.")
        lado_c = _lado(c.lo, c.hi, 0.10)
        if lado_c == 1:
            a_favor.append(frase)
        elif lado_c == -1:
            en_contra.append(frase)

    estructura = suma(estructura_mes(e))
    cuota = cuota_mes(e, hoy)
    fijo = estructura + (cuota.monto if cuota else Decimal(0)) + suma(fondos_mes(e, CONSERVACION))
    h_des = horas_deseadas(e).monto
    h_flete = Decimal(str(cat.horas.valor)) if cat.horas.valor else e.costos.horas_flete
    lo, medio, hi = _rango_fletes(fijo, m)
    if medio is not None and h_des and lo is not None and hi is not None:
        h_lo, h_hi = Decimal(lo) * h_flete, Decimal(hi) * h_flete
        frase = (f"Para que la camioneta se pague y se conserve hacen falta entre {lo} y {hi} fletes por mes "
                 f"({h_lo:.0f} a {h_hi:.0f} h) y querés dar {h_des:.0f} h.")
        if h_hi <= h_des:
            a_favor.append(frase)
        elif h_lo > h_des:
            en_contra.append(frase)
        else:
            no_sabemos.append(frase + " Todavía no se sabe si entra.")

    for x in cat.todas():
        if x.estado is Estado.EXPLORATORIO:
            motivo = x.razones[0] if x.razones else "pocos datos"
            no_sabemos.append(f"{x.metrica.capitalize()}: {motivo}.")
    prox = lo_que_mas_falta([(x, IMPORTANCIA.get(x.metrica, 1)) for x in cat.todas()])

    # La pregunta estrategica: ¿$1.000.000 por mes sin mas horas?
    comp = []
    necesario = meta + fijo
    lo, medio, hi = _rango_fletes(necesario, m)
    veredicto_horas = None
    if medio is None:
        comp.append(Compatibilidad("margen", "Sin margen medido no se puede estimar.", Estado.EXPLORATORIO))
    else:
        rango = f"entre {lo} y {hi}" if lo and hi and lo != hi else f"~{medio}"
        comp.append(Compatibilidad("margen", f"Harían falta {rango} fletes por mes con el margen de hoy.", m.estado))
        if h_des:
            h_lo = Decimal(lo or medio) * h_flete
            h_hi = Decimal(hi or medio) * h_flete
            if h_lo > h_des:
                veredicto_horas, cola = -1, ": aun en el mejor caso no entra sin trabajar más."
            elif h_hi <= h_des:
                veredicto_horas, cola = 1, ": entra aun en el peor caso."
            else:
                cola = ": depende de datos que todavía no tenemos."
            comp.append(Compatibilidad(
                "horas", f"Serían entre {h_lo:.0f} y {h_hi:.0f} h por mes; querés dar {h_des:.0f} h{cola}",
                min(m.estado, cat.horas.estado, key=lambda x: x.value)))
        cap = e.disponibilidad.fletes_mes(pico=True)
        comp.append(Compatibilidad(
            "capacidad", f"Tu máximo (días pico todo el mes) es ~{cap:.0f} fletes"
                         + (": no alcanza ni en el mejor caso." if (lo or medio) > cap else "."), m.estado))
        km_mes = Decimal(medio) * Decimal(str(cat.km.valor or e.costos.km_por_flete))
        comp.append(Compatibilidad("desgaste", f"~{km_mes:,.0f} km por mes de camioneta.".replace(",", "."),
                                   cat.km.estado))
        if cat.conversion.valor:
            nuevos = medio * (1 - (cat.recurrencia.valor or 0))
            consultas = math.ceil(nuevos / cat.conversion.valor)
            comp.append(Compatibilidad(
                "adquisición", f"Si los nuevos vienen por publicidad: ~{consultas} conversaciones por mes.",
                cat.conversion.estado))
        comp.append(Compatibilidad(
            "recurrencia", "Cuántos clientes vuelven cambia mucho la cuenta: "
                           + (f"hoy ~{cat.recurrencia.valor:.0%}." if cat.recurrencia.valor is not None
                              else "todavía no se sabe."), cat.recurrencia.estado))
    if veredicto_horas == -1:
        conclusion = ("Todavía hay pocos datos, pero aun en el mejor caso de lo que se ve hoy, "
                      "pediría más horas de las que querés dar.")
    elif veredicto_horas == 1:
        conclusion = "Aun en el peor caso de lo que se ve hoy, entra en tus horas."
    else:
        conclusion = "Todavía no tenemos suficiente evidencia para responder."
    return Validacion(a_favor, en_contra, no_sabemos, prox.falta if prox else None, meta, comp, conclusion)
