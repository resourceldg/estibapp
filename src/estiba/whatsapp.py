"""WhatsApp por WAHA: recibir, importar historial y enviar lo aprobado.

Enviar es lo unico que le habla al cliente, y solo se llama despues de que
una persona aprobo el borrador. Si WhatsApp falla, el borrador queda
aprobado con el error a la vista, para reintentar o mandarlo a mano.
"""

from __future__ import annotations

import importlib.util
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta

from estiba.adapters.waha import (
    ErrorWaha,
    WahaClient,
    es_persona,
    ingresar,
    normalizar,
    telefono_de,
)
from estiba.audio.pipeline import AudioPipeline, ReporteAudio
from estiba.conversation.store import ConversationStore
from estiba.core.models import Message
from estiba.storage.repositories import CRMRepository, DraftRepository

EVENTOS_DE_MENSAJE = ("message", "message.any")

#: Cuanto antes del ultimo mensaje guardado se vuelve a mirar. Un webhook
#: que estaba viajando cuando se cerro la app, o un reloj que no coincide
#: del todo con el de WAHA, dejarian un hueco justo en el borde.
SOLAPE = timedelta(minutes=15)
#: Mas atras que esto no se recupera solo: para eso esta importar a mano.
TOPE_RECUPERACION = timedelta(days=30)


@dataclass(frozen=True)
class Recuperacion:
    desde: datetime
    chats: int  # con movimiento desde entonces
    nuevos: int  # mensajes que Estiba no tenia


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
            self._guardar_nombre(conn, jid, chat.get("name"))
        return nuevos

    def ponerse_al_dia(self, conn: sqlite3.Connection, ahora: datetime,
                       limite_chats: int = 300) -> Recuperacion | None:
        """Lo que llego mientras Estiba estaba cerrada.

        Los mensajes entran por webhook: si la app no esta escuchando, WAHA
        reintenta un rato y despues los da por entregados, y en Estiba no
        existen. WhatsApp si los tiene. Esto los busca desde el ultimo
        mensaje que se guardo de WAHA, chat por chat, del mas reciente al
        mas viejo, y corta en el primero que no se movio desde entonces.

        None si nunca entro nada de WAHA: sin un ultimo mensaje no hay un
        "desde donde", y traer todo el historial es importar, que se pide.
        """
        ultimo = ConversationStore(conn).ultimo_de("waha")
        if ultimo is None:
            return None
        desde = max(ultimo - SOLAPE, ahora - TOPE_RECUPERACION)
        store = ConversationStore(conn)
        chats = nuevos = 0
        for chat in self.cliente.chats(limite_chats):
            movido = chat.get("conversationTimestamp")
            if movido and datetime.fromtimestamp(movido) < desde:
                break  # vienen ordenados: los que siguen estan mas quietos todavia
            jid = chat["id"] if isinstance(chat["id"], str) else chat["id"].get("_serialized", "")
            if not es_persona(jid):
                continue
            chats += 1
            mensajes = [m for p in self.cliente.mensajes(jid, desde) if (m := ingresar(self.cliente, p))]
            nuevos += store.ingest(mensajes)
            self._guardar_nombre(conn, jid, chat.get("name"))
        return Recuperacion(desde, chats, nuevos)

    def nombres(self, conn: sqlite3.Connection, limite_chats: int = 300) -> int:
        """Trae de WhatsApp como se llama cada contacto. Sin historial ni audios.

        Una lista de telefonos no dice nada: con el nombre se reconoce de un
        vistazo quien escribio.
        """
        traidos = 0
        for chat in self.cliente.chats(limite_chats):
            jid = chat["id"] if isinstance(chat["id"], str) else chat["id"].get("_serialized", "")
            if es_persona(jid):
                traidos += int(self._guardar_nombre(conn, jid, chat.get("name")))
        return traidos

    def _guardar_nombre(self, conn: sqlite3.Connection, jid: str, nombre: str | None) -> bool:
        if not (nombre or "").strip():
            return False
        chat_id = telefono_de(jid) or self.cliente.telefono_de_lid(jid) or jid
        CRMRepository(conn).set_wa_name(chat_id, nombre, datetime.now())
        return True

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
