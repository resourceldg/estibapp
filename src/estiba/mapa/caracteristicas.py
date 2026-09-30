"""La ciudad que dice el telefono.

Si la direccion no nombra la ciudad, la caracteristica del telefono suele
decirla: un 223 es de Mar del Plata, un 2291 de Miramar. Con eso "Rivadavia
1234" se busca en la ciudad del cliente y no en cualquiera de las cientos
que tienen una calle Rivadavia.

El 11 no dice nada: mucha gente de Buenos Aires vive en la costa y siguio
con su numero de alla. Con un 11 decide la cercania a la base, como si no
hubiera telefono.
"""

from __future__ import annotations

#: Caracteristica (sin el 0) -> localidad, como la nombra el INDEC.
CIUDADES = {
    "223": "Mar del Plata",
    "2291": "Miramar",
    "2262": "Necochea",
    "2266": "Balcarce",
    "2254": "Pinamar",
    "2255": "Villa Gesell",
    "2257": "Mar de Ajó",
    "2252": "San Clemente del Tuyú",
    "2246": "Santa Teresita",
    "2267": "General Juan Madariaga",
    "2983": "Tres Arroyos",
    "221": "La Plata",
    "291": "Bahía Blanca",
}


def ciudad_del_telefono(chat_id: str) -> str | None:
    """"5492235550001" -> "Mar del Plata". None para el 11 o lo desconocido."""
    numero = chat_id.split("@")[0]
    if not numero.isdigit():
        return None  # un @lid no es un telefono
    if numero.startswith("549"):
        numero = numero[3:]
    elif numero.startswith("54"):
        numero = numero[2:]
    else:
        return None
    for largo in (4, 3):
        if (ciudad := CIUDADES.get(numero[:largo])) is not None:
            return ciudad
    return None
