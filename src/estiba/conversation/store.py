"""Reconstruye una conversacion completa desde el log de mensajes.

No infiere nada del negocio: eso es trabajo del JobEngine (Fase 4). Aca solo
se junta el hilo y se le pegan las transcripciones que correspondan.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from estiba.core.models import Conversation, Message
from estiba.storage.repositories import MessageRepository, TranscriptRepository


class ConversationStore:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.mensajes = MessageRepository(conn)
        self.transcripciones = TranscriptRepository(conn)

    def ingest(self, mensajes: list[Message]) -> int:
        """Mete mensajes al log. Devuelve cuantos eran nuevos."""
        return self.mensajes.save_many(mensajes)

    def get(self, chat_id: str) -> Conversation:
        """El hilo entero, en orden, con los audios ya transcriptos."""
        return Conversation(
            chat_id=chat_id,
            messages=self.mensajes.by_chat(chat_id),
            transcripts=self.transcripciones.best_for_chat(chat_id),
        )

    def chat_ids(self) -> list[str]:
        return self.mensajes.chat_ids()

    def pending_audio(self) -> list[Message]:
        """Lo que le falta procesar al AudioPipeline."""
        return self.mensajes.pending_audio()

    def ultimo_de(self, source: str) -> datetime | None:
        """El mensaje mas nuevo que entro por `source` ("waha", "fixture"...)."""
        return self.mensajes.ultimo_de(source)
