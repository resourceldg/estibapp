"""Que quiere decir el cliente con cada mensaje.

Una intencion por mensaje, la de mayor prioridad: "dale, manana te confirmo"
es posponer aunque empiece con "dale". Que hacer con la intencion (mover el
flete de estado, arrancar el reloj del silencio) es trabajo del JobEngine.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from estiba.extraction.texto import cita, plano


class Intencion(Enum):
    CANCELA = "cancela"
    POSPONE = "pospone"
    CORRIGE = "corrige"
    ACEPTA = "acepta"
    CONSULTA = "consulta"


#: En orden de prioridad: gana la primera que aparezca en el mensaje.
_PATRONES: list[tuple[Intencion, re.Pattern]] = [
    (Intencion.CANCELA, re.compile(
        r"\b(?:ya\s+(?:consegui|lo\s+resolvi|no\s+(?:hace\s+falta|lo\s+necesito))"
        r"|no\s+(?:me\s+)?(?:interesa|sirve)|lo\s+dejo|dejalo|mejor\s+no\b|cancel\w*)"
    )),
    # Ojo con el orden: va antes que ACEPTA a proposito. "Ok perfecto,
    # estamos en contacto" es una despedida, no un trato cerrado, y el "ok"
    # del principio hacia que se leyera como que aceptaba.
    (Intencion.POSPONE, re.compile(
        r"\b(?:(?:manana|despues|luego|mas\s+tarde|en\s+un\s+rato)\s+te\s+"
        r"(?:confirmo|aviso|digo|escribo|hablo|contesto)"
        r"|(?:manana|despues|luego|mas\s+tarde)\s+(?:hablamos|seguimos|vemos|charlamos)"
        r"|estamos\s+en\s+contacto|seguimos\s+en\s+contacto"
        r"|cualquier\s+cosa\s+te\s+(?:aviso|escribo|hablo)"
        r"|tengo\s+que\s+(?:ver|consultar|hablar|pensar)"
        r"|lo\s+(?:pienso|veo|charlo)|te\s+confirmo)\b"
    )),
    (Intencion.CORRIGE, re.compile(
        r"\b(?:cambio\s+de\s+planes|no\s+es\s+mas|en\s+realidad|me\s+equivoque"
        r"|al\s+final|mejor\s+(?:a|para|el|la)\b)"
    )),
    (Intencion.ACEPTA, re.compile(
        r"^\W*(?:dale|si|ok|oka|okey|perfecto|listo|barbaro|joya|genial|de\s+una"
        r"|buenisimo|va)\b"
        r"|\b(?:cerramos|lo\s+hacemos|trato\s+hecho|ahi\s+te\s+espero|me\s+sirve"
        r"|me\s+parece\s+bien)\b"
    )),
    (Intencion.CONSULTA, re.compile(
        r"\b(?:hace(?:s)?\s+(?:fletes?|mudanzas?)"
        r"|cuanto\s+(?:me\s+)?(?:sale|saldria|cobras|cobrarias|cuesta|seria)"
        r"|que\s+(?:precio|valor)|el\s+valor|precio|presupuesto|presupuestar"
        r"|necesito\s+(?:mudar|llevar|trasladar|un\s+flete|una\s+mudanza))"
    )),
]


@dataclass(frozen=True)
class IntencionDetectada:
    message_id: str
    intencion: Intencion
    cita: str


def intencion(texto: str) -> tuple[Intencion, str] | None:
    p = plano(texto)
    for tipo, patron in _PATRONES:
        if m := patron.search(p):
            return tipo, cita(texto, m)
    return None
