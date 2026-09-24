"""WhatsApp de verdad, por WAHA (WhatsApp HTTP API) corriendo en Docker.

Tres cosas:

- `WahaClient`: la API de WAHA que usa Estiba (sesion, QR, enviar texto,
  bajar audios, historial, traducir @lid a telefono). Verificada contra
  WAHA 2026.8.2.
- `normalizar`: de un mensaje de WAHA a un `Message` del nucleo. Lo que no
  es una charla con un cliente (grupos, estados, canales) se descarta.
- `firma_valida`: los webhooks vienen firmados con HMAC-SHA512 del cuerpo.

El nucleo no sabe nada de esto: le llegan `Message` como le llegaban de los
fixtures.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from estiba.core.models import Message, MessageKind

log = logging.getLogger(__name__)

#: Donde se guardan los audios que se bajan de WAHA. No se versiona.
MEDIA_DIR = Path("data/audios")

#: Eventos que Estiba necesita: todo mensaje (tambien los que mandas vos desde
#: el celular, para que el hilo este completo) y los cambios de sesion.
EVENTOS = ["message.any", "session.status"]


@dataclass(frozen=True)
class ConfigWaha:
    url: str
    api_key: str | None = None
    session: str = "default"
    #: Clave para firmar los webhooks (HMAC). Si no hay, solo protege el secreto de la URL.
    hmac_key: str | None = None
    #: Como llega WAHA (desde Docker) a Estiba, para el webhook.
    url_estiba: str = "http://host.docker.internal:8000"

    @classmethod
    def desde_entorno(cls) -> ConfigWaha | None:
        url = os.environ.get("ESTIBA_WAHA_URL")
        if not url:
            return None
        return cls(
            url=url.rstrip("/"),
            api_key=os.environ.get("ESTIBA_WAHA_API_KEY"),
            session=os.environ.get("ESTIBA_WAHA_SESION", "default"),
            hmac_key=os.environ.get("ESTIBA_WAHA_HMAC"),
            url_estiba=os.environ.get("ESTIBA_URL_PARA_WAHA", "http://host.docker.internal:8000").rstrip("/"),
        )


class ErrorWaha(RuntimeError):
    pass


# --------------------------------------------------------------------------
# Identidades: WhatsApp manda "549...@c.us" o, cada vez mas, "...@lid"
# --------------------------------------------------------------------------

_PERSONA = re.compile(r"^(\d+)@(?:c\.us|s\.whatsapp\.net)$")
_LID = re.compile(r"^(\d+)@lid$")


def es_persona(jid: str) -> bool:
    return bool(_PERSONA.match(jid) or _LID.match(jid))


def telefono_de(jid: str) -> str | None:
    """"5491133334444@c.us" -> "5491133334444". Un @lid no es un telefono: None."""
    m = _PERSONA.match(jid)
    return m.group(1) if m else None


def jid_de(chat_id: str) -> str:
    """El inverso, para mandar: "5491133334444" -> "5491133334444@c.us"."""
    return chat_id if "@" in chat_id else f"{chat_id}@c.us"


# --------------------------------------------------------------------------
# Normalizar
# --------------------------------------------------------------------------


def normalizar(payload: dict, resolver_lid=None, media_path: str | None = None) -> Message | None:
    """Un WAMessage de WAHA a un Message. None si no es una charla con una persona.

    - Texto: tal cual.
    - Audio: con el archivo ya bajado (`media_path`), para el AudioPipeline.
    - Foto o archivo con epigrafe: cuenta el epigrafe como texto.
    - Lo demas (sticker, ubicacion, audio que no se pudo bajar): queda en el
      hilo como "otro", para que se vea que el cliente mando algo.

    `resolver_lid(jid) -> telefono | None` traduce los @lid; si no se puede,
    el @lid mismo queda como chat_id: es estable, solo que no es un telefono.
    """
    desde_mi = bool(payload.get("fromMe"))
    # Cada motor pone el chat en un lado distinto cuando el mensaje es propio:
    # WEBJS manda "from" = tu numero y "to" = el cliente; GOWS deja "to" vacio
    # y pone el chat en "from". Buscarlo solo en "to" hacia que todo lo que
    # escribe el fletero se descartara en silencio, y Estiba quedaba ciega a
    # la mitad de la conversacion: repreguntaba lo que el ya habia preguntado.
    jid = (payload.get("to") if desde_mi else payload.get("from")) or payload.get("from")
    if not jid or not es_persona(jid):
        return None
    chat_id = telefono_de(jid)
    if chat_id is None and resolver_lid is not None:
        chat_id = resolver_lid(jid)

    texto = payload.get("body") or None
    mime = ((payload.get("media") or {}).get("mimetype") or "").lower()
    if not payload.get("hasMedia"):
        kind = MessageKind.TEXT if texto else MessageKind.OTHER
    elif mime.startswith("audio/") and media_path:
        kind, texto = MessageKind.AUDIO, None
    elif texto:
        kind = MessageKind.TEXT
    else:
        kind = MessageKind.OTHER

    return Message(
        id=str(payload["id"]),
        chat_id=chat_id or jid,
        from_customer=not desde_mi,
        sent_at=datetime.fromtimestamp(payload["timestamp"]),
        kind=kind,
        text=texto,
        media_path=media_path if kind is MessageKind.AUDIO else None,
        source="waha",
    )


def firma_valida(cuerpo: bytes, firma: str | None, clave: str) -> bool:
    if not firma:
        return False
    esperada = hmac.new(clave.encode(), cuerpo, hashlib.sha512).hexdigest()
    return hmac.compare_digest(esperada, firma.strip().lower())


# --------------------------------------------------------------------------
# Cliente
# --------------------------------------------------------------------------


class WahaClient:
    def __init__(self, config: ConfigWaha, transport: httpx.BaseTransport | None = None) -> None:
        self.config = config
        headers = {"X-Api-Key": config.api_key} if config.api_key else {}
        self.http = httpx.Client(base_url=config.url, headers=headers, timeout=20, transport=transport)
        self._lids: dict[str, str | None] = {}

    def _pedir(self, metodo: str, ruta: str, **kw) -> httpx.Response:
        try:
            r = self.http.request(metodo, ruta, **kw)
        except httpx.HTTPError as e:
            raise ErrorWaha(f"no se pudo hablar con WAHA en {self.config.url}: {e}") from e
        if r.status_code == 401:
            raise ErrorWaha("WAHA rechazo la API key (ESTIBA_WAHA_API_KEY)")
        return r

    # -- sesion ------------------------------------------------------------

    def sesion(self) -> dict | None:
        """La sesion, o None si todavia no existe."""
        r = self._pedir("GET", f"/api/sessions/{self.config.session}")
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()

    def configuracion_sesion(self, secreto_webhook: str) -> dict:
        webhook = {
            "url": f"{self.config.url_estiba}/webhooks/waha/{secreto_webhook}",
            "events": EVENTOS,
        }
        if self.config.hmac_key:
            webhook["hmac"] = {"key": self.config.hmac_key}
        return {
            # Grupos, estados y canales no son clientes: ni llegan.
            "ignore": {"status": True, "groups": True, "channels": True, "broadcast": True},
            "webhooks": [webhook],
        }

    def conectar(self, secreto_webhook: str) -> dict:
        """Crea la sesion (o la actualiza) con el webhook de Estiba y la arranca."""
        config = self.configuracion_sesion(secreto_webhook)
        if self.sesion() is None:
            r = self._pedir("POST", "/api/sessions",
                            json={"name": self.config.session, "start": True, "config": config})
        else:
            r = self._pedir("PUT", f"/api/sessions/{self.config.session}", json={"config": config})
            r.raise_for_status()
            estado = r.json().get("status")
            if estado in ("STOPPED", "FAILED"):
                r = self._pedir("POST", f"/api/sessions/{self.config.session}/start")
        r.raise_for_status()
        return r.json()

    def qr(self) -> bytes:
        r = self._pedir("GET", f"/api/{self.config.session}/auth/qr", params={"format": "image"},
                        headers={"Accept": "image/png"})
        if r.status_code != 200:
            raise ErrorWaha(f"no hay QR disponible ({r.status_code})")
        return r.content

    def desconectar(self) -> None:
        self._pedir("POST", f"/api/sessions/{self.config.session}/logout").raise_for_status()

    # -- mensajes ----------------------------------------------------------

    def enviar_texto(self, chat_id: str, texto: str) -> dict:
        """Devuelve el WAMessage enviado."""
        r = self._pedir("POST", "/api/sendText",
                        json={"session": self.config.session, "chatId": jid_de(chat_id), "text": texto})
        if r.status_code >= 400:
            try:
                detalle = r.json().get("error") or r.text
            except ValueError:
                detalle = r.text
            raise ErrorWaha(f"WhatsApp no lo mando: {str(detalle)[:200]}")
        return r.json()

    def bajar(self, url: str, destino: Path) -> Path:
        """Baja un archivo de WAHA (usa la API key) y lo guarda.

        WAHA arma la URL con su WAHA_BASE_URL, que puede no ser como Estiba
        lo alcanza: se usa solo la ruta, contra la URL configurada.
        """
        partes = urlsplit(url)
        r = self._pedir("GET", partes.path + (f"?{partes.query}" if partes.query else ""))
        r.raise_for_status()
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(r.content)
        return destino

    def telefono_de_lid(self, jid: str) -> str | None:
        if jid not in self._lids:
            r = self._pedir("GET", f"/api/{self.config.session}/lids/{jid}")
            pn = r.json().get("pn") if r.status_code == 200 else None
            self._lids[jid] = telefono_de(pn) if pn else None
        return self._lids[jid]

    def chats(self, limite: int = 100) -> list[dict]:
        r = self._pedir("GET", f"/api/{self.config.session}/chats",
                        params={"sortBy": "conversationTimestamp", "sortOrder": "desc", "limit": limite})
        r.raise_for_status()
        return r.json()

    def mensajes(self, chat_jid: str, desde: datetime, limite: int = 200) -> list[dict]:
        r = self._pedir(
            "GET", f"/api/{self.config.session}/chats/{chat_jid}/messages",
            params={"limit": limite, "downloadMedia": "true", "sortBy": "timestamp", "sortOrder": "asc",
                    "filter.timestamp.gte": int(desde.timestamp())},
            # Con downloadMedia, WAHA baja fotos y videos del chat antes de
            # contestar: en un chat cargado tarda bastante mas que 20 s.
            timeout=180,
        )
        r.raise_for_status()
        return r.json()


def ruta_audio(message_id: str, mimetype: str | None) -> Path:
    extension = {"audio/ogg": ".ogg", "audio/mpeg": ".mp3", "audio/mp4": ".m4a"}.get(
        (mimetype or "").split(";")[0].strip(), ".ogg"
    )
    return MEDIA_DIR / (re.sub(r"[^A-Za-z0-9_.-]", "_", message_id) + extension)


def ingresar(cliente: WahaClient, payload: dict) -> Message | None:
    """De un WAMessage a un Message listo para guardar, bajando el audio si hay."""
    media_path = None
    if payload.get("hasMedia"):
        media = payload.get("media") or {}
        url = media.get("url") or payload.get("mediaUrl")
        if url and (media.get("mimetype") or "").lower().startswith("audio/"):
            destino = ruta_audio(str(payload["id"]), media.get("mimetype"))
            try:
                media_path = str(cliente.bajar(url, destino))
            except (httpx.HTTPError, OSError) as e:
                # WhatsApp caduca los archivos viejos y WAHA contesta 404. Perder
                # el audio es malo; perder el mensaje entero es peor: sin esto el
                # webhook devuelve 500 y WAHA lo descarta tras reintentar.
                log.warning("no se pudo bajar el audio de %s: %s", payload.get("id"), e)
    return normalizar(payload, cliente.telefono_de_lid, media_path)
