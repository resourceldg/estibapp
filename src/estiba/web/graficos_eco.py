"""Graficos del motor economico, como SVG escrito a mano.

Mismo espiritu que `informe/graficos.py`, pero los colores salen de clases
CSS (`.g-*` en estiba.css) y no de hex sueltos: asi un grafico y la pantalla
nunca se contradicen.

Reglas (skill de dataviz):

- Paleta categorica validada contra el fondo oscuro de las tarjetas
  (azul, naranja, aqua, amarillo, magenta, en ese orden, nunca reciclada).
- Los colores de estado (bien / mal / aviso) solo cuando significan eso,
  y siempre con su etiqueta.
- Marcas finas: barras de hasta 22 px con la punta redondeada, lineas de 2 px,
  puntos de radio 4 con anillo del color del fondo, grilla de 1 px lisa.
- El texto nunca lleva el color de la serie. Etiquetas selectivas: la punta,
  el extremo o la serie de la que se habla, no un numero en cada punto.
- Un solo eje y. Cada grafico tiene su tabla plegada (la arma la plantilla).
- Hover: cada marca lleva un <title> y una zona de toque mas grande que la marca.
"""

from __future__ import annotations

import math
from datetime import timedelta
from html import escape

ANCHO = 480
FUENTE = 12.5
IZQ, DER, ARR, ABA = 58, 16, 18, 30


def _t(x, y, texto, clase="g-t2", ancla="start", tam=FUENTE, peso=400) -> str:
    return (f"<text x='{x:.1f}' y='{y:.1f}' class='{clase}' font-size='{tam}' text-anchor='{ancla}' "
            f"font-weight='{peso}'>{escape(str(texto))}</text>")


def _svg(alto: int, cuerpo: list[str], titulo: str) -> str:
    return (f"<svg class='grafico' viewBox='0 0 {ANCHO} {alto}' width='100%' role='img' "
            f"aria-label='{escape(titulo)}' xmlns='http://www.w3.org/2000/svg'>{''.join(cuerpo)}</svg>")


def _nice(lo: float, hi: float, n: int = 4) -> list[float]:
    """Ticks redondos (0 / 10.000 / 20.000), incluyendo el cero si esta en rango."""
    if hi == lo:
        hi = lo + 1
    # Un minimo negativo chico (menos del 15 % del rango) no merece un tick
    # entero: se usa un paso mas fino para no desperdiciar medio grafico.
    n_ = n + 2 if lo < 0 and -lo < (hi - lo) * 0.15 else n
    paso_crudo = (hi - lo) / n_
    mag = 10 ** math.floor(math.log10(abs(paso_crudo)))
    paso = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= paso_crudo)
    inicio = math.floor(lo / paso) * paso
    ticks, v = [], inicio
    while v <= hi + paso * 0.001:
        ticks.append(round(v, 6))
        v += paso
    # El ultimo tick siempre cubre el maximo: nada queda fuera del grafico.
    while ticks[-1] < hi:
        ticks.append(round(ticks[-1] + paso, 6))
    return ticks


def _barra_h(x0: float, x1: float, y: float, alto: float, clase: str, titulo: str) -> str:
    """Barra horizontal con la punta redondeada y la base recta."""
    izq, der = min(x0, x1), max(x0, x1)
    ancho = max(der - izq, 0.8)
    r = min(4, ancho / 2, alto / 2)
    if x1 >= x0:   # crece a la derecha: redondeada a la derecha
        d = (f"M{izq:.1f},{y:.1f}h{ancho - r:.1f}a{r},{r} 0 0 1 {r},{r}v{alto - 2 * r:.1f}"
             f"a{r},{r} 0 0 1 -{r},{r}h-{ancho - r:.1f}z")
    else:          # crece a la izquierda (negativo)
        d = (f"M{der:.1f},{y:.1f}h-{ancho - r:.1f}a{r},{r} 0 0 0 -{r},{r}v{alto - 2 * r:.1f}"
             f"a{r},{r} 0 0 0 {r},{r}h{ancho - r:.1f}z")
    return f"<path d='{d}' class='{clase}'><title>{escape(titulo)}</title></path>"


# --------------------------------------------------------------------------
# Lineas
# --------------------------------------------------------------------------


def linea(xs: list[float], series: list[tuple[str, list[float | None], str]], fy, fx=str, *,
          refs: list[tuple[float, str]] = (), marcas: list[tuple[float, float, str]] = (),
          alto: int = 230, titulo: str = "") -> str:
    """Una o mas series sobre el mismo eje (misma unidad). `refs`: lineas horizontales
    de referencia con su etiqueta; `marcas`: puntos destacados (x, y, texto)."""
    if not xs or not series:
        return ""
    valores = [v for _, ys, _ in series for v in ys if v is not None] + [r for r, _ in refs]
    valores += [y for _, y, _ in marcas]
    lo, hi = min(0.0, *valores), max(0.0, *valores)
    ticks = _nice(lo, hi)
    lo, hi = ticks[0], ticks[-1]
    leyenda = 18 if len(series) > 1 else 0
    arr = ARR + leyenda
    w, h = ANCHO - IZQ - DER, alto - arr - ABA
    X = lambda x: IZQ + (x - xs[0]) / ((xs[-1] - xs[0]) or 1) * w
    Y = lambda v: arr + h - (v - lo) / ((hi - lo) or 1) * h
    c = []
    if leyenda:
        x = IZQ
        for nombre, _, clase in series:
            c.append(f"<line x1='{x}' x2='{x + 14}' y1='11' y2='11' class='{clase} g-linea'/>")
            c.append(_t(x + 19, 15, nombre, "g-t"))
            x += 30 + len(nombre) * 6.6
    for v in ticks:
        c.append(f"<line x1='{IZQ}' x2='{ANCHO - DER}' y1='{Y(v):.1f}' y2='{Y(v):.1f}' class='g-grilla'/>")
        c.append(_t(IZQ - 6, Y(v) + 4, fy(v), ancla="end", tam=11))
    paso_x = max(1, len(xs) // 6)
    for i in range(0, len(xs), paso_x):
        c.append(_t(X(xs[i]), alto - 10, fx(xs[i]), ancla="middle", tam=11))
    if lo < 0 < hi:
        c.append(f"<line x1='{IZQ}' x2='{ANCHO - DER}' y1='{Y(0):.1f}' y2='{Y(0):.1f}' class='g-cero'/>")
    # Los rotulos de referencia van del lado contrario a las marcas, para no pisarse.
    marca_derecha = any(X(x) > IZQ + w * 0.5 for x, _, _ in marcas)
    for valor, etiqueta in refs:
        c.append(f"<line x1='{IZQ}' x2='{ANCHO - DER}' y1='{Y(valor):.1f}' y2='{Y(valor):.1f}' class='g-ref'/>")
        if marca_derecha:
            c.append(_t(IZQ + 4, Y(valor) - 5, etiqueta, "g-t", ancla="start", tam=11))
        else:
            c.append(_t(ANCHO - DER, Y(valor) - 5, etiqueta, "g-t", ancla="end", tam=11))
    for nombre, ys, clase in series:
        puntos = [(X(x), Y(y)) for x, y in zip(xs, ys) if y is not None]
        if len(puntos) > 1:
            d = "M" + "L".join(f"{px:.1f},{py:.1f}" for px, py in puntos)
            c.append(f"<path d='{d}' class='{clase} g-linea' fill='none'/>")
        if puntos:
            px, py = puntos[-1]
            c.append(f"<circle cx='{px:.1f}' cy='{py:.1f}' r='4' class='{clase} g-punto'/>")
    for x, y, texto in marcas:
        c.append(f"<circle cx='{X(x):.1f}' cy='{Y(y):.1f}' r='5' class='g-marca g-punto'/>")
        ancla = "end" if X(x) > ANCHO * 0.65 else "start"
        dx = -9 if ancla == "end" else 9
        c.append(_t(X(x) + dx, Y(y) - 9, texto, "g-t", ancla=ancla, tam=12, peso=600))
    # Hover: una columna invisible por x con todos los valores de ese punto.
    ancho_col = w / max(1, len(xs) - 1)
    for i, x in enumerate(xs):
        linea_txt = f"{fx(x)}: " + " · ".join(f"{n} {fy(ys[i])}" for n, ys, _ in series if ys[i] is not None)
        c.append(f"<rect x='{X(x) - ancho_col / 2:.1f}' y='{arr}' width='{ancho_col:.1f}' height='{h}' "
                 f"class='g-toque'><title>{escape(linea_txt)}</title></rect>")
    return _svg(alto, c, titulo)


# --------------------------------------------------------------------------
# Barras horizontales (con negativos) e intervalos
# --------------------------------------------------------------------------


def barras(filas: list[tuple[str, float, str]], fv, *, titulo: str = "", izq: int = 170,
           alto_fila: int = 30) -> str:
    """Una barra por fila desde el cero. `filas`: (etiqueta, valor, clase).

    El monto va en una columna fija a la derecha: con valores negativos, un
    rotulo en la punta caeria encima del nombre."""
    if not filas:
        return ""
    vals = [v for _, v, _ in filas]
    lo, hi = min(0.0, *vals), max(0.0, *vals)
    if hi == lo:
        hi = lo + 1
    der = 78
    w = ANCHO - izq - der - 8
    X = lambda v: izq + (v - lo) / (hi - lo) * w
    alto = ARR + alto_fila * len(filas) + 6
    c = [f"<line x1='{X(0):.1f}' x2='{X(0):.1f}' y1='{ARR - 6}' y2='{alto - 4}' class='g-cero'/>"]
    for i, (etq, v, clase) in enumerate(filas):
        y = ARR + i * alto_fila
        c.append(_t(izq - 8, y + alto_fila / 2 + 4, etq, "g-t", ancla="end"))
        grosor = min(18, alto_fila - 10)
        y0 = y + (alto_fila - grosor) / 2
        c.append(_barra_h(X(0), X(v), y0, grosor, clase, f"{etq}: {fv(v)}"))
        c.append(_t(ANCHO - 4, y + alto_fila / 2 + 4, fv(v), "g-t2", ancla="end", tam=11.5))
    return _svg(alto, c, titulo)


def intervalos(filas: list[tuple[str, float | None, float | None, float | None, bool, str]], fv, *,
               ref: tuple[float, str] | None = None, titulo: str = "", izq: int = 150) -> str:
    """Punto y bigote por fila, mismo eje. Lo debil va apagado: se ve que no alcanza.
    `filas`: (etiqueta, valor, lo, hi, firme, nota)."""
    filas = [f for f in filas if f[1] is not None]
    if not filas:
        return ""
    vals = [x for f in filas for x in (f[1], f[2], f[3]) if x is not None] + ([ref[0]] if ref else [])
    lo, hi = min(vals), max(vals)
    ticks = _nice(min(lo, 0) if lo > 0 and lo < hi * 0.3 else lo, hi)
    lo, hi = ticks[0], ticks[-1]
    der, alto_fila = 20, 30
    w = ANCHO - izq - der
    X = lambda v: izq + (v - lo) / ((hi - lo) or 1) * w
    alto = ARR + alto_fila * len(filas) + 24
    c = []
    for v in ticks:
        c.append(f"<line x1='{X(v):.1f}' x2='{X(v):.1f}' y1='{ARR - 6}' y2='{alto - 22}' class='g-grilla'/>")
        c.append(_t(X(v), alto - 6, fv(v), ancla="middle", tam=11))
    if ref:
        c.append(f"<line x1='{X(ref[0]):.1f}' x2='{X(ref[0]):.1f}' y1='{ARR - 10}' y2='{alto - 22}' class='g-ref'/>")
        c.append(_t(X(ref[0]) + 4, ARR - 8, ref[1], "g-t", tam=11))
    for i, (etq, v, a, b, firme, nota) in enumerate(filas):
        y = ARR + i * alto_fila + alto_fila / 2
        clase = "g-s1" if firme else "g-apagado"
        c.append(_t(izq - 8, y + 4, etq, "g-t", ancla="end"))
        if a is not None and b is not None:
            c.append(f"<line x1='{X(a):.1f}' x2='{X(b):.1f}' y1='{y:.1f}' y2='{y:.1f}' class='{clase} g-bigote'/>")
        detalle = f"{etq}: {fv(v)}" + (f" (entre {fv(a)} y {fv(b)})" if a is not None and b is not None else "")
        c.append(f"<g><circle cx='{X(v):.1f}' cy='{y:.1f}' r='12' class='g-toque'/>"
                 f"<circle cx='{X(v):.1f}' cy='{y:.1f}' r='4.5' class='{clase} g-punto'/>"
                 f"<title>{escape(detalle + (' · ' + nota if nota else ''))}</title></g>")
    return _svg(alto, c, titulo)


# --------------------------------------------------------------------------
# Parte de un todo, escalera y rango
# --------------------------------------------------------------------------

CATEGORICAS = ("g-s1", "g-s2", "g-s3", "g-s4", "g-s5")


def apilada(partes: list[tuple[str, float]], fv, *, titulo: str = "") -> str:
    """Una barra partida en hasta 5 partes, con hueco de 2 px y leyenda debajo."""
    partes = [(n, v) for n, v in partes if v > 0][:5]
    total = sum(v for _, v in partes)
    if not total:
        return ""
    w = ANCHO - 2 * 8
    c, x = [], 8.0
    for i, (n, v) in enumerate(partes):
        ancho = v / total * w
        c.append(f"<rect x='{x:.1f}' y='8' width='{max(ancho - 2, 0.5):.1f}' height='22' rx='3' "
                 f"class='{CATEGORICAS[i]}'><title>{escape(f'{n}: {fv(v)} ({v / total:.0%})')}</title></rect>")
        x += ancho
    fila = 0
    for i, (n, v) in enumerate(partes):
        y = 52 + fila * 20
        c.append(f"<rect x='8' y='{y - 9}' width='10' height='10' rx='2' class='{CATEGORICAS[i]}'/>")
        c.append(_t(24, y, n, "g-t"))
        c.append(_t(ANCHO - 8, y, f"{fv(v)} · {v / total:.0%}", "g-t2", ancla="end"))
        fila += 1
    return _svg(52 + fila * 20, c, titulo)


def escalera(escalones: list[tuple[str, float]], disponible: float, fv, *, titulo: str = "") -> str:
    """Los escalones del mes uno al lado del otro; lo cubierto, lleno. Un escalon
    sin monto no ocupa lugar, pero conserva su numero (el de la leyenda).
    La marca dice hasta donde llego lo disponible."""
    con = [(f"{i}. {n}", v, i) for i, (n, v) in enumerate(escalones, start=1) if v > 0]
    total = sum(v for _, v, _ in con)
    if not total:
        return ""
    fin = max(total, disponible)
    w = ANCHO - 2 * 8
    X = lambda v: 8 + v / fin * w
    xd = X(max(0.0, disponible))
    c, acum = [], 0.0
    for n, v, numero in con:
        x0, x1 = X(acum), X(acum + v)
        c.append(f"<rect x='{x0:.1f}' y='26' width='{max(x1 - x0 - 2, 0.5):.1f}' height='24' rx='3' class='g-pista'>"
                 f"<title>{escape(f'{n}: {fv(v)}')}</title></rect>")
        lleno = max(0.0, min(v, disponible - acum))
        if lleno > 0:
            c.append(f"<rect x='{x0:.1f}' y='26' width='{max(X(acum + lleno) - x0 - 2, 0.5):.1f}' height='24' "
                     f"rx='3' class='g-s1'><title>{escape(f'{n}: cubierto {fv(lleno)} de {fv(v)}')}</title></rect>")
        # El numero no se dibuja encima de la marca de "llego a": no se lee.
        if x1 - x0 > 22 and abs((x0 + x1) / 2 - xd) > 10:
            c.append(_t((x0 + x1) / 2 - 1, 42, str(numero), "g-sobre", ancla="middle", tam=11.5, peso=600))
        acum += v
    c.append(f"<line x1='{xd:.1f}' x2='{xd:.1f}' y1='18' y2='58' class='g-marca-linea'/>")
    ancla = "end" if xd > ANCHO * 0.7 else "start"
    c.append(_t(xd + (-5 if ancla == "end" else 5), 13, f"Llegó a {fv(disponible)}", "g-t", ancla=ancla,
                tam=12, peso=600))
    return _svg(66, c, titulo)


def rango(lo: float, hi: float, medio: float, refs: list[tuple[float, str]], fv, *, titulo: str = "",
          etiqueta: str = "") -> str:
    """Un intervalo (lo..hi con su punto medio) contra lineas de referencia en el mismo eje."""
    vals = [lo, hi, medio] + [r for r, _ in refs]
    ticks = _nice(0, max(vals))
    top = ticks[-1]
    w = ANCHO - 2 * 12
    X = lambda v: 12 + v / (top or 1) * w
    c = []
    for v in ticks:
        c.append(f"<line x1='{X(v):.1f}' x2='{X(v):.1f}' y1='32' y2='72' class='g-grilla'/>")
        c.append(_t(X(v), 88, fv(v), ancla="middle", tam=11))
    c.append(f"<rect x='{X(lo):.1f}' y='38' width='{max(X(hi) - X(lo), 2):.1f}' height='16' rx='4' "
             f"class='g-s2 g-banda'><title>{escape(f'{etiqueta}: entre {fv(lo)} y {fv(hi)}')}</title></rect>")
    c.append(f"<circle cx='{X(medio):.1f}' cy='46' r='4.5' class='g-s2 g-punto'/>")
    # Las referencias se escalonan: dos rotulos cercanos no se pisan.
    for i, (v, texto) in enumerate(sorted(refs)):
        y_txt = 12 + (i % 2) * 14
        c.append(f"<line x1='{X(v):.1f}' x2='{X(v):.1f}' y1='{y_txt + 3}' y2='72' class='g-ref'/>")
        ancla = "end" if X(v) > ANCHO * 0.6 else "start"
        c.append(_t(X(v) + (-4 if ancla == "end" else 4), y_txt, texto, "g-t", ancla=ancla, tam=11.5))
    return _svg(94, c, titulo)


def pesos_cortos(v: float) -> str:
    """Para ejes: $12 mil, $1,2 M. Sin decimales falsos."""
    s = "−" if v < 0 else ""
    v = abs(v)
    if v >= 1_000_000:
        return f"{s}${v / 1_000_000:.1f} M".replace(".", ",").replace(",0 M", " M")
    if v >= 1000:
        return f"{s}${v / 1000:.0f} mil"
    return f"{s}${v:.0f}"


def cascada(pasos: list[tuple[str, float]], fv, *, titulo: str = "", izq: int = 170) -> str:
    """De lo cobrado a lo que queda, en filas: el primero y el ultimo son totales,
    los del medio restan. Horizontal para que los nombres entren enteros."""
    if not pasos:
        return ""
    tramos, acum = [], 0.0
    for i, (etq, v) in enumerate(pasos):
        if i == 0 or i == len(pasos) - 1:
            tramos.append((etq, 0.0, v, v, True))
            acum = v
        else:
            tramos.append((etq, acum, acum + v, v, False))
            acum += v
    vals = [x for _, a, b, _, _ in tramos for x in (a, b)]
    lo, hi = min(0.0, *vals), max(0.0, *vals)
    der, alto_fila = 74, 26
    w = ANCHO - izq - der
    X = lambda v: izq + (v - lo) / ((hi - lo) or 1) * w
    alto = 10 + alto_fila * len(tramos) + 6
    c = [f"<line x1='{X(0):.1f}' x2='{X(0):.1f}' y1='4' y2='{alto - 4}' class='g-cero'/>"]
    for i, (etq, a, b, v, total) in enumerate(tramos):
        y = 10 + i * alto_fila
        clase = ("g-s1" if i == 0 else ("g-s3" if v >= 0 else "g-s2")) if total else "g-contexto"
        c.append(_t(izq - 8, y + 16, etq, "g-t" if total else "g-t2", ancla="end", peso=600 if total else 400))
        c.append(f"<rect x='{min(X(a), X(b)):.1f}' y='{y + 4}' width='{max(abs(X(b) - X(a)), 1):.1f}' height='16' "
                 f"rx='3' class='{clase}'><title>{escape(f'{etq}: {fv(v)}')}</title></rect>")
        c.append(_t(max(X(a), X(b)) + 6, y + 16, fv(v), "g-t" if total else "g-t2", tam=11.5,
                    peso=600 if total else 400))
    return _svg(alto, c, titulo)


def histograma(valores: list[float], fv, *, ref: tuple[float, str] | None = None, cajas: int = 24,
               titulo: str = "") -> str:
    """Cuantos meses simulados cayeron en cada rango. Lo que supera la referencia, en color."""
    if not valores:
        return ""
    # Del percentil 1 al 99: unas pocas roturas no aplastan el resto. Lo de
    # afuera cae en las cajas de los bordes.
    orden = sorted(valores)
    lo, hi = orden[int(len(orden) * 0.01)], orden[min(len(orden) - 1, int(len(orden) * 0.99))]
    if ref and ref[0] > hi and ref[0] - hi < (hi - lo) * 0.25:
        hi = ref[0] + (hi - lo) * 0.05
    if hi == lo:
        hi = lo + 1
    paso = (hi - lo) / cajas
    cuentas = [0] * cajas
    for v in valores:
        cuentas[max(0, min(cajas - 1, int((v - lo) / paso)))] += 1
    tope = max(cuentas)
    alto, arr, aba = 170, 22, 26
    w, h = ANCHO - 24, alto - arr - aba
    X = lambda v: 12 + (v - lo) / (hi - lo) * w
    c = [f"<line x1='12' x2='{ANCHO - 12}' y1='{arr + h}' y2='{arr + h}' class='g-cero'/>"]
    for k, n in enumerate(cuentas):
        desde = lo + k * paso
        bh = n / tope * h
        clase = "g-s1" if ref is None or desde + paso / 2 >= ref[0] else "g-apagado"
        c.append(f"<rect x='{X(desde) + 1:.1f}' y='{arr + h - bh:.1f}' width='{max(w / cajas - 2, .5):.1f}' "
                 f"height='{max(bh, 0.5):.1f}' rx='2' class='{clase}'>"
                 f"<title>{escape(f'{n} meses entre {fv(desde)} y {fv(desde + paso)}')}</title></rect>")
    if ref and lo < ref[0] < hi:
        xr = X(ref[0])
        c.append(f"<line x1='{xr:.1f}' x2='{xr:.1f}' y1='{arr - 4}' y2='{arr + h}' class='g-ref'/>")
        derecha = xr > ANCHO * 0.7
        c.append(_t(xr + (-4 if derecha else 4), arr - 8, ref[1], "g-t", ancla="end" if derecha else "start",
                    tam=11.5))
    for v in (lo, (lo + hi) / 2, hi):
        c.append(_t(X(v), alto - 8, fv(v), ancla="middle", tam=11))
    return _svg(alto, c, titulo)


# --------------------------------------------------------------------------
# Evolucion: la linea de tiempo con hitos (ancho completo)
# --------------------------------------------------------------------------


def evolucion(semanas: list[tuple], hitos: list[tuple], fv, *, ref_hora: float | None = None,
              exploratorio: bool = False, titulo: str = "") -> str:
    """Dos paneles con el mismo eje de tiempo, uno sobre otro (nunca dos escalas
    en el mismo eje): arriba el resultado de cada semana (barras) y el
    acumulado (linea); abajo lo que dejo cada hora trabajada contra lo que
    tiene que aportar. Los hitos cruzan los dos paneles con su numero.

    `semanas`: (lunes, fin, resultado, acumulado, por_hora | None, fletes).
    `hitos`: (dia, numero, texto).
    """
    if not semanas:
        return ""
    W, izq, der = 960, 70, 24
    arr_a, alto_a = 44, 180
    arr_b, alto_b = arr_a + alto_a + 40, 110
    alto = arr_b + alto_b + 30
    w = W - izq - der
    x0, x1 = semanas[0][0], semanas[-1][1] + timedelta(days=1)
    total_dias = max(1, (x1 - x0).days)
    X = lambda d: izq + (d - x0).days / total_dias * w
    c = []

    # ---- panel A: resultado semanal y acumulado (misma unidad, un eje)
    valores = [s[2] for s in semanas] + [s[3] for s in semanas]
    ticks = _nice(min(0.0, *valores), max(0.0, *valores))
    lo, hi = ticks[0], ticks[-1]
    Ya = lambda v: arr_a + alto_a - (v - lo) / ((hi - lo) or 1) * alto_a
    for v in ticks:
        c.append(f"<line x1='{izq}' x2='{W - der}' y1='{Ya(v):.1f}' y2='{Ya(v):.1f}' class='g-grilla'/>")
        c.append(_t(izq - 8, Ya(v) + 4, fv(v), ancla="end", tam=11))
    c.append(f"<line x1='{izq}' x2='{W - der}' y1='{Ya(0):.1f}' y2='{Ya(0):.1f}' class='g-cero'/>")
    c.append(_t(izq, 14, "Resultado real", "g-t", tam=12.5, peso=650))
    # leyenda
    lx = W - der - 300
    c.append(f"<rect x='{lx}' y='5' width='12' height='10' rx='2' class='g-s1'/>")
    c.append(_t(lx + 17, 14, "de la semana", "g-t2", tam=11.5))
    c.append(f"<line x1='{lx + 120}' x2='{lx + 136}' y1='10' y2='10' class='g-s2 g-linea'/>")
    c.append(_t(lx + 141, 14, "acumulado", "g-t2", tam=11.5))
    ancho_barra = max(3.0, w / len(semanas) * 0.5)
    for ini, fin, res, acum, ph, n in semanas:
        cx = (X(ini) + X(fin + timedelta(days=1))) / 2
        y0, y1 = Ya(0), Ya(res)
        top, h = min(y0, y1), max(abs(y1 - y0), 0.8)
        c.append(f"<rect x='{cx - ancho_barra / 2:.1f}' y='{top:.1f}' width='{ancho_barra:.1f}' height='{h:.1f}' "
                 f"rx='2' class='g-s1'/>")
    pts = [((X(s[0]) + X(s[1] + timedelta(days=1))) / 2, Ya(s[3])) for s in semanas]
    if len(pts) > 1:
        c.append("<path d='M" + "L".join(f"{x:.1f},{y:.1f}" for x, y in pts) + "' class='g-s2 g-linea' fill='none'/>")
    px, py = pts[-1]
    c.append(f"<circle cx='{px:.1f}' cy='{py:.1f}' r='4.5' class='g-s2 g-punto'/>")
    ult = semanas[-1][3]
    c.append(_t(px - 8, py - 9, f"{fv(ult)}", "g-t", ancla="end", tam=12, peso=650))

    # ---- panel B: lo que dejo cada hora
    ph_vals = [s[4] for s in semanas if s[4] is not None] + ([ref_hora] if ref_hora else [])
    c.append(_t(izq, arr_b - 12, "Lo que dejó cada hora trabajada" + (" · exploratorio" if exploratorio else ""),
                "g-t", tam=12.5, peso=650))
    if ph_vals:
        tb = _nice(min(0.0, *ph_vals), max(0.0, *ph_vals), 3)
        lb, hb = tb[0], tb[-1]
        Yb = lambda v: arr_b + alto_b - (v - lb) / ((hb - lb) or 1) * alto_b
        for v in tb:
            c.append(f"<line x1='{izq}' x2='{W - der}' y1='{Yb(v):.1f}' y2='{Yb(v):.1f}' class='g-grilla'/>")
            c.append(_t(izq - 8, Yb(v) + 4, fv(v), ancla="end", tam=11))
        if lb < 0 < hb:
            c.append(f"<line x1='{izq}' x2='{W - der}' y1='{Yb(0):.1f}' y2='{Yb(0):.1f}' class='g-cero'/>")
        if ref_hora:
            c.append(f"<line x1='{izq}' x2='{W - der}' y1='{Yb(ref_hora):.1f}' y2='{Yb(ref_hora):.1f}' "
                     "class='g-ref' stroke-dasharray='5 4'/>")
            c.append(_t(W - der, Yb(ref_hora) - 5, f"Tiene que aportar {fv(ref_hora)}", "g-t", ancla="end", tam=11))
        pb = [((X(s[0]) + X(s[1] + timedelta(days=1))) / 2, Yb(s[4]), s) for s in semanas if s[4] is not None]
        if len(pb) > 1:
            c.append("<path d='M" + "L".join(f"{x:.1f},{y:.1f}" for x, y, _ in pb)
                     + "' class='g-s1 g-linea' fill='none' stroke-opacity='.55'/>")
        for x, y, s in pb:
            # Hueco si es exploratorio: el punto existe, pero no es firme.
            estilo = "fill='var(--superficie)' stroke-width='2'" if exploratorio else ""
            c.append(f"<circle cx='{x:.1f}' cy='{y:.1f}' r='4.5' class='g-s1 g-punto' {estilo}/>")
    else:
        c.append(_t(izq, arr_b + 30, "Todavía no hay horas trabajadas.", "g-t2", tam=12))

    # ---- eje x: el lunes de cada semana
    paso = max(1, math.ceil(len(semanas) / 10))
    for i in range(0, len(semanas), paso):
        d = semanas[i][0]
        c.append(_t(X(d) + 2, alto - 10, f"{d:%d/%m}", ancla="start", tam=11))

    # ---- hitos: una linea que cruza los dos paneles, con su numero arriba
    for dia, numero, texto in hitos:
        if not (x0 <= dia <= x1):
            continue
        hx = X(dia)
        c.append(f"<line x1='{hx:.1f}' x2='{hx:.1f}' y1='26' y2='{arr_b + alto_b}' class='g-ref' "
                 "stroke-dasharray='2 4' stroke-opacity='.8'/>")
        c.append(f"<circle cx='{hx:.1f}' cy='30' r='8' class='g-marca'><title>{escape(f'{dia:%d/%m}: {texto}')}"
                 "</title></circle>")
        c.append(_t(hx, 34, str(numero), "g-hito-num", ancla="middle", tam=10.5, peso=700))

    # ---- hover: una columna por semana con todo lo de esa semana
    for ini, fin, res, acum, ph, n in semanas:
        a, b = X(ini), X(fin + timedelta(days=1))
        txt = (f"Semana del {ini:%d/%m}: {n} {'flete' if n == 1 else 'fletes'} · resultado {fv(res)} · "
               f"acumulado {fv(acum)}" + (f" · {fv(ph)} por hora" if ph is not None else ""))
        c.append(f"<rect x='{a:.1f}' y='{arr_a}' width='{b - a:.1f}' height='{arr_b + alto_b - arr_a}' "
                 f"class='g-toque'><title>{escape(txt)}</title></rect>")
    return (f"<svg class='grafico grafico-ancho' viewBox='0 0 {W} {alto}' width='100%' role='img' "
            f"aria-label='{escape(titulo)}' xmlns='http://www.w3.org/2000/svg'>{''.join(c)}</svg>")
