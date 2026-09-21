"""Lectura y escritura de las tablas. Nada de logica de negocio aca."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Iterator
from datetime import date, datetime, time
from decimal import Decimal
from typing import TYPE_CHECKING

from estiba.core.models import FreightJob, JobState, Message, MessageKind, Slot, Transcript

if TYPE_CHECKING:
    from estiba.jobs.engine import Evento


def _a_texto(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _a_fecha(s: str) -> datetime:
    return datetime.fromisoformat(s)


class MessageRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def save(self, mensaje: Message) -> bool:
        """Guarda un mensaje. Devuelve False si ya estaba.

        Es idempotente a proposito: reprocesar un fixture o recibir el mismo
        webhook dos veces no puede duplicar el historial.
        """
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO messages "
            "(id, chat_id, from_customer, sent_at, kind, text, media_path, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                mensaje.id,
                mensaje.chat_id,
                int(mensaje.from_customer),
                _a_texto(mensaje.sent_at),
                mensaje.kind.value,
                mensaje.text,
                mensaje.media_path,
                mensaje.source,
            ),
        )
        return cur.rowcount == 1

    def save_many(self, mensajes: Iterable[Message]) -> int:
        """Guarda varios en una transaccion. Devuelve cuantos eran nuevos."""
        nuevos = 0
        with self.conn:
            for m in mensajes:
                nuevos += int(self.save(m))
        return nuevos

    def by_chat(self, chat_id: str) -> list[Message]:
        filas = self.conn.execute(
            "SELECT * FROM messages WHERE chat_id = ? ORDER BY sent_at, id",
            (chat_id,),
        )
        return [self._a_mensaje(f) for f in filas]

    def pending_audio(self) -> list[Message]:
        """Audios sin transcribir. La cola del AudioPipeline (Fase 2)."""
        filas = self.conn.execute(
            "SELECT m.* FROM messages m "
            "LEFT JOIN transcripts t ON t.message_id = m.id "
            "WHERE m.kind = 'audio' AND t.message_id IS NULL "
            "ORDER BY m.sent_at"
        )
        return [self._a_mensaje(f) for f in filas]

    def audio_without_model(self, model: str) -> list[Message]:
        """Audios que este modelo todavia no transcribio, tengan o no otra."""
        filas = self.conn.execute(
            "SELECT m.* FROM messages m "
            "LEFT JOIN transcripts t ON t.message_id = m.id AND t.model = ? "
            "WHERE m.kind = 'audio' AND t.message_id IS NULL "
            "ORDER BY m.sent_at",
            (model,),
        )
        return [self._a_mensaje(f) for f in filas]

    def chat_ids(self) -> list[str]:
        return [
            r[0]
            for r in self.conn.execute(
                "SELECT DISTINCT chat_id FROM messages ORDER BY chat_id"
            )
        ]

    @staticmethod
    def _a_mensaje(fila: sqlite3.Row) -> Message:
        return Message(
            id=fila["id"],
            chat_id=fila["chat_id"],
            from_customer=bool(fila["from_customer"]),
            sent_at=_a_fecha(fila["sent_at"]),
            kind=MessageKind(fila["kind"]),
            text=fila["text"],
            media_path=fila["media_path"],
            source=fila["source"],
        )


class TranscriptRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def save(self, t: Transcript) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO transcripts "
                "(message_id, text, confidence, model, duration_s) "
                "VALUES (?, ?, ?, ?, ?)",
                (t.message_id, t.text, t.confidence, t.model, t.duration_s),
            )

    def best_for_chat(self, chat_id: str) -> dict[str, Transcript]:
        """La mejor transcripcion de cada audio del chat.

        Si un audio se transcribio con varios modelos, gana el de mayor
        confianza. Las otras quedan guardadas para poder comparar.
        """
        filas = self.conn.execute(
            "SELECT t.* FROM transcripts t "
            "JOIN messages m ON m.id = t.message_id "
            "WHERE m.chat_id = ? "
            "ORDER BY t.message_id, t.confidence DESC",
            (chat_id,),
        )
        mejores: dict[str, Transcript] = {}
        for f in filas:
            if f["message_id"] in mejores:
                continue
            mejores[f["message_id"]] = Transcript(
                message_id=f["message_id"],
                text=f["text"],
                confidence=f["confidence"],
                model=f["model"],
                duration_s=f["duration_s"],
            )
        return mejores

    def all_for_message(self, message_id: str) -> Iterator[Transcript]:
        filas = self.conn.execute(
            "SELECT * FROM transcripts WHERE message_id = ? ORDER BY confidence DESC",
            (message_id,),
        )
        for f in filas:
            yield Transcript(
                message_id=f["message_id"],
                text=f["text"],
                confidence=f["confidence"],
                model=f["model"],
                duration_s=f["duration_s"],
            )


#: Campos del FreightJob que se guardan como slot en job_slots.
SLOT_FIELDS = (
    "origin", "destination", "items", "when", "hour", "daypart",
    "floors_no_lift", "helpers", "quoted_price",
)


def _a_json(valor: object) -> str:
    def convertir(v: object) -> object:
        if isinstance(v, (date, time)):
            return v.isoformat()
        if isinstance(v, Decimal):
            return str(v)
        raise TypeError(f"no se como guardar {type(v).__name__}")

    return json.dumps(valor, default=convertir, ensure_ascii=False)


class JobRepository:
    """Guarda el resultado del JobEngine.

    Todo lo de un flete se deriva de la conversacion, asi que guardar es
    reemplazar: los slots y la historia de estados se reescriben enteros.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def save(self, job: FreightJob, eventos: Iterable[Evento]) -> None:
        creado = _a_texto(job.created_at or datetime.now())
        ahora = _a_texto(datetime.now())
        with self.conn:
            self.conn.execute(
                "INSERT INTO jobs (id, conversation_id, state, calendar_event_id, created_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT (id) DO UPDATE SET state = excluded.state, "
                "calendar_event_id = excluded.calendar_event_id",
                (job.id, job.conversation_id, job.state.value, job.calendar_event_id, creado),
            )
            self.conn.execute("DELETE FROM job_slots WHERE job_id = ?", (job.id,))
            for campo in SLOT_FIELDS:
                slot: Slot | None = getattr(job, campo)
                if slot is None:
                    continue
                self.conn.execute(
                    "INSERT INTO job_slots (job_id, field, value_json, confidence, "
                    "message_id, quote, method, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        job.id, campo, _a_json(slot.value), slot.confidence,
                        slot.evidence.message_id, slot.evidence.quote,
                        slot.evidence.method.value, ahora,
                    ),
                )
            self.conn.execute("DELETE FROM job_events WHERE job_id = ?", (job.id,))
            for e in eventos:
                self.conn.execute(
                    "INSERT INTO job_events (job_id, from_state, to_state, at, cause, message_id) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        job.id, e.desde.value if e.desde else None, e.hacia.value,
                        _a_texto(e.at), e.causa, e.message_id,
                    ),
                )

    def state(self, job_id: str) -> JobState | None:
        fila = self.conn.execute("SELECT state FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return JobState(fila["state"]) if fila else None

    def slots(self, job_id: str) -> dict[str, sqlite3.Row]:
        filas = self.conn.execute("SELECT * FROM job_slots WHERE job_id = ?", (job_id,))
        return {f["field"]: f for f in filas}

    def events(self, job_id: str) -> list[sqlite3.Row]:
        return list(
            self.conn.execute("SELECT * FROM job_events WHERE job_id = ? ORDER BY id", (job_id,))
        )

    def by_state(self) -> dict[JobState, int]:
        filas = self.conn.execute("SELECT state, COUNT(*) AS n FROM jobs GROUP BY state")
        return {JobState(f["state"]): f["n"] for f in filas}
