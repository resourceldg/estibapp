"""Las piezas publicadas y como reconocerlas en el primer mensaje."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

TIPOS = ("anuncio", "posteo_grupo", "marketplace", "pagina", "ficha_google", "flyer", "otro")
MEDIOS = ("pago", "organico")
#: Como se supo. De la evidencia mas firme a la menos.
METODOS = ("manual", "codigo", "mensaje_precargado", "declarado")


@dataclass(frozen=True)
class Pieza:
    id: int
    clave: str
    nombre: str
    tipo: str
    medio: str
    canal: str
    lugar: str | None = None
    campania: str | None = None
    anuncio_id: str | None = None
    marcador: str | None = None
    codigo: str | None = None
    url: str | None = None
    desde: date | None = None
    hasta: date | None = None
    costo_fijo: Decimal = Decimal(0)
    vigente: bool = True

    def patron(self) -> re.Pattern | None:
        return re.compile(self.marcador) if self.marcador else None


def _fecha(v: str | None) -> date | None:
    return date.fromisoformat(v) if v else None


def _pieza(r: sqlite3.Row) -> Pieza:
    return Pieza(r["id"], r["clave"], r["nombre"], r["tipo"], r["medio"], r["canal"], r["lugar"],
                 r["campania"], r["anuncio_id"], r["marcador"], r["codigo"], r["url"],
                 _fecha(r["desde"]), _fecha(r["hasta"]), Decimal(r["costo_fijo"]), bool(r["vigente"]))


def todas(conn: sqlite3.Connection, solo_vigentes: bool = False) -> list[Pieza]:
    sql = "SELECT * FROM piezas" + (" WHERE vigente = 1" if solo_vigentes else "") + " ORDER BY id"
    return [_pieza(r) for r in conn.execute(sql)]


def por_clave(conn: sqlite3.Connection, clave: str) -> Pieza | None:
    r = conn.execute("SELECT * FROM piezas WHERE clave = ?", (clave,)).fetchone()
    return _pieza(r) if r else None


def agregar(conn: sqlite3.Connection, clave: str, nombre: str, tipo: str, medio: str, canal: str, *,
            lugar: str | None = None, campania: str | None = None, anuncio_id: str | None = None,
            marcador: str | None = None, codigo: str | None = None, url: str | None = None,
            desde: date | None = None, hasta: date | None = None, costo_fijo: Decimal = Decimal(0),
            texto: str | None = None) -> Pieza:
    if tipo not in TIPOS:
        raise ValueError(f"tipo '{tipo}' no es uno de {', '.join(TIPOS)}")
    if medio not in MEDIOS:
        raise ValueError(f"medio '{medio}' no es pago ni organico")
    if marcador:
        re.compile(marcador)  # que falle al cargarlo, no al leer los mensajes
    with conn:
        conn.execute(
            "INSERT INTO piezas (clave, nombre, tipo, medio, canal, lugar, campania, anuncio_id, marcador,"
            " codigo, url, desde, hasta, costo_fijo, texto) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (clave, nombre, tipo, medio, canal, lugar, campania, anuncio_id, marcador, codigo, url,
             desde.isoformat() if desde else None, hasta.isoformat() if hasta else None,
             str(costo_fijo), texto))
    return por_clave(conn, clave)


def reconocer(texto_plano: str, piezas: list[Pieza]) -> tuple[Pieza, str, str] | None:
    """Que pieza dejo este primer mensaje: (pieza, metodo, evidencia).

    Primero el codigo (un link armado por nosotros: no hay duda). Si no,
    el marcador del mensaje precargado; si coinciden varios, gana el que
    calza el tramo mas largo (el especifico le gana al generico "quiero mas
    informacion"). None si no hay nada: queda sin atribuir.
    """
    for p in piezas:
        if p.codigo and re.search(rf"\b{re.escape(p.codigo.lower())}\b", texto_plano):
            return p, "codigo", p.codigo
    mejor: tuple[int, Pieza, str] | None = None
    for p in piezas:
        patron = p.patron()
        if patron and (m := patron.search(texto_plano)):
            largo = m.end() - m.start()
            if mejor is None or largo > mejor[0]:
                mejor = (largo, p, m.group(0))
    return (mejor[1], "mensaje_precargado", mejor[2]) if mejor else None
