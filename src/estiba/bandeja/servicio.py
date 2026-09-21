"""Sincronizar: evaluar cada conversacion, guardar su flete y su borrador.

Es lo que corre al abrir el dashboard o al tocar "actualizar". Idempotente:
correrlo dos veces seguidas no cambia nada.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from estiba.bandeja.redactor import proponer
from estiba.conversation.store import ConversationStore
from estiba.core.models import Conversation
from estiba.jobs.engine import Evaluacion, JobEngine
from estiba.storage.repositories import DraftRepository, JobRepository


def sincronizar(
    conn: sqlite3.Connection, engine: JobEngine, ahora: datetime
) -> dict[str, tuple[Evaluacion, Conversation]]:
    store = ConversationStore(conn)
    jobs = JobRepository(conn)
    borradores = DraftRepository(conn)
    evaluaciones = {}
    for chat_id in store.chat_ids():
        conv = store.get(chat_id)
        if not any(m.sent_at <= ahora for m in conv.messages):
            continue
        ev = engine.evaluar(conv, ahora)
        jobs.save(ev.job, ev.eventos)
        borradores.sync(ev.job.id, chat_id, proponer(ev, conv, ahora), ahora)
        evaluaciones[chat_id] = (ev, conv)
    return evaluaciones
