"""Del motor economico a frases: la pantalla Camioneta (y lo que venga despues).

Regla de la casa: primero la frase que responde, despues el porque. Cada
numero va con su tipo (declarado, calculado, estimado...) y nunca con mas
precision que la que tiene: plata redondeada a mil, horas a la media hora,
km a la unidad.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from estiba.economia.config import CONSERVACION, Economia
from estiba.economia.estructura import (
    Contribucion,
    RindeBase,
    contribucion_por_hora,
    costo_uso_km,
    horas_deseadas,
    rinde_base,
)
from estiba.economia.modelo import Parte, Tipo
from estiba.formato import pesos

ETIQUETA_TIPO = {
    Tipo.DECLARADO: "lo cargaste vos",
    Tipo.MEDIDO: "medido",
    Tipo.CALCULADO: "calculado",
    Tipo.ESTIMADO: "estimado",
    Tipo.SUPUESTO: "supuesto",
    Tipo.FALTA: "falta el dato",
}


def plata(d: Decimal | None) -> str:
    """A mil, con signo si es negativa: $12.370 promete una precision que no hay."""
    if d is None:
        return "—"
    r = (d / 1000).quantize(Decimal(1), ROUND_HALF_UP) * 1000
    return f"−{pesos(-r)}" if r < 0 else pesos(r)


def plata_fina(d: Decimal | None, a: int = 10) -> str:
    """Para montos chicos por unidad (por km): a mil daria $0."""
    if d is None:
        return "—"
    r = (d / a).quantize(Decimal(1), ROUND_HALF_UP) * a
    return f"−{pesos(-r)}" if r < 0 else pesos(r)


def horas(h: Decimal | None) -> str:
    """A la media hora: "2 h 30", "45 min"."""
    if h is None:
        return "—"
    medias = int((h * 2).quantize(Decimal(1), ROUND_HALF_UP))
    if medias <= 0:
        minutos = int((h * 60).quantize(Decimal(1), ROUND_HALF_UP))
        return f"{max(minutos, 5)} min"
    hs, media = divmod(medias, 2)
    if hs == 0:
        return "30 min"
    return f"{hs} h 30" if media else f"{hs} h"


def km(d: Decimal | None) -> str:
    return "—" if d is None else f"{int(d.quantize(Decimal(1), ROUND_HALF_UP))} km"


@dataclass
class Renglon:
    nombre: str
    monto: str
    tipo: str
    detalle: str = ""


def renglones(partes: list[Parte]) -> list[Renglon]:
    return [Renglon(p.nombre, plata(p.monto), ETIQUETA_TIPO[p.tipo], p.detalle) for p in partes]


@dataclass
class VistaCamioneta:
    precio_base: str
    #: La respuesta a "¿bajo que condiciones mi base sigue siendo rentable?".
    rinde: list[str]
    rinde_tipo: str
    contribucion: list[str]
    partes_objetivo: list[Renglon]
    horas_deseadas: str
    historial: list[dict]
    credito: dict
    avisos: list[str] = field(default_factory=list)


def _frases_rinde(r: RindeBase) -> list[str]:
    if r.km_max is None:
        return ["Falta cuántas horas querés trabajar por mes para poder calcularlo."]
    base = plata(r.base)
    frases = [
        f"A {base}, un flete es saludable hasta unos {km(r.km_max)} en total "
        f"(de casa a casa), con un cliente que ya tenés.",
        f"Un flete típico ({km(r.km_tipico)}, {horas(r.horas_tipico)}) banca "
        + (f"hasta {horas(r.espera_max)} de espera." if r.espera_max else "poca o ninguna espera."),
        f"Por encima, sumá {plata_fina(r.adicional_km)} por km o {plata(r.adicional_hora)} por hora de más.",
    ]
    if r.cac_max is not None and r.cac_max > 0:
        frases.append(f"Para conseguir un cliente nuevo con ese flete podés gastar hasta {plata(r.cac_max)}.")
    else:
        frases.append("A este precio, un flete típico no deja lugar para pagar la publicidad de un cliente nuevo.")
    if (vacios := r.km_vacios_max(Decimal(10))) is not None:
        frases.append(f"Si el tramo con carga mide 10 km, el resto del recorrido sin carga puede llegar a "
                      f"{km(vacios)}.")
    return frases


def _frases_contribucion(w: Contribucion) -> list[str]:
    if w.objetivo is None:
        return ["Sin horas deseadas no se puede repartir nada."]
    frases = [
        f"Para que la camioneta y el negocio se paguen, cada hora que querés trabajar tiene que aportar "
        f"{plata(w.minima)}.",
    ]
    if w.objetivo > w.minima:
        frases.append(f"Para cumplir también tus fondos y lo que querés ganar: {plata(w.objetivo)} por hora.")
    frases.append("Es una exigencia, no una ganancia: lo que ganaste de verdad se mide aparte, con lo que pasó.")
    return frases


def vista_camioneta(e: Economia, hoy: date, historial) -> VistaCamioneta:
    r = rinde_base(e, hoy)
    w = contribucion_por_hora(e, hoy)
    h = horas_deseadas(e)
    avisos = []
    faltan = [n for n, v in (("valor de mercado", e.camioneta.valor_mercado),
                             ("valor de reposición", e.camioneta.valor_reposicion),
                             ("km del odómetro", e.camioneta.odometro)) if v is None]
    if faltan:
        avisos.append(f"Falta {', '.join(faltan)} de la camioneta: no cambia el precio, pero sin eso no se "
                      "puede medir cuánto la gastás.")
    if not any(f.aporte_mes > 0 for f in e.fondos if f.tipo == CONSERVACION):
        avisos.append("No hay aporte a los fondos de mantenimiento o reposición: la próxima reparación grande "
                      "va a salir de la caja del mes.")
    uso = costo_uso_km(e)
    if uso.tipo is not Tipo.CALCULADO:
        avisos.append("Algunos costos por km son los de fábrica: cargá los tuyos en Costos y metas.")
    cr = e.credito
    credito = {
        "cuota": plata(e.costos.credito), "cuotas": cr.cuotas,
        "primera": cr.primera.isoformat()[:7] if cr.primera else "",
        "restantes": cr.restantes(hoy), "ultima": cr.ultima().strftime("%m/%Y") if cr.ultima() else None,
        "interes": _campo(cr.interes_mes),
    }
    return VistaCamioneta(
        precio_base=plata(e.precio_base),
        rinde=_frases_rinde(r),
        rinde_tipo=ETIQUETA_TIPO[r.tipo],
        contribucion=_frases_contribucion(w),
        partes_objetivo=renglones(w.partes_objetivo),
        horas_deseadas=f"{horas(h.monto)} por mes ({h.detalle})",
        historial=[{"desde": date.fromisoformat(f["desde"]).strftime("%d/%m/%Y"), "monto": plata(Decimal(f["monto"])),
                    "anterior": plata(Decimal(f["anterior"])) if f["anterior"] else None, "nota": f["nota"] or ""}
                   for f in historial],
        credito=credito,
        avisos=avisos,
    )


def _campo(d: Decimal | None) -> str:
    """Como se escribe en un input: sin signo y sin decimales de mas."""
    if d is None:
        return ""
    return f"{int(d):,}".replace(",", ".") if d == d.to_integral_value() else str(d).replace(".", ",")


campo = _campo
