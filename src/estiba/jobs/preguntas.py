"""Que preguntarle al cliente para completar el flete.

Salen como borrador: nada de esto se manda solo. Se pregunta lo que falta y
lo que se dijo con duda, en el orden en que hace falta para avanzar: primero
lo que se necesita para cotizar, despues lo que se necesita para confirmar.
"""

from __future__ import annotations

from estiba.core.models import JobState
from estiba.extraction.extractor import Extraccion
from estiba.extraction.fechas import nombre_dia
from estiba.extraction.reglas import leer_item

ETIQUETAS = {
    "origin": "origen", "destination": "destino", "items": "qué lleva",
    "when": "fecha", "hour": "hora", "daypart": "franja",
    "floors_no_lift": "pisos x escalera", "helpers": "ayudantes",
    "quoted_price": "precio cotizado",
}

PARA_COTIZAR = ("origin", "destination", "items", "floors_no_lift")
PARA_CONFIRMAR = ("when",)

_SI_FALTA = {
    "origin": "¿Desde qué barrio sería?",
    "destination": "¿Hasta dónde lo llevamos?",
    "items": "¿Qué cosas hay que llevar?",
    "floors_no_lift": "¿Hay ascensor? Si no hay, ¿en qué piso es?",
    "when": "¿Para qué día lo necesitás?",
    "hour": "¿A qué hora te queda bien?",
}
_SI_HAY_DUDA = {
    "origin": "¿Me pasás bien el barrio de donde sale?",
    "destination": "¿Me pasás bien el barrio a donde va?",
    "floors_no_lift": "¿En qué piso es?",
    "hour": "¿Sería de la mañana o de la tarde?",
    "helpers": "¿Necesitás que lleve ayudantes para cargar?",
}


def preguntas(ex: Extraccion, estado: JobState) -> list[str]:
    if estado in (JobState.CANCELLED, JobState.DONE):
        return []
    campos = ["hour"] if estado in (JobState.CONFIRMED, JobState.SCHEDULED) else [
        *PARA_COTIZAR, *PARA_CONFIRMAR, "hour", "helpers"
    ]
    salida = []
    for campo in campos:
        ganador = ex.ganadores.get(campo)
        if ganador is None:
            if campo == "hour" and ("when" not in ex.ganadores or "daypart" in ex.ganadores):
                continue  # sin dia no se pregunta la hora; con franja alcanza por ahora
            if campo in _SI_FALTA:
                salida.append(_SI_FALTA[campo])
            continue
        if ganador.duda:
            if campo == "when":
                d = ganador.slot.value
                salida.append(f"¿Sería el {nombre_dia(d)} {d:%d/%m}?")
            elif campo in _SI_HAY_DUDA:
                salida.append(_SI_HAY_DUDA[campo])
        if campo == "items":
            for texto in ganador.slot.value:
                cosa = leer_item(texto)
                if cosa is not None and cosa.en_plural and cosa.cantidad is None:
                    salida.append(f"¿Cuántas {cosa.plural} son, más o menos?")

    # "no hay ascensor" sin piso no llena el campo, pero hay que preguntarlo.
    if "floors_no_lift" in campos and "floors_no_lift" not in ex.ganadores:
        if any(c.campo == "floors_no_lift" for c in ex.sin_resolver):
            salida[salida.index(_SI_FALTA["floors_no_lift"])] = _SI_HAY_DUDA["floors_no_lift"]
    return salida
