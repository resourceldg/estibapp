from __future__ import annotations

from datetime import datetime

import pytest

from estiba.conversation.store import ConversationStore
from estiba.core.models import Message, MessageKind
from estiba.storage.db import connect


@pytest.fixture(autouse=True)
def entorno_limpio(monkeypatch):
    """Los tests no leen tu configuracion: ni tu WAHA, ni tu CalDAV, ni tu clave."""
    import os

    for clave in [k for k in os.environ if k.startswith("ESTIBA_")]:
        monkeypatch.delenv(clave)


@pytest.fixture
def conn():
    c = connect(":memory:")
    yield c
    c.close()


@pytest.fixture
def store(conn):
    return ConversationStore(conn)


def mensaje(
    id: str = "chat1:000",
    chat_id: str = "chat1",
    from_customer: bool = True,
    minuto: int = 0,
    texto: str | None = "hola",
    audio: str | None = None,
) -> Message:
    return Message(
        id=id,
        chat_id=chat_id,
        from_customer=from_customer,
        sent_at=datetime(2026, 9, 15, 10, minuto),
        kind=MessageKind.AUDIO if audio else MessageKind.TEXT,
        text=texto if not audio else None,
        media_path=audio,
    )
