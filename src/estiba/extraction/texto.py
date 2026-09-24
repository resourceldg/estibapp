"""Herramientas de texto compartidas por las reglas.

Las reglas buscan sobre una version "plana" del mensaje (minusculas, sin
tildes) pero citan el texto original. Por eso `plano` conserva el largo:
una posicion en el texto plano es la misma posicion en el original.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


def plano(texto: str) -> str:
    """Minusculas y sin tildes, caracter por caracter, sin cambiar el largo."""
    salida = []
    for c in texto:
        base = unicodedata.normalize("NFD", c.lower())
        salida.append(base[0] if base else c)
    return "".join(salida)


NUMEROS = {
    "un": 1, "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5,
    "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10, "once": 11,
    "doce": 12, "trece": 13, "catorce": 14, "quince": 15, "dieciseis": 16,
    "diecisiete": 17, "dieciocho": 18, "diecinueve": 19, "veinte": 20,
    "treinta": 30, "cuarenta": 40, "cincuenta": 50,
}

#: Para meter en una regex: un numero en cifras o en palabras.
NUMERO = r"(?:\d{1,3}|" + "|".join(sorted(NUMEROS, key=len, reverse=True)) + r")"


def a_numero(token: str) -> int | None:
    token = plano(token.strip())
    if token.isdigit():
        return int(token)
    return NUMEROS.get(token)


@dataclass(frozen=True)
class Hallazgo:
    """Un dato que una regla encontro en un mensaje, antes de volverse Slot."""

    campo: str
    valor: object
    confianza: float
    cita: str
    duda: str | None = None


def cita(original: str, m: re.Match) -> str:
    """El pedazo del mensaje original que corresponde a un match sobre `plano`."""
    return original[m.start() : m.end()].strip()

#: Una conversacion pegada o reenviada: "[15/8, 12:24 p. m.] Fulano: ...".
#: Lo que se dijo ahi adentro no es lo que este cliente esta pidiendo ahora,
#: y la marca de hora se leia como la fecha del flete.
_LINEA_PEGADA = re.compile(
    r"^\s*[\[(]\s*\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?[,\s].{0,24}?[\])]\s*[^:\n]{1,40}:",
    re.MULTILINE,
)


def sin_conversacion_pegada(texto: str) -> str:
    """Corta desde la primera marca de chat pegado hasta el final.

    No alcanza con sacar las lineas que tienen corchete: cuando alguien
    reenvia una conversacion entera, las lineas que siguen tambien son de
    esa charla ajena. De ahi salian "10 a 12:30 hs" como hora del flete y
    un "[15/8, 12:24]" como fecha.

    Lo que el cliente escribio antes de pegar si se conserva ("mira esto:").
    Se reemplaza por espacios en vez de borrar, para no mover las citas.
    """
    m = _LINEA_PEGADA.search(texto)
    if m is None:
        return texto
    corte = texto.rfind("\n", 0, m.start()) + 1
    cola = "".join(" " if c != "\n" else "\n" for c in texto[corte:])
    return texto[:corte] + cola
