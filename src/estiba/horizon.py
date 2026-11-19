"""Vinculo con Horizon: que se manda, cuando y que se recuerda.

Se manda lo que sirve para mirar de un vistazo: cada flete con su etapa,
ruta, fecha, km y plata (sugerido, cotizado, cobrado), el resumen de
"¿Como estoy este mes?" y el reporte de actividad de WhatsApp (contadores
y quien hablo ultimo; ver estiba.actividad) y, desde el 18-nov-2026 por decision
de Lucas, el texto de las charlas DE NEGOCIO con sus transcripciones (ver
estiba.charlas). Las charlas privadas y el spam nunca salen de esta compu.

Solo viajan los fletes que cambiaron desde el ultimo envio (se recuerda una
huella de cada uno). El resumen del mes va siempre, asi Horizon sabe cuando
fue la ultima vez que Estiba estuvo prendida.
"""
from __future__ import annotations

import re

import hashlib
import json
import sqlite3
from datetime import datetime
from decimal import Decimal

from estiba.adapters.horizon import ErrorHorizon, HorizonClient
from estiba.economia.mes import NOMBRES
from estiba.storage.repositories import SettingsRepository

HUELLAS = "horizon.enviados"
ULTIMO = "horizon.ultimo"
MARCA_CHARLAS = "horizon.charlas"


def _numero(valor) -> float | None:
    if valor is None or valor == "":
        return None
    try:
        return round(float(Decimal(str(valor).replace(" km", "").strip())), 2)
    except (ArithmeticError, ValueError):
        return None


def _con_zona(texto: str | None, hora: str | None = None) -> str | None:
    """Fecha con su zona horaria: Horizon esta en UTC y un "2026-11-20"
    pelado se leeria como las 21 h del 19 en Argentina."""
    if not texto:
        return None
    try:
        if len(texto) == 10:
            # «11 h», «11:30», «9 hs»: la hora acordada; sin hora, el mediodía (Andrés, 18-nov: «11 h» viajaba como 12:00).
            m = re.match(r"\s*(\d{1,2})(?:[:.](\d{2}))?", hora or "")
            hh = f"{int(m.group(1)):02d}:{m.group(2) or '00'}" if m and int(m.group(1)) < 24 else "12:00"
            texto = f"{texto}T{hh}"
        return datetime.fromisoformat(texto).astimezone().isoformat()
    except ValueError:
        return None


def flete_para_horizon(f, montos: dict | None = None, creado: str | None = None) -> dict:
    """Un flete tal como lo ve el fletero en la lista, sin la conversacion."""
    m = montos or {}
    return {
        "id": f.job_id,
        "numero": f.numero,
        "nombre": f.nombre,
        "telefono": f.telefono,
        "estado": f.estado,
        "etapa": f.etapa,
        "origen": f.origen,
        "destino": f.destino,
        "fecha_iso": _con_zona(f.fecha_iso, f.hora),
        "hora": f.hora,
        "cosas": f.cosas,
        "km": _numero(f.km),
        "sugerido": _numero(m.get("sugerido")),
        "cotizado": _numero(m.get("cotizado")) if m.get("cotizado") else _numero(f.cotizado_crudo),
        "cobrado": _numero(m.get("cobrado")),
        "ultimo_at": _con_zona(f.ultimo_at),
        "ultimo_del_cliente": f.ultimo_del_cliente,
        # El cliente aceptó y falta que el fletero lo confirme (19-nov-2026): Horizon lo muestra arriba de todo.
        "acepto": any("aceptó" in a for a in (getattr(f, "alertas", None) or [])),
        # Lo que falta para agendar o para completar un flete agendado (19-nov): Horizon te lo pregunta.
        "falta": next((a for a in (getattr(f, "alertas", None) or []) if a.startswith(("agendado, pero", "aceptó, pero falta fecha", "aceptó, pero fecha con duda"))), None),
        "creado": _con_zona(creado),
    }


def mes_para_horizon(m) -> dict:
    """«¿Como estoy este mes?» en numeros, para la tarjeta de Horizon."""
    def n(v):
        return None if v is None else round(float(v), 2)

    return {
        "desde": m.desde.isoformat(), "hasta": m.hasta.isoformat(),
        "ingresos": n(m.ingresos), "publicidad": n(m.publicidad), "disponible": n(m.disponible),
        "km": n(m.km), "horas": n(m.horas), "fletes": len(m.fletes),
        "nivel": m.nivel, "nivel_texto": NOMBRES.get(m.nivel, "Todavia no cubre el uso"),
        "autonomia": None if m.autonomia is None else round(m.autonomia, 3),
        "falta_para_pagarse": n(m.falta_para_pagarse), "fletes_que_faltan": m.fletes_que_faltan,
        "margen_tipico": n(m.margen_tipico), "objetivo_cumplido": bool(m.objetivo_cumplido),
        "ticket": n(m.ticket), "capacidad_restante": n(m.capacidad_restante), "obligacion": n(m.obligacion),
        "avisos": list(m.avisos)[:3],
    }


def _huella(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


TIPOS = ("confirmar", "realizado", "perdido", "reabrir")


def aplicar_comandos(comandos: list[dict], ejecutar) -> list[dict]:
    """Aplica los cambios pedidos desde Horizon, uno por uno, con las reglas de Estiba.

    `ejecutar(tipo, job_id, datos)` hace lo mismo que el boton de la pantalla
    del flete; si Estiba no lo permite (por ejemplo, confirmar algo que nunca
    se cotizo) levanta y el motivo vuelve a Horizon para que se vea alla.
    """
    acks = []
    for c in comandos:
        tipo, job_id, datos = c.get("tipo"), c.get("flete"), c.get("datos") or {}
        if tipo not in TIPOS or not job_id:
            acks.append({"id": c.get("id"), "ok": False, "error": "cambio desconocido"})
            continue
        try:
            ejecutar(tipo, job_id, datos)
            acks.append({"id": c.get("id"), "ok": True})
        except Exception as e:  # la regla de Estiba dijo que no: se informa, no se cae
            acks.append({"id": c.get("id"), "ok": False, "error": (getattr(e, "detail", None) or str(e) or type(e).__name__)[:300]})
    return acks


def sincronizar(conn: sqlite3.Connection, cliente: HorizonClient, fletes: list[dict], mes: dict | None,
                ahora: datetime, acks: list[dict] | None = None, catalogos: dict | None = None,
                forzar: set[str] | None = None, actividad: dict | None = None, charlas: dict | None = None,
                inteligencia: dict | None = None) -> dict:
    """Manda lo que cambio y anota el resultado. Nunca levanta: un Horizon
    caido no es motivo para que Estiba deje de andar."""
    ajustes = SettingsRepository(conn)
    previas = ajustes.json(HUELLAS) or {}
    huellas = {f["id"]: _huella(f) for f in fletes}
    # Los que recibieron un pedido de cambio van siempre: si Estiba lo rechazo, Horizon tiene que volver al estado real.
    forzar = forzar or set()
    cambiados = [f for f in fletes if f["id"] in forzar or previas.get(f["id"]) != huellas[f["id"]]]
    try:
        r = cliente.enviar(cambiados, mes, ahora.astimezone().isoformat(), acks=acks, catalogos=catalogos,
                           actividad=actividad, charlas=charlas, inteligencia=inteligencia)
    except ErrorHorizon as e:
        estado = {"cuando": ahora.isoformat(), "ok": False, "error": str(e), "pendientes": len(cambiados)}
        ajustes.guardar_json(ULTIMO, estado)
        return estado
    if charlas and charlas.get("marca") is not None:
        # Horizon recibio los mensajes: la proxima vez se manda desde aca.
        ajustes.guardar_json(MARCA_CHARLAS, charlas["marca"])
    fallidos = set(r["fallidos"])
    previas.update({f["id"]: huellas[f["id"]] for f in cambiados if f["id"] not in fallidos})
    ajustes.guardar_json(HUELLAS, previas)
    estado = {"cuando": ahora.isoformat(), "ok": not fallidos, "enviados": len(cambiados) - len(fallidos),
              "creados": r["creados"], "precios": r["precios"], "fallidos": len(fallidos),
              "cambios_desde_horizon": len(acks or [])}
    ajustes.guardar_json(ULTIMO, estado)
    return estado
