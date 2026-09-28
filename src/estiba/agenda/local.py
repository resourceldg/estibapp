"""La agenda propia de Estiba, en SQLite.

Implementa el puerto `CalendarProvider`. Es la fuente de verdad de los
eventos: de aca sale el .ics al que se suscribe el celular y, si esta
configurado, el espejo en CalDAV.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime

from estiba.agenda.evento import Evento, evento_de
from estiba.core.models import FreightJob
from estiba.storage.repositories import SettingsRepository


def _texto(v: date | datetime) -> str:
    return v.isoformat(timespec="minutes") if isinstance(v, datetime) else v.isoformat()


class LocalCalendar:
    def __init__(self, conn: sqlite3.Connection, reloj=datetime.now) -> None:
        self.conn = conn
        self.reloj = reloj

    # -- puerto CalendarProvider -------------------------------------------

    def create(self, job: FreightJob) -> str:
        return self.programar(job)[0].uid

    def move(self, event_id: str, when: datetime) -> None:
        with self.conn:
            cur = self.conn.execute(
                "UPDATE calendar_events SET starts_at = ?, all_day = 0, mirrored = 0, updated_at = ? "
                "WHERE id = ?",
                (_texto(when), _texto(self.reloj()), event_id),
            )
        if cur.rowcount == 0:
            raise KeyError(f"no existe el evento {event_id}")

    def cancel(self, event_id: str) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE calendar_events SET status = 'cancelado', mirrored = 0, updated_at = ? "
                "WHERE id = ? AND status != 'cancelado'",
                (_texto(self.reloj()), event_id),
            )

    # -- lo que usa la sincronizacion ---------------------------------------

    def programar(self, job: FreightJob, tentativo: bool = False) -> tuple[Evento, str]:
        """Deja el evento del flete como tiene que estar. Devuelve que hizo:
        "creado", "movido", "actualizado" o "igual"."""
        e = evento_de(job, tentativo)
        previo = self.conn.execute("SELECT * FROM calendar_events WHERE id = ?", (e.uid,)).fetchone()
        valores = (_texto(e.inicio), int(e.todo_el_dia), int(e.aproximado), e.titulo,
                   e.descripcion, int(tentativo))
        ahora = _texto(self.reloj())
        with self.conn:
            if previo is None:
                self.conn.execute(
                    "INSERT INTO calendar_events (id, job_id, starts_at, all_day, approximate, title, "
                    "description, tentative, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (e.uid, e.job_id, *valores, ahora),
                )
                return e, "creado"
            actuales = (previo["starts_at"], previo["all_day"], previo["approximate"],
                        previo["title"], previo["description"], previo["tentative"])
            if actuales == valores and previo["status"] == "activo":
                return e, "igual"
            self.conn.execute(
                "UPDATE calendar_events SET starts_at = ?, all_day = ?, approximate = ?, title = ?, "
                "description = ?, tentative = ?, status = 'activo', mirrored = 0, updated_at = ? "
                "WHERE id = ?",
                (*valores, ahora, e.uid),
            )
            return e, "movido" if previo["starts_at"] != valores[0] else "actualizado"

    def cancelar_de(self, job_id: str) -> bool:
        fila = self.conn.execute(
            "SELECT id FROM calendar_events WHERE job_id = ? AND status = 'activo'", (job_id,)
        ).fetchone()
        if fila is None:
            return False
        self.cancel(fila["id"])
        return True

    def eventos(self, incluir_cancelados: bool = True) -> list[tuple[Evento, bool]]:
        filas = self.conn.execute(
            "SELECT * FROM calendar_events "
            + ("" if incluir_cancelados else "WHERE status = 'activo' ")
            + "ORDER BY starts_at"
        )
        return [(_evento(f), f["status"] == "cancelado") for f in filas]

    def sin_reflejar(self) -> list[tuple[Evento, bool]]:
        filas = self.conn.execute("SELECT * FROM calendar_events WHERE mirrored = 0")
        return [(_evento(f), f["status"] == "cancelado") for f in filas]

    def marcar_reflejado(self, uid: str) -> None:
        with self.conn:
            self.conn.execute("UPDATE calendar_events SET mirrored = 1 WHERE id = ?", (uid,))

    def token(self) -> str:
        """El secreto de la URL del .ics: se crea una vez y queda."""
        return SettingsRepository(self.conn).secreto("ics_token")


def _evento(f: sqlite3.Row) -> Evento:
    inicio: date | datetime = (
        date.fromisoformat(f["starts_at"]) if f["all_day"] else datetime.fromisoformat(f["starts_at"])
    )
    return Evento(f["id"], f["job_id"], inicio, bool(f["approximate"]), f["title"],
                  f["description"], bool(f["tentative"]))
