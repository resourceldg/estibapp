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

FUERA = ("privado", "spam")
MAX_CONVERSACIONES = 30
#: Pasado este tiempo sin respuesta del cliente, una cotizacion "quedo en el aire".
COTIZADO_SIN_RESPUESTA = timedelta(hours=24)


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
        "SELECT chat_id, from_customer, sent_at FROM messages WHERE sent_at >= ? ORDER BY chat_id, sent_at",
        (desde_semana.isoformat(timespec="seconds"),)).fetchall()
    por_chat: dict[str, list[tuple[bool, datetime]]] = {}
    for chat_id, del_cliente, sent_at in filas:
        if chat_id in fuera or chat_id in ("0", ""):
            continue
        cuando = _fecha(sent_at)
        if cuando is None or cuando > ahora + timedelta(minutes=5):
            continue
        por_chat.setdefault(chat_id, []).append((bool(del_cliente), cuando))

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
        if ultimo_del_cliente:
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
            "final": _final(chat_id),
            "clase": clases.get(chat_id, "nueva"),
            "flete": getattr(f, "job_id", None),
            "etapa": etapa,
            "tema": _tema(f),
            "fecha_flete": getattr(f, "fecha_iso", None),
            "ultimo_at": ultimo_at.isoformat(timespec="seconds"),
            "ultimo_de": "cliente" if ultimo_del_cliente else "vos",
            "mensajes_hoy": sum(1 for _, c in msgs if c.date() == hoy),
            "espera_horas": _horas(ultimo_at, ahora) if ultimo_del_cliente else None,
            "estado": estado,
        })

    manana = hoy + timedelta(days=1)

    def es_manana(c) -> bool:
        d = _fecha(c["fecha_flete"])
        return bool(d and d.date() == manana)

    atencion = {
        "sin_responder": sum(1 for c in conversaciones if c["ultimo_de"] == "cliente" and c["etapa"] not in ("vendido", "perdido")),
        "cotizados_sin_respuesta": sum(1 for c in conversaciones if c["etapa"] == "cotizado" and c["ultimo_de"] == "vos"
                                       and (u := _fecha(c["ultimo_at"])) and ahora - u >= COTIZADO_SIN_RESPUESTA),
        "manana_sin_confirmar": sum(1 for c in conversaciones if c["etapa"] in ("cotizado", "sin-cotizar") and es_manana(c)),
        "para_decidir": sum(1 for c in conversaciones if c["clase"] == "dudosa"),
    }
    # Primero lo que espera respuesta (lo mas viejo arriba), despues lo mas reciente.
    conversaciones.sort(key=lambda c: (c["ultimo_de"] != "cliente" or c["etapa"] in ("vendido", "perdido"),
                                       -(c["espera_horas"] or 0) if c["ultimo_de"] == "cliente" else 0,
                                       c["ultimo_at"]), reverse=False)
    pendientes = [c for c in conversaciones if c["ultimo_de"] == "cliente" and c["etapa"] not in ("vendido", "perdido")]
    resto = sorted([c for c in conversaciones if c not in pendientes], key=lambda c: c["ultimo_at"], reverse=True)
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
