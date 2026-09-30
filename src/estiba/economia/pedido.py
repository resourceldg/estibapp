"""¿Este flete me conviene? ¿Cuanto cobro?

La respuesta sale primero en una linea ("Tomaria este flete desde $X") y
despues el porque, con todas las variables a la vista: margen, margen por
hora, km, km vacios, tiempo, CAC, recurrencia e impacto sobre la agenda.
No hay un puntaje que las esconda.

Veredictos (umbrales = los del fletero, ninguno escondido):

    Conviene              P >= P_rec
    Conviene con ajuste   P_min <= P < P_rec     -> dice el precio que lo vuelve "conviene"
    No conviene           P < P_min              -> no paga ni la estructura y la cuota
    Requiere decision     cualquiera de los anteriores + un trade-off real:
                          ocupa mas de media jornada, o cae en un dia que ya
                          tiene el ritmo comodo cubierto.

P es el precio que se hablo con el cliente; si no hay, el precio base (la
pregunta es "¿puedo cobrarlo a precio base?").

Capas: automatico (conviene, sin trade-off: queda listo para aprobar),
atencion (ajuste o no conviene: se muestran opciones) y decision (hay un
trade-off: pide criterio). Nada se manda ni se rechaza solo.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum

from estiba.economia.config import Economia
from estiba.economia.estructura import contribucion_por_hora, costo_uso_km, horas_de, precios
from estiba.economia.modelo import CERO, Parte, Tipo, Valor, combinar


class Veredicto(Enum):
    CONVIENE = "conviene"
    AJUSTE = "conviene con ajuste"
    NO_CONVIENE = "no conviene"
    DECISION = "requiere tu decisión"


class Capa(Enum):
    AUTOMATICO = "automatico"
    ATENCION = "atencion"
    DECISION = "decision"


@dataclass(frozen=True)
class Cliente:
    #: Fletes hechos antes con esta persona.
    fletes_previos: int = 0
    #: Llego tocando un anuncio.
    por_publicidad: bool = False

    @property
    def recurrente(self) -> bool:
        return self.fletes_previos > 0


@dataclass(frozen=True)
class Pedido:
    km: Decimal
    km_tipo: Tipo
    #: ida (base -> origen), carga (origen -> destino), vuelta (destino -> base). None: no se sabe.
    tramos: dict[str, Decimal] | None
    #: Horas de carga y descarga de mas por lo que se lleva y los pisos.
    horas_carga_extra: Decimal = CERO
    #: Lo que cuesta el flete aparte de la camioneta (ayudantes pagados, peajes).
    directos: Decimal = CERO
    #: El precio hablado con el cliente, si hay.
    precio_hablado: Decimal | None = None
    dia: date | None = None
    #: Otros fletes que ya hay ese dia (confirmados o tentativos).
    fletes_ese_dia: int = 0
    supuestos: tuple[str, ...] = ()


@dataclass(frozen=True)
class Dato:
    etiqueta: str
    valor: str
    tipo: Tipo


@dataclass(frozen=True)
class Evaluacion:
    veredicto: Veredicto
    capa: Capa
    titular: str
    razones: list[str]
    precio_evaluado: Decimal
    minimo: Decimal | None
    recomendado: Decimal | None
    base: Decimal
    horas: Decimal
    km: Decimal
    km_vacios: Decimal | None
    margen: Decimal
    margen_hora: Decimal | None
    cac: Valor
    cliente: Cliente
    partes: list[Parte] = field(default_factory=list)
    tipo: Tipo = Tipo.CALCULADO
    #: Lo que se supuso para poder dar el numero.
    supuestos: tuple[str, ...] = ()

    @property
    def deadhead(self) -> Decimal | None:
        return self.km_vacios / self.km if self.km_vacios is not None and self.km else None


def _mil(d: Decimal) -> str:
    return f"${int(d):,}".replace(",", ".")


def cac_de(e: Economia, cliente: Cliente) -> Valor:
    """Lo que costo conseguir a este cliente. Solo lo paga el primer flete de
    un cliente nuevo que vino por publicidad: el que vuelve ya lo pago, y el
    que llego solo no costo nada."""
    if cliente.recurrente:
        return Valor(CERO, Tipo.CALCULADO, "cliente que vuelve: la publicidad ya se pagó")
    if not cliente.por_publicidad:
        return Valor(CERO, Tipo.ESTIMADO, "no llegó por un anuncio")
    pub = e.costos.publicidad_por_flete
    if pub is None:
        return Valor.falta("no se sabe cuántas consultas terminan en flete")
    return Valor(pub, Tipo.ESTIMADO, "costo por consulta ÷ cierre, con impuestos (de tus costos)")


def evaluar(e: Economia, mes: date, p: Pedido, cliente: Cliente, *, objetivo_cumplido: bool = False) -> Evaluacion:
    cac = cac_de(e, cliente)
    horas = horas_de(e, p.km, p.horas_carga_extra)
    pr = precios(e, mes, p.km, horas, cac=cac.monto or CERO, directos=p.directos, km_tipo=p.km_tipo)
    P = p.precio_hablado if p.precio_hablado is not None else e.precio_base
    uso = costo_uso_km(e).monto
    margen = P - p.km * uso - p.directos - (cac.monto or CERO)
    margen_hora = margen / horas if horas > 0 else None
    km_vacios = (p.tramos["ida"] + p.tramos["vuelta"]) if p.tramos else None
    w = contribucion_por_hora(e, mes)
    d = e.disponibilidad

    if pr.recomendado is None or pr.minimo is None:
        veredicto = Veredicto.DECISION
    elif P >= pr.recomendado:
        veredicto = Veredicto.CONVIENE
    elif P >= pr.minimo:
        veredicto = Veredicto.AJUSTE
    else:
        veredicto = Veredicto.NO_CONVIENE

    razones: list[str] = []
    que = "el precio que hablaron" if p.precio_hablado is not None else "precio base"
    if pr.recomendado is None:
        razones.append("Falta cuántas horas querés trabajar por mes: sin eso no se puede decir si conviene.")
    elif veredicto is Veredicto.CONVIENE:
        razones.append(f"A {_mil(P)} ({que}) cubre la camioneta y lo que cada hora tiene que aportar a tus objetivos.")
    elif veredicto is Veredicto.AJUSTE:
        razones.append(f"A {_mil(P)} cubre los costos, pero queda debajo de tu objetivo por hora; "
                       f"a {_mil(pr.recomendado)} entra en tus parámetros.")
    else:
        razones.append(f"A {_mil(P)} no llega a pagar la camioneta, la estructura y la cuota por las horas que usa "
                       f"(mínimo {_mil(pr.minimo)}).")

    if cliente.recurrente:
        razones.append(f"Es un cliente que vuelve ({cliente.fletes_previos} "
                       f"{'flete' if cliente.fletes_previos == 1 else 'fletes'} antes): no carga publicidad.")
    elif cac.monto:
        razones.append(f"Cliente nuevo por publicidad: carga {_mil(cac.monto.quantize(Decimal(1)))} de conseguirlo.")

    if km_vacios is not None and p.km:
        razones.append(f"{p.km:.0f} km en total, {km_vacios:.0f} sin carga ({km_vacios / p.km:.0%}).")
    elif p.km_tipo is not Tipo.CALCULADO:
        razones.append(f"La distancia es tu promedio ({p.km:.0f} km): el mapa no ubicó las direcciones.")

    tradeoffs = []
    # Si la distancia no es segura y moverla un 30 % cambia el veredicto, la
    # incertidumbre decide: no se da una recomendacion fuerte.
    if p.km_tipo is not Tipo.CALCULADO and veredicto is not Veredicto.DECISION:
        alternativos = {_veredicto_con_km(e, mes, p, cliente, f) for f in (Decimal("0.7"), Decimal("1.3"))}
        if alternativos - {veredicto}:
            tradeoffs.append("La distancia no es segura y un 30 % más o menos cambia el veredicto: "
                             "confirmá las direcciones antes de cerrar el precio.")
    if horas > d.horas_dia / 2:
        tradeoffs.append(f"Ocupa {horas:.1f} h, más de media jornada.".replace(".", ",", 1))
    if p.fletes_ese_dia >= d.fletes_dia_pico:
        tradeoffs.append(f"Ese día ya hay {p.fletes_ese_dia} fletes: con este pasás tu máximo de un día pico.")
    elif p.fletes_ese_dia >= d.fletes_dia_comodo:
        tradeoffs.append(f"Ese día ya tenés {p.fletes_ese_dia} fletes: este lo vuelve un día pico, "
                         "no uno cómodo.")
    if tradeoffs and veredicto is not Veredicto.NO_CONVIENE:
        veredicto = Veredicto.DECISION
    razones += tradeoffs
    if objetivo_cumplido:
        razones.append("Este mes ya cumpliste tus prioridades: no lo necesitás. Tomalo solo si te conviene.")

    if veredicto is Veredicto.CONVIENE:
        capa = Capa.AUTOMATICO
        titular = ("Este flete puede cobrarse a precio base." if pr.recomendado == e.precio_base
                   else f"Tomaría este flete desde {_mil(pr.recomendado)}.")
        if p.precio_hablado is not None:
            titular = f"Conviene a {_mil(P)}."
    elif veredicto is Veredicto.AJUSTE:
        capa = Capa.ATENCION
        titular = (f"A {_mil(P)} queda corto: tomaría este flete desde {_mil(pr.recomendado)}."
                   if p.precio_hablado is not None else f"Tomaría este flete desde {_mil(pr.recomendado)}.")
    elif veredicto is Veredicto.NO_CONVIENE:
        capa = Capa.ATENCION
        titular = (f"A {_mil(P)} no conviene. Para tus objetivos, cobralo al menos {_mil(pr.recomendado)}."
                   if pr.recomendado is not None else f"A {_mil(P)} no conviene.")
    else:
        capa = Capa.DECISION
        titular = (f"Tomaría este flete desde {_mil(pr.recomendado)}, pero decidí vos."
                   if pr.recomendado is not None else "No se puede evaluar todavía.")

    return Evaluacion(
        veredicto=veredicto, capa=capa, titular=titular, razones=razones[:5],
        precio_evaluado=P, minimo=pr.minimo, recomendado=pr.recomendado, base=e.precio_base,
        horas=horas, km=p.km, km_vacios=km_vacios, margen=margen,
        margen_hora=margen_hora, cac=cac, cliente=cliente, partes=pr.partes,
        tipo=combinar(pr.tipo, cac.tipo), supuestos=p.supuestos,
    )


def _veredicto_con_km(e: Economia, mes: date, p: Pedido, cliente: Cliente, factor: Decimal) -> Veredicto:
    cac = cac_de(e, cliente).monto or CERO
    km = p.km * factor
    pr = precios(e, mes, km, horas_de(e, km, p.horas_carga_extra), cac=cac, directos=p.directos)
    P = p.precio_hablado if p.precio_hablado is not None else e.precio_base
    if pr.recomendado is None or pr.minimo is None:
        return Veredicto.DECISION
    if P >= pr.recomendado:
        return Veredicto.CONVIENE
    return Veredicto.AJUSTE if P >= pr.minimo else Veredicto.NO_CONVIENE


def objetivo_por_hora(e: Economia, mes: date) -> Decimal | None:
    return contribucion_por_hora(e, mes).objetivo
