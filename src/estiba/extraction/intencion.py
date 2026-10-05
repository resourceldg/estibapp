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
    # "Te confirmo hoy a las 19" es confirmar, no posponer: va antes que
    # POSPONE, que tiene "te confirmo" suelto. "Mañana te confirmo" no entra:
    # el "te confirmo" tiene que ir seguido de cuando o de que.
    (Intencion.ACEPTA, re.compile(
        r"(?<!manana\s)(?<!despues\s)(?<!luego\s)(?<!tarde\s)\bte\s+confirmo\s+"
        r"(?:(?:para\s+)?hoy|a\s+las\s+\d|el\s+flete|que\s+si)\b"
    )),
    (Intencion.POSPONE, re.compile(
        r"\b(?:(?:manana|despues|luego|mas\s+tarde|en\s+un\s+rato)\s+te\s+"
        r"(?:confirmo|aviso|digo|escribo|hablo|contesto)"
        r"|(?:manana|despues|luego|mas\s+tarde)\s+(?:hablamos|seguimos|vemos|charlamos)"
        r"|estamos\s+en\s+contacto|seguimos\s+en\s+contacto"
        r"|cualquier\s+cosa\s+te\s+(?:aviso|escribo|hablo)"
        r"|tengo\s+que\s+(?:ver|consultar|hablar|pensar)"
        r"|lo\s+(?:pienso|veo|charlo)|te\s+confirmo"
        # "Ok..te aviso en la semana", "Dale gracias, te contacto luego",
        # "apenas tenga la fecha te escribo": el "ok" del principio no es un
        # trato cerrado. Antes solo contaba "cualquier cosa te aviso".
        r"|te\s+(?:aviso|contacto|mantengo\s+al\s+tanto|voy\s+avisando)"
        # "te escribo" solo si es para despues: "apenas tenga la fecha te
        # escribo". "Te escribo de nuevo, ahora necesito..." es una consulta.
        r"|(?:apenas|a\s+penas|cuando)\b[^.?!]{0,40}?\bte\s+(?:escribo|llamo)"
        r"|(?:le|les)\s+digo\s+a\s+\w+(?:\s+\w+)?\s+y\s+te"
        r"|(?:estoy|ando)\s+averiguando|lo\s+coordino)\b"
    )),
    (Intencion.CORRIGE, re.compile(
        r"\b(?:cambio\s+de\s+planes|no\s+es\s+mas|en\s+realidad|me\s+equivoque"
        r"|al\s+final|mejor\s+(?:a|para|el|la)\b)"
    )),
    (Intencion.ACEPTA, re.compile(
        # "Si" sin tilde seguido de un verbo es condicional ("Si están
        # enganchados...", "Si quiere le pago"): no es un si. Y "dale gracias"
        # u "ok gracias" solos son cortesia, no un trato.
        r"^\W*(?:(?:dale|ok|oka|okey)(?!\W*gracias\W*$)|si(?=\W*$|\W*[,.!]|\s+(?:si|dale|claro|perfecto|ok|obvio"
        r"|de\s+una|gracias|como|te|hoy|manana|el|la|confirmo|quedamos|no\s+hay|senor|senora|senod)\b)"
        r"|perfecto|listo|barbaro|joya|genial|de\s+una|buenisimo|va)\b"
        r"|\b(?:cerramos|lo\s+hacemos|trato\s+hecho|ahi\s+te\s+espero|me\s+sirve"
        r"|me\s+parece\s+bien)\b"
    )),
    (Intencion.CONSULTA, re.compile(
        r"\b(?:hace(?:s)?\s+(?:fletes?|mudanzas?)"
        r"|cuanto\s+(?:me\s+)?(?:sale|saldria|cobras|cobrarias|cuesta|seria)"
        r"|que\s+(?:precio|valor)|el\s+valor|precio|presupuesto|presupuestar"
        # "necesito mover un placard" es una consulta igual que "necesito un
        # flete": sin esto, el cliente que vuelve por otra cosa quedaba
        # pegado al flete anterior en vez de abrir uno nuevo.
        # "necesito otro flete" es la frase del cliente que vuelve, y quedaba
        # afuera porque el patron pedia el sustantivo pegado al verbo.
        r"|(?<!no\s)(?:necesito|tengo\s+que(?:\s+hacer)?|queria|quiero|hay\s+que)\s+"
        r"(?:mudar|mudarme|mover|llevar|trasladar|transportar|retirar"
        r"|(?:un|otro|un\s+par\s+de)\s+flete|(?:una|otra)\s+mudanza))"
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
