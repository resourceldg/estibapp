"""Lectura y escritura de las tablas. Nada de logica de negocio aca."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Iterator
from datetime import datetime

from estiba.core.models import Message, MessageKind, Transcript


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
