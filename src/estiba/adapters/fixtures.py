"""Fuente de mensajes desde archivos JSON.

Es el adaptador que usamos hasta la Fase 7. Implementa el mismo puerto
`MessageSource` que va a implementar WAHA, asi que el dia que conectemos
WhatsApp real el nucleo no cambia una linea.

Formato de un fixture:

    {
      "chat_id": "5491133334444",
      "descripcion": "para que sirve esta conversacion como caso de prueba",
      "mensajes": [
        {"de": "cliente", "hora": "2026-09-15T10:02:00", "texto": "hola"},
        {"de": "vos",     "hora": "2026-09-15T10:05:00", "texto": "buenas"},
        {"de": "cliente", "hora": "2026-09-15T10:07:00",
         "audio": "fixtures/audios/nota1.ogg",
         "transcripcion_esperada": "lo que de verdad dice el audio"}
      ]
    }

`transcripcion_esperada` no se usa en la Fase 1: queda ahi para medir la tasa
de error de Whisper en la Fase 2 contra tus audios reales.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from estiba.core.models import Message, MessageKind

FIXTURES_DIR = Path("fixtures/conversaciones")


class FixtureMessageSource:
    """Lee conversaciones de un directorio de JSON."""

    def __init__(self, directorio: Path | str = FIXTURES_DIR) -> None:
        self.directorio = Path(directorio)
        self._entregados: set[str] = set()

    def poll(self) -> Iterator[Message]:
        """Mensajes que todavia no entrego esta instancia."""
        for mensaje in self.read_all():
            if mensaje.id in self._entregados:
                continue
            self._entregados.add(mensaje.id)
            yield mensaje

    def read_all(self) -> Iterator[Message]:
        if not self.directorio.is_dir():
            raise FileNotFoundError(f"no existe el directorio {self.directorio}")
        for archivo in sorted(self.directorio.glob("*.json")):
            yield from self.read_file(archivo)

    @staticmethod
    def read_file(archivo: Path) -> Iterator[Message]:
        datos = json.loads(archivo.read_text(encoding="utf-8"))
        chat_id = datos["chat_id"]
        for i, crudo in enumerate(datos["mensajes"]):
            yield _a_mensaje(chat_id, i, crudo, archivo.stem)

    @staticmethod
    def expected_transcripts(archivo: Path) -> dict[str, str]:
        """Lo que de verdad dice cada audio, para medir el error en Fase 2."""
        datos = json.loads(archivo.read_text(encoding="utf-8"))
        chat_id = datos["chat_id"]
        esperadas = {}
        for i, crudo in enumerate(datos["mensajes"]):
            if "transcripcion_esperada" in crudo:
                esperadas[_id_de(chat_id, i)] = crudo["transcripcion_esperada"]
        return esperadas


def _id_de(chat_id: str, indice: int) -> str:
    """Id estable: reprocesar un fixture no duplica nada."""
    return f"{chat_id}:{indice:03d}"


def _a_mensaje(chat_id: str, indice: int, crudo: dict, origen: str) -> Message:
    quien = crudo["de"]
    if quien not in ("cliente", "vos"):
        raise ValueError(f"{origen}[{indice}]: 'de' debe ser 'cliente' o 'vos'")

    if "audio" in crudo:
        kind = MessageKind.AUDIO
    elif "texto" in crudo:
        kind = MessageKind.TEXT
    else:
        raise ValueError(f"{origen}[{indice}]: necesita 'texto' o 'audio'")

    return Message(
        id=_id_de(chat_id, indice),
        chat_id=chat_id,
        from_customer=(quien == "cliente"),
        sent_at=datetime.fromisoformat(crudo["hora"]),
        kind=kind,
        text=crudo.get("texto"),
        media_path=crudo.get("audio"),
        source="fixture",
    )
