"""Redactor: que le escribiria Estiba al cliente, si le toca a uno.

Propone a lo sumo un mensaje por flete, el mas util para avanzar. Nunca lo
manda: queda como borrador en la bandeja hasta que alguien lo aprueba.

Solo propone cuando le toca hablar al fletero: si el ultimo mensaje es
nuestro y el cliente no contesto, no se insiste, salvo el seguimiento.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from estiba.core.models import Conversation, JobState
from estiba.extraction.fechas import hora_legible, nombre_dia
from estiba.formato import pesos
from estiba.jobs.engine import Evaluacion
from estiba.jobs.preguntas import ETIQUETAS

RESPUESTA = "respuesta"
PREGUNTAS = "preguntas"
COTIZACION = "cotizacion"
CONFIRMACION = "confirmacion"
SEGUIMIENTO = "seguimiento"


@dataclass(frozen=True)
class Propuesta:
    kind: str
    reason: str
    text: str
    basis_message_id: str


def _cosas(items: list[str]) -> str:
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " y " + items[-1]


def _reconocer(job) -> str:
    """Lo que ya se entendio, dicho como lo diria una persona.

    Repreguntar algo que el cliente ya dijo es la forma mas rapida de que
    piense que no lo leyeron. Primero se le muestra que si.
    """
    if job.origin and job.destination:
        donde = f"de {job.origin.value} a {job.destination.value}"
    elif job.origin:
        donde = f"desde {job.origin.value}"
    elif job.destination:
        donde = f"hasta {job.destination.value}"
    else:
        donde = ""
    que = _cosas(job.items.value) if job.items else ""
    if que and donde:
        return f"Perfecto, {que} {donde}."
    if donde:
        return f"Perfecto, {donde}."
    if que:
        return f"Perfecto, {que}."
    return ""


def _juntar(frases: list[str]) -> str:
    """Un mensaje de WhatsApp, no un formulario: frases seguidas y corto."""
    return " ".join(f for f in frases if f).strip()



def proponer(ev: Evaluacion, conv: Conversation, ahora: datetime) -> Propuesta | None:
    mensajes = [m for m in conv.messages if m.sent_at <= ahora]
    if not mensajes:
        return None
    ultimo = mensajes[-1]
    job = ev.job
    estado = job.state

    def propuesta(kind: str, reason: str, text: str) -> Propuesta:
        return Propuesta(kind, reason, text, ultimo.id)

    if estado in (JobState.CANCELLED, JobState.DONE):
        return None

    if not ultimo.from_customer or estado is JobState.WAITING_CUSTOMER:
        # Nos toca esperar: o el ultimo mensaje es nuestro, o el cliente
        # pospuso ("mañana te confirmo"). Solo el seguimiento rompe el silencio.
        if not ev.requiere_seguimiento:
            return None
        razon = (
            "Dijo que confirmaba y no volvió a escribir"
            if estado is JobState.WAITING_CUSTOMER
            else "Hace más de un día que no responde"
        )
        flete = "lo del flete"
        if job.origin and job.destination:
            flete += f" de {job.origin.value} a {job.destination.value}"
        return propuesta(
            SEGUIMIENTO, razon, f"¡Hola! ¿Pudiste ver {flete}? Si me decís el día, te lo reservo."
        )

    if estado in (JobState.CONFIRMED, JobState.SCHEDULED):
        if job.when and job.when.value < ahora.date():
            return None  # ya paso: no hay nada que confirmarle
        partes = ["¡Listo!"]
        if job.when:
            d = job.when.value
            cuando = f"el {nombre_dia(d)} {d:%d/%m}"
            if job.hour:
                cuando += f" a las {hora_legible(job.hour.value)}"
            elif job.daypart:
                cuando += f" a la {job.daypart.value.replace('manana', 'mañana')}"
            partes.append(f"Quedamos {cuando}.")
        if job.origin and job.destination and job.items:
            partes.append(
                f"Retiro en {job.origin.value} y llevamos a {job.destination.value}: "
                f"{_cosas(job.items.value)}."
            )
        if job.quoted_price:
            partes.append(f"Total {pesos(job.quoted_price.value)}.")
        texto = " ".join(partes)
        if ev.preguntas:
            texto += "\n" + " ".join(ev.preguntas)
        else:
            texto += " Cualquier cambio, avisame."
        return propuesta(CONFIRMACION, "Aceptó: falta confirmarle los detalles", texto)

    # Borrador o presupuestado, y el ultimo en escribir fue el cliente.
    texto_ultimo = conv.text_of(ultimo) or ""
    entendido = any(i.message_id == ultimo.id for i in ev.extraccion.intenciones)
    if texto_ultimo.rstrip().endswith("?") and not entendido:
        # Pregunto algo que las reglas no saben contestar: primero se le responde.
        return propuesta(RESPUESTA, "Te hizo una pregunta", "")

    p = ev.presupuesto
    if estado is JobState.DRAFT and p is not None and p.completo:
        texto = (
            f"Te sale {pesos(p.total)}, de {job.origin.value} a {job.destination.value}, "
            f"con carga y descarga de {_cosas(job.items.value)}."
        )
        if job.when is None:
            texto += " ¿Para cuándo lo necesitás?"
        return propuesta(COTIZACION, "Tiene todos los datos para cotizar", texto)

    if ev.preguntas:
        # Como maximo dos preguntas: tres ya es un formulario, y el cliente
        # contesta una sola. Las que quedan se preguntan en el mensaje que
        # viene, cuando conteste estas.
        pide = ev.preguntas[:2]
        reconocido = _reconocer(job)
        if reconocido:
            texto = _juntar([reconocido, *pide])
        else:
            texto = _juntar(["¡Buenas!", *pide])
        # "Falta origen y destino" dice mas que "Faltan datos": ya se sabe
        # que falta, no hace falta abrir el flete para verlo.
        faltan = [ETIQUETAS[campo] for campo in job.missing]
        razon = f"Falta {_cosas(faltan)}" if faltan else "Faltan datos para avanzar"
        return propuesta(PREGUNTAS, razon, texto)

    # El cliente pregunto algo que las reglas no saben contestar.
    return propuesta(RESPUESTA, "Te escribió y espera respuesta", "")
