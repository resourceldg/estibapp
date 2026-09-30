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
    if h <= 0:
        return "0 h"
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


# --------------------------------------------------------------------------
# La evaluacion de un pedido
# --------------------------------------------------------------------------

TONO_VEREDICTO = {"conviene": "ok", "conviene con ajuste": "aviso", "no conviene": "mal",
                  "requiere tu decisión": "decision"}


def vista_evaluacion(ev) -> dict | None:
    """Primero la linea que decide; despues razones; el detalle, plegado."""
    if ev is None:
        return None
    datos = [
        ("Precio evaluado", plata(ev.precio_evaluado),
         "hablado con el cliente" if ev.precio_evaluado != ev.base else "precio base"),
        ("Mínimo", plata(ev.minimo), "debajo, el flete pierde plata"),
        ("Recomendado", plata(ev.recomendado), "cubre tus objetivos por hora"),
        ("Margen esperado", plata(ev.margen), "precio − camioneta − ayudantes − CAC"),
        ("Margen por hora", plata(ev.margen_hora), f"en {horas(ev.horas)}"),
        ("Km", km(ev.km), f"{km(ev.km_vacios)} sin carga" if ev.km_vacios is not None else "sin tramos: promedio"),
        ("CAC", plata(ev.cac.monto) if ev.cac.monto is not None else "—", ev.cac.detalle),
        ("Cliente", "vuelve" if ev.cliente.recurrente else "nuevo",
         (f"{ev.cliente.fletes_previos} {'flete' if ev.cliente.fletes_previos == 1 else 'fletes'} antes")
         if ev.cliente.recurrente else
         ("llegó por un anuncio" if ev.cliente.por_publicidad else "no vino por un anuncio")),
    ]
    return {
        "titular": ev.titular,
        "veredicto": ev.veredicto.value.capitalize(),
        "tono": TONO_VEREDICTO[ev.veredicto.value],
        "capa": ev.capa.value,
        "razones": ev.razones,
        "datos": datos,
        "tipo": ETIQUETA_TIPO[ev.tipo],
        "supuestos": list(ev.supuestos),
    }


def estimado_cierre(ev) -> dict | None:
    """Lo que el formulario de cierre muestra como "asi fue", para confirmar de un toque."""
    if ev is None:
        return None
    from estiba.economia.registro import desde_evaluacion

    r = desde_evaluacion(ev)
    partes = [km(r.km)]
    if r.km_vacios is not None:
        partes.append(f"{km(r.km_vacios)} sin carga")
    partes.append(horas(r.horas))
    partes.append(f"ayudantes {plata(r.ayudante_pagado)}" if r.ayudante_pagado else "sin ayudantes")
    return {"linea": " · ".join(partes), "canal": r.canal}


# --------------------------------------------------------------------------
# ¿Como estoy? El mes de la camioneta
# --------------------------------------------------------------------------


def _pct(x: float | None) -> str:
    return "—" if x is None else f"{x:.0%}"


def rango_faltan(m, margen_s) -> tuple[int, int] | None:
    """Si el margen tipico no es firme, cuantos fletes faltan como rango."""
    import math

    if margen_s is None or margen_s.fuerte or not m.falta_para_pagarse:
        return None
    if not margen_s.lo or not margen_s.hi or margen_s.lo <= 0:
        return None
    falta = float(m.falta_para_pagarse)
    lo, hi = math.ceil(falta / margen_s.hi), math.ceil(falta / margen_s.lo)
    return (lo, hi) if lo != hi else None


def frase_mes(m, rango: tuple[int, int] | None = None) -> str:
    """La linea que responde "¿como voy?". Nunca empuja a trabajar mas."""
    if m.objetivo_cumplido:
        return "Objetivo del mes alcanzado. No necesitás tomar más trabajos para cumplir lo que te propusiste."
    if m.dia_autofinanciado is not None:
        return (f"La camioneta se pagó el {m.dia_autofinanciado.day}. "
                "Lo que venga ahora va a tus fondos y a lo que querés.")
    if m.fletes_que_faltan is None:
        return "Con los números de hoy no se puede decir cuánto falta: revisá tus costos."
    if m.fletes_que_faltan > m.capacidad_restante:
        # No se empuja a compensar: se muestra como un dato de la estructura.
        return (f"Este mes la camioneta no llega a pagarse sola con tu ritmo cómodo: faltarían unos "
                f"{m.fletes_que_faltan} fletes como los últimos y no entran en lo que queda. "
                "No es para compensar trabajando más: es para revisar precio, costos o cuota.")
    if not m.fletes:
        return (f"Todavía no hay fletes este mes. Para que la camioneta se pague sola hacen falta unos "
                f"{m.fletes_que_faltan} como los últimos.")
    if rango:
        return (f"Faltan entre {rango[0]} y {rango[1]} fletes como los últimos para que la camioneta se pague "
                "sola este mes (todavía hay pocos datos para decirlo más preciso).")
    return (f"Faltan unos {m.fletes_que_faltan} fletes como los últimos para que la camioneta se pague sola "
            "este mes.")


def vista_mes(m, economico, prioridades_texto: dict[str, str], cat=None) -> dict:
    from estiba.economia.mes import tipo_de_horas
    from estiba.economia.suficiencia import lo_que_mas_falta

    rango = rango_faltan(m, cat.margen if cat else None)
    aprender = None
    if cat is not None:
        from estiba.economia.evidencia import IMPORTANCIA

        prox = lo_que_mas_falta([(x, IMPORTANCIA.get(x.metrica, 1)) for x in cat.todas()])
        aprender = f"{prox.falta} ({prox.metrica}: {prox.estado.etiqueta})." if prox else None

    tipo_h = ETIQUETA_TIPO[tipo_de_horas(m)]
    horas_eco = economico.resultado_economico / m.horas if m.horas else None
    por_100 = economico.resultado_economico / m.km * 100 if m.km else None
    confianza = (f"el margen típico sale de {m.margen_tipico_casos} fletes"
                 if m.margen_tipico_casos else "sin fletes todavía: se usa el precio base")
    indicadores = [
        ("La camioneta se pagó", _pct(m.autonomia) if m.autonomia is not None else "—",
         "de lo que cuesta sostenerla y conservarla este mes"),
        ("Horas para que se pague sola", horas(m.horas_para_autofinanciar) if m.horas_para_autofinanciar else "—",
         f"todavía no se pagó" if m.dia_autofinanciado is None else f"hasta el {m.dia_autofinanciado:%d/%m} · {tipo_h}"),
        ("Horas trabajadas", horas(m.horas),
         f"de {horas(m.horas_deseadas)} que querés · {tipo_h}" if m.horas_deseadas else tipo_h),
        ("Resultado real por hora", plata(horas_eco),
         "a hoy, con los fijos del mes prorrateados; no es lo que cada hora tiene que aportar"),
        ("Por cada 100 km", plata(por_100), "resultado real por 100 km de camioneta"),
        ("Km", km(m.km), f"{_pct(m.deadhead)} sin carga" if m.deadhead is not None else "sin tramos"),
        ("Fletes", str(len(m.fletes)),
         f"ticket {plata(m.ticket)} · {_pct(m.recurrentes)} de clientes que vuelven" if m.fletes else ""),
    ]
    return {
        "frase": frase_mes(m, rango),
        "aprender": aprender,
        "nota": f"Cuenta de caja: la cuota entra entera. ({confianza})",
        "escalones": [{"nivel": x.nivel, "nombre": x.nombre, "monto": plata(x.monto), "detalle": x.detalle,
                       "lleno": round(x.lleno * 100), "alcanzado": x.nivel <= m.nivel,
                       "sin_definir": x.nivel > 3 and x.monto == 0} for x in m.escalones],
        "nivel": m.nivel,
        "disponible": plata(m.disponible),
        "indicadores": indicadores,
        "prioridades": [(prioridades_texto[p], m.cumplidas.get(p, False)) for p in m.prioridades],
        "avisos": m.avisos,
        "sin_contar": economico.sin_contar,
        "resultado": plata(economico.resultado_economico),
    }


def vista_catalogo(cat) -> list[dict]:
    """Cada metrica con su estado, su valor a la precision que tiene y lo que le falta."""
    from estiba.economia.suficiencia import mostrar

    unidades = {"ticket": "$", "margen por flete": "$", "horas por flete": "h", "km por flete": "km"}
    salida = []
    for x in cat.todas():
        if x.metrica in unidades:
            valor = mostrar(x.valor, x, unidades[x.metrica])
        elif x.valor is not None and x.lo is not None:
            valor = f"{x.valor:.0%} (entre {x.lo:.0%} y {x.hi:.0%})"
        else:
            valor = "—"
        salida.append({"metrica": x.metrica.capitalize(), "estado": x.estado.etiqueta, "valor": valor,
                       "razones": "; ".join(x.razones), "falta": x.falta, "n": x.n})
    return salida


# --------------------------------------------------------------------------
# ¿Que pasa si...? El plan del mes, escenarios, curva y simulacion
# --------------------------------------------------------------------------


def vista_plan(rec, escenarios, puntos, marginales, conviene_hasta, mejor, sim, m) -> dict:
    exploratorio = rec.exploratoria
    fmt = (lambda x: f"~{plata(x)}") if exploratorio else plata
    filas = []
    for x in escenarios:
        filas.append({
            "nombre": x.nombre, "fletes": x.fletes, "horas": horas(Decimal(str(x.horas))),
            "km": km(Decimal(str(x.km))), "deja": fmt(Decimal(str(x.deja))),
            "cuota": _pct(x.cuota_cubierta), "viajes": _pct(x.viajes_cubiertos),
            "por_hora": fmt(Decimal(str(x.por_hora))) if x.por_hora is not None else "—",
        })
    curva = []
    for p, marg in zip(puntos, marginales):
        marca = []
        if mejor is not None and p.fletes == mejor.fletes:
            marca.append("mejor promedio por hora")
        if conviene_hasta is not None and p.fletes == conviene_hasta.fletes:
            marca.append("hasta acá la hora extra vale más que tu hora libre")
        if p.dias_pico:
            marca.append(f"{p.dias_pico} días pico")
        curva.append({"fletes": p.fletes, "horas": horas(Decimal(str(p.horas))), "deja": fmt(Decimal(str(p.deja))),
                      "promedio": fmt(Decimal(str(p.por_hora))) if p.por_hora is not None else "—",
                      "marginal": fmt(Decimal(str(marg))) if marg is not None else "—",
                      "marca": " · ".join(marca)})
    return {
        "frase": rec.frase,
        "cumple": rec.cumple, "afuera": rec.afuera,
        "exploratorio": exploratorio,
        "escenarios": filas,
        "curva": curva,
        "sim": {"p10": plata(Decimal(str(sim.p10))), "p50": plata(Decimal(str(sim.p50))),
                "p90": plata(Decimal(str(sim.p90))), "se_paga": _pct(sim.p_se_paga),
                "rotura": _pct(sim.con_rotura), "entradas": sim.entradas, "exploratoria": sim.exploratoria},
    }
