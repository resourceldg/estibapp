"""Del motor economico a frases: la pantalla Camioneta (y lo que venga despues).

Regla de la casa: primero la frase que responde, despues el porque. Cada
numero va con su tipo (declarado, calculado, estimado...) y nunca con mas
precision que la que tiene: plata redondeada a mil, horas a la media hora,
km a la unidad.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
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
from estiba.web import graficos_eco as G

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
    graficos: dict
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


def _grafico_rinde(e: Economia, hoy: date, r: RindeBase, w: Contribucion) -> str:
    """El precio que hace falta segun los km totales, contra el precio base."""
    from estiba.economia.estructura import horas_de
    from estiba.economia.pedido import Cliente, cac_de

    if w.objetivo is None or r.km_max is None:
        return ""
    uso = e.costos.costo_km
    cac = cac_de(e, Cliente(por_publicidad=True)).monto or Decimal(0)
    tope = max(120, int(float(r.km_max) * 1.8) // 10 * 10)
    xs = [float(k) for k in range(0, tope + 1, 5)]
    necesario = lambda k, extra: float(Decimal(k) * uso + horas_de(e, Decimal(k)) * w.objetivo + extra)
    series = [("Cliente que vuelve", [necesario(k, 0) for k in xs], "g-s1")]
    if cac:
        series.append(("Cliente nuevo por publicidad", [necesario(k, cac) for k in xs], "g-s2"))
    return G.linea(xs, series, G.pesos_cortos, lambda k: f"{k:.0f} km",
                   refs=[(float(e.precio_base), f"Precio base {plata(e.precio_base)}")],
                   marcas=[(float(r.km_max), float(e.precio_base), f"Rinde hasta {km(r.km_max)}")],
                   titulo="Precio que hace falta según los km del flete")


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
    graficos = {"rinde": _grafico_rinde(e, hoy, r, w),
                "reparto": G.apilada([(corto(p_.nombre), float(p_.monto)) for p_ in w.partes_objetivo],
                                     G.pesos_cortos, titulo="Qué se reparte entre tus horas")}
    return VistaCamioneta(
        graficos=graficos,
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


def partir(frase: str) -> tuple[str, str]:
    """Una frase larga en dos: la idea que responde y el resto.

    En escritorio la respuesta va arriba y grande, y tiene que entrar en una
    linea o dos; la explicacion queda debajo, chica. Se corta en el primer
    ": ", ". " o " (", lo que llegue antes.
    """
    frase = (frase or "").strip()
    cortes = [(i, sep) for sep in (": ", ". ", " (") if (i := frase.find(sep)) > 0]
    if not cortes:
        return frase, ""
    i, sep = min(cortes)
    cabeza, resto = frase[:i].rstrip(), frase[i + len(sep):].strip()
    if sep == " (":
        resto = resto.rstrip(".").removesuffix(")") + "."
    if not resto:
        return frase, ""
    return cabeza + ".", resto[0].upper() + resto[1:]


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


def tono_mes(m) -> str:
    """El color de la respuesta: verde si se pago, rojo si no entra en el mes, ambar si falta."""
    if m.objetivo_cumplido or m.dia_autofinanciado is not None:
        return "ok"
    if m.fletes_que_faltan is not None and m.fletes_que_faltan > m.capacidad_restante:
        return "mal"
    return "aviso"


#: Nombres cortos para los graficos: la etiqueta tiene que entrar entera.
CORTOS = {
    "Seguro, patente y VTV/GNC de la camioneta": "Seguro, patente, VTV",
    "Monotributo, celular y otros del negocio": "Monotributo y celular",
    "Ayudantes y gastos de los fletes": "Ayudantes",
    "Publicidad pagada": "Publicidad",
    "Interés del crédito": "Interés",
    "Depreciación (supuesto)": "Depreciación (sup.)",
}


def corto(nombre: str) -> str:
    base = nombre.split(" (")[0] if not nombre.startswith("Depreciación") else nombre
    return CORTOS.get(nombre, CORTOS.get(base, base))


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
         "todavía no se pagó" if m.dia_autofinanciado is None else f"hasta el {m.dia_autofinanciado:%d/%m} · {tipo_h}"),
        ("Horas trabajadas", horas(m.horas),
         f"de {horas(m.horas_deseadas)} que querés · {tipo_h}" if m.horas_deseadas else tipo_h),
        ("Resultado real por hora", plata(horas_eco),
         "a hoy, con los fijos del mes prorrateados; no es lo que cada hora tiene que aportar"),
        ("Por cada 100 km", plata(por_100), "resultado real por 100 km de camioneta"),
        ("Km", km(m.km), f"{_pct(m.deadhead)} sin carga" if m.deadhead is not None else "sin tramos"),
        ("Fletes", str(len(m.fletes)),
         f"ticket {plata(m.ticket)} · {_pct(m.recurrentes)} de clientes que vuelven" if m.fletes else ""),
    ]
    graficos = {
        "escalera": G.escalera([(x.nombre, float(x.monto)) for x in m.escalones], float(m.disponible),
                               G.pesos_cortos, titulo="Hasta dónde llegó el mes"),
    }
    if m.fletes and m.acumulado:
        dias = [float(d.day) for d, _ in m.acumulado]
        marcas = []
        if m.dia_autofinanciado is not None:
            valor = next(float(v) for d, v in m.acumulado if d == m.dia_autofinanciado)
            marcas.append((float(m.dia_autofinanciado.day), valor, f"Se pagó el {m.dia_autofinanciado.day}"))
        graficos["dia"] = G.linea(
            dias, [("Lo que dejaron los fletes", [float(v) for _, v in m.acumulado], "g-s1")], G.pesos_cortos,
            lambda x: f"{int(x)}", refs=[(float(m.estructura), "Paga su estructura"),
                                         (float(m.obligacion), "Se paga sola")],
            marcas=marcas, titulo="Lo que dejaron los fletes, día a día")
    pasos = [("Cobrado", float(economico.economico[0].monto))]
    pasos += [(corto(p_.nombre), float(p_.monto)) for p_ in economico.economico[1:] if p_.monto]
    pasos.append(("Resultado real", float(economico.resultado_economico)))
    if m.ingresos:
        graficos["resultado"] = G.cascada(pasos, G.pesos_cortos, titulo="De lo cobrado al resultado real")
    return {
        "graficos": graficos,
        "pasos": [(n, plata(Decimal(str(v)))) for n, v in pasos],
        "frase": frase_mes(m, rango),
        "tono": tono_mes(m),
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
                       "pips": x.estado.value + 1,
                       "razones": "; ".join(x.razones), "falta": x.falta, "n": x.n})
    return salida


# --------------------------------------------------------------------------
# ¿Que pasa si...? El plan del mes, escenarios, curva y simulacion
# --------------------------------------------------------------------------


def vista_plan(rec, escenarios, puntos, marginales, conviene_hasta, mejor, sim, m, valor_libre=None) -> dict:
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
    base = escenarios[0].nombre if escenarios else ""
    graficos = {
        "escenarios": G.barras(
            [(x.nombre.replace("Fondo de mantenimiento", "Fondo mant."), x.deja,
              "g-s2" if x.nombre == base else "g-s1") for x in escenarios],
            G.pesos_cortos, titulo="Lo que deja el mes en cada escenario", izq=196),
        "deja": G.linea([p.horas for p in puntos], [("Lo que deja el mes", [p.deja for p in puntos], "g-s1")],
                        G.pesos_cortos, lambda h: f"{h:.0f} h", refs=[(0.0, "Se paga sola")],
                        titulo="Lo que deja el mes según las horas trabajadas"),
        "por_hora": G.linea(
            [float(p.fletes) for p in puntos],
            [("Promedio por hora", [p.por_hora for p in puntos], "g-s1"),
             ("Cada hora de más", [None] + [m_ for m_ in marginales[1:]], "g-s2")],
            G.pesos_cortos, lambda n: f"{n:.0f}",
            refs=[(float(valor_libre), "Tu hora libre")] if valor_libre else [],
            titulo="Ganancia por hora según los fletes del mes"),
        "sim": G.histograma(list(sim.valores), G.pesos_cortos, ref=(0.0, "Se paga sola"),
                            titulo="Lo que deja el mes en cada mes simulado"),
    }
    return {
        "graficos": graficos,
        "frase": rec.frase,
        "cumple": rec.cumple, "afuera": rec.afuera,
        "exploratorio": exploratorio,
        "escenarios": filas,
        "curva": curva,
        "sim": {"p10": plata(Decimal(str(sim.p10))), "p50": plata(Decimal(str(sim.p50))),
                "p90": plata(Decimal(str(sim.p90))), "se_paga": _pct(sim.p_se_paga),
                "rotura": _pct(sim.con_rotura), "entradas": sim.entradas, "exploratoria": sim.exploratoria},
    }


# --------------------------------------------------------------------------
# Lo que se aprende de la historia
# --------------------------------------------------------------------------


def _fila_grupo(g) -> dict:
    from estiba.economia.suficiencia import mostrar

    s = g.margen_hora
    return {"nombre": g.nombre, "n": g.n, "estado": s.estado.etiqueta,
            "valor": mostrar(s.valor, s) if s.valor is not None else "—",
            "nota": "" if s.fuerte else (s.razones[0] if s.razones else s.falta)}


def _con_nombre(g, nombre: str):
    from dataclasses import replace as _r

    return _r(g, nombre=nombre)


def _intervalos(grupos, titulo: str) -> str:
    filas = [(g.nombre, g.margen_hora.valor, g.margen_hora.lo, g.margen_hora.hi, g.margen_hora.fuerte,
              f"{g.n} {'flete' if g.n == 1 else 'fletes'}, {g.margen_hora.estado.etiqueta}") for g in grupos]
    return G.intervalos(filas, G.pesos_cortos, titulo=titulo)


def grafico_horas_meta(v) -> str:
    """Las horas que pediria la meta, como rango, contra lo que queres dar y tu maximo."""
    if v.horas_meta is None:
        return ""
    lo, medio, hi = v.horas_meta
    refs = []
    if v.horas_deseadas:
        refs.append((v.horas_deseadas, f"Querés dar {v.horas_deseadas:.0f} h"))
    if v.horas_maximas:
        refs.append((v.horas_maximas, f"Tu máximo {v.horas_maximas:.0f} h"))
    return G.rango(lo, hi, medio, refs, lambda h: f"{h:.0f} h", etiqueta="Horas por mes que pediría",
                   titulo="Horas por mes que pediría la meta")


from estiba.economia.registro import CANALES as _CANALES

CANAL_NOMBRE = dict(_CANALES)


def vista_historia(segmentos, canales, jornadas, conclusion_jornadas, valores, oportunidad,
                   nombres: dict[str, str] | None = None, anuncios=()) -> dict:
    from estiba.formato import telefono

    ok_oportunidad, porque = oportunidad
    nombre = lambda c: ((nombres or {}).get(c) or telefono(c))[:18]
    return {
        "graficos": {
            "segmentos": _intervalos(segmentos, "Margen por hora por tipo de flete"),
            "canales": _intervalos([_con_nombre(g, CANAL_NOMBRE.get(g.nombre, g.nombre)) for g in canales],
                                   "Margen por hora por cómo llegó el cliente"),
            "jornadas": _intervalos(jornadas, "Margen por hora por jornada"),
            "anuncios": _intervalos(list(anuncios), "Margen por hora por anuncio"),
            "ltv": G.barras([(nombre(v_.cliente), float(v_.ltv), "g-s1" if v_.ltv >= 0 else "g-s2")
                             for v_ in valores[:8]], G.pesos_cortos, titulo="Lo que dejó cada cliente", izq=150),
        },
        "segmentos": [_fila_grupo(g) for g in segmentos],
        "canales": [_fila_grupo(_con_nombre(g, CANAL_NOMBRE.get(g.nombre, g.nombre))) for g in canales],
        "jornadas": [_fila_grupo(g) for g in jornadas],
        "anuncios": [_fila_grupo(g) for g in anuncios],
        "conclusion_jornadas": conclusion_jornadas or "Todavía no hay jornadas suficientes para comparar.",
        "ltv": [{"cliente": nombre(v.cliente), "fletes": v.fletes, "margen": plata(v.margen), "cac": plata(v.cac),
                 "ltv": plata(v.ltv)} for v in valores[:8]],
        "oportunidad": "Se calcula con la historia." if ok_oportunidad else f"Todavía no: {porque}.",
    }


def despues_del_credito(e, hoy: date) -> dict | None:
    """Como cambia la estructura cuando termina el credito, y que hacer con esa plata."""

    ultima = e.credito.ultima()
    if e.costos.credito <= 0:
        return None
    if ultima is None:
        return {"frase": "Cargá el mes de la primera cuota para ver qué cambia cuando termines de pagarla."}
    despues = date(ultima.year + (ultima.month // 12), ultima.month % 12 + 1, 1)
    antes_w = contribucion_por_hora(e, hoy)
    luego_w = contribucion_por_hora(e, despues)
    if antes_w.objetivo is None or luego_w.objetivo is None:
        return None
    cuota = plata(e.costos.credito)
    return {
        "frase": (f"Desde {despues:%m/%Y}, sin la cuota de {cuota}, cada hora tiene que aportar "
                  f"{plata(luego_w.objetivo)} en vez de {plata(antes_w.objetivo)}."),
        "sugerencia": (f"Para que esos {cuota} no desaparezcan sin plan, podés pasar parte al fondo de reposición "
                       "(la próxima camioneta), parte a mantenimiento y parte a viajes. Se configura en Fondos."),
    }


def vista_reparaciones(reps) -> list[dict]:
    return [{"fecha": r.fecha.strftime("%d/%m/%Y"), "componente": r.componente, "tipo": r.tipo,
             "costo": plata(r.costo), "dias": r.dias_parada,
             "pago": f"fondo {r.fondo}" if r.fondo else "caja"} for r in reps[:10]]


# --------------------------------------------------------------------------
# Evolucion: semana a semana, con hitos
# --------------------------------------------------------------------------


def vista_evolucion(ss, hitos, ref_hora: Decimal | None, exploratorio: bool) -> dict | None:
    """El grafico de linea de tiempo, los hitos numerados y una frase que lo resume."""
    from estiba.economia.evolucion import acumulado

    if not ss:
        return None
    acum = acumulado(ss)
    filas = [(s.inicio, s.inicio + timedelta(days=6), float(s.resultado),
              float(a), float(s.por_hora) if s.por_hora is not None else None, s.fletes) for s, a in zip(ss, acum)]
    numerados = [(h.dia, i, h.texto) for i, h in enumerate(hitos, start=1)]
    con_horas = [s for s in ss if s.por_hora is not None]
    frase = f"En {len(ss)} {'semana' if len(ss) == 1 else 'semanas'} el resultado real acumulado es {plata(acum[-1])}."
    if con_horas:
        mejor = max(con_horas, key=lambda s: s.por_hora)
        frase += f" La semana que más rindió fue la del {mejor.inicio:%d/%m}: {plata(mejor.por_hora)} por hora."
    return {
        "grafico": G.evolucion(filas, numerados, G.pesos_cortos,
                               ref_hora=float(ref_hora) if ref_hora else None, exploratorio=exploratorio,
                               titulo="Evolución semana a semana"),
        "hitos": [{"n": i, "fecha": d.strftime("%d/%m"), "texto": t} for d, i, t in numerados],
        "frase": frase,
        "exploratorio": exploratorio,
        "tabla": [{"semana": s.inicio.strftime("%d/%m"), "fletes": s.fletes, "cobrado": plata(s.cobrado),
                   "publicidad": plata(s.publicidad), "fijos": plata(s.fijos), "resultado": plata(s.resultado),
                   "acumulado": plata(a), "por_hora": plata(s.por_hora) if s.por_hora is not None else "—"}
                  for s, a in zip(ss, acum)],
    }


# --------------------------------------------------------------------------
# En curso y de donde salen los numeros
# --------------------------------------------------------------------------

ETAPA_CORTA = {"nuevo": "consulta", "calificado": "con datos", "presupuestado": "presupuestado",
               "confirmado": "confirmado"}


def vista_en_curso(ps, quien: dict[str, str]) -> dict | None:
    from estiba.economia.en_curso import rango

    if not ps:
        return None
    lo, medio, hi = rango(ps)
    filas = []
    for x in ps:
        p = x.p
        prob = "—" if p.valor is None else (f"{p.valor:.0%}" + (f" (entre {p.lo:.0%} y {p.hi:.0%})"
                                                               if p.lo is not None else ""))
        filas.append({"job": x.job_id, "quien": quien.get(x.job_id, x.job_id.split("@")[0][-6:]),
                      "etapa": ETAPA_CORTA.get(x.etapa.value, x.etapa.value), "monto": plata(x.monto),
                      "fuente": x.fuente, "prob": prob, "firmeza": p.estado.etiqueta,
                      "cuando": x.cuando.strftime("%d/%m") if x.cuando else None, "vencido": x.vencido})
    return {"filas": filas, "n": len(ps), "total": plata(sum((x.monto for x in ps), Decimal(0))),
            "esperado": plata(medio), "lo": plata(lo), "hi": plata(hi),
            "vencidos": sum(1 for x in ps if x.vencido),
            "exploratorio": not all(x.p.fuerte for x in ps)}


def vista_fuentes(hechos: int, cobro_anotado: int, cobro_cotizado: int, medidos: int, dia_chat: int,
                  publicidad_hasta: date | None, hoy: date, ultimo_mensaje: str | None, fijos: Decimal) -> list[dict]:
    """Cada dato de los tableros con su origen y cuanto creerle (firme, estimado, supuesto, falta)."""
    def fila(dato, origen, nivel):
        return {"dato": dato, "origen": origen, "nivel": nivel}
    salida = []
    if hechos:
        salida.append(fila("Lo cobrado",
                           f"{cobro_anotado} de {hechos} anotados al cerrar el flete"
                           + (f"; {cobro_cotizado} con lo cotizado" if cobro_cotizado else ""),
                           "firme" if cobro_anotado == hechos else "estimado"))
        salida.append(fila("Km y horas de cada flete",
                           f"{medidos} de {hechos} confirmados al cerrar; el resto, tu promedio"
                           if medidos < hechos else "todos confirmados al cerrar",
                           "firme" if medidos == hechos else ("estimado" if medidos else "supuesto")))
        salida.append(fila("El día de cada flete",
                           f"{dia_chat} de {hechos} con la fecha que dice el chat; el resto, el último mensaje",
                           "firme" if dia_chat == hechos else "estimado"))
    else:
        salida.append(fila("Fletes hechos", "ninguno marcado como realizado", "falta"))
    if publicidad_hasta is None:
        salida.append(fila("Publicidad", "sin cargar", "falta"))
    else:
        atraso = (hoy - publicidad_hasta).days - 1
        salida.append(fila("Publicidad", f"cargada hasta el {publicidad_hasta:%d/%m}"
                           + (f": faltan {atraso} días" if atraso > 0 else ", al día"),
                           "firme" if atraso <= 0 else "desactualizado"))
    salida.append(fila("Conversaciones", f"última leída {ultimo_mensaje}" if ultimo_mensaje else "sin leer todavía",
                       "firme" if ultimo_mensaje else "falta"))
    salida.append(fila("Costos fijos", f"{plata(fijos)} por mes, los que cargaste en Costos y metas", "firme"))
    return salida
