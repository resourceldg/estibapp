"""Gasto de Meta leido de un export: el adaptador del puerto AdSpendProvider.

Estiba no tiene token propio de la API de Meta. Lo que si hay es un export
JSON por campania y por dia (sacado del MCP de Meta o de Ads Manager) con
este formato:

    {
      "plataforma": "meta",
      "cuenta": "2656599231362025",
      "moneda": "ARS",
      "zona_horaria": "America/Argentina/Buenos_Aires",
      "actualizado": "2026-10-07T12:00:00-03:00",
      "metrica": "onsite_conversion.messaging_conversation_started_7d",
      "filas": [
        {"campania_id": "120...", "campania": "Flete Surfero | ...",
         "dia": "2026-09-16", "gasto": "2100.74", "conversaciones": 4}
      ],
      "totales": [
        {"campania_id": "120...", "gasto": "81335.95", "conversaciones": 37}
      ]
    }

`gasto` es lo que Meta muestra como gastado, sin impuestos. `totales` es
opcional: si viene, se usa para conciliar la suma de los dias contra lo que
Meta dice de la campania entera. Cuando haya token, un adaptador de la API
devuelve el mismo `ExportMeta` y nada mas cambia.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from estiba.core.models import Campaign


class ExportInvalido(ValueError):
    """El archivo no dice lo minimo para importarlo sin adivinar."""


@dataclass(frozen=True)
class FilaMeta:
    campania_id: str
    campania: str
    dia: date
    #: Sin impuestos, como lo muestra Meta.
    gasto: Decimal
    #: Conversaciones iniciadas segun Meta. Sin resultados Meta dice "no
    #: disponible": eso es cero, no un dato faltante.
    conversaciones: int


@dataclass(frozen=True)
class TotalMeta:
    campania_id: str
    gasto: Decimal
    conversaciones: int | None


@dataclass(frozen=True)
class ExportMeta:
    plataforma: str
    cuenta: str
    moneda: str
    zona_horaria: str
    actualizado: datetime
    metrica: str
    filas: tuple[FilaMeta, ...]
    totales: tuple[TotalMeta, ...] = ()


def _decimal(valor: object, donde: str) -> Decimal:
    try:
        d = Decimal(str(valor))
    except (InvalidOperation, TypeError) as e:
        raise ExportInvalido(f"{donde}: '{valor}' no es un monto") from e
    if d < 0:
        raise ExportInvalido(f"{donde}: un gasto no puede ser negativo")
    return d


def _conversaciones(valor: object, donde: str) -> int:
    if valor in (None, "", "Not available"):
        return 0
    try:
        n = int(valor)
    except (TypeError, ValueError) as e:
        raise ExportInvalido(f"{donde}: '{valor}' no es una cantidad de conversaciones") from e
    if n < 0:
        raise ExportInvalido(f"{donde}: conversaciones negativas")
    return n


def desde_dict(datos: dict) -> ExportMeta:
    faltan = [c for c in ("cuenta", "moneda", "zona_horaria", "actualizado", "filas") if not datos.get(c)]
    if faltan:
        raise ExportInvalido(f"faltan datos del export: {', '.join(faltan)}")
    try:
        actualizado = datetime.fromisoformat(datos["actualizado"])
    except ValueError as e:
        raise ExportInvalido("'actualizado' no es una fecha ISO") from e
    filas = []
    vistas = set()
    for i, f in enumerate(datos["filas"]):
        donde = f"fila {i + 1}"
        try:
            dia = date.fromisoformat(f["dia"])
            campania_id = str(f["campania_id"])
        except (KeyError, ValueError) as e:
            raise ExportInvalido(f"{donde}: falta el dia o la campania") from e
        # Un dia que todavia no paso no tiene gasto: Meta lo devuelve en
        # cero y, importado, pareceria un dia sin entrega.
        if dia > actualizado.date():
            continue
        if (campania_id, dia) in vistas:
            raise ExportInvalido(f"{donde}: {campania_id} {dia} esta repetida en el export")
        vistas.add((campania_id, dia))
        filas.append(FilaMeta(campania_id, str(f.get("campania") or campania_id), dia,
                              _decimal(f.get("gasto", 0), donde),
                              _conversaciones(f.get("conversaciones"), donde)))
    totales = tuple(
        TotalMeta(str(t["campania_id"]), _decimal(t["gasto"], f"total {t['campania_id']}"),
                  None if t.get("conversaciones") is None else _conversaciones(t["conversaciones"], "total"))
        for t in datos.get("totales", ())
    )
    return ExportMeta(str(datos.get("plataforma") or "meta"), str(datos["cuenta"]), str(datos["moneda"]),
                      str(datos["zona_horaria"]), actualizado, str(datos.get("metrica") or ""),
                      tuple(filas), totales)


def leer_export(ruta: Path | str) -> ExportMeta:
    try:
        datos = json.loads(Path(ruta).read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ExportInvalido(f"{ruta} no es JSON: {e}") from e
    return desde_dict(datos)


class MetaExportProvider:
    """AdSpendProvider sobre un export: una Campaign por campania y dia."""

    def __init__(self, export: ExportMeta) -> None:
        self.export = export

    def spend(self, since: date, until: date) -> Iterable[Campaign]:
        for f in self.export.filas:
            if since <= f.dia <= until:
                yield Campaign(f"{f.campania_id}:{f.dia.isoformat()}", self.export.plataforma,
                               f.campania_id, f.dia, f.dia, f.gasto)
