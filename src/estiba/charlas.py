"""El texto de las charlas DE NEGOCIO, para que el asistente de Horizon pueda leer el contexto.

Decision de Lucas (18-nov-2026): el reporte de actividad no alcanza para saber si es el mismo cliente, si son dos
fletes o que pedia un trabajo; hace falta el texto. Reglas:
  * solo charlas de negocio (lead, cliente, dudosa); NUNCA privadas, spam ni charlas sin clasificar;
  * los audios van con su transcripcion, marcada como tal (puede tener errores);
  * de a poco: solo lo nuevo desde el ultimo envio, y lo que se transcribio despues de mandado;
  * ventana de 60 dias (Horizon borra lo mas viejo solo);
  * si una charla pasa a privada o spam, se avisa en `fuera` para que Horizon la borre.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

NEGOCIO = ("lead", "cliente", "dudosa")
FUERA = ("privado", "spam")
VENTANA = timedelta(days=60)
TANDA = 400
MARCA = "horizon.charlas"


def _con_zona(texto: str) -> str | None:
    try:
        d = datetime.fromisoformat(texto)
    except (TypeError, ValueError):
        return None
    return d.astimezone().isoformat(timespec="seconds")


def pendientes(conn: sqlite3.Connection, ahora: datetime, marca: dict | None) -> tuple[list[dict], dict]:
    """Los mensajes a mandar y la marca nueva (se guarda solo si Horizon confirmo)."""
    marca = marca or {}
    desde = (ahora.replace(tzinfo=None) if ahora.tzinfo else ahora) - VENTANA
    ingest, trans = marca.get("ingest") or "", marca.get("trans") or ""
    filas = conn.execute(f"""
        SELECT m.id, m.chat_id, c.display_name, m.from_customer, m.sent_at, m.kind, m.text, t.text, m.ingested_at,
               coalesce(t.created_at, '')
          FROM messages m
          JOIN chat_clase k ON k.chat_id = m.chat_id AND k.clase IN ({','.join('?' * len(NEGOCIO))})
          LEFT JOIN contacts c ON c.chat_id = m.chat_id
          LEFT JOIN transcripts t ON t.message_id = m.id
         WHERE m.sent_at >= ? AND (m.ingested_at > ? OR coalesce(t.created_at, '') > ?)
         ORDER BY m.ingested_at, m.id
         LIMIT ?""", (*NEGOCIO, desde.isoformat(timespec="seconds"), ingest, trans, TANDA)).fetchall()
    mensajes = []
    nuevo_ingest, nuevo_trans = ingest, trans
    for mid, chat, nombre, del_cliente, sent_at, kind, texto, transcripcion, ingested_at, trans_at in filas:
        # La marca avanza tambien con lo que se saltea (stickers, sin texto): si no, se volveria a consultar siempre.
        nuevo_ingest = max(nuevo_ingest, ingested_at or "")
        nuevo_trans = max(nuevo_trans, trans_at or "")
        if kind == "audio":
            cuerpo, es_transcripcion = (transcripcion, True) if transcripcion else ("[audio sin transcribir todavía]", False)
        elif kind == "image":
            cuerpo, es_transcripcion = (texto or "[imagen]"), False
        else:
            cuerpo, es_transcripcion = texto, False
        if not cuerpo:
            continue
        at = _con_zona(sent_at)
        if at is None:
            continue
        mensajes.append({"id": mid, "chat": chat, "nombre": nombre, "de": "cliente" if del_cliente else "vos",
                         "at": at, "tipo": kind, "texto": cuerpo[:4000], "transcripcion": es_transcripcion})
    return mensajes, {"ingest": nuevo_ingest, "trans": nuevo_trans}


def fuera(conn: sqlite3.Connection) -> list[str]:
    """Charlas que NO son de negocio: Horizon borra lo que tenga de ellas (por si cambiaron de clase)."""
    return [r[0] for r in conn.execute(
        f"SELECT chat_id FROM chat_clase WHERE clase IN ({','.join('?' * len(FUERA))})", FUERA)]


def audios_sin_transcribir(conn: sqlite3.Connection) -> int:
    return conn.execute("""SELECT count(*) FROM messages m JOIN chat_clase k ON k.chat_id = m.chat_id
                           WHERE m.kind = 'audio' AND k.clase IN ('lead', 'cliente', 'dudosa')
                             AND NOT EXISTS (SELECT 1 FROM transcripts t WHERE t.message_id = m.id)""").fetchone()[0]
