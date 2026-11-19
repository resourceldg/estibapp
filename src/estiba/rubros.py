"""De qué rubro es cada trabajo de una charla, y el nombre que el cliente dice de sí mismo.

Decisión de Lucas (18-nov-2026): Estiba es el cerebro del número de WhatsApp y por ese número entra más que fletes
(carpintería, ventas, reparaciones, transporte de tablas de surf…). Reglas:
  * Un rubro por TRABAJO (episodio): una charla con dos trabajos puede tener dos rubros distintos.
  * Señales por palabras (reglas antes que modelo, como el resto de Estiba) y los datos del flete (origen y destino).
  * Si las señales no alcanzan o se contradicen, el rubro queda «a revisar»: no se adivina.
  * El nombre se toma solo cuando el cliente lo dice («me llamo…», «mi nombre es…»), nunca del perfil.
"""
from __future__ import annotations

import re

from estiba.core.models import Conversation
from estiba.extraction.texto import plano

RUBROS = ("flete", "carpinteria", "venta", "otro")

_SENALES: dict[str, re.Pattern] = {
    "flete": re.compile(
        r"\b(?:flete\w*|mudanza\w*|mudar\w*|traslad\w+|llevar|traer|mover|retirar|camioneta|heladera|lavarropa\w*"
        r"|colchon\w*|sommier|ropero|placard|cama|freezer|bultos?|cajas?|tablas?\s+de\s+surf|tablas?)\b"),
    "carpinteria": re.compile(
        r"\b(?:carpinter\w*|a\s+medida|fabricar|hacer(?:me)?\s+un[ao]?\s+(?:mueble|mesa|estante\w*|puerta|banco|cama)"
        r"|madera|cedro|pino|melamina|barniz\w*|lij\w+|encol\w+|bisagra\w*|estanter\w+|bando\b|mueble\s+de\s+cocina"
        r"|(?:arreglar|reparar)\s+(?:un[ao]?\s+|el\s+|la\s+)?(?:mueble|puerta|cajon\w*|silla|mesa|placard|ropero))\b"),
    "venta": re.compile(
        r"\b(?:vend[oe]s?\b|venta|vendiendo|lo\s+compro|te\s+lo\s+compro|cuanto\s+ped[ií]s|precio\s+del?\s+(?:traje|producto|articulo)"
        r"|talle|traje|neoprene|esta\s+disponible|sigue\s+disponible|lo\s+tenes\s+todavia)\b"),
    "otro": re.compile(
        r"\b(?:vidrio\w*|colocacion|instalacion|reparacion|plomer\w*|electric\w*|pintura\s+de\s+pared|limpieza|jardin\w*)\b"),
}


def rubro(conv: Conversation, job=None) -> dict:
    """{"valor": flete|carpinteria|venta|otro, "revisar": bool, "motivo": str} para un trabajo (episodio)."""
    textos = [plano(conv.text_of(m) or "") for m in conv.messages]
    texto = " \n ".join(t for t in textos if t)
    puntos: dict[str, int] = {r: 0 for r in RUBROS}
    ejemplos: dict[str, str] = {}
    for r, patron in _SENALES.items():
        hallados = patron.findall(texto)
        puntos[r] = len(hallados)
        if hallados:
            ejemplos[r] = hallados[0] if isinstance(hallados[0], str) else hallados[0][0]
    origen = getattr(getattr(job, "origin", None), "value", None)
    destino = getattr(getattr(job, "destination", None), "value", None)
    if origen and destino:
        puntos["flete"] += 3
        ejemplos.setdefault("flete", "origen y destino")
    orden = sorted(RUBROS, key=lambda r: -puntos[r])
    primero, segundo = orden[0], orden[1]
    if puntos[primero] == 0:
        return {"valor": "otro", "revisar": True, "motivo": "sin señales claras de rubro"}
    # Gana con claridad si duplica al segundo; si no, queda a revisar con el más probable.
    claro = puntos[primero] >= 2 * max(1, puntos[segundo]) or puntos[segundo] == 0
    motivo = f"{primero}: «{ejemplos.get(primero, '')}»"
    if not claro:
        motivo += f"; también {segundo}: «{ejemplos.get(segundo, '')}»"
    return {"valor": primero, "revisar": not claro, "motivo": motivo[:200]}


_NOMBRE = re.compile(
    r"\b(?:me\s+llamo|mi\s+nombre\s+es|soy)\s+"
    r"([A-Za-zÁÉÍÓÚÑáéíóúñ][a-záéíóúñ]+(?:\s+[A-Za-zÁÉÍÓÚÑáéíóúñ][a-záéíóúñ]+){0,2})", re.IGNORECASE)
#: Palabras que cortan el nombre o indican que no es un nombre («soy de Mar del Plata», «me llamo y te cuento»).
_NO_NOMBRE = {"de", "del", "la", "el", "y", "e", "que", "muchas", "gracias", "yo", "un", "una", "el", "quien", "nuevo",
              "nueva", "cliente", "vecino", "vecina", "amigo", "amiga", "hijo", "hija", "la", "los", "las", "mama", "papa",
              "senor", "senora", "con", "para", "por", "en", "a", "al", "como", "muy", "tan", "su", "tu", "mi", "lo"}


def nombre_declarado(conv: Conversation) -> str | None:
    """El nombre que el CLIENTE dice de sí mismo; el más reciente si hay varios. None si no lo dijo."""
    hallado = None
    for m in conv.messages:
        if not m.from_customer:
            continue
        texto = conv.text_of(m) or ""
        for match in _NOMBRE.finditer(texto):
            disparador = plano(match.group(0).split()[0])
            palabras = []
            for p in match.group(1).split():
                if plano(p) in _NO_NOMBRE:
                    break
                palabras.append(p)
            if not palabras:
                continue
            # «soy» solo cuenta con nombre propio en mayúscula («Soy Andrés»), no «soy nuevo por acá».
            if disparador == "soy" and not palabras[0][0].isupper():
                continue
            hallado = " ".join(w[:1].upper() + w[1:] for w in palabras)
    return hallado
