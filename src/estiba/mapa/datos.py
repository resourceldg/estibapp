"""El mapa de la zona, en la maquina: calles, alturas y esquinas del INDEC.

Salen de Georef, el servicio de direcciones del Estado (datos.gob.ar), que
publica las cuadras de todo el pais con su rango de numeracion y todas las
esquinas. Se bajan una vez, se queda solo la provincia, y de ahi en mas
ubicar una direccion no sale de la maquina: ni la direccion ni nada del
cliente viaja a ningun lado.

Todo el pais son casi 2 GB de descarga; la provincia queda en unos cientos
de MB, en su propio archivo para no engordar la base de Estiba.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path

import httpx

from estiba.mapa.nombres import clave

MAPA_DEFAULT = Path("data/mapa.db")
URL = "https://infra.datos.gob.ar/georef"

#: Que se baja. Codigos de provincia del INDEC: 06 es Buenos Aires.
PROVINCIAS = ("06",)

#: La provincia entera, pero la Costa Atlantica primero: "Rivadavia 1234"
#: existe en cientos de lugares. Si la direccion no dice la ciudad, gana la
#: mas cercana a la base (ver `ubicar.py`), que desde Santa Clara del Mar es
#: la costa. Si la nombra ("..., Necochea"), se busca ahi.

ESQUEMA = """
CREATE TABLE IF NOT EXISTS cuadras (
    calle_id     TEXT NOT NULL,
    nombre       TEXT NOT NULL,
    clave        TEXT NOT NULL,
    departamento TEXT NOT NULL,
    localidad    TEXT,
    desde        INTEGER NOT NULL,   -- menor altura de la cuadra, 0 si no tiene
    hasta        INTEGER NOT NULL,
    geometria    TEXT NOT NULL       -- [[lon, lat], ...] de punta a punta
);
CREATE INDEX IF NOT EXISTS cuadras_clave ON cuadras (clave);
CREATE TABLE IF NOT EXISTS esquinas (
    a            TEXT NOT NULL,      -- clave de una calle
    b            TEXT NOT NULL,      -- clave de la otra
    departamento TEXT NOT NULL,
    lat          REAL NOT NULL,
    lon          REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS esquinas_ab ON esquinas (a, b);
CREATE TABLE IF NOT EXISTS origen (
    archivo      TEXT PRIMARY KEY,
    version      TEXT,
    fecha        TEXT,
    filas        INTEGER NOT NULL
);
"""


def conectar(ruta: Path | str = MAPA_DEFAULT) -> sqlite3.Connection:
    conn = sqlite3.connect(ruta)
    conn.row_factory = sqlite3.Row
    conn.executescript(ESQUEMA)
    return conn


def _lineas(archivo: str, avance: Callable[[int], None] | None) -> Iterator[str]:
    """El archivo, linea por linea, sin guardarlo entero en disco ni en memoria."""
    leidos = 0
    with httpx.stream("GET", f"{URL}/{archivo}", timeout=httpx.Timeout(60, read=300),
                      follow_redirects=True) as r:
        r.raise_for_status()
        for linea in r.iter_lines():
            leidos += len(linea) + 1
            if avance and leidos % (50 * 1024 * 1024) < len(linea) + 1:
                avance(leidos)
            yield linea


def _quizas(linea: str, provincias: Iterable[str]) -> bool:
    """Filtro barato antes de parsear: 2 millones de lineas, muchas de afuera."""
    return any(f'"{p}"' in linea for p in provincias)


def _provincia(fila: dict) -> str:
    calle = fila.get("calle") or fila.get("calle_a") or {}
    return (calle.get("provincia") or {}).get("id", "")


def cuadra(fila: dict) -> tuple | None:
    alturas = [v for lado in ("inicio", "fin") for v in fila["altura"][lado].values() if v]
    lineas = fila["geometria"]["coordinates"]
    puntos = [p for tramo in lineas for p in tramo]
    if len(puntos) < 2:
        return None
    c = fila["calle"]
    # Seis decimales son 10 cm: mas no suma nada y la provincia pesa el doble.
    puntos = [[round(lon, 6), round(lat, 6)] for lon, lat in puntos]
    return (
        c["id"], c["nombre"], clave(c["nombre"]), c["departamento"]["id"],
        (c.get("localidad_censal") or {}).get("nombre"),
        min(alturas, default=0), max(alturas, default=0), json.dumps(puntos),
    )


def esquina(fila: dict) -> tuple:
    lon, lat = fila["geometria"]["coordinates"]
    a, b = sorted((clave(fila["calle_a"]["nombre"]), clave(fila["calle_b"]["nombre"])))
    return (a, b, fila["calle_a"]["departamento"]["id"], lat, lon)


def descargar(conn: sqlite3.Connection, provincias: Iterable[str] = PROVINCIAS,
              avance: Callable[[str, int], None] | None = None,
              fuente: Callable[[str], Iterable[str]] | None = None) -> dict[str, int]:
    """Baja cuadras y esquinas y se queda con las de `provincias`. Pisa lo que habia.

    `fuente(archivo)` reemplaza la descarga (los tests le pasan lineas).
    """
    provincias = list(provincias)
    cargadas = {}
    for archivo, tabla, convertir in (("cuadras.ndjson", "cuadras", cuadra),
                                      ("intersecciones.ndjson", "esquinas", esquina)):
        lineas = fuente(archivo) if fuente else _lineas(
            archivo, (lambda n, a=archivo: avance(a, n)) if avance else None)
        cabecera = None
        with conn:
            conn.execute(f"DELETE FROM {tabla}")
        filas: list[tuple] = []
        total = 0
        for linea in lineas:
            if cabecera is None:
                cabecera = json.loads(linea)  # la primera dice version y fecha
                continue
            if not _quizas(linea, provincias):
                continue
            fila = json.loads(linea)
            if _provincia(fila) not in provincias:
                continue
            if (f := convertir(fila)) is not None:
                filas.append(f)
            if len(filas) >= 20000:  # de a tandas: la provincia no entra comoda en memoria
                total += _guardar(conn, tabla, filas)
                filas = []
        total += _guardar(conn, tabla, filas)
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO origen VALUES (?, ?, ?, ?)",
                (archivo, (cabecera or {}).get("version"), (cabecera or {}).get("fecha_creacion"), total),
            )
        cargadas[tabla] = total
    return cargadas


def _guardar(conn: sqlite3.Connection, tabla: str, filas: list[tuple]) -> int:
    if filas:
        with conn:
            conn.executemany(f"INSERT INTO {tabla} VALUES ({','.join('?' * len(filas[0]))})", filas)
    return len(filas)


def cargado(conn: sqlite3.Connection) -> bool:
    return conn.execute("SELECT COUNT(*) FROM cuadras").fetchone()[0] > 0
