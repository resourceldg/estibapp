"""Juntar desde la base todo lo que el motor necesita. La unica parte con E/S."""

from __future__ import annotations

import sqlite3
from datetime import date
from decimal import Decimal

from estiba.economia.config import Camioneta, Credito, Disponibilidad, Economia, fondos_desde
from estiba.finanzas.costos import Costos

#: Si la base no tiene precio base (una base anterior a la 023 que no
#: corrio la migracion no existe, pero por las dudas): el del 2026-09-29.
PRECIO_BASE_INICIAL = Decimal(40000)


def economia(conn: sqlite3.Connection, hoy: date) -> Economia:
    from estiba.storage.repositories import EconomiaRepository, SettingsRepository

    guardado = SettingsRepository(conn).json("costos") or {}
    repo = EconomiaRepository(conn)
    return Economia(
        costos=Costos.desde_dict(guardado),
        precio_base=repo.precio_base(hoy) or PRECIO_BASE_INICIAL,
        camioneta=Camioneta.desde_dict(repo.config("camioneta")),
        credito=Credito.desde_dict(repo.config("credito")),
        disponibilidad=Disponibilidad.desde_dict(repo.config("disponibilidad")),
        fondos=fondos_desde(repo.config("fondos")),
        declarados=frozenset(guardado),
    )
