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
        # «Te confirmo para el flete el domingo!!» (Andrés, 18-nov-2026): confirma, no pospone.
        r"(?:(?:para\s+)?(?:hoy|el\s+flete|el\s+(?:lunes|martes|miercoles|jueves|viernes|sabado|domingo))"
        r"|a\s+las\s+\d|que\s+si)\b"
    )),
    (Intencion.POSPONE, re.compile(
        r"\b(?:(?:manana|despues|luego|mas\s+tarde|en\s+un\s+rato)\s+te\s+"
        r"(?:confirmo|aviso|digo|escribo|hablo|contesto)"
        r"|(?:manana|despues|luego|mas\s+tarde)\s+(?:hablamos|seguimos|vemos|charlamos)"
        r"|estamos\s+en\s+contacto|seguimos\s+en\s+contacto"
        r"|cualquier\s+cosa\s+te\s+(?:aviso|escribo|hablo)"
        # «Tema mesas tengo que confirmarte aún! Pero el precio está perfecto» (18-nov-2026): queda pendiente.
        r"|tengo\s+que\s+(?:ver|consultar|hablar|pensar|confirmar\w*)"
        r"|lo\s+(?:pienso|veo|charlo)|te\s+confirmo"
        # "Ok..te aviso en la semana", "Dale gracias, te contacto luego",
        # "apenas tenga la fecha te escribo": el "ok" del principio no es un
        # trato cerrado. Antes solo contaba "cualquier cosa te aviso".
        r"|te\s+(?:aviso|contacto|mantengo\s+al\s+tanto|voy\s+avisando)"
        # "te escribo" solo si es para despues: "apenas tenga la fecha te
        # escribo". "Te escribo de nuevo, ahora necesito..." es una consulta.
        r"|(?:apenas|a\s+penas|cuando)\b[^.?!]{0,40}?\bte\s+(?:escribo|llamo)"
        r"|(?:le|les)\s+digo\s+a\s+\w+(?:\s+\w+)?\s+y\s+te"
        r"|(?:estoy|ando)\s+averiguando|lo\s+coordino"
        # El "no por ahora" amable (noviembre 2026, charlas reales): "Te agendo por si necesito",
        # "estoy buscando presupuestos .. si lo necesito le aviso", "cualquier cosa le aviso".
        # No es un no rotundo (eso es CANCELA): queda esperando, con mas plazo antes de darlo por perdido.
        r"|te\s+agendo|(?:te|lo)\s+tengo\s+agendado|por\s+si\s+(?:lo\s+)?necesito"
        r"|si\s+(?:lo\s+)?necesito\s+(?:te|le)\s+(?:aviso|escribo|llamo)"
        r"|(?:cualquier\s+cosa\s+)?le\s+(?:aviso|escribo)"
        r"|(?:estoy|ando)\s+(?:buscando|pidiendo|juntando|comparando)\s+(?:presupuestos?|precios?|opciones))\b"
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
        r"|(?:un|otro|un\s+par\s+de)\s+flete|(?:una|otra)\s+mudanza)"
        # "Tengo unos bultos para llevar": el cliente que vuelve por otra cosa.
        r"|\btengo\s+(?:un|una|unos|unas|\d+|dos|tres)\s+\w+(?:\s+\w+)?\s+para\s+(?:llevar|trasladar|mover))"
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


#: Cortesia o cierre: el cliente agradece o asiente y no pregunta nada. No pide respuesta.
#: "Genial gracias!!", "Genial Buenisimo!", "Dale gracias", "👍", "Ok perfecto". Corto y sin pregunta:
#: "gracias, ¿y el sabado?" si pide respuesta, igual que un mensaje largo.
_CORTESIA = re.compile(
    r"^\W*(?:(?:muchas\s+)?gracias|genial|buenisimo|perfecto|dale|ok|oka|okey|listo|joya|barbaro|de\s+nada"
    r"|excelente|buenas\s+noches|buen\s+dia|hasta\s+luego|saludos|abrazo|tranqui|se\s+entiende|ja+|je+|si)"
    r"(?:\W+(?:(?:muchas\s+)?gracias|genial|buenisimo|perfecto|dale|ok|listo|joya|barbaro|excelente|igualmente"
    r"|se\s+entiende|tranqui|saludos|abrazo|ja+|je+))*\W*$"
)


def es_cortesia(texto: str | None) -> bool:
    """El mensaje no pide respuesta: agradece, saluda o asiente, corto y sin pregunta (o es solo emojis)."""
    if texto is None:
        return False
    if not texto.strip():
        return False
    if "?" in texto:
        return False
    p = plano(texto)
    if not re.search(r"[a-z0-9]", p):          # solo emojis o signos: un 👍, un ❤️
        return True
    return len(p.split()) <= 6 and bool(_CORTESIA.match(p))
