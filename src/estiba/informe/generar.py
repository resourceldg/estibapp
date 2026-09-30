"""El informe de gestion: lo que paso, lo que cuesta, lo que viene y que hacer.

Pensado para sentarse a decidir con un papel adelante, o mandarselo a un
contador o a un socio. Arranca por las conclusiones (una persona ocupada lee
la primera pagina y nada mas) y despues muestra de donde sale cada una.

Todo sale de las mismas cuentas que la pantalla de Plata y objetivos: el
informe no calcula nada propio, solo lo ordena y lo dibuja.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from estiba.finanzas import costos as F
from estiba.finanzas.pronostico import Pronostico
from estiba.formato import pesos
from estiba.informe import graficos as G

PLANTILLAS = Path(__file__).parent


@dataclass(frozen=True)
class FleteDelInforme:
    dia: date
    cliente: str
    ruta: str
    km: int | None
    cobrado: Decimal | None
    sugerido: Decimal | None
    minimo: Decimal | None


def _signo(v) -> str:
    v = Decimal(v)
    return f"−{pesos(-v)}" if v < 0 else pesos(v)


def _pct(x: float | None) -> str:
    if x is None:
        return "—"
    return f"−{-x:.0%}" if x < 0 else f"{x:.0%}"


def _semanas(c: F.Costos, hechos, gastos, desde: date, hasta: date) -> list[tuple[str, float, float, float]]:
    """Semana por semana, de lunes a domingo, recortadas al periodo."""
    salida = []
    inicio = desde - timedelta(days=desde.weekday())
    while inicio <= hasta:
        a, b = max(inicio, desde), min(inicio + timedelta(days=6), hasta)
        bal = F.balance(c, hechos, gastos, a, b)
        salida.append((f"{a:%d/%m}", float(bal.ingresos), float(bal.costos), float(bal.ganancia)))
        inicio += timedelta(days=7)
    return salida


def _delta(actual, antes, mas_es_mejor=True, formato=pesos) -> dict | None:
    """Cuanto cambio contra el periodo anterior, y si eso es bueno o malo."""
    if antes is None or actual is None:
        return None
    dif = Decimal(str(actual)) - Decimal(str(antes))
    if dif == 0:
        return {"texto": "igual que antes", "tono": "neutro", "flecha": "="}
    bueno = (dif > 0) == mas_es_mejor
    return {"texto": f"{'+' if dif > 0 else '−'}{formato(abs(dif))} vs. antes", "tono": "ok" if bueno else "mal",
            "flecha": "▲" if dif > 0 else "▼"}


def datos(*, negocio: str, periodo: str, desde: date | None, hoy: date, ahora: datetime,
          c: F.Costos, hechos: list[F.FleteHecho], gastos: list[F.GastoPublicidad],
          embudo, pronostico: Pronostico, fletes: list[FleteDelInforme],
          tema: G.Tema = G.PAPEL, origen: str = "todos", historia=None,
          elasticidad: float = 1.0, saturacion: float = 0.8, tope_dia: float | None = None) -> dict:
    T = tema
    b = F.balance(c, hechos, gastos, desde, hoy)
    # El mismo largo, justo antes: contra que comparar. Desde siempre no
    # tiene "antes".
    antes = None
    if desde is not None:
        largo = (hoy - desde).days + 1
        antes = F.balance(c, hechos, gastos, desde - timedelta(days=largo), desde - timedelta(days=1))
    inicio = hoy - timedelta(days=b.dias - 1)
    ritmo = b.fletes * 30 / b.dias if b.dias else 0.0
    a_pleno = F.plan_a_capacidad(c)
    minimo_hoy = F.precio_minimo(c, max(1, round(ritmo)))
    probable = pronostico.escenarios[1] if pronostico.escenarios else None

    # -- las conclusiones: lo que alguien apurado tiene que llevarse -------
    conclusiones = []
    conclusiones.append(
        f"En {b.dias} días hiciste {b.fletes} fletes y facturaste {pesos(b.ingresos)}. Con todos los costos "
        f"adentro te quedaron {_signo(b.ganancia)} ({_pct(b.margen)} de lo facturado).")
    costos = [("la camioneta", b.camioneta), ("la publicidad", b.publicidad), ("los costos fijos", b.fijos)]
    mayor, cuanto = max(costos, key=lambda x: x[1])
    if b.costos:
        conclusiones.append(f"El costo que más pesa es {mayor}: {pesos(cuanto)}, el "
                            f"{float(cuanto / b.costos):.0%} de lo que gastaste.")
    if b.ticket and minimo_hoy:
        if b.ticket < minimo_hoy:
            conclusiones.append(
                f"Cobrás {pesos(b.ticket.quantize(Decimal(1)))} por flete, y a tu ritmo actual "
                f"({ritmo:.0f} por mes) el mínimo para no perder es {pesos(F.redondear(minimo_hoy))}. "
                "Hoy cada flete no alcanza a pagar su parte de los fijos.")
        else:
            conclusiones.append(
                f"Cobrás {pesos(b.ticket.quantize(Decimal(1)))} por flete, arriba del mínimo de "
                f"{pesos(F.redondear(minimo_hoy))} a tu ritmo actual.")
    if a_pleno:
        conclusiones.append(
            f"Para ganar {pesos(c.ganancia_objetivo)} por mes trabajando a pleno ({a_pleno.fletes} fletes), "
            f"cada flete tiene que dejar {pesos(F.redondear(a_pleno.precio))} en promedio, con unos "
            f"{pesos(F.redondear(a_pleno.publicidad_dia, Decimal(500)))} por día de publicidad.")
    if probable:
        conclusiones.append(
            f"Si todo sigue como viene, lo más probable es que el mes cierre en {_signo(probable.ganancia)} "
            f"con {probable.fletes} fletes.")

    # -- los graficos ------------------------------------------------------
    graficos = {
        "cascada": G.cascada([("Facturado", b.ingresos), ("Camioneta", -b.camioneta),
                              ("Publicidad", -b.publicidad), ("Fijos", -b.fijos), ("Te queda", b.ganancia)], T=T),
        "semanas": G.barras_semanales(_semanas(c, hechos, gastos, inicio, hoy), T=T),
        "embudo": G.barras_horizontales(
            [(p.etiqueta, p.cantidad, f"{p.cantidad} · {p.del_total:.0%}") for p in embudo.pasos], T=T),
        "motivos": G.barras_horizontales([(m, n, str(n)) for m, n in embudo.motivos], color=T.mal, T=T),
        "escenarios": G.escenarios([(e.nombre, float(e.ganancia), e.fletes) for e in pronostico.escenarios],
                                   float(c.ganancia_objetivo), T=T),
        "minimo": G.curva_minimo(
            [(n, float(F.precio_minimo(c, n))) for n in range(4, int(c.fletes_por_mes_max) + 1, 2)
             if F.precio_minimo(c, n) is not None],
            float(b.ticket) if b.ticket else None,
            float(a_pleno.precio) if a_pleno else None,
            ritmo, T=T),
    }
    numero = lambda v: str(int(v))
    porcentaje = lambda v: f"{float(v):.0%}"
    comparar = []
    if antes is not None:
        comparar = [
            ("Facturado", pesos(b.ingresos), pesos(antes.ingresos), _delta(b.ingresos, antes.ingresos)),
            ("Ganancia limpia", _signo(b.ganancia), _signo(antes.ganancia), _delta(b.ganancia, antes.ganancia)),
            ("Fletes hechos", str(b.fletes), str(antes.fletes), _delta(b.fletes, antes.fletes, formato=numero)),
            ("Cobrado por flete", pesos(b.ticket.quantize(Decimal(1))) if b.ticket else "—",
             pesos(antes.ticket.quantize(Decimal(1))) if antes.ticket else "—",
             _delta(b.ticket.quantize(Decimal(1)), antes.ticket.quantize(Decimal(1))) if b.ticket and antes.ticket else None),
            ("Margen", _pct(b.margen), _pct(antes.margen),
             _delta(b.margen, antes.margen, formato=porcentaje) if b.margen is not None and antes.margen is not None else None),
            ("Publicidad", pesos(b.publicidad.quantize(Decimal(1))), pesos(antes.publicidad.quantize(Decimal(1))),
             _delta(b.publicidad.quantize(Decimal(1)), antes.publicidad.quantize(Decimal(1)), mas_es_mejor=False)),
        ]

    planes = []
    if a_pleno:
        planes.append(("Trabajando a pleno", a_pleno))
    if b.ticket and (p := F.plan_con_precio(c, b.ticket)):
        planes.append((f"Cobrando {pesos(b.ticket.quantize(Decimal(1)))}", p))
    tipico = c.tarifa(c.km_por_flete)
    if p := F.plan_con_precio(c, tipico):
        planes.append((f"Con tu tarifa ({pesos(tipico)})", p))

    return {
        "analisis": analisis(c, pronostico, historia, elasticidad=elasticidad, saturacion=saturacion,
                             tope_dia=tope_dia, tema=tema) if historia is not None else None,
        "negocio": negocio,
        "periodo": periodo,
        "origen": origen,
        "comparar": comparar,
        "antes_desde": (desde - timedelta(days=(hoy - desde).days + 1)) if desde else None,
        "hay_datos": bool(b.fletes or b.publicidad),
        "desde": inicio,
        "hasta": hoy,
        "generado": ahora,
        "conclusiones": conclusiones,
        "kpis": [
            ("Facturado", pesos(b.ingresos), f"{b.fletes} fletes"),
            ("Ganancia limpia", _signo(b.ganancia), f"margen {_pct(b.margen)}"),
            ("Por flete", pesos(b.ticket.quantize(Decimal(1))) if b.ticket else "—", "cobrado promedio"),
            ("Retorno publicidad", f"{b.retorno_publicidad:.1f}×".replace(".", ",") if b.retorno_publicidad else "—",
             "facturado por $1"),
            ("Cierre", f"{pronostico.cierre.valor:.0f}%", "medido" if pronostico.cierre.medido else "supuesto"),
            ("Costo por conversación", pesos(pronostico.costo_consulta.valor),
             "medido" if pronostico.costo_consulta.medido else "supuesto"),
        ],
        "cuenta": [
            ("Facturado", pesos(b.ingresos), f"{b.fletes} fletes"),
            ("Camioneta", _signo(-b.camioneta), f"{b.fletes} × {int(c.km_por_flete)} km × {pesos(c.costo_km)}/km"),
            ("Publicidad", _signo(-b.publicidad), f"lo gastado + {int(c.impuestos_publicidad)}% de impuestos"),
            ("Fijos", _signo(-b.fijos), f"{pesos(c.fijos_mes)} por mes, {b.dias} días"),
            ("Te queda", _signo(b.ganancia), f"{_pct(b.margen)} de margen"),
        ],
        "graficos": graficos,
        "escenarios": [(e.nombre, _signo(e.ganancia), e.fletes, pesos(e.ingresos), f"{e.cierre:.0%}")
                       for e in pronostico.escenarios],
        "fuentes": [
            ("Cierre", f"{pronostico.cierre.valor:.0f}%", pronostico.cierre.medido, pronostico.cierre.detalle),
            ("Costo por conversación", pesos(pronostico.costo_consulta.valor), pronostico.costo_consulta.medido,
             pronostico.costo_consulta.detalle),
            ("Cobrás en promedio", pesos(pronostico.ticket.valor.quantize(Decimal(1))), pronostico.ticket.medido,
             pronostico.ticket.detalle),
        ],
        "semana": pronostico.semana,
        "planes": [(nombre, p.fletes, pesos(F.redondear(p.precio)), p.consultas,
                    pesos(F.redondear(p.publicidad_mes)), pesos(F.redondear(p.publicidad_dia, Decimal(500))), p.entra)
                   for nombre, p in planes],
        "minimos": [(n, pesos(F.redondear(F.precio_minimo(c, n))))
                    for n in sorted({10, 20, 30, int(c.fletes_por_mes_max), max(1, round(ritmo))})
                    if F.precio_minimo(c, n) is not None],
        "ritmo": round(ritmo),
        "fletes": [
            {"dia": f"{f.dia:%d/%m}", "cliente": f.cliente, "ruta": f.ruta, "km": f.km,
             "cobrado": pesos(f.cobrado) if f.cobrado is not None else "—",
             "sugerido": pesos(f.sugerido) if f.sugerido is not None else "—",
             "dif": _signo(f.cobrado - f.sugerido) if f.cobrado is not None and f.sugerido is not None else "",
             "bajo_minimo": f.cobrado is not None and f.minimo is not None and f.cobrado < f.minimo,
             "tono": ("mal" if f.cobrado is not None and f.sugerido is not None and f.cobrado < f.sugerido else "ok")}
            for f in sorted(fletes, key=lambda f: f.dia)
        ],
        "costos": [
            ("Fijos por mes", [("Crédito camioneta", pesos(c.credito)), ("Seguro", pesos(c.seguro)), ("Monotributo", pesos(c.monotributo)),
                               ("VTV y GNC", pesos(c.vtv_gnc)), ("Patente", pesos(c.patente)),
                               ("Celular", pesos(c.celular)), ("Otros", pesos(c.otros_fijos))]),
            ("Camioneta", [("GNC cada 100 km", pesos(c.gnc_cada_100km)), ("Aceite", f"{pesos(c.aceite_km)}/km"),
                           ("Cubiertas", f"{pesos(c.cubiertas_km)}/km"), ("Arreglos", f"{pesos(c.arreglos_km)}/km"),
                           ("Costo total", f"{pesos(c.costo_km)}/km")]),
            ("Trabajo y meta", [("Meta de ganancia", pesos(c.ganancia_objetivo)),
                                ("Fletes que entran por mes", str(int(c.fletes_por_mes_max))),
                                ("Horas por flete", f"{c.horas_flete}".replace(".", ",")),
                                ("Tu hora vale", pesos(c.valor_hora) if c.valor_hora else "—")]),
        ],
    }


def html(contexto: dict) -> str:
    from jinja2 import Environment, FileSystemLoader, select_autoescape

    entorno = Environment(loader=FileSystemLoader(PLANTILLAS), autoescape=select_autoescape(["html"]))
    return entorno.get_template("informe.html").render(**contexto)


def pdf(contexto: dict) -> bytes:
    """El PDF. Necesita WeasyPrint: uv sync --extra informe."""
    from weasyprint import HTML

    return HTML(string=html(contexto), base_url=str(PLANTILLAS)).write_pdf()


def analisis(c: F.Costos, p: Pronostico, h, *, elasticidad: float = 1.0, saturacion: float = 0.8,
             tope_dia: float | None = None, tema: G.Tema = G.PAPEL) -> dict:
    """Optimo, sensibilidad, equilibrio y Monte Carlo, con lo que dicen en palabras."""
    from estiba.finanzas import decision as D

    m = D.Modelo(precio=float(p.ticket.valor), gasto_dia=float(p.gasto_dia) or 4000.0,
                 costo_consulta=float(p.costo_consulta.valor), cierre=float(p.cierre.valor) / 100,
                 solos_mes=float(p.sin_publicidad_mes), elasticidad=elasticidad, saturacion=saturacion)
    o = D.optimizar(c, m, gasto_max=tope_dia)
    base, palancas = D.sensibilidad(c, m, m.precio, m.gasto_dia)
    eq = D.equilibrio(c, m.precio, m.gasto_dia)
    casos = h.consultas_meta or h.consultas_publicidad
    mc = D.montecarlo(c, m, m.precio, m.gasto_dia, exitos=min(h.hechos_publicidad, casos), casos=casos,
                      cobrados=[float(x) for x in h.cobrado])
    mc_opt = D.montecarlo(c, m, o.mejor.precio, o.mejor.gasto_dia, exitos=min(h.hechos_publicidad, casos),
                          casos=casos, cobrados=[float(x) for x in h.cobrado])
    meta = float(c.ganancia_objetivo)
    dinero = lambda v: _signo(Decimal(str(round(v))))

    frases = {
        "optimo": (f"La combinación que más deja es cobrar {pesos(Decimal(str(o.mejor.precio)))} y poner "
                   f"{pesos(Decimal(str(o.mejor.gasto_dia)))} por día en Meta: unos {o.mejor.fletes:.0f} fletes y "
                   f"{dinero(o.mejor.ganancia)} al mes, contra {dinero(o.actual.ganancia)} de hoy."),
        "meta": (f"La forma más barata de llegar a {pesos(c.ganancia_objetivo)}: cobrar "
                 f"{pesos(Decimal(str(o.meta.precio)))} con {pesos(Decimal(str(o.meta.gasto_dia)))} por día."
                 if o.meta else
                 f"Con estos supuestos, ninguna combinación de precio y publicidad llega a {pesos(c.ganancia_objetivo)}. "
                 "La meta pide otra fuente de fletes (clientes que vuelven, empresas, recomendados) o más capacidad, "
                 "no solo más anuncios."),
        "tornado": (f"La palanca que más mueve es {palancas[0].nombre.lower()}: un 20 % para un lado o para el otro "
                    f"cambia la ganancia en {pesos(Decimal(str(round(palancas[0].rango))))}. "
                    f"Después vienen {palancas[1].nombre.lower()} y {palancas[2].nombre.lower()}."),
        "equilibrio": (f"A {pesos(Decimal(str(round(m.precio))))} por flete y con esta publicidad, para salir hecho "
                       f"hacen falta {eq:.0f} fletes por mes." if eq else
                       "A este precio cada flete pierde: no hay cantidad de fletes que alcance."),
        "montecarlo": (f"De {len(mc.ganancias):,} meses".replace(",", ".")
                       + " simulados con la incertidumbre de tus datos, "
                       f"en el {mc.p_ganar:.0%} ganás plata y en el {mc.p_meta:.0%} llegás a la meta. "
                       f"En 8 de cada 10 meses el resultado queda entre {dinero(mc.p10)} y {dinero(mc.p90)}."),
        "montecarlo_opt": (f"Con la combinación óptima, ganar plata pasa del {mc.p_ganar:.0%} al {mc_opt.p_ganar:.0%} "
                           f"de los meses."),
    }
    return {
        "frases": frases,
        "elasticidad": elasticidad,
        "saturacion": saturacion,
        "tope_dia": round(o.gastos[-1]),
        "optimo": {"precio": pesos(Decimal(str(o.mejor.precio))), "gasto": pesos(Decimal(str(o.mejor.gasto_dia))),
                   "fletes": f"{o.mejor.fletes:.0f}", "ganancia": dinero(o.mejor.ganancia),
                   "sobran": o.mejor.sobran, "precio_crudo": int(o.mejor.precio), "gasto_crudo": int(o.mejor.gasto_dia)},
        "actual": {"ganancia": dinero(o.actual.ganancia), "fletes": f"{o.actual.fletes:.0f}"},
        "mc": {"p_ganar": f"{mc.p_ganar:.0%}", "p_meta": f"{mc.p_meta:.0%}", "p10": dinero(mc.p10),
               "p50": dinero(mc.p50), "p90": dinero(mc.p90), "fletes": f"{mc.fletes_p50:.0f}",
               "p_ganar_opt": f"{mc_opt.p_ganar:.0%}"},
        "equilibrio": f"{eq:.0f}" if eq else "—",
        "palancas": [(x.nombre, dinero(x.baja), dinero(x.sube), dinero(x.rango)) for x in palancas],
        "graficos": {
            "calor": G.mapa_calor(o.precios, o.gastos, o.grilla, (m.precio, m.gasto_dia),
                                  (o.mejor.precio, o.mejor.gasto_dia), meta, T=tema),
            "tornado": G.tornado(base, [(x.nombre, x.baja, x.sube) for x in palancas], T=tema),
            "histograma": G.histograma(mc.ganancias, meta, T=tema),
        },
    }
