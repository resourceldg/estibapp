"""Conexion a SQLite y migraciones versionadas.

Sin ORM: el esquema es chico y las consultas son legibles a mano.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
DEFAULT_DB = Path("estiba.db")

#: Cuanto espera un escritor a que el otro suelte la base, en segundos.
#: Las escrituras de Estiba son cortas: si en diez segundos no se pudo, el
#: problema es otro y conviene que falle.
ESPERA_LOCK = 10.0


def connect(path: Path | str = DEFAULT_DB) -> sqlite3.Connection:
    """Abre la base y la deja lista. `:memory:` sirve para los tests."""
    conn = sqlite3.connect(path, detect_types=0, timeout=ESPERA_LOCK)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    # Con WAL los lectores no molestan, pero dos escritores si se pisan: el
    # webhook guardando un mensaje mientras el dashboard sincroniza, o una
    # consola abierta al lado. Sin esto SQLite no espera ni un milisegundo y
    # tira "database is locked", que en el navegador se ve como un error de
    # red salido de la nada.
    conn.execute(f"PRAGMA busy_timeout = {int(ESPERA_LOCK * 1000)}")
    migrate(conn)
    return conn


def migrate(conn: sqlite3.Connection) -> list[str]:
    """Aplica las migraciones pendientes. Devuelve las que corrio."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "  name TEXT PRIMARY KEY,"
        "  applied_at TEXT NOT NULL DEFAULT (datetime('now'))"
        ")"
    )
    ya = {r["name"] for r in conn.execute("SELECT name FROM schema_migrations")}
    corridas = []
    for archivo in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if archivo.name in ya:
            continue
        with conn:
            conn.executescript(archivo.read_text(encoding="utf-8"))
            conn.execute(
                "INSERT INTO schema_migrations (name) VALUES (?)", (archivo.name,)
            )
        corridas.append(archivo.name)
    return corridas
