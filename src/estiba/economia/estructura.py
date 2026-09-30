"""Cuanto tiene que aportar cada hora, que precio cobrar y hasta donde rinde el base.

Dos preguntas distintas que no se mezclan:

- "¿Cuanto tiene que aportar cada hora para financiar lo que quiero?" es la
  *contribucion objetivo por hora*: la estructura, la cuota, los fondos y
  la ganancia libre deseada, repartidos entre las horas que se *quieren*
  trabajar (no las que entran). No es una ganancia: es una exigencia.
- "¿Cuanto gane de verdad?" es el *resultado economico*, en `cuentas.py`.

Formulas (en pesos, h = horas, km = km totales del flete):

    c_uso      = GNC/100 + aceite + cubiertas + desgaste            [por km]
    w_min      = (estructura + cuota) / H_deseadas                  [por hora]
    w_obj      = (estructura + cuota + fondos + ganancia libre) / H_deseadas
    P_min      = km·c_uso + directos + CAC + h·w_min
    P_rec      = max(P_base, km·c_uso + directos + CAC + h·w_obj)   (redondeado para arriba)
    h          = h_coord + h_carga + h_cobro + km/v + extra
    km_max     = (P_base − CAC − directos − h0·w_obj) / (c_uso + w_obj/v)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from estiba.economia.config import CONSERVACION, PERSONAL, Economia
from estiba.economia.modelo import CERO, Parte, Tipo, Valor, combinar, redondear_arriba, suma, tipo_de


def _t(e: Economia, *campos: str) -> Tipo:
    """Declarado si el fletero guardo todos esos costos; si no, supuesto."""
    return Tipo.DECLARADO if all(c in e.declarados for c in campos) else Tipo.SUPUESTO


# --------------------------------------------------------------------------
# Costos de la camioneta y del negocio
# --------------------------------------------------------------------------


def costo_uso_km(e: Economia) -> Valor:
    c = e.costos
    return Valor(c.costo_km, combinar(_t(e, "gnc_cada_100km", "aceite_km", "cubiertas_km", "arreglos_km")),
                 "GNC, aceite, cubiertas y desgaste por km")


def estructura_mes(e: Economia) -> list[Parte]:
    """Lo que se paga todos los meses haya o no fletes, sin la cuota."""
    c = e.costos
    return [
        Parte("Seguro, patente y VTV/GNC de la camioneta", c.seguro + c.patente + c.vtv_gnc,
              _t(e, "seguro", "patente", "vtv_gnc")),
        Parte("Monotributo, celular y otros del negocio", c.monotributo + c.celular + c.otros_fijos,
              _t(e, "monotributo", "celular", "otros_fijos")),
    ]


def cuota_mes(e: Economia, mes: date) -> Parte | None:
    if e.costos.credito <= 0 or not e.credito.vigente(mes):
        return None
    detalle = "cuota del crédito"
    if e.credito.primera is None:
        detalle += " (no cargaste cuándo empezó: se cuenta como vigente)"
    return Parte("Cuota del crédito", e.costos.credito, _t(e, "credito"), detalle)


def fondos_mes(e: Economia, tipo: str) -> list[Parte]:
    return [Parte(f.nombre, f.aporte_mes, Tipo.DECLARADO, "aporte mensual al fondo")
            for f in e.fondos if f.tipo == tipo and f.aporte_mes > 0]


def ganancia_libre(e: Economia) -> Parte | None:
    g = e.costos.ganancia_objetivo
    return Parte("Ganancia libre que querés", g, _t(e, "ganancia_objetivo")) if g > 0 else None


# --------------------------------------------------------------------------
# Capacidad deseada
# --------------------------------------------------------------------------


def horas_flete_tipico(e: Economia) -> Decimal:
    return e.costos.horas_flete


def horas_deseadas(e: Economia) -> Valor:
    d = e.disponibilidad
    h = horas_flete_tipico(e)
    horas = d.horas_mes(h)
    detalle = (f"{d.dias_semana:g} días × {d.fletes_dia_comodo:g} fletes cómodos × {h:g} h, por mes"
               if d.tope_horas_mes is None or horas < d.tope_horas_mes else "tu tope de horas por mes")
    return Valor(horas.quantize(Decimal("0.1")), Tipo.CALCULADO if horas > 0 else Tipo.FALTA, detalle)


# --------------------------------------------------------------------------
# Contribucion por hora
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Contribucion:
    """Lo que cada hora deseada tiene que aportar. No es una ganancia."""

    horas: Valor
    #: Para que la camioneta y el negocio se paguen (estructura + cuota).
    minima: Decimal | None
    #: Para cumplir todo lo que se le pide al negocio.
    objetivo: Decimal | None
    partes_minima: list[Parte] = field(default_factory=list)
    partes_objetivo: list[Parte] = field(default_factory=list)

    @property
    def tipo(self) -> Tipo:
        return combinar(self.horas.tipo, tipo_de(self.partes_objetivo))

    @property
    def total_minima(self) -> Decimal:
        return suma(self.partes_minima)

    @property
    def total_objetivo(self) -> Decimal:
        return suma(self.partes_objetivo)


def contribucion_por_hora(e: Economia, mes: date) -> Contribucion:
    horas = horas_deseadas(e)
    minima = list(estructura_mes(e))
    if (cuota := cuota_mes(e, mes)) is not None:
        minima.append(cuota)
    objetivo = minima + fondos_mes(e, CONSERVACION) + fondos_mes(e, PERSONAL)
    if (libre := ganancia_libre(e)) is not None:
        objetivo.append(libre)
    h = horas.monto
    if not h:
        return Contribucion(horas, None, None, minima, objetivo)
    return Contribucion(horas, suma(minima) / h, suma(objetivo) / h, minima, objetivo)


# --------------------------------------------------------------------------
# Tiempo y precio de un flete
# --------------------------------------------------------------------------


def horas_fijas(e: Economia) -> Decimal:
    """Lo que lleva cualquier flete aunque sea a la vuelta: coordinar, cargar y cobrar."""
    d = e.disponibilidad
    return d.h_coordinacion + e.costos.horas_carga + d.h_cobro


def horas_de(e: Economia, km: Decimal, extra: Decimal = CERO) -> Decimal:
    v = e.costos.velocidad_kmh
    return horas_fijas(e) + (km / v if v > 0 else CERO) + extra


@dataclass(frozen=True)
class Precios:
    base: Decimal
    minimo: Decimal | None
    recomendado: Decimal | None
    #: Lo que cuesta sin la contribucion por hora: por debajo, se pierde plata.
    costo_directo: Decimal
    horas: Decimal
    partes: list[Parte] = field(default_factory=list)
    tipo: Tipo = Tipo.CALCULADO


def precios(e: Economia, mes: date, km: Decimal, horas: Decimal, cac: Decimal = CERO,
            directos: Decimal = CERO, km_tipo: Tipo = Tipo.CALCULADO) -> Precios:
    uso = costo_uso_km(e)
    w = contribucion_por_hora(e, mes)
    camioneta = km * uso.monto
    partes = [Parte(f"Camioneta: {km:.0f} km", camioneta, combinar(uso.tipo, km_tipo), uso.detalle)]
    if directos:
        partes.append(Parte("Ayudantes y otros gastos del flete", directos, Tipo.ESTIMADO))
    if cac:
        partes.append(Parte("Conseguir un cliente nuevo (CAC)", cac, Tipo.ESTIMADO))
    costo = camioneta + directos + cac
    if w.objetivo is None or w.minima is None:
        return Precios(e.precio_base, None, None, costo, horas, partes, Tipo.FALTA)
    partes.append(Parte(f"Tus horas: {horas:.1f} h × {w.objetivo:.0f} por hora", horas * w.objetivo,
                        w.tipo, "lo que cada hora tiene que aportar a tus objetivos"))
    minimo = costo + horas * w.minima
    recomendado = max(e.precio_base, redondear_arriba(costo + horas * w.objetivo))
    return Precios(e.precio_base, redondear_arriba(minimo), recomendado, costo, horas, partes,
                   combinar(*(p.tipo for p in partes)))


# --------------------------------------------------------------------------
# Hasta donde rinde el precio base
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class RindeBase:
    base: Decimal
    km_tipico: Decimal
    horas_tipico: Decimal
    #: Km totales hasta los que el base cumple la contribucion objetivo.
    km_max: Decimal | None
    horas_max: Decimal | None
    #: Horas de espera que el base banca en un flete tipico.
    espera_max: Decimal | None
    #: Lo maximo que se puede pagar por conseguir un cliente con un flete tipico.
    cac_max: Decimal | None
    #: Lo que hay que cobrar de mas por cada km o cada hora por encima.
    adicional_km: Decimal | None
    adicional_hora: Decimal | None
    tipo: Tipo

    def km_vacios_max(self, km_con_carga: Decimal) -> Decimal | None:
        """Cuantos km sin carga admite el base si el tramo cargado mide `km_con_carga`."""
        return None if self.km_max is None else max(CERO, self.km_max - km_con_carga)


def rinde_base(e: Economia, mes: date, cac: Decimal = CERO, directos: Decimal = CERO) -> RindeBase:
    P = e.precio_base
    uso = costo_uso_km(e).monto
    w = contribucion_por_hora(e, mes)
    v = e.costos.velocidad_kmh
    km_t = e.costos.km_por_flete
    h_t = horas_de(e, km_t)
    h0 = horas_fijas(e)
    tipo = combinar(w.tipo, costo_uso_km(e).tipo, _t(e, "km_por_flete", "velocidad_kmh", "horas_carga"))
    if w.objetivo is None or v <= 0:
        return RindeBase(P, km_t, h_t, None, None, None, None, None, None, Tipo.FALTA)
    wo = w.objetivo
    km_max = (P - cac - directos - h0 * wo) / (uso + wo / v)
    horas_max = (P - cac - directos - km_t * uso) / wo if wo > 0 else None
    return RindeBase(
        base=P, km_tipico=km_t, horas_tipico=h_t,
        km_max=max(CERO, km_max),
        horas_max=max(CERO, horas_max) if horas_max is not None else None,
        espera_max=max(CERO, horas_max - h_t) if horas_max is not None else None,
        cac_max=P - directos - km_t * uso - h_t * wo,
        adicional_km=uso + wo / v,
        adicional_hora=wo,
        tipo=tipo,
    )
