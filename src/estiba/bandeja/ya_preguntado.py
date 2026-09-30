"""Que datos ya le pediste vos, aunque los hayas pedido a mano.

Estiba miraba solo si el ultimo mensaje era nuestro. Eso alcanza cuando la
conversacion esta completa, pero no cuando falta un mensaje: paso de verdad
que el fletero contesto a las 20:41 con su plantilla de cinco preguntas,
WhatsApp tardo un dia en sincronizar ese mensaje, y Estiba mando "¿desde
que barrio seria? ¿hasta donde lo llevamos?" a las 00:43. El cliente vio la
misma pregunta dos veces.

Con esto no importa quien pregunto ni cuando: si el dato ya se pidio, no se
vuelve a pedir. Se busca sobre lo que mandamos nosotros, en cualquier
mensaje, sea de Estiba o escrito a mano desde el celular.
"""

from __future__ import annotations

import re

from estiba.core.models import Conversation
from estiba.extraction.texto import plano

#: Como suena cada dato cuando se lo pide una persona. Cubre lo que escribe
#: Estiba y tambien las plantillas que el fletero manda a mano.
_PEDIDOS = {
    "origin": re.compile(
        r"desde\s+(?:donde|que\s+barrio|que\s+direccion)|de\s+donde\s+(?:hay\s+que\s+)?"
        r"(?:lo\s+)?(?:buscar|retirar|sale|salimos)|retiro\s*:|direccion\s+de\s+retiro"
    ),
    "destination": re.compile(
        r"hasta\s+donde|a\s+donde\s+(?:lo\s+)?(?:llevamos|va|hay\s+que\s+llevar)"
        r"|entrega\s*:|direccion\s+de\s+entrega"
    ),
    "items": re.compile(
        r"que\s+(?:cosas|cositas|muebles)|que\s+hay\s+que\s+(?:llevar|transportar|mudar)"
        r"|carga\s*:|que\s+(?:es\s+lo\s+que|tenes\s+que)\s+llevar"
    ),
    "floors_no_lift": re.compile(
        r"ascensor|escalera|que\s+piso|acceso\s*:|planta\s+baja"
    ),
    "when": re.compile(
        r"que\s+dia|para\s+cuando|que\s+fecha|cuando\s+(?:lo\s+)?(?:necesitas|seria)"
    ),
    "hour": re.compile(r"que\s+hora|a\s+que\s+hora|de\s+la\s+mañana\s+o\s+de\s+la\s+tarde"),
    "helpers": re.compile(r"ayuda\s+para\s+(?:cargar|descargar)|ayudantes?|ayuda\s*:"),
}


def campos_ya_pedidos(conv: Conversation) -> set[str]:
    """Los datos que ya se le pidieron al cliente, por el medio que sea."""
    nuestros = " ".join(
        t for m in conv.messages if not m.from_customer and (t := conv.text_of(m))
    )
    if not nuestros:
        return set()
    texto = plano(nuestros)
    return {campo for campo, patron in _PEDIDOS.items() if patron.search(texto)}


def sin_lo_ya_pedido(preguntas: list[str], pedidos: set[str]) -> list[str]:
    """Saca de la lista las preguntas por datos que ya se pidieron.

    Se reconoce de que dato habla cada pregunta con los mismos patrones:
    sirven igual para lo que escribe Estiba que para tu plantilla.
    """
    quedan = []
    for pregunta in preguntas:
        liso = plano(pregunta)
        campo = next((c for c, patron in _PEDIDOS.items() if patron.search(liso)), None)
        if campo is None or campo not in pedidos:
            quedan.append(pregunta)
    return quedan
