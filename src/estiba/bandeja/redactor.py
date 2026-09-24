"""Redactor: que le escribiria Estiba al cliente, si le toca a uno.

Propone a lo sumo un mensaje por flete, el mas util para avanzar. Nunca lo
manda: queda como borrador en la bandeja hasta que alguien lo aprueba.

Solo propone cuando le toca hablar al fletero: si el ultimo mensaje es
nuestro y el cliente no contesto, no se insiste, salvo el seguimiento.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from estiba.bandeja.accion import Accion, decidir
from estiba.core.models import Conversation
from estiba.extraction.fechas import hora_legible, nombre_dia
from estiba.formato import pesos
from estiba.jobs.engine import Evaluacion
from estiba.jobs.preguntas import ETIQUETAS

#: El hueco del precio cuando Estiba no puede calcularlo. Se completa desde
#: el campo de precio; el mensaje no sale con esto adentro.
SIN_PRECIO = "$___"

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


#: Lo primero que se le contesta a alguien que recien escribe.
#:
#: Primero engancharlo, despues sacarle los datos. Un cuestionario de
#: entrada espanta: el que escribio "hola" todavia no decidio nada, y
#: contestarle con cinco preguntas lo pone a llenar un formulario. Una sola
#: pregunta abierta deja que cuente lo que tenga, y de ahi Estiba saca lo
#: que pueda. Los datos que falten se piden despues, de a dos.
APERTURA = (
    "🚚 ¡Hola! Gracias por escribir. Contame qué tenés que llevar y te paso "
    "un precio. Si te resulta más fácil, mandame una foto o un audio 📸"
)


def _primer_contacto(conv: Conversation, ahora: datetime) -> bool:
    """Si el fletero todavia no le contesto nunca a esta persona."""
    return not any(
        not m.from_customer for m in conv.messages if m.sent_at <= ahora
    )


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
    """El mensaje que Estiba dejaria escrito, si corresponde mandar alguno.

    Primero se decide que toca hacer (`accion.decidir`) y recien despues se
    escribe. Hay acciones que no llevan mensaje: esperar no es escribir
    "¿pudiste ver lo del flete?", y posponer tampoco.
    """
    mensajes = [m for m in conv.messages if m.sent_at <= ahora]
    if not mensajes:
        return None
    ultimo = mensajes[-1]
    job = ev.job
    d = decidir(ev, conv, ahora)

    def propuesta(text: str) -> Propuesta:
        return Propuesta(d.accion.value, d.razon, text, ultimo.id)

    if d.accion in (Accion.ESPERAR, Accion.POSPONER):
        return None   # no hay nada que mandarle: la accion no es un mensaje

    if d.accion is Accion.RESPONDER:
        return propuesta("")   # te toca a vos: Estiba no inventa la respuesta

    if d.accion is Accion.SEGUIR:
        flete = "lo del flete"
        if job.origin and job.destination:
            flete += f" de {job.origin.value} a {job.destination.value}"
        return propuesta(f"¡Hola! ¿Pudiste ver {flete}? Si me decís el día, te lo reservo.")

    if d.accion is Accion.CONFIRMAR:
        partes = ["¡Listo!"]
        if job.when:
            f = job.when.value
            cuando = f"el {nombre_dia(f)} {f:%d/%m}"
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
        texto += ("\n" + " ".join(ev.preguntas[:2])) if ev.preguntas else " Cualquier cambio, avisame."
        return propuesta(texto)

    if d.accion is Accion.COTIZAR:
        p = ev.presupuesto
        # Sin tarifa no hay numero, pero el mensaje se arma igual: el hueco
        # se completa con el campo de precio, no editando el texto.
        cuanto = pesos(p.total) if p is not None and p.completo else SIN_PRECIO
        texto = (
            f"Te sale {cuanto}, de {job.origin.value} a {job.destination.value}, "
            f"con carga y descarga de {_cosas(job.items.value)}."
        )
        if job.when is None:
            texto += " ¿Para cuándo lo necesitás?"
        return propuesta(texto)

    # PEDIR_DATOS: reconocer lo entendido y pedir a lo sumo dos cosas. Tres
    # ya es un formulario, y el cliente contesta una sola.
    reconocido = _reconocer(job)
    if not reconocido and _primer_contacto(conv, ahora):
        texto = APERTURA
    else:
        texto = _juntar([reconocido or "¡Buenas!", *ev.preguntas[:2]])
    faltan = [ETIQUETAS[campo] for campo in job.missing]
    razon = f"Falta {_cosas(faltan)}" if faltan else d.razon
    return Propuesta(d.accion.value, razon, texto, ultimo.id)
