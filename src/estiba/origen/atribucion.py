"""Que pieza trajo cada flete, guardado con su evidencia.

Lo automatico solo escribe donde no hay nada: una correccion a mano nunca
se pisa. Corregir deja la anterior en `vigente = 0`, asi queda la historia.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from estiba.origen import piezas as P


@dataclass(frozen=True)
class Atribucion:
    job_id: str
    pieza_id: int
    metodo: str
    evidencia: str | None
    at: datetime


def vigentes(conn: sqlite3.Connection) -> dict[str, Atribucion]:
    return {
        r["job_id"]: Atribucion(r["job_id"], r["pieza_id"], r["metodo"], r["evidencia"],
                                datetime.fromisoformat(r["at"]))
        for r in conn.execute("SELECT * FROM atribuciones WHERE vigente = 1")
    }


def sincronizar(conn: sqlite3.Connection, primeros: dict[str, tuple[str, datetime]]) -> int:
    """Atribuye los fletes nuevos por su primer mensaje. Devuelve cuantos.

    `primeros`: job_id -> (texto `plano` del primer mensaje, cuando llego).
    Los que ya tienen atribucion (automatica o manual) no se tocan.
    """
    ya = vigentes(conn)
    lista = P.todas(conn)
    nuevas = 0
    with conn:
        for job_id, (texto, at) in primeros.items():
            if job_id in ya:
                continue
            hallada = P.reconocer(texto, lista)
            if hallada is None:
                continue  # sin atribuir: no se adivina
            pieza, metodo, evidencia = hallada
            conn.execute(
                "INSERT INTO atribuciones (job_id, pieza_id, metodo, evidencia, at) VALUES (?,?,?,?,?)",
                (job_id, pieza.id, metodo, evidencia, at.isoformat()))
            nuevas += 1
    return nuevas


def asignar(conn: sqlite3.Connection, job_id: str, clave: str, metodo: str = "manual",
            evidencia: str | None = None, ahora: datetime | None = None) -> Atribucion:
    """Corrige o completa a mano. La anterior queda como historia."""
    if metodo not in P.METODOS:
        raise ValueError(f"metodo '{metodo}' no es uno de {', '.join(P.METODOS)}")
    pieza = P.por_clave(conn, clave)
    if pieza is None:
        raise ValueError(f"no hay una pieza '{clave}'")
    at = ahora or datetime.now()
    with conn:
        conn.execute("UPDATE atribuciones SET vigente = 0 WHERE job_id = ? AND vigente = 1", (job_id,))
        conn.execute(
            "INSERT INTO atribuciones (job_id, pieza_id, metodo, evidencia, at) VALUES (?,?,?,?,?)",
            (job_id, pieza.id, metodo, evidencia, at.isoformat()))
    return vigentes(conn)[job_id]
