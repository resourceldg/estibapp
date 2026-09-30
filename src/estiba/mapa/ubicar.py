"""De una direccion escrita en un chat a un punto en el mapa, sin salir de la maquina.

Lo que escribe la gente, tal como sale del extractor:

- "Catamarca 1185", "Entre Rios 1839 3°G": calle y altura. Se busca la
  cuadra que tiene esa altura y se ubica el punto adentro de la cuadra.
- "chile y alvarado", "Catamarca y 11 de setiembre": una esquina. El INDEC
  tiene todas.
- "constitución", "Rawson": solo la calle. Se usa el medio de la calle y se
  dice que es aproximado: una avenida larga puede errarle por kilometros.

Si la calle existe en varias ciudades y el texto no dice cual, gana la mas
cercana a la base. Desde Santa Clara del Mar eso es la Costa Atlantica, que
es donde se trabaja casi siempre.

Nada de esto adivina: si no encuentra la calle, devuelve None y el precio
usa el promedio de km, diciendolo.
"""

from __future__ import annotations

import difflib
import json
import math
import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass

from estiba.mapa.nombres import palabras, para_mostrar, plano

#: Precision, de mejor a peor. Cambia cuanto se le cree al numero de km.
ALTURA, ESQUINA, CALLE = "altura", "esquina", "calle"


@dataclass(frozen=True)
class Ubicacion:
    lat: float
    lon: float
    precision: str
    #: Como lo encontro, para mostrarlo: "CATAMARCA 1185, Mar del Plata".
    como: str

    @property
    def punto(self) -> tuple[float, float]:
        return self.lat, self.lon


def km_entre(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Distancia en linea recta (haversine)."""
    (la1, lo1), (la2, lo2) = a, b
    f1, f2 = math.radians(la1), math.radians(la2)
    df, dl = f2 - f1, math.radians(lo2 - lo1)
    h = math.sin(df / 2) ** 2 + math.cos(f1) * math.cos(f2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


# --------------------------------------------------------------------------
# Leer la direccion
# --------------------------------------------------------------------------

_PISO = re.compile(r"\s+\d{1,2}\s*[°º]\s*[a-z]?\b.*$|\s*\b(?:piso|dpto|depto|dto|departamento|timbre)\b.*$", re.I)
_RUIDO = re.compile(r"\b(?:mas\s*o\s*menos|masomenos|aprox\w*|al\s+lado|cerca\s+de)\b", re.I)
_ENTRE = re.compile(r"\s+entre\s+(.+?)\s+y\s+(.+)$", re.I)
_ESQUINA = re.compile(r"\s+(?:y|e|esq\.?|esquina)\s+", re.I)
_ALTURA = re.compile(r"^(.*?[^\d\s].*?)\s+(?:n[°º]?\s*)?(\d{1,5})$", re.I)


@dataclass(frozen=True)
class Direccion:
    calle: str
    altura: int | None = None
    otra: str | None = None  # la de la esquina
    ciudad: str | None = None


def leer(texto: str, ciudades: dict[str, str] | None = None) -> Direccion | None:
    """"Entre Rios 1839 3°G" -> Direccion("Entre Rios", 1839)."""
    t, ciudad = _separar_ciudad(" ".join(texto.split()), ciudades or {})
    t = t.split(",")[0].strip()
    t = _PISO.sub("", t)
    t = _RUIDO.sub("", t).strip(" .-")
    if not t:
        return None
    if m := _ENTRE.search(t):
        principal = t[: m.start()].strip()
        if a := _ALTURA.match(principal):
            return Direccion(a.group(1).strip(), int(a.group(2)), ciudad=ciudad)
        return Direccion(principal, otra=m.group(1).strip(), ciudad=ciudad)
    partes = _ESQUINA.split(t, maxsplit=1)
    if len(partes) == 2 and all(palabras(p) for p in partes):
        return Direccion(partes[0].strip(), otra=partes[1].strip(), ciudad=ciudad)
    if (a := _ALTURA.match(t)) and palabras(a.group(1)):
        return Direccion(a.group(1).strip(), int(a.group(2)), ciudad=ciudad)
    return Direccion(t, ciudad=ciudad) if palabras(t) else None


def _separar_ciudad(texto: str, ciudades: dict[str, str]) -> tuple[str, str | None]:
    """"Rivadavia 1234, Necochea" -> ("Rivadavia 1234", "Necochea").

    Solo cuenta como ciudad si viene despues de una coma o de "en", o si
    tiene varias palabras ("Mar del Plata"). Alberti, Colon o Rivadavia son
    ciudades de la provincia y tambien calles de Mar del Plata: "Alberti
    2359" es una calle, no una ciudad.
    """
    for nombre_plano, nombre in sorted(ciudades.items(), key=lambda x: -len(x[0])):
        n = len(nombre_plano.split())
        palabras_texto = texto.split()
        if len(palabras_texto) <= n:
            continue
        cola = plano(" ".join(palabras_texto[-n:]))
        if cola != nombre_plano:
            continue
        antes = " ".join(palabras_texto[:-n])
        separada = antes.endswith(",") or plano(antes).endswith(" en") or n >= 2
        if separada:
            antes = re.sub(r"(?:\s*,|\s+en)\s*$", "", antes, flags=re.I)
            return antes.strip(), nombre
    return texto, None


# --------------------------------------------------------------------------
# El mapa
# --------------------------------------------------------------------------


#: Lo que se considera "la zona": desde Santa Clara llega a Mar del Plata,
#: Miramar y Balcarce. Una calle de la zona le gana a una con el nombre mas
#: parecido que queda a 300 km: "Funes" es la Dean Funes de Mar del Plata,
#: no la "Funes A" de Bolivar.
ZONA_KM = 80.0

#: Que tan parecido es el nombre. Menos es mejor.
EXACTO, CONTIENE, CONTENIDO, TIPEO = 0, 1, 2, 3


class Mapa:
    """Las calles de la provincia, listas para buscar.

    Carga en memoria solo los nombres (para comparar rapido); cuadras y
    esquinas se leen de la base cuando hacen falta.

    Para elegir entre calles con el mismo nombre en distintos lugares manda
    la geografia, en este orden: la ciudad que dice la direccion (si la dice
    y ahi no esta la calle, no se inventa otra), la ciudad del telefono, la
    zona alrededor de la base, lo mas parecido de nombre y lo mas cercano.
    """

    def __init__(self, conn: sqlite3.Connection, base: tuple[float, float] | None = None) -> None:
        self.conn = conn
        self.base = base
        self._por_palabra: dict[str, set[str]] = defaultdict(set)
        self.claves: set[str] = set()
        # Los nombres de las esquinas tambien: hay calles que el INDEC tiene
        # en un cruce y no en una cuadra con altura.
        for (k,) in conn.execute("SELECT clave FROM cuadras UNION SELECT a FROM esquinas UNION SELECT b FROM esquinas"):
            if not k:
                continue
            self.claves.add(k)
            for p in k.split():
                self._por_palabra[p].add(k)
        self._vocabulario = sorted(self._por_palabra)
        self.ciudades: dict[str, str] = {}
        for (loc,) in conn.execute("SELECT DISTINCT localidad FROM cuadras WHERE localidad IS NOT NULL"):
            # "Necochea - Quequén" se puede nombrar por cualquiera de las dos.
            # El INDEC a veces trae el guion roto ("Necochea ? Quequén").
            for parte in [loc, *re.split(r"\s+[-–?]\s+", loc)]:
                if len(plano(parte)) >= 4:
                    self.ciudades.setdefault(plano(parte), loc)

    # -- nombres ----------------------------------------------------------

    def calles(self, nombre: str) -> dict[str, int]:
        """Las calles que pueden ser `nombre`, cada una con que tan parecida es."""
        pedidas = list(palabras(nombre))
        if not pedidas:
            return {}
        tipeo = False
        for i, p in enumerate(pedidas):
            if p not in self._por_palabra and len(p) >= 5:
                # "bordabhere" -> "bordabehere". Solo palabras largas: en una
                # corta, una letra cambia la calle entera.
                cerca = difflib.get_close_matches(p, self._vocabulario, n=1, cutoff=0.8)
                if cerca:
                    pedidas[i], tipeo = cerca[0], True
        k = " ".join(sorted(pedidas))
        conjunto = set(pedidas)
        salida: dict[str, int] = {}
        if k in self.claves:
            salida[k] = EXACTO
        # Todas las palabras que escribio estan en la calle: "funes" -> "dean funes".
        comunes = set.intersection(*(self._por_palabra.get(p, set()) for p in conjunto))
        for c in comunes:
            salida.setdefault(c, CONTIENE)
        # La calle tiene menos palabras que lo escrito: "sebastian gaboto" -> "gaboto".
        for p in conjunto:
            for c in self._por_palabra.get(p, set()):
                if set(c.split()) <= conjunto:
                    salida.setdefault(c, CONTENIDO)
        if tipeo:
            salida = {c: max(n, TIPEO) for c, n in salida.items()}
        return salida

    # -- buscar -----------------------------------------------------------

    def ubicar(self, texto: str, ciudad: str | None = None) -> Ubicacion | None:
        """`ciudad` es una pista (la del telefono): manda la que diga el texto."""
        d = leer(texto, self.ciudades)
        if d is None:
            return None
        pista = None
        if d.ciudad is None and ciudad:
            pista = self.ciudades.get(plano(ciudad))
        calles = self.calles(d.calle)
        if not calles:
            return None
        if d.otra:
            otras = self.calles(d.otra)
            if u := self._esquina(calles, otras, d.ciudad, pista):
                return u
        calle = self._calle(calles, d.ciudad, pista)
        if d.altura:
            u = self._altura(calles, d.altura, d.ciudad, pista)
            # Si la unica con esa altura queda lejos y la calle esta en la
            # zona sin numeracion (pasa en Santa Clara), gana la de la zona:
            # "Sebastian Gaboto 423" es la de al lado, no la de Ezeiza.
            if u is not None and not (calle and self._en_zona(calle) and not self._en_zona(u)):
                return u
        return calle

    def _en_zona(self, u: Ubicacion) -> bool:
        return self.base is None or km_entre(self.base, u.punto) <= ZONA_KM

    def _mejor(self, filas: list, parecido, punto, ciudad: str | None, pista: str | None):
        """La fila que gana, o None si la ciudad que se nombro no la tiene."""
        if ciudad:
            filas = [f for f in filas if f["localidad"] == ciudad]
            if not filas:
                return None

        def orden(f):
            km = km_entre(self.base, punto(f)) if self.base else 0.0
            return (bool(pista) and f["localidad"] != pista, km > ZONA_KM, parecido(f), km)

        return min(filas, key=orden, default=None)

    def _cuadras(self, calles: dict[str, int], extra: str = "", args: tuple = ()) -> list:
        marcas = ",".join("?" * len(calles))
        return self.conn.execute(f"SELECT * FROM cuadras WHERE clave IN ({marcas}) {extra}",
                                 (*calles, *args)).fetchall()

    def _altura(self, calles, altura: int, ciudad, pista) -> Ubicacion | None:
        filas = self._cuadras(calles, "AND desde <= ? AND hasta >= ? AND hasta > 0", (altura, altura))
        f = self._mejor(filas, lambda f: calles[f["clave"]], _inicio, ciudad, pista)
        if f is None:
            return None
        puntos = json.loads(f["geometria"])
        fraccion = (altura - f["desde"]) / (f["hasta"] - f["desde"]) if f["hasta"] > f["desde"] else 0.5
        lon, lat = _a_lo_largo(puntos, fraccion)
        return Ubicacion(lat, lon, ALTURA, f"{para_mostrar(f['nombre'])} {altura}, {f['localidad']}")

    def _esquina(self, calles, otras, ciudad, pista) -> Ubicacion | None:
        """Una sola consulta con todas las candidatas: hay cientos de "Funes"
        en la provincia, y la de Mar del Plata no tiene por que estar entre
        las primeras de la lista."""
        a, b = list(calles)[:400], list(otras)[:400]
        if not a or not b:
            return None
        ma, mb = ",".join("?" * len(a)), ",".join("?" * len(b))
        filas = self.conn.execute(
            "SELECT e.*, (SELECT localidad FROM cuadras c WHERE c.clave IN (e.a, e.b) "
            "AND c.departamento = e.departamento LIMIT 1) AS localidad FROM esquinas e "
            f"WHERE (a IN ({ma}) AND b IN ({mb})) OR (a IN ({mb}) AND b IN ({ma}))",
            (*a, *b, *b, *a)).fetchall()
        filas = [f for f in filas if f["a"] != f["b"]]

        def parecido(f):
            return min(calles.get(f["a"], 9) + otras.get(f["b"], 9), calles.get(f["b"], 9) + otras.get(f["a"], 9))

        f = self._mejor(filas, parecido, lambda f: (f["lat"], f["lon"]), ciudad, pista)
        if f is None:
            return None
        return Ubicacion(f["lat"], f["lon"], ESQUINA,
                         f"{f['a'].upper()} y {f['b'].upper()}, {f['localidad'] or ''}".rstrip(", "))

    def _calle(self, calles, ciudad, pista) -> Ubicacion | None:
        filas = self._cuadras(calles)
        elegida = self._mejor(filas, lambda f: calles[f["clave"]], _inicio, ciudad, pista)
        if elegida is None:
            return None
        # El medio de esa calle en esa ciudad, no el de todas las que se
        # llaman igual (que caeria en el medio del campo).
        mismas = [f for f in filas if f["clave"] == elegida["clave"] and f["localidad"] == elegida["localidad"]]
        puntos = [p for f in mismas for p in json.loads(f["geometria"])]
        lon = sum(p[0] for p in puntos) / len(puntos)
        lat = sum(p[1] for p in puntos) / len(puntos)
        return Ubicacion(lat, lon, CALLE, f"{para_mostrar(elegida['nombre'])}, {elegida['localidad']} (sin altura)")


def _inicio(fila) -> tuple[float, float]:
    lon, lat = json.loads(fila["geometria"])[0]
    return lat, lon


def _a_lo_largo(puntos: list[list[float]], fraccion: float) -> tuple[float, float]:
    """El punto a `fraccion` del largo de la linea (0 = el primero, 1 = el ultimo)."""
    fraccion = min(1.0, max(0.0, fraccion))
    tramos = [km_entre((a[1], a[0]), (b[1], b[0])) for a, b in zip(puntos, puntos[1:])]
    total = sum(tramos)
    if total == 0:
        return puntos[0][0], puntos[0][1]
    falta = fraccion * total
    for (a, b), largo in zip(zip(puntos, puntos[1:]), tramos):
        if falta <= largo and largo > 0:
            t = falta / largo
            return a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t
        falta -= largo
    return puntos[-1][0], puntos[-1][1]
