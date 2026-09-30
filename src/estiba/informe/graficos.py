"""Graficos del informe, como SVG escrito a mano.

Sin matplotlib ni nada que dibuje por su cuenta: son cinco formas de grafico,
siempre las mismas, con la misma tipografia y los mismos colores que la
app. Un SVG en el PDF se imprime nitido a cualquier tamaño y pesa nada.

Cada funcion recibe numeros y devuelve un `<svg>` listo para meter en el
HTML. Los montos llegan en pesos (Decimal o float) y se rotulan en miles
("$175 mil") para que las etiquetas entren.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from html import escape

@dataclass(frozen=True)
class Tema:
    """Los colores de un grafico. Los mismos tonos en papel y en pantalla,
    ajustados al fondo: lo que en blanco se lee, en grafito se apaga."""

    tinta: str
    tenue: str
    linea: str
    pista: str
    ok: str
    mal: str
    aviso: str
    acento: str
    borde_punto: str
    partes: dict
    fuente: str = "Noto Sans, DejaVu Sans, sans-serif"
    #: En el celular el grafico se achica a la mitad: se dibuja mas angosto
    #: y con letra mas grande para que se lea a 10-12 px reales.
    ancho: int = 640
    texto: float = 1.0


PAPEL = Tema(
    tinta="#1d1b20", tenue="#77717a", linea="#e7e3dd", pista="#f3f0ec", ok="#2f8f5b", mal="#c8463d",
    aviso="#b7791f", acento="#e0662a", borde_punto="#ffffff",
    partes={"Camioneta": "#5b7fd6", "Tu trabajo": "#e0662a", "Publicidad": "#a066c2",
            "Ayudantes": "#3f9aa6", "Fijos": "#8a8f98"},
)
#: El de la app: los tokens de estiba.css.
PANTALLA = Tema(
    tinta="#ece9e4", tenue="#9a938e", linea="#302e36", pista="#2a292f", ok="#5cc485", mal="#e0857e",
    aviso="#d9a15c", acento="#e0662a", borde_punto="#1f1e23",
    partes={"Camioneta": "#86a8ff", "Tu trabajo": "#e0662a", "Publicidad": "#c08bd9",
            "Ayudantes": "#6fb9c4", "Fijos": "#a9a4a0"},
    fuente="ui-sans-serif, system-ui, sans-serif",
    ancho=440,
    texto=1.3,
)


def miles(v) -> str:
    """175000 -> "$175 mil"; 2000000 -> "$2 M"."""
    v = float(v)
    signo = "−" if v < 0 else ""
    v = abs(v)
    if v >= 1_000_000:
        return f"{signo}${v / 1_000_000:.1f} M".replace(".", ",").replace(",0 M", " M")
    if v >= 1000:
        return f"{signo}${v / 1000:.0f} mil"
    return f"{signo}${v:.0f}"


def _texto(T: Tema, x, y, t, tam=11, color=None, ancla="middle", peso=400) -> str:
    tam = round(tam * T.texto, 1)
    return (f"<text x='{x:.1f}' y='{y:.1f}' font-size='{tam}' fill='{color or T.tenue}' text-anchor='{ancla}' "
            f"font-weight='{peso}' font-family='{T.fuente}'>{escape(str(t))}</text>")


def _escala(valores) -> tuple[float, float]:
    """De donde a donde va el eje: siempre incluye el cero, con aire arriba."""
    vals = [float(v) for v in valores]
    lo, hi = min([0.0, *vals]), max([0.0, *vals])
    if hi == lo:
        hi = lo + 1
    aire = (hi - lo) * 0.1
    return (lo - aire if lo < 0 else lo), hi + aire


def barras_semanales(semanas: list[tuple[str, float, float, float]], ancho=None, alto=230, T: Tema = PAPEL) -> str:
    """Por semana: facturado y costos lado a lado, y la ganancia como punto.

    `semanas`: [(etiqueta, facturado, costos, ganancia)].
    """
    if not semanas:
        return ""
    ancho = ancho or T.ancho
    m_izq, m_der, m_arr, m_ab = 56, 12, 16, 34
    w, h = ancho - m_izq - m_der, alto - m_arr - m_ab
    valores = [v for _, f, c, g in semanas for v in (f, c, g)]
    lo, hi = _escala(valores)
    y = lambda v: m_arr + h - (float(v) - lo) / (hi - lo) * h
    partes = [f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 {ancho} {alto}' width='100%'>"]
    for i in range(5):
        v = lo + (hi - lo) * i / 4
        partes.append(f"<line x1='{m_izq}' x2='{ancho - m_der}' y1='{y(v):.1f}' y2='{y(v):.1f}' stroke='{T.linea}'/>")
        partes.append(_texto(T, m_izq - 6, y(v) + 4, miles(v), 10, ancla="end"))
    paso = w / len(semanas)
    barra = min(28, paso * 0.32)
    for i, (etq, fact, cost, gan) in enumerate(semanas):
        cx = m_izq + paso * (i + 0.5)
        for dx, v, color in ((-barra / 2 - 1, fact, T.ok), (barra / 2 + 1, cost, T.mal)):
            y0, y1 = sorted((y(0), y(v)))
            partes.append(f"<rect x='{cx + dx - barra / 2:.1f}' y='{y0:.1f}' width='{barra:.1f}' "
                          f"height='{max(y1 - y0, 0.5):.1f}' rx='2' fill='{color}' opacity='.85'/>")
        partes.append(f"<circle cx='{cx:.1f}' cy='{y(gan):.1f}' r='4.5' fill='{T.tinta}' stroke='{T.borde_punto}' stroke-width='1.5'/>")
        partes.append(_texto(T, cx, alto - m_ab + 16, etq, 10))
    partes.append(f"<line x1='{m_izq}' x2='{ancho - m_der}' y1='{y(0):.1f}' y2='{y(0):.1f}' stroke='{T.tenue}'/>")
    partes.append("</svg>")
    return "".join(partes)


def cascada(pasos: list[tuple[str, float]], ancho=None, alto=230, T: Tema = PAPEL) -> str:
    """De lo facturado a lo que queda: cada costo baja un escalon.

    `pasos`: [("Facturado", +x), ("Camioneta", -y), ..., ("Te queda", total)].
    El primero y el ultimo son totales; los del medio, restas.
    """
    ancho = ancho or T.ancho
    m_izq, m_der, m_arr, m_ab = 56, 12, 22, 34
    w, h = ancho - m_izq - m_der, alto - m_arr - m_ab
    acumulado, tramos = 0.0, []
    for i, (etq, v) in enumerate(pasos):
        v = float(v)
        if i == 0 or i == len(pasos) - 1:
            tramos.append((etq, 0.0, v, v))
            acumulado = v
        else:
            tramos.append((etq, acumulado, acumulado + v, v))
            acumulado += v
    lo, hi = _escala([x for _, a, b, _ in tramos for x in (a, b)])
    y = lambda v: m_arr + h - (v - lo) / (hi - lo) * h
    paso = w / len(tramos)
    barra = paso * 0.56
    partes = [f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 {ancho} {alto}' width='100%'>"]
    partes.append(f"<line x1='{m_izq}' x2='{ancho - m_der}' y1='{y(0):.1f}' y2='{y(0):.1f}' stroke='{T.tenue}'/>")
    for i, (etq, a, b, v) in enumerate(tramos):
        x = m_izq + paso * i + (paso - barra) / 2
        y0, y1 = sorted((y(a), y(b)))
        total = i in (0, len(tramos) - 1)
        color = (T.ok if v >= 0 else T.mal) if total else T.partes.get(etq, T.mal)
        partes.append(f"<rect x='{x:.1f}' y='{y0:.1f}' width='{barra:.1f}' height='{max(y1 - y0, 1):.1f}' "
                      f"rx='2' fill='{color}' opacity='{1 if total else .8}'/>")
        if i < len(tramos) - 1:
            nx = m_izq + paso * (i + 1) + (paso - barra) / 2
            partes.append(f"<line x1='{x + barra:.1f}' x2='{nx:.1f}' y1='{y(b):.1f}' y2='{y(b):.1f}' "
                          f"stroke='{T.tenue}' stroke-dasharray='2 2'/>")
        partes.append(_texto(T, x + barra / 2, y0 - 5, miles(v), 10, T.tinta, peso=600))
        partes.append(_texto(T, x + barra / 2, alto - m_ab + 16, etq, 10))
    partes.append("</svg>")
    return "".join(partes)


def barras_horizontales(filas: list[tuple[str, float, str]], ancho=None, alto_fila=26, color=None, T: Tema = PAPEL) -> str:
    """Una barra por fila, con su valor al final. `filas`: [(etiqueta, valor, nota)]."""
    if not filas:
        return ""
    color = color or T.acento
    ancho = ancho or T.ancho
    m_izq, m_der = 150, 110
    alto = alto_fila * len(filas) + 6
    tope = max(float(v) for _, v, _ in filas) or 1.0
    w = ancho - m_izq - m_der
    partes = [f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 {ancho} {alto}' width='100%'>"]
    for i, (etq, v, nota) in enumerate(filas):
        yy = 3 + i * alto_fila
        largo = max(float(v) / tope * w, 1.5)
        partes.append(f"<rect x='{m_izq}' y='{yy + 5}' width='{w}' height='{alto_fila - 12}' rx='3' fill='{T.pista}'/>")
        partes.append(f"<rect x='{m_izq}' y='{yy + 5}' width='{largo:.1f}' height='{alto_fila - 12}' rx='3' fill='{color}'/>")
        partes.append(_texto(T, m_izq - 8, yy + alto_fila / 2 + 3, etq, 11, T.tinta, "end"))
        partes.append(_texto(T, m_izq + w + 8, yy + alto_fila / 2 + 3, nota, 11, T.tinta, "start", 600))
    partes.append("</svg>")
    return "".join(partes)


def escenarios(filas: list[tuple[str, float, int]], meta: float, ancho=None, alto=210, T: Tema = PAPEL) -> str:
    """Ganancia del mes en tres escenarios, contra la linea de la meta."""
    ancho = ancho or T.ancho
    m_izq, m_der, m_arr, m_ab = 56, 12, 20, 36
    w, h = ancho - m_izq - m_der, alto - m_arr - m_ab
    lo, hi = _escala([v for _, v, _ in filas] + [meta])
    y = lambda v: m_arr + h - (float(v) - lo) / (hi - lo) * h
    paso = w / len(filas)
    barra = paso * 0.42
    partes = [f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 {ancho} {alto}' width='100%'>"]
    partes.append(f"<line x1='{m_izq}' x2='{ancho - m_der}' y1='{y(0):.1f}' y2='{y(0):.1f}' stroke='{T.tenue}'/>")
    partes.append(f"<line x1='{m_izq}' x2='{ancho - m_der}' y1='{y(meta):.1f}' y2='{y(meta):.1f}' "
                  f"stroke='{T.ok}' stroke-dasharray='5 4' stroke-width='1.5'/>")
    partes.append(_texto(T, ancho - m_der, y(meta) - 6, f"meta {miles(meta)}", 10, T.ok, "end", 600))
    for i, (etq, v, fletes) in enumerate(filas):
        cx = m_izq + paso * (i + 0.5)
        y0, y1 = sorted((y(0), y(v)))
        color = T.ok if v >= 0 else T.mal
        partes.append(f"<rect x='{cx - barra / 2:.1f}' y='{y0:.1f}' width='{barra:.1f}' height='{max(y1 - y0, 1):.1f}' "
                      f"rx='3' fill='{color}' opacity='{1 if i == 1 else .55}'/>")
        partes.append(_texto(T, cx, (y1 + 14) if v < 0 else (y0 - 6), miles(v), 11, T.tinta, peso=700))
        partes.append(_texto(T, cx, alto - m_ab + 15, etq, 10, T.tinta, peso=600))
        partes.append(_texto(T, cx, alto - m_ab + 28, f"{fletes} fletes", 9))
    partes.append("</svg>")
    return "".join(partes)


def curva_minimo(puntos: list[tuple[int, float]], ticket: float | None, meta: float | None,
                 ritmo: float | None, ancho=None, alto=230, T: Tema = PAPEL) -> str:
    """El precio minimo segun cuantos fletes por mes, con lo que se cobra y lo que pide la meta.

    Donde la linea del ticket queda debajo de la curva, se pierde plata.
    """
    ancho = ancho or T.ancho
    m_izq, m_der, m_arr, m_ab = 56, 118, 14, 32
    w, h = ancho - m_izq - m_der, alto - m_arr - m_ab
    xs = [n for n, _ in puntos]
    ys = [v for _, v in puntos] + [v for v in (ticket, meta) if v]
    x0, x1 = min(xs), max(xs)
    lo, hi = 0.0, max(ys) * 1.08
    x = lambda n: m_izq + (n - x0) / ((x1 - x0) or 1) * w
    y = lambda v: m_arr + h - (float(v) - lo) / (hi - lo) * h
    partes = [f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 {ancho} {alto}' width='100%'>"]
    for i in range(5):
        v = lo + (hi - lo) * i / 4
        partes.append(f"<line x1='{m_izq}' x2='{m_izq + w}' y1='{y(v):.1f}' y2='{y(v):.1f}' stroke='{T.linea}'/>")
        partes.append(_texto(T, m_izq - 6, y(v) + 4, miles(v), 10, ancla="end"))
    for n in xs[:: max(1, len(xs) // 8)]:
        partes.append(_texto(T, x(n), alto - m_ab + 16, n, 10))
    partes.append(_texto(T, m_izq + w / 2, alto - 4, "fletes por mes", 10))
    camino = " ".join(f"{'M' if i == 0 else 'L'}{x(n):.1f},{y(v):.1f}" for i, (n, v) in enumerate(puntos))
    area = camino + f" L{x(x1):.1f},{y(lo):.1f} L{x(x0):.1f},{y(lo):.1f} Z"
    partes.append(f"<path d='{area}' fill='{T.mal}' opacity='.07'/>")
    partes.append(f"<path d='{camino}' fill='none' stroke='{T.mal}' stroke-width='2.2'/>")
    partes.append(_texto(T, m_izq + w + 6, y(puntos[-1][1]) + 4, "mínimo", 10, T.mal, "start", 600))
    for valor, color, nombre in ((ticket, T.tinta, "cobrás"), (meta, T.ok, "meta")):
        if valor:
            partes.append(f"<line x1='{m_izq}' x2='{m_izq + w}' y1='{y(valor):.1f}' y2='{y(valor):.1f}' "
                          f"stroke='{color}' stroke-dasharray='5 4' stroke-width='1.4'/>")
            partes.append(_texto(T, m_izq + w + 6, y(valor) + 4, f"{nombre} {miles(valor)}", 10, color, "start", 600))
    if ritmo is not None and x0 <= ritmo <= x1:
        partes.append(f"<line x1='{x(ritmo):.1f}' x2='{x(ritmo):.1f}' y1='{m_arr}' y2='{m_arr + h}' stroke='{T.aviso}' stroke-width='1.2'/>")
        partes.append(_texto(T, x(ritmo) + 4, m_arr + 10, "tu ritmo", 10, T.aviso, "start", 600))
    partes.append("</svg>")
    return "".join(partes)


def _mezcla(a: str, b: str, t: float) -> str:
    """Un color entre `a` y `b` (hex), t de 0 a 1."""
    t = max(0.0, min(1.0, t))
    ca = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    cb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(ca, cb))


def mapa_calor(precios: list[float], gastos: list[float], grilla: list[tuple[float, float, float]],
               actual: tuple[float, float] | None, optimo: tuple[float, float] | None,
               meta: float | None, ancho=None, alto=330, T: Tema = PAPEL) -> str:
    """La ganancia del mes para cada precio (horizontal) y publicidad por dia (vertical).

    Rojo pierde, verde gana; el blanco/gris es salir hecho. El circulo es hoy,
    la estrella el optimo. Si alguna celda llega a la meta, lleva un borde.
    """
    ancho = ancho or T.ancho
    m_izq, m_der, m_arr, m_ab = 62, 14, 12, 40
    w, h = ancho - m_izq - m_der, alto - m_arr - m_ab
    cw, ch = w / len(precios), h / len(gastos)
    valores = [v for _, _, v in grilla]
    peor, mejor = min(valores), max(valores)
    neutro = T.pista
    idx_p = {p: i for i, p in enumerate(precios)}
    idx_g = {g: j for j, g in enumerate(gastos)}
    partes = [f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 {ancho} {alto}' width='100%'>"]
    for p, g, v in grilla:
        i, j = idx_p[p], idx_g[g]
        if v >= 0:
            color = _mezcla(neutro, T.ok, (v / mejor) ** 0.6 if mejor > 0 else 0)
        else:
            color = _mezcla(neutro, T.mal, (v / peor) ** 0.6 if peor < 0 else 0)
        x, y = m_izq + i * cw, m_arr + h - (j + 1) * ch
        borde = f" stroke='{T.tinta}' stroke-width='1.2'" if meta is not None and v >= meta else ""
        partes.append(f"<rect x='{x:.1f}' y='{y:.1f}' width='{cw + .4:.1f}' height='{ch + .4:.1f}' fill='{color}'{borde}/>")
    for i in range(0, len(precios), max(1, len(precios) // 6)):
        partes.append(_texto(T, m_izq + (i + 0.5) * cw, alto - m_ab + 14, miles(precios[i]), 9))
    partes.append(_texto(T, m_izq + w / 2, alto - 6, "precio promedio por flete", 10))
    for j in range(0, len(gastos), max(1, len(gastos) // 5)):
        partes.append(_texto(T, m_izq - 6, m_arr + h - (j + 0.5) * ch + 3, miles(gastos[j]), 9, ancla="end"))
    partes.append(f"<text x='12' y='{m_arr + h / 2:.1f}' font-size='10' fill='{T.tenue}' text-anchor='middle' "
                  f"font-family='{T.fuente}' transform='rotate(-90 12 {m_arr + h / 2:.1f})'>publicidad por día</text>")

    def pos(p, g):
        x0, x1 = precios[0], precios[-1]
        y0, y1 = gastos[0], gastos[-1]
        return (m_izq + (p - x0) / ((x1 - x0) or 1) * (w - cw) + cw / 2,
                m_arr + h - (g - y0) / ((y1 - y0) or 1) * (h - ch) - ch / 2)

    if actual:
        x, y = pos(*actual)
        partes.append(f"<circle cx='{x:.1f}' cy='{y:.1f}' r='6' fill='none' stroke='{T.tinta}' stroke-width='2.2'/>")
        partes.append(_texto(T, x + 9, y + 4, "hoy", 10, T.tinta, "start", 700) if x < m_izq + w - 40
                      else _texto(T, x - 9, y + 4, "hoy", 10, T.tinta, "end", 700))
    if optimo:
        x, y = pos(*optimo)
        puntas = []
        for k in range(10):
            radio = 8 if k % 2 == 0 else 3.5
            a = math.pi / 2 + k * math.pi / 5
            puntas.append(f"{x + radio * math.cos(a):.1f},{y - radio * math.sin(a):.1f}")
        partes.append(f"<polygon points='{' '.join(puntas)}' fill='{T.acento}' stroke='{T.borde_punto}' stroke-width='1'/>")
        cerca_del_borde = x > m_izq + w - 60
        partes.append(_texto(T, x - 10 if cerca_del_borde else x + 10, y - 8, "óptimo", 10, T.tinta,
                             "end" if cerca_del_borde else "start", 700))
    partes.append("</svg>")
    return "".join(partes)


def tornado(base: float, palancas: list[tuple[str, float, float]], ancho=None, alto_fila=30, T: Tema = PAPEL) -> str:
    """Cuanto cambia la ganancia si cada variable baja o sube un 20 %, de a una.

    `palancas`: [(nombre, ganancia_si_baja, ganancia_si_sube)], de mayor a menor efecto.
    """
    if not palancas:
        return ""
    ancho = ancho or T.ancho
    m_izq, m_der, m_arr = round(150 * T.texto), 16, 20
    alto = m_arr + alto_fila * len(palancas) + 8
    # Lugar a cada lado para los montos: sin esto, en el celular pisaban los
    # nombres de las palancas.
    reserva = 70 * T.texto
    w = ancho - m_izq - m_der - 2 * reserva
    extremos = [v for _, a, b in palancas for v in (a, b)] + [base]
    lo, hi = min(extremos), max(extremos)
    if hi == lo:
        hi = lo + 1
    x = lambda v: m_izq + reserva + (v - lo) / (hi - lo) * w
    partes = [f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 {ancho} {alto}' width='100%'>"]
    partes.append(_texto(T, x(base), 12, f"hoy {miles(base)}", 10, T.tinta, peso=700))
    for i, (nombre, baja, sube) in enumerate(palancas):
        y = m_arr + i * alto_fila
        for valor, etq in ((baja, "−20%"), (sube, "+20%")):
            x0, x1 = sorted((x(base), x(valor)))
            color = T.ok if valor >= base else T.mal
            partes.append(f"<rect x='{x0:.1f}' y='{y + 5}' width='{max(x1 - x0, 1):.1f}' height='{alto_fila - 10}' "
                          f"rx='2' fill='{color}' opacity='.85'/>")
            lado = "end" if valor < base else "start"
            dx = -4 if valor < base else 4
            partes.append(_texto(T, x(valor) + dx, y + alto_fila / 2 + 3, f"{etq} {miles(valor)}", 8.5, T.tenue, lado))
        partes.append(_texto(T, m_izq - 8, y + alto_fila / 2 + 3, nombre, 11, T.tinta, "end"))
    partes.append(f"<line x1='{x(base):.1f}' x2='{x(base):.1f}' y1='{m_arr - 2}' y2='{alto - 4}' stroke='{T.tinta}' stroke-width='1.2'/>")
    partes.append("</svg>")
    return "".join(partes)


def histograma(valores: list[float], meta: float | None, ancho=None, alto=210, cajas=28, T: Tema = PAPEL) -> str:
    """Cuantos de los meses simulados cayeron en cada rango de ganancia."""
    if not valores:
        return ""
    ancho = ancho or T.ancho
    m_izq, m_der, m_arr, m_ab = 20, 16, 16, round(40 * T.texto)
    w, h = ancho - m_izq - m_der, alto - m_arr - m_ab
    lo, hi = min(valores), max(valores)
    if hi == lo:
        hi = lo + 1
    paso = (hi - lo) / cajas
    cuentas = [0] * cajas
    for v in valores:
        cuentas[min(cajas - 1, int((v - lo) / paso))] += 1
    tope = max(cuentas)
    x = lambda v: m_izq + (v - lo) / (hi - lo) * w
    partes = [f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 {ancho} {alto}' width='100%'>"]
    for k, n in enumerate(cuentas):
        desde = lo + k * paso
        color = T.ok if desde + paso / 2 >= 0 else T.mal
        bh = n / tope * h
        partes.append(f"<rect x='{x(desde) + .6:.1f}' y='{m_arr + h - bh:.1f}' width='{max(w / cajas - 1.2, .5):.1f}' "
                      f"height='{bh:.1f}' fill='{color}' opacity='.8'/>")
    if lo < 0 < hi:
        partes.append(f"<line x1='{x(0):.1f}' x2='{x(0):.1f}' y1='{m_arr}' y2='{m_arr + h}' stroke='{T.tinta}' stroke-width='1.2'/>")
        partes.append(_texto(T, x(0), m_arr - 4, "salir hecho", 9, T.tinta, peso=600))
    if meta is not None and lo < meta < hi:
        partes.append(f"<line x1='{x(meta):.1f}' x2='{x(meta):.1f}' y1='{m_arr}' y2='{m_arr + h}' stroke='{T.ok}' "
                      f"stroke-dasharray='5 4' stroke-width='1.4'/>")
        partes.append(_texto(T, x(meta), m_arr - 4, "meta", 9, T.ok, peso=600))
    for v in (lo, (lo + hi) / 2, hi):
        partes.append(_texto(T, x(v), alto - m_ab + 16, miles(v), 9))
    partes.append(_texto(T, m_izq + w / 2, alto - 3, "ganancia del mes en cada mes simulado", 9))
    partes.append("</svg>")
    return "".join(partes)
