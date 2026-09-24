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


class DraftRepository:
    """La bandeja. Un borrador por flete y por ultimo mensaje, a lo sumo."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def sync(self, job_id: str, chat_id: str, propuesta, ahora: datetime) -> None:
        """Deja la bandeja de este flete al dia con lo que propone el redactor.

        - Si la propuesta ya existe (mismo tipo y mismo ultimo mensaje), se
          respeta lo que se haya decidido sobre ella.
        - Todo borrador pendiente que no sea esa propuesta queda reemplazado:
          llego un mensaje nuevo o ya no hace falta decir nada.
        """
        with self.conn:
            vigente = None
            if propuesta is not None:
                vigente = self.conn.execute(
                    "SELECT id, status FROM drafts WHERE job_id = ? AND kind = ? AND basis_message_id = ?",
                    (job_id, propuesta.kind, propuesta.basis_message_id),
                ).fetchone()
            self.conn.execute(
                "UPDATE drafts SET status = 'reemplazado', decided_at = ? "
                "WHERE job_id = ? AND status = 'pendiente' AND id IS NOT ?",
                (_a_texto(ahora), job_id, vigente["id"] if vigente else None),
            )
            if propuesta is None:
                return
            if vigente is None:
                self.conn.execute(
                    "INSERT INTO drafts (job_id, chat_id, kind, reason, text, basis_message_id, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (job_id, chat_id, propuesta.kind, propuesta.reason, propuesta.text,
                     propuesta.basis_message_id, _a_texto(ahora)),
                )
            elif vigente["status"] == "pendiente":
                self.conn.execute(
                    "UPDATE drafts SET reason = ?, text = ? WHERE id = ?",
                    (propuesta.reason, propuesta.text, vigente["id"]),
                )

    def pending(self) -> list[sqlite3.Row]:
        return list(
            self.conn.execute("SELECT * FROM drafts WHERE status = 'pendiente' ORDER BY created_at, id")
        )

    def mark_sent(self, draft_id: int, message_id: str | None, ahora: datetime) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE drafts SET sent_at = ?, sent_message_id = ?, send_error = NULL WHERE id = ?",
                (_a_texto(ahora), message_id, draft_id),
            )

    def mark_send_error(self, draft_id: int, error: str) -> None:
        with self.conn:
            self.conn.execute("UPDATE drafts SET send_error = ? WHERE id = ?", (error, draft_id))

    def unsent(self) -> list[sqlite3.Row]:
        """Aprobados que todavia no salieron: fallo el envio y hay que reintentar."""
        return list(self.conn.execute(
            "SELECT * FROM drafts WHERE status = 'aprobado' AND sent_at IS NULL "
            "AND send_error IS NOT NULL ORDER BY decided_at"
        ))

    def get(self, draft_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM drafts WHERE id = ?", (draft_id,)).fetchone()

    def for_job(self, job_id: str) -> list[sqlite3.Row]:
        return list(
            self.conn.execute("SELECT * FROM drafts WHERE job_id = ? ORDER BY id DESC", (job_id,))
        )

    def decide(self, draft_id: int, aprobado: bool, texto: str | None, ahora: datetime) -> sqlite3.Row:
        """Aprobar o descartar. Solo un borrador pendiente se puede decidir."""
        borrador = self.get(draft_id)
        if borrador is None:
            raise KeyError(f"no existe el borrador {draft_id}")
        if borrador["status"] != "pendiente":
            raise ValueError(f"el borrador {draft_id} ya esta {borrador['status']}")
        if aprobado and not (texto or "").strip():
            raise ValueError("no se puede aprobar un mensaje vacio")
        with self.conn:
            self.conn.execute(
                "UPDATE drafts SET status = ?, final_text = ?, decided_at = ? WHERE id = ?",
                ("aprobado" if aprobado else "descartado",
                 texto.strip() if aprobado else None, _a_texto(ahora), draft_id),
            )
        return self.get(draft_id)


class ActionRepository:
    """Lo que marco una persona o el calendario sobre cada flete."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def add(self, job_id: str, tipo: str, at: datetime, actor: str = "humano",
            nota: str | None = None, monto: Decimal | None = None) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO job_actions (job_id, action, at, actor, note, monto) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (job_id, tipo, _a_texto(at), actor, nota, str(monto) if monto is not None else None),
            )

    def for_job(self, job_id: str) -> list:
        from estiba.jobs.engine import Accion

        filas = self.conn.execute(
            "SELECT * FROM job_actions WHERE job_id = ? ORDER BY at, id", (job_id,)
        )
        return [
            Accion(_a_fecha(f["at"]), f["action"], f["actor"], f["note"],
                   Decimal(f["monto"]) if f["monto"] else None)
            for f in filas
        ]

    def has(self, job_id: str, tipo: str) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM job_actions WHERE job_id = ? AND action = ?", (job_id, tipo)
        ).fetchone() is not None


class CRMRepository:
    """Contactos, leads y su historia de etapas, derivados de los fletes."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def save(self, lead) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO contacts (id, chat_id, first_seen) VALUES (?, ?, ?) "
                "ON CONFLICT (id) DO UPDATE SET first_seen = min(first_seen, excluded.first_seen)",
                (lead.chat_id, lead.chat_id, _a_texto(lead.creado)),
            )
            self.conn.execute(
                "INSERT INTO leads (id, contact_id, conversation_id, stage, lost_reason, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (id) DO UPDATE SET stage = excluded.stage, "
                "lost_reason = excluded.lost_reason, created_at = excluded.created_at",
                (lead.job_id, lead.chat_id, lead.chat_id, lead.etapa.value, lead.motivo_perdida,
                 _a_texto(lead.creado)),
            )
            self.conn.execute("DELETE FROM lead_stage_history WHERE lead_id = ?", (lead.job_id,))
            previa = None
            for paso in lead.historia:
                self.conn.execute(
                    "INSERT INTO lead_stage_history (lead_id, from_stage, to_stage, at, reason) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (lead.job_id, previa, paso.etapa.value, _a_texto(paso.at), paso.motivo),
                )
                previa = paso.etapa.value

    def name(self, chat_id: str) -> str | None:
        """El nombre a mostrar: manda el que puso el fletero, si puso uno."""
        fila = self.conn.execute(
            "SELECT display_name, wa_name FROM contacts WHERE id = ?", (chat_id,)
        ).fetchone()
        return (fila["display_name"] or fila["wa_name"]) if fila else None

    def names(self) -> dict[str, str]:
        filas = self.conn.execute(
            "SELECT id, display_name, wa_name FROM contacts "
            "WHERE display_name IS NOT NULL OR wa_name IS NOT NULL"
        )
        return {f["id"]: f["display_name"] or f["wa_name"] for f in filas}

    def set_wa_name(self, chat_id: str, nombre: str | None, ahora: datetime) -> None:
        """El nombre que viene de WhatsApp. Crea el contacto si no existia."""
        nombre = (nombre or "").strip() or None
        if nombre is None:
            return
        with self.conn:
            self.conn.execute(
                "INSERT INTO contacts (id, chat_id, first_seen, wa_name) VALUES (?, ?, ?, ?) "
                "ON CONFLICT (id) DO UPDATE SET wa_name = excluded.wa_name",
                (chat_id, chat_id, _a_texto(ahora), nombre),
            )

    def set_name(self, chat_id: str, nombre: str | None) -> None:
        nombre = (nombre or "").strip() or None
        with self.conn:
            cur = self.conn.execute("UPDATE contacts SET display_name = ? WHERE id = ?", (nombre, chat_id))
        if cur.rowcount == 0:
            raise KeyError(f"no existe el contacto {chat_id}")


class ExclusionRepository:
    """Chats marcados 'no es cliente': charla personal, spam, numero equivocado.

    No borra mensajes. `sincronizar()` salta estos chats, y asi desaparecen
    de la bandeja, los fletes y el CRM sin perder el historial.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def excluir(self, chat_id: str, motivo: str | None, ahora: datetime) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO excluded_chats (chat_id, motivo, at) VALUES (?, ?, ?) "
                "ON CONFLICT (chat_id) DO UPDATE SET motivo = excluded.motivo, at = excluded.at",
                (chat_id, (motivo or "").strip() or None, _a_texto(ahora)),
            )

    def incluir(self, chat_id: str) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM excluded_chats WHERE chat_id = ?", (chat_id,))

    def excluidos(self) -> set[str]:
        return {f["chat_id"] for f in self.conn.execute("SELECT chat_id FROM excluded_chats")}


class ClaseRepository:
    """Que es cada conversacion, y quien lo decidio.

    Una correccion humana no se pisa nunca: si el fletero dijo que esto es
    un cliente, Estiba no vuelve a opinar por mas que las reglas cambien.
    Guarda ademas lo que Estiba habia inferido, para poder mejorar despues.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def guardar_inferida(self, chat_id: str, clase: str, porque: str, ahora: datetime) -> None:
        """Lo que dedujo Estiba. No toca lo que ya decidio una persona."""
        with self.conn:
            self.conn.execute(
                "INSERT INTO chat_clase (chat_id, clase, porque, quien, at) "
                "VALUES (?, ?, ?, 'estiba', ?) "
                "ON CONFLICT (chat_id) DO UPDATE SET clase = excluded.clase, "
                "porque = excluded.porque, at = excluded.at WHERE chat_clase.quien = 'estiba'",
                (chat_id, clase, porque, _a_texto(ahora)),
            )

    def corregir(self, chat_id: str, clase: str, ahora: datetime) -> None:
        """Lo que dijo una persona. Se queda con lo que Estiba habia dicho."""
        previa = self.conn.execute(
            "SELECT clase FROM chat_clase WHERE chat_id = ?", (chat_id,)
        ).fetchone()
        with self.conn:
            self.conn.execute(
                "INSERT INTO chat_clase (chat_id, clase, porque, quien, clase_previa, at) "
                "VALUES (?, ?, 'lo dijiste vos', 'humano', ?, ?) "
                "ON CONFLICT (chat_id) DO UPDATE SET clase = excluded.clase, "
                "porque = excluded.porque, quien = 'humano', "
                "clase_previa = excluded.clase_previa, at = excluded.at",
                (chat_id, clase, previa["clase"] if previa else None, _a_texto(ahora)),
            )

    def de(self, chat_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM chat_clase WHERE chat_id = ?", (chat_id,)
        ).fetchone()

    def todas(self) -> dict[str, sqlite3.Row]:
        return {f["chat_id"]: f for f in self.conn.execute("SELECT * FROM chat_clase")}

    def borrar_chat(self, chat_id: str) -> int:
        """Borra una conversacion entera: mensajes, transcripciones y clase.

        Solo para el spam. Es lo unico de Estiba que borra de verdad, y por
        eso lo pide una persona a mano: el resto del sistema nunca toca un
        mensaje una vez guardado.
        """
        mensajes = "(SELECT id FROM messages WHERE chat_id = ?)"
        jobs = "(SELECT id FROM jobs WHERE conversation_id = ?)"
        with self.conn:
            # En orden, de lo que depende a lo que sostiene: si no, saltan
            # las claves foraneas.
            self.conn.execute(f"DELETE FROM transcripts WHERE message_id IN {mensajes}", (chat_id,))
            self.conn.execute("DELETE FROM drafts WHERE chat_id = ?", (chat_id,))
            self.conn.execute(f"DELETE FROM job_slots WHERE job_id IN {jobs}", (chat_id,))
            self.conn.execute(f"DELETE FROM job_events WHERE job_id IN {jobs}", (chat_id,))
            self.conn.execute(f"DELETE FROM job_actions WHERE job_id IN {jobs}", (chat_id,))
            self.conn.execute(
                "DELETE FROM lead_stage_history WHERE lead_id IN "
                "(SELECT id FROM leads WHERE contact_id = ?)", (chat_id,))
            self.conn.execute("DELETE FROM leads WHERE contact_id = ?", (chat_id,))
            self.conn.execute("DELETE FROM calendar_events WHERE job_id IN " + jobs, (chat_id,))
            self.conn.execute("DELETE FROM jobs WHERE conversation_id = ?", (chat_id,))
            self.conn.execute("DELETE FROM contacts WHERE id = ?", (chat_id,))
            cur = self.conn.execute("DELETE FROM messages WHERE chat_id = ?", (chat_id,))
            self.conn.execute("DELETE FROM chat_clase WHERE chat_id = ?", (chat_id,))
        return cur.rowcount

    def correcciones(self) -> list[sqlite3.Row]:
        """Donde Estiba se equivoco: para mejorar las reglas mas adelante."""
        return list(self.conn.execute(
            "SELECT * FROM chat_clase WHERE quien = 'humano' AND clase_previa IS NOT NULL "
            "AND clase_previa != clase ORDER BY at DESC"
        ))


class SnoozeRepository:
    """Conversaciones pospuestas: vuelven solas cuando se cumple la fecha.

    Posponer no cierra ni archiva. Mientras dura, la conversacion no esta
    entre las urgentes; cumplida la fecha reaparece sin que nadie haga nada.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def posponer(self, chat_id: str, hasta: datetime, motivo: str | None, ahora: datetime) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO snoozed_chats (chat_id, hasta, motivo, at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT (chat_id) DO UPDATE SET hasta = excluded.hasta, "
                "motivo = excluded.motivo, at = excluded.at",
                (chat_id, _a_texto(hasta), (motivo or "").strip() or None, _a_texto(ahora)),
            )

    def despertar(self, chat_id: str) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM snoozed_chats WHERE chat_id = ?", (chat_id,))

    def vigentes(self, ahora: datetime) -> dict[str, datetime]:
        """chat_id -> cuando vuelve. Lo ya cumplido no cuenta: ya desperto."""
        filas = self.conn.execute(
            "SELECT chat_id, hasta FROM snoozed_chats WHERE hasta > ?", (_a_texto(ahora),)
        )
        return {f["chat_id"]: datetime.fromisoformat(f["hasta"]) for f in filas}


class SettingsRepository:
    """Ajustes que genera el sistema, como los secretos de las URLs."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def secreto(self, clave: str) -> str:
        """Un secreto al azar que se crea la primera vez y despues no cambia."""
        import secrets

        fila = self.conn.execute("SELECT value FROM settings WHERE key = ?", (clave,)).fetchone()
        if fila:
            return fila["value"]
        nuevo = secrets.token_urlsafe(24)
        with self.conn:
            self.conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (clave, nuevo))
        return self.conn.execute("SELECT value FROM settings WHERE key = ?", (clave,)).fetchone()["value"]
