"""WhatsApp por WAHA: recibir, importar historial y enviar lo aprobado.

Enviar es lo unico que le habla al cliente, y solo se llama despues de que
una persona aprobo el borrador. Si WhatsApp falla, el borrador queda
aprobado con el error a la vista, para reintentar o mandarlo a mano.
"""

from __future__ import annotations

import importlib.util
import sqlite3
from dataclasses import dataclass
from datetime import datetime

from estiba.adapters.waha import ErrorWaha, WahaClient, es_persona, ingresar, normalizar
from estiba.audio.pipeline import AudioPipeline, ReporteAudio
from estiba.conversation.store import ConversationStore
from estiba.core.models import Message
from estiba.storage.repositories import DraftRepository

EVENTOS_DE_MENSAJE = ("message", "message.any")


@dataclass(frozen=True)
class Envio:
    ok: bool
    message_id: str | None = None
    error: str | None = None


class WhatsApp:
    def __init__(self, cliente: WahaClient) -> None:
        self.cliente = cliente
        self._transcriptor = None

    # -- entrada -----------------------------------------------------------

    def recibir(self, conn: sqlite3.Connection, evento: dict) -> Message | None:
        """Un evento de webhook. Devuelve el mensaje si era uno nuevo de una charla."""
        if evento.get("event") not in EVENTOS_DE_MENSAJE:
            return None
        mensaje = ingresar(self.cliente, evento.get("payload") or {})
        if mensaje is None:
            return None
        nuevos = ConversationStore(conn).ingest([mensaje])
        return mensaje if nuevos else None

    def importar(self, conn: sqlite3.Connection, desde: datetime, limite_chats: int = 100) -> int:
        """Trae el historial de los chats con personas desde una fecha. Idempotente."""
        store = ConversationStore(conn)
        nuevos = 0
        for chat in self.cliente.chats(limite_chats):
            jid = chat["id"] if isinstance(chat["id"], str) else chat["id"].get("_serialized", "")
            if not es_persona(jid):
                continue
            mensajes = [m for p in self.cliente.mensajes(jid, desde) if (m := ingresar(self.cliente, p))]
            nuevos += store.ingest(mensajes)
        return nuevos

    def transcribir(self, conn: sqlite3.Connection) -> ReporteAudio | None:
        """Los audios pendientes, si Whisper esta instalado. Si no, quedan en espera."""
        if importlib.util.find_spec("faster_whisper") is None:
            return None
        if self._transcriptor is None:
            from estiba.adapters.whisper import FasterWhisperTranscriber

            self._transcriptor = FasterWhisperTranscriber()
        store = ConversationStore(conn)
        if not store.pending_audio():
            return None
        return AudioPipeline(store, self._transcriptor).run()

    # -- salida ------------------------------------------------------------

    def enviar(self, conn: sqlite3.Connection, draft_id: int, ahora: datetime) -> Envio:
        """Manda un borrador ya aprobado. Nunca uno pendiente ni descartado."""
        repo = DraftRepository(conn)
        b = repo.get(draft_id)
        if b is None or b["status"] != "aprobado":
            raise ValueError(f"el borrador {draft_id} no esta aprobado: no se manda")
        if b["sent_at"]:
            return Envio(True, b["sent_message_id"])
        try:
            respuesta = self.cliente.enviar_texto(b["chat_id"], b["final_text"])
        except ErrorWaha as e:
            repo.mark_send_error(draft_id, str(e))
            return Envio(False, error=str(e))
        repo.mark_sent(draft_id, respuesta.get("id"), ahora)
        # El mensaje enviado entra al hilo ya, sin esperar el webhook.
        if (m := normalizar(respuesta)) is not None:
            ConversationStore(conn).ingest([m])
        return Envio(True, respuesta.get("id"))
