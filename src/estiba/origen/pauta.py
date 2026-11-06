"""Gasto por anuncio y por dia, importado de un export de Meta.

Formato (lo saca el MCP de Meta con nivel anuncio y time_increment=1):

    {
      "plataforma": "meta", "cuenta": "2656599231362025",
      "actualizado": "2026-11-13T10:00:00-03:00",
      "filas": [
        {"anuncio_id": "1202...", "anuncio": "FS Nov | Hogar", "campania_id": "1202...",
         "dia": "2026-11-07", "gasto": "2900.10", "impresiones": 812, "conversaciones": 2}
      ]
    }

`gasto` sin impuestos, como lo muestra Meta. Reimportar no duplica: cada
(plataforma, cuenta, anuncio, dia) se actualiza en el lugar. Un dia que
todavia no termino se guarda igual y se corrige con la proxima importacion.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from estiba.adapters.meta import ExportInvalido, _conversaciones, _decimal


@dataclass(frozen=True)
class FilaPauta:
    anuncio_id: str
    anuncio: str
    campania_id: str | None
    dia: date
    gasto: Decimal
    impresiones: int | None
    conversaciones: int


@dataclass(frozen=True)
class Pauta:
    plataforma: str
    cuenta: str
    actualizado: datetime
    filas: tuple[FilaPauta, ...]


def desde_dict(datos: dict) -> Pauta:
    faltan = [c for c in ("cuenta", "actualizado", "filas") if not datos.get(c)]
    if faltan:
        raise ExportInvalido(f"faltan datos del export: {', '.join(faltan)}")
    actualizado = datetime.fromisoformat(datos["actualizado"])
    filas, vistas = [], set()
    for i, f in enumerate(datos["filas"]):
        donde = f"fila {i + 1}"
        try:
            anuncio_id, dia = str(f["anuncio_id"]), date.fromisoformat(f["dia"])
        except (KeyError, ValueError) as e:
            raise ExportInvalido(f"{donde}: falta el anuncio o el dia") from e
        if dia > actualizado.date():
            continue
        if (anuncio_id, dia) in vistas:
            raise ExportInvalido(f"{donde}: {anuncio_id} {dia} esta repetida")
        vistas.add((anuncio_id, dia))
        imp = f.get("impresiones")
        filas.append(FilaPauta(anuncio_id, str(f.get("anuncio") or anuncio_id),
                               str(f["campania_id"]) if f.get("campania_id") else None, dia,
                               _decimal(f.get("gasto", 0), donde),
                               None if imp in (None, "") else int(imp),
                               _conversaciones(f.get("conversaciones"), donde)))
    return Pauta(str(datos.get("plataforma") or "meta"), str(datos["cuenta"]), actualizado, tuple(filas))


def leer(ruta: Path | str) -> Pauta:
    try:
        return desde_dict(json.loads(Path(ruta).read_text(encoding="utf-8")))
    except json.JSONDecodeError as e:
        raise ExportInvalido(f"{ruta} no es JSON: {e}") from e


def importar(conn: sqlite3.Connection, pauta: Pauta) -> int:
    with conn:
        for f in pauta.filas:
            conn.execute(
                "INSERT INTO pauta_diaria (plataforma, cuenta, anuncio_id, anuncio, campania_id, dia, gasto,"
                " impresiones, conversaciones, actualizado) VALUES (?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT (plataforma, cuenta, anuncio_id, dia) DO UPDATE SET"
                " anuncio = excluded.anuncio, campania_id = excluded.campania_id, gasto = excluded.gasto,"
                " impresiones = excluded.impresiones, conversaciones = excluded.conversaciones,"
                " actualizado = excluded.actualizado",
                (pauta.plataforma, pauta.cuenta, f.anuncio_id, f.anuncio, f.campania_id, f.dia.isoformat(),
                 str(f.gasto), f.impresiones, f.conversaciones, pauta.actualizado.isoformat()))
    return len(pauta.filas)


def por_anuncio(conn: sqlite3.Connection, desde: date | None = None,
                hasta: date | None = None) -> dict[str, tuple[Decimal, int]]:
    """anuncio_id -> (gasto sin impuestos, conversaciones segun Meta)."""
    sql, args = "SELECT anuncio_id, gasto, conversaciones FROM pauta_diaria WHERE 1=1", []
    if desde:
        sql, args = sql + " AND dia >= ?", [*args, desde.isoformat()]
    if hasta:
        sql, args = sql + " AND dia <= ?", [*args, hasta.isoformat()]
    salida: dict[str, tuple[Decimal, int]] = {}
    for r in conn.execute(sql, args):
        g, c = salida.get(r["anuncio_id"], (Decimal(0), 0))
        salida[r["anuncio_id"]] = (g + Decimal(r["gasto"]), c + (r["conversaciones"] or 0))
    return salida
