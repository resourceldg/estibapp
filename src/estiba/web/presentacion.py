"""Del dominio a lo que se muestra.

Las paginas HTML y la API JSON salen de las mismas funciones: lo que ve el
fletero en el celular y lo que devuelve /api son siempre la misma cosa.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time
from decimal import Decimal
from urllib.parse import quote

from estiba.core.models import Conversation, JobState
from estiba.extraction.fechas import hora_legible, nombre_dia
from estiba.jobs.engine import Evaluacion
from estiba.jobs.preguntas import ETIQUETAS

ESTADOS = {
    JobState.DRAFT: ("Nuevo", "neutro"),
    JobState.QUOTED: ("Presupuestado", "info"),
    JobState.WAITING_CUSTOMER: ("Esperando al cliente", "aviso"),
    JobState.CONFIRMED: ("Confirmado", "ok"),
    JobState.SCHEDULED: ("Agendado", "ok"),
    JobState.DONE: ("Realizado", "neutro"),
    JobState.CANCELLED: ("Perdido", "mal"),
}

TIPOS_BORRADOR = {
    "respuesta": "Te toca responder",
    "preguntas": "Faltan datos",
    "cotizacion": "Para cotizar",
    "confirmacion": "Para confirmar",
    "seguimiento": "Seguimiento",
}

_ORDEN_DATOS = ("origin", "destination", "when", "hour", "daypart", "items",
                "floors_no_lift", "helpers", "quoted_price")


def pesos(monto: Decimal | int | None) -> str | None:
    if monto is None:
        return None
    return f"${int(monto):,}".replace(",", ".")


def telefono(chat_id: str) -> str:
    """5491133334444 -> +54 9 11 3333-4444. Si no lo reconoce, lo deja como esta."""
    m = re.fullmatch(r"549(11)(\d{4})(\d{4})", chat_id)
    if m:
        return f"+54 9 {m.group(1)} {m.group(2)}-{m.group(3)}"
    return f"+{chat_id}" if chat_id.isdigit() else chat_id


def fecha_corta(d: date) -> str:
    return f"{nombre_dia(d)[:3]} {d:%d/%m}"


def hace(desde: datetime, ahora: datetime) -> str:
    segundos = int((ahora - desde).total_seconds())
    if segundos < 60:
        return "recién"
    if segundos < 3600:
        return f"hace {segundos // 60} min"
    if segundos < 86400:
        return f"hace {segundos // 3600} h"
    dias = segundos // 86400
    return "ayer" if dias == 1 else f"hace {dias} días"


def ancla(message_id: str) -> str:
    """Id de HTML para un mensaje: los ':' no se llevan bien con los selectores."""
    return "m-" + re.sub(r"[^A-Za-z0-9_-]", "-", message_id)


def whatsapp(chat_id: str, texto: str) -> str:
    return f"https://wa.me/{chat_id}?text={quote(texto)}"


def _mostrar(campo: str, valor: object) -> str:
    if isinstance(valor, list):
        return ", ".join(map(str, valor))
    if isinstance(valor, date):
        return f"{nombre_dia(valor)} {valor:%d/%m}"
    if isinstance(valor, time):
        return f"{hora_legible(valor)} h"
    if campo == "daypart":
        return {"manana": "mañana"}.get(str(valor), str(valor))
    if campo == "quoted_price":
        return pesos(valor)
    if campo == "floors_no_lift":
        return "con ascensor" if valor == 0 else f"{valor} por escalera"
    return str(valor)


@dataclass
class Dato:
    campo: str
    etiqueta: str
    valor: str
    confianza: float
    cita: str
    message_id: str
    ancla: str
    duda: str | None


@dataclass
class Evento:
    cuando: str
    hacia: str
    tono: str
    causa: str
    ancla: str | None


@dataclass
class Mensaje:
    id: str
    ancla: str
    del_cliente: bool
    hora: str
    texto: str | None
    es_audio: bool
    datos: list[str] = field(default_factory=list)  # que datos salieron de este mensaje


@dataclass
class Borrador:
    id: int
    chat_id: str
    tipo: str
    tipo_etiqueta: str
    razon: str
    texto: str
    estado: str
    texto_final: str | None
    whatsapp: str | None


@dataclass
class Flete:
    chat_id: str
    telefono: str
    estado: str
    estado_etiqueta: str
    tono: str
    origen: str | None
    destino: str | None
    fecha: str | None
    fecha_iso: str | None
    dia: str | None
    dia_semana: str | None
    hora: str | None
    cosas: str | None
    cotizado: str | None
    sugerido: str | None
    sugerido_completo: bool
    alertas: list[str]
    ultimo: str
    ultimo_texto: str | None
    ultimo_del_cliente: bool
    ultimo_at: str
    pasado: bool

    @property
    def ruta(self) -> str:
        return f"{self.origen or '¿?'} → {self.destino or '¿?'}"


@dataclass
class DetalleFlete:
    flete: Flete
    datos: list[Dato]
    faltan: list[str]
    dudas: list[str]
    preguntas: list[str]
    desglose: list[tuple[str, str]]
    sin_contar: list[str]
    historia: list[Evento]
    mensajes: list[Mensaje]
    borradores: list[Borrador]


def flete(ev: Evaluacion, conv: Conversation, ahora: datetime) -> Flete:
    job = ev.job
    etiqueta, tono = ESTADOS[job.state]
    mensajes = [m for m in conv.messages if m.sent_at <= ahora]
    ultimo = mensajes[-1]
    cuando = job.when.value if job.when else None
    hora = None
    if job.hour:
        hora = f"{hora_legible(job.hour.value)} h"
    elif job.daypart:
        hora = _mostrar("daypart", job.daypart.value)
    return Flete(
        chat_id=conv.chat_id,
        telefono=telefono(conv.chat_id),
        estado=job.state.value,
        estado_etiqueta=etiqueta,
        tono=tono,
        origen=job.origin.value if job.origin else None,
        destino=job.destination.value if job.destination else None,
        fecha=fecha_corta(cuando) if cuando else None,
        fecha_iso=cuando.isoformat() if cuando else None,
        dia=f"{cuando.day}" if cuando else None,
        dia_semana=nombre_dia(cuando)[:3] if cuando else None,
        hora=hora,
        cosas=", ".join(job.items.value) if job.items else None,
        cotizado=pesos(job.quoted_price.value) if job.quoted_price else None,
        sugerido=pesos(ev.presupuesto.total) if ev.presupuesto else None,
        sugerido_completo=bool(ev.presupuesto and ev.presupuesto.completo),
        alertas=ev.alertas,
        ultimo=hace(ultimo.sent_at, ahora),
        ultimo_texto=conv.text_of(ultimo),
        ultimo_del_cliente=ultimo.from_customer,
        ultimo_at=ultimo.sent_at.isoformat(),
        pasado=bool(cuando and cuando < ahora.date()),
    )


def borrador(fila: sqlite3.Row) -> Borrador:
    texto_final = fila["final_text"]
    return Borrador(
        id=fila["id"],
        chat_id=fila["chat_id"],
        tipo=fila["kind"],
        tipo_etiqueta=TIPOS_BORRADOR.get(fila["kind"], fila["kind"]),
        razon=fila["reason"],
        texto=fila["text"],
        estado=fila["status"],
        texto_final=texto_final,
        whatsapp=whatsapp(fila["chat_id"], texto_final) if texto_final else None,
    )


def detalle(ev: Evaluacion, conv: Conversation, ahora: datetime, borradores: list[sqlite3.Row]) -> DetalleFlete:
    ex = ev.extraccion
    datos = []
    por_mensaje: dict[str, list[str]] = {}
    for campo in _ORDEN_DATOS:
        c = ex.ganadores.get(campo)
        if c is None:
            continue
        mid = c.slot.evidence.message_id
        datos.append(Dato(
            campo=campo,
            etiqueta=ETIQUETAS[campo].capitalize(),
            valor=_mostrar(campo, c.slot.value),
            confianza=c.slot.confidence,
            cita=c.slot.evidence.quote,
            message_id=mid,
            ancla=ancla(mid),
            duda=c.duda,
        ))
        por_mensaje.setdefault(mid, []).append(ETIQUETAS[campo])

    historia = []
    for e in ev.eventos:
        etiqueta, tono = ESTADOS[e.hacia]
        historia.append(Evento(
            cuando=f"{fecha_corta(e.at.date())} {e.at:%H:%M}",
            hacia=etiqueta,
            tono=tono,
            causa=e.causa,
            ancla=ancla(e.message_id) if e.message_id else None,
        ))

    mensajes = []
    for m in conv.messages:
        if m.sent_at > ahora:
            continue
        mensajes.append(Mensaje(
            id=m.id,
            ancla=ancla(m.id),
            del_cliente=m.from_customer,
            hora=f"{fecha_corta(m.sent_at.date())} {m.sent_at:%H:%M}",
            texto=conv.text_of(m),
            es_audio=m.kind.value == "audio",
            datos=por_mensaje.get(m.id, []),
        ))

    p = ev.presupuesto
    return DetalleFlete(
        flete=flete(ev, conv, ahora),
        datos=datos,
        faltan=[ETIQUETAS[f] for f in ev.job.missing],
        dudas=ex.dudas,
        preguntas=ev.preguntas,
        desglose=[(c, pesos(m)) for c, m in p.desglose] if p else [],
        sin_contar=p.sin_contar if p else [],
        historia=historia,
        mensajes=mensajes,
        borradores=[borrador(b) for b in borradores],
    )


def a_json(obj) -> dict:
    datos = asdict(obj)
    if isinstance(obj, Flete):
        datos["ruta"] = obj.ruta
    return datos
