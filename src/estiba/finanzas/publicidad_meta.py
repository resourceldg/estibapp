"""Importar el gasto de Meta a `campaigns` sin duplicar ni pisar lo manual.

Ver la migracion 031 para las reglas. Este modulo solo escribe; quien lee
(GastoRepository.todos) ve unicamente lo vigente, asi que el tablero, el
pronostico y el informe toman lo importado sin cambiar nada.
"""

from __future__ import annotations

import secrets
import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from estiba.adapters.meta import ExportMeta, FilaMeta

CERO = Decimal(0)
#: Lo que se tolera entre la suma de los dias y el total de Meta: redondeo.
TOLERANCIA = Decimal("0.05")


@dataclass(frozen=True)
class Diferencia:
    """Algo que no cierra entre dos numeros que deberian ser iguales."""

    que: str
    esperado: Decimal
    obtenido: Decimal
    #: False si es una cantidad (conversaciones), no plata.
    es_monto: bool = True
    #: "meta" si se compara con lo que dice Meta; "manual" si con una carga a mano.
    contra: str = "meta"

    @property
    def cierra(self) -> bool:
        return abs(self.esperado - self.obtenido) <= TOLERANCIA


@dataclass
class Resultado:
    nuevas: int = 0
    actualizadas: int = 0
    iguales: int = 0
    #: Cargas manuales que la importacion reemplazo (id, desde, hasta).
    manuales_reemplazadas: list[tuple[str, date, date]] = field(default_factory=list)
    #: Dias importados que no cuentan porque una carga manual los cubre a medias.
    dias_tapados: int = 0
    conciliacion: list[Diferencia] = field(default_factory=list)

    @property
    def todo_cierra(self) -> bool:
        """Lo importado coincide con Meta. Que una carga manual no coincida es informacion, no falla."""
        return all(d.cierra for d in self.conciliacion if d.contra == "meta")


def _dias(desde: date, hasta: date) -> set[date]:
    return {desde + timedelta(days=i) for i in range((hasta - desde).days + 1)}


def _upsert(conn: sqlite3.Connection, export: ExportMeta, f: FilaMeta, r: Resultado) -> None:
    clave = (export.plataforma, export.cuenta, f.campania_id, f.dia.isoformat())
    previa = conn.execute(
        "SELECT id, spend, resultados, nombre FROM campaigns WHERE origen = 'importado' "
        "AND platform = ? AND cuenta = ? AND external_id = ? AND period_start = ?", clave).fetchone()
    if previa is None:
        conn.execute(
            "INSERT INTO campaigns (id, platform, external_id, period_start, period_end, spend, resultados, "
            "impuestos, incluye_impuestos, origen, cuenta, nombre, moneda, zona_horaria, actualizado, vigente) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, NULL, 0, 'importado', ?, ?, ?, ?, ?, 1)",
            (secrets.token_hex(6), export.plataforma, f.campania_id, f.dia.isoformat(), f.dia.isoformat(),
             str(f.gasto), f.conversaciones, export.cuenta, f.campania, export.moneda, export.zona_horaria,
             export.actualizado.isoformat()))
        r.nuevas += 1
        return
    if Decimal(previa["spend"]) == f.gasto and previa["resultados"] == f.conversaciones \
            and previa["nombre"] == f.campania:
        r.iguales += 1
    else:
        r.actualizadas += 1
    # Aunque no cambie el numero, se anota que se volvio a leer.
    conn.execute(
        "UPDATE campaigns SET spend = ?, resultados = ?, nombre = ?, moneda = ?, zona_horaria = ?, "
        "actualizado = ? WHERE id = ?",
        (str(f.gasto), f.conversaciones, f.campania, export.moneda, export.zona_horaria,
         export.actualizado.isoformat(), previa["id"]))


def importar(conn: sqlite3.Connection, export: ExportMeta) -> Resultado:
    """Carga el export. Todo o nada: si algo falla, la base queda como estaba."""
    r = Resultado()
    por_campania: dict[str, dict[date, FilaMeta]] = {}
    for f in export.filas:
        por_campania.setdefault(f.campania_id, {})[f.dia] = f

    with conn:
        for f in export.filas:
            _upsert(conn, export, f, r)

        # Lo manual que se pisa con lo importado. Una carga manual sin
        # campania ("manual") vale para todas las de esta cuenta.
        manuales = conn.execute(
            "SELECT * FROM campaigns WHERE origen = 'manual' AND platform = ? AND vigente = 1",
            (export.plataforma,)).fetchall()
        for m in manuales:
            desde, hasta = date.fromisoformat(m["period_start"]), date.fromisoformat(m["period_end"])
            dias = _dias(desde, hasta)
            campanias = list(por_campania) if m["external_id"] == "manual" else \
                [m["external_id"]] if m["external_id"] in por_campania else []
            if not campanias:
                continue
            cubiertos = [d for d in dias if any(d in por_campania[c] for c in campanias)]
            if not cubiertos:
                continue
            importado = sum((por_campania[c][d].gasto for c in campanias for d in dias if d in por_campania[c]), CERO)
            convs = sum(por_campania[c][d].conversaciones for c in campanias for d in dias if d in por_campania[c])
            nombre = f"carga manual {desde:%d/%m}-{hasta:%d/%m}"
            r.conciliacion.append(Diferencia(f"{nombre}: gasto", Decimal(m["spend"]), importado, contra="manual"))
            if m["resultados"] is not None:
                r.conciliacion.append(Diferencia(f"{nombre}: conversaciones", Decimal(m["resultados"]),
                                                 Decimal(convs), es_monto=False, contra="manual"))
            if len(cubiertos) == len(dias):
                conn.execute(
                    "UPDATE campaigns SET vigente = 0, reemplazado_por = ? WHERE id = ?",
                    (f"importacion de Meta del {export.actualizado:%d/%m/%Y %H:%M}", m["id"]))
                r.manuales_reemplazadas.append((m["id"], desde, hasta))
            else:
                # La manual sigue valiendo: los dias importados que se pisan con ella no cuentan.
                for c in campanias:
                    for d in cubiertos:
                        if d in por_campania[c]:
                            conn.execute(
                                "UPDATE campaigns SET vigente = 0, reemplazado_por = ? WHERE origen = 'importado' "
                                "AND platform = ? AND cuenta = ? AND external_id = ? AND period_start = ?",
                                (f"tapado por la carga manual {m['id']}", export.plataforma, export.cuenta, c,
                                 d.isoformat()))
                            r.dias_tapados += 1

        # Que la suma de los dias de cada campania de lo que Meta dice del total.
        for t in export.totales:
            # Sin float: los montos se suman como Decimal (ver 001_init).
            filas = conn.execute(
                "SELECT spend, resultados FROM campaigns WHERE origen = 'importado' "
                "AND platform = ? AND cuenta = ? AND external_id = ?",
                (export.plataforma, export.cuenta, t.campania_id)).fetchall()
            gasto = sum((Decimal(f["spend"]) for f in filas), CERO)
            convs = sum(f["resultados"] or 0 for f in filas)
            r.conciliacion.append(Diferencia(f"campania {t.campania_id}: gasto total", t.gasto, gasto))
            if t.conversaciones is not None:
                r.conciliacion.append(Diferencia(f"campania {t.campania_id}: conversaciones totales",
                                                 Decimal(t.conversaciones), Decimal(convs), es_monto=False))
    return r


def gastado_desde(conn: sqlite3.Connection, desde: date, cuenta: str | None = None) -> Decimal:
    """Lo vigente gastado desde un dia (inclusive), sin impuestos. Para controlar un tope."""
    sql = "SELECT spend, period_start, period_end FROM campaigns WHERE vigente = 1 AND period_end >= ?"
    params: list = [desde.isoformat()]
    if cuenta:
        sql += " AND cuenta = ?"
        params.append(cuenta)
    total = CERO
    for g in conn.execute(sql, params):
        ini, fin = date.fromisoformat(g["period_start"]), date.fromisoformat(g["period_end"])
        dias = (fin - ini).days + 1
        dentro = (fin - max(ini, desde)).days + 1
        total += Decimal(g["spend"]) * Decimal(dentro) / Decimal(dias)
    return total
