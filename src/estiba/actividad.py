"""Reporte de actividad de WhatsApp para Horizon: que paso hoy y en la semana, y que necesita atencion.

Viaja a Horizon con cada sincronizacion para que Lucas (y su asistente) puedan "chusmear" sin abrir Estiba.
Respeta el principio de `estiba.horizon`: NO viajan mensajes, audios ni transcripciones. Solo contadores,
quien hablo ultimo y cuando, y el tema armado con los datos del flete (ruta, carga, dia), que ya viajan.
Nunca entran las charlas privadas ni el spam (las viejas «excluidas» son privadas desde la migracion 017).

Inmediato: `Rafaga` decide cuando mandar despues de que entran o salen mensajes (espera unos segundos de
calma para mandar un solo reporte por rafaga, con un tope para no demorar de mas).
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from estiba.extraction.intencion import es_cortesia

FUERA = ("privado", "spam")
MAX_CONVERSACIONES = 30
#: Pasado este tiempo sin respuesta del cliente, una cotizacion "quedo en el aire".
COTIZADO_SIN_RESPUESTA = timedelta(hours=24)
#: Hasta cuándo un «aceptó» o «¿se hizo?» con la charla quieta sigue siendo un pendiente.
PENDIENTE_VIEJO = timedelta(days=30)


def _local(dt: datetime) -> datetime:
    """Los mensajes se guardan en hora local sin zona: se compara igual."""
    return dt.astimezone().replace(tzinfo=None) if dt.tzinfo else dt


def _fecha(texto: str | None) -> datetime | None:
    if not texto:
        return None
    try:
        return _local(datetime.fromisoformat(texto))
    except ValueError:
        return None


def _con_zona(texto: str | None) -> str | None:
    d = _fecha(texto)
    return d.astimezone().isoformat(timespec="seconds") if d else None


def _final(chat_id: str) -> str | None:
    digitos = "".join(c for c in chat_id if c.isdigit())
    return digitos[-4:] if len(digitos) >= 6 else None


def _tema(f) -> str | None:
    """El tema sale de los datos del flete, nunca del texto de la charla."""
    if f is None:
        return None
    partes = []
    if f.origen or f.destino:
        partes.append(f"Flete {f.origen or '¿?'} → {f.destino or '¿?'}")
    if getattr(f, "cosas", None):
        partes.append(str(f.cosas)[:60])
    if getattr(f, "dia_semana", None):
        partes.append(f.dia_semana + (f" {f.hora}" if getattr(f, "hora", None) else ""))
    return " · ".join(partes) or "Flete sin datos todavía"


def _horas(desde: datetime, hasta: datetime) -> float:
    return round(max(0.0, (hasta - desde).total_seconds() / 3600), 1)


def armar_reporte(conn: sqlite3.Connection, ahora: datetime, fletes_por_chat: dict | None = None) -> dict:
    """El reporte, listo para mandar. `fletes_por_chat`: chat_id -> Flete (el mas reciente abierto)."""
    ahora = _local(ahora)
    fletes_por_chat = fletes_por_chat or {}
    hoy = ahora.date()
    desde_semana = datetime.combine(hoy - timedelta(days=6), datetime.min.time())
    fuera = {r[0] for r in conn.execute(
        f"SELECT chat_id FROM chat_clase WHERE clase IN ({','.join('?' * len(FUERA))})", FUERA)}
    clases = dict(conn.execute("SELECT chat_id, clase FROM chat_clase"))
    nombres = dict(conn.execute("SELECT chat_id, display_name FROM contacts WHERE display_name IS NOT NULL"))
    primeros = dict(conn.execute("SELECT chat_id, first_seen FROM contacts"))

    filas = conn.execute(
        """SELECT m.chat_id, m.from_customer, m.sent_at, coalesce(m.text, t.text) FROM messages m
           LEFT JOIN transcripts t ON t.message_id = m.id WHERE m.sent_at >= ? ORDER BY m.chat_id, m.sent_at""",
        (desde_semana.isoformat(timespec="seconds"),)).fetchall()
    por_chat: dict[str, list[tuple[bool, datetime]]] = {}
    ultimo_texto: dict[str, str | None] = {}
    for chat_id, del_cliente, sent_at, texto in filas:
        if chat_id in fuera or chat_id in ("0", ""):
            continue
        cuando = _fecha(sent_at)
        if cuando is None or cuando > ahora + timedelta(minutes=5):
            continue
        por_chat.setdefault(chat_id, []).append((bool(del_cliente), cuando))
        ultimo_texto[chat_id] = texto

    def totales(dia_desde: date) -> dict:
        personas, recibidos, enviados = set(), 0, 0
        for chat_id, msgs in por_chat.items():
            for del_cliente, cuando in msgs:
                if cuando.date() < dia_desde:
                    continue
                personas.add(chat_id)
                if del_cliente:
                    recibidos += 1
                else:
                    enviados += 1
        nuevos = sum(1 for chat_id in por_chat
                     if (p := _fecha(primeros.get(chat_id))) and p.date() >= dia_desde)
        return {"personas": len(personas), "recibidos": recibidos, "enviados": enviados, "nuevos": nuevos}

    conversaciones = []
    for chat_id, msgs in por_chat.items():
        ultimo_del_cliente, ultimo_at = msgs[-1]
        f = fletes_por_chat.get(chat_id)
        etapa = getattr(f, "etapa", None)
        # Los avisos de Estiba sobre el flete (antes se quedaban adentro de Estiba): «aceptó y falta confirmarlo»,
        # «¿se hizo el flete?». Sin el texto de la charla: son frases de Estiba.
        alertas = [str(x)[:140] for x in (getattr(f, "alertas", None) or [])][:4]
        # Un «Genial gracias!!» despues de cotizar no pide respuesta (misma regla que el JobEngine).
        cortesia = ultimo_del_cliente and etapa == "cotizado" and es_cortesia(ultimo_texto.get(chat_id))
        espera = ultimo_del_cliente and not cortesia
        if any("¿se hizo el flete?" in a for a in alertas):
            estado = "¿se hizo el flete?"
        elif any("aceptó" in a and "falta confirmarlo" in a for a in alertas):
            estado = "aceptó: falta confirmar"
        elif espera:
            estado = "esperando tu respuesta"
        elif etapa == "cotizado":
            estado = "cotizado, esperando al cliente"
        elif etapa == "cerrado-falta-hacer":
            estado = "agendado"
        elif etapa in ("vendido", "perdido"):
            estado = etapa
        else:
            estado = "esperando al cliente"
        conversaciones.append({
            "nombre": nombres.get(chat_id) or getattr(f, "nombre", None) or "Sin nombre",
            "chat": chat_id,
            "final": _final(chat_id),
            "clase": clases.get(chat_id, "nueva"),
            "flete": getattr(f, "job_id", None),
            "etapa": etapa,
            "tema": _tema(f),
            # Con zona: Horizon esta en UTC y una hora local pelada se leeria 3 h corrida.
            "fecha_flete": _con_zona(getattr(f, "fecha_iso", None)),
            "ultimo_at": ultimo_at.astimezone().isoformat(timespec="seconds"),
            "ultimo_de": "cliente" if ultimo_del_cliente else "vos",
            "espera_respuesta": espera,
            "alertas": alertas,
            "mensajes_hoy": sum(1 for _, c in msgs if c.date() == hoy),
            "espera_horas": _horas(ultimo_at, ahora) if espera else None,
            "estado": estado,
        })

    # Pendientes viejos: un flete que aceptó o con la fecha cumplida entra aunque la charla tenga más de una semana
    # (justo esos son los que se olvidan: noviembre 2026, «aceptó hace 8 días» no aparecía).
    for chat_id, f in fletes_por_chat.items():
        if chat_id in por_chat or chat_id in fuera:
            continue
        alertas = [str(x)[:140] for x in (getattr(f, "alertas", None) or [])][:4]
        if any("¿se hizo el flete?" in a for a in alertas):
            estado = "¿se hizo el flete?"
        elif any("aceptó" in a and "falta confirmarlo" in a for a in alertas):
            estado = "aceptó: falta confirmar"
        else:
            continue
        ult = _fecha(getattr(f, "ultimo_at", None))
        if ult is None or ahora - ult > PENDIENTE_VIEJO:
            continue          # de hace meses: ya se resolvió y quedó mal anotado; no es un pendiente
        conversaciones.append({
            "nombre": nombres.get(chat_id) or getattr(f, "nombre", None) or "Sin nombre", "chat": chat_id,
            "final": _final(chat_id), "clase": clases.get(chat_id, "nueva"), "flete": getattr(f, "job_id", None),
            "etapa": getattr(f, "etapa", None), "tema": _tema(f), "fecha_flete": _con_zona(getattr(f, "fecha_iso", None)),
            "ultimo_at": ult.astimezone().isoformat(timespec="seconds") if ult else None,
            "ultimo_de": "cliente" if getattr(f, "ultimo_del_cliente", False) else "vos", "espera_respuesta": False,
            "alertas": alertas, "mensajes_hoy": 0, "espera_horas": None, "estado": estado,
        })

    manana = hoy + timedelta(days=1)

    def es_manana(c) -> bool:
        d = _fecha(c["fecha_flete"])
        return bool(d and d.date() == manana)

    atencion = {
        "sin_responder": sum(1 for c in conversaciones if c["espera_respuesta"] and c["etapa"] not in ("vendido", "perdido")),
        "cotizados_sin_respuesta": sum(1 for c in conversaciones if c["etapa"] == "cotizado" and c["ultimo_de"] == "vos"
                                       and (u := _fecha(c["ultimo_at"])) and ahora - u >= COTIZADO_SIN_RESPUESTA),
        "manana_sin_confirmar": sum(1 for c in conversaciones if c["etapa"] in ("cotizado", "sin-cotizar") and es_manana(c)),
        "para_decidir": sum(1 for c in conversaciones if c["clase"] == "dudosa"),
        "aceptaron_sin_confirmar": sum(1 for c in conversaciones if c["estado"] == "aceptó: falta confirmar"),
        "fecha_cumplida": sum(1 for c in conversaciones if c["estado"] == "¿se hizo el flete?"),
    }
    # Primero lo que espera respuesta (lo mas viejo arriba), despues lo mas reciente.
    conversaciones.sort(key=lambda c: (c["ultimo_de"] != "cliente" or c["etapa"] in ("vendido", "perdido"),
                                       -(c["espera_horas"] or 0) if c["ultimo_de"] == "cliente" else 0,
                                       c["ultimo_at"] or ""), reverse=False)
    # Primero lo que pide algo de vos: ¿se hizo?, aceptó sin confirmar, espera respuesta.
    pendientes = [c for c in conversaciones if (c["espera_respuesta"] or c["estado"] in ("¿se hizo el flete?", "aceptó: falta confirmar"))
                  and c["etapa"] not in ("vendido", "perdido")]
    resto = sorted([c for c in conversaciones if c not in pendientes], key=lambda c: c["ultimo_at"] or "", reverse=True)
    conversaciones = (pendientes + resto)[:MAX_CONVERSACIONES]

    return {
        "version": 1,
        "generado": ahora.astimezone().isoformat(timespec="seconds"),
        "hoy": totales(hoy),
        "semana": totales(desde_semana.date()),
        "atencion": atencion,
        "conversaciones": conversaciones,
    }


@dataclass
class Rafaga:
    """Cuando mandar el reporte despues de mensajes: unos segundos de calma, con un tope.

    `marcar` en cada mensaje (entrante o saliente); `lista` pregunta si ya toca mandar.
    Con cinco mensajes seguidos se manda uno solo, despues de la calma; si no paran, a lo sumo al tope.
    """
    calma: timedelta = timedelta(seconds=15)
    tope: timedelta = timedelta(seconds=60)
    primero: datetime | None = None
    ultimo: datetime | None = None

    def marcar(self, ahora: datetime) -> None:
        self.primero = self.primero or ahora
        self.ultimo = ahora

    def lista(self, ahora: datetime) -> bool:
        if self.primero is None:
            return False
        return ahora - self.ultimo >= self.calma or ahora - self.primero >= self.tope

    def mandada(self) -> None:
        self.primero = self.ultimo = None
