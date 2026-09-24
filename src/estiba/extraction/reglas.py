"""Reglas para los datos del flete que no son fechas.

Cada funcion recibe el texto de UN mensaje y devuelve lo que encontro. No
mira el resto de la conversacion: combinar y decidir que dato gana es trabajo
del extractor.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from estiba.extraction.texto import NUMERO, Hallazgo, a_numero, cita, plano

# --------------------------------------------------------------------------
# Lugares
# --------------------------------------------------------------------------

BARRIOS_CABA = [
    "Agronomía", "Almagro", "Balvanera", "Barracas", "Belgrano", "Boedo",
    "Caballito", "Chacarita", "Coghlan", "Colegiales", "Constitución", "Flores",
    "Floresta", "La Boca", "La Paternal", "Liniers", "Mataderos", "Monte Castro",
    "Montserrat", "Nueva Pompeya", "Núñez", "Palermo", "Parque Avellaneda",
    "Parque Chacabuco", "Parque Chas", "Parque Patricios", "Puerto Madero",
    "Recoleta", "Retiro", "Saavedra", "San Cristóbal", "San Nicolás",
    "San Telmo", "Vélez Sarsfield", "Versalles", "Villa Crespo",
    "Villa del Parque", "Villa Devoto", "Villa General Mitre", "Villa Lugano",
    "Villa Luro", "Villa Ortúzar", "Villa Pueyrredón", "Villa Real",
    "Villa Riachuelo", "Villa Santa Rita", "Villa Soldati", "Villa Urquiza",
]
CONURBANO = [
    "Adrogué", "Avellaneda", "Banfield", "Berazategui", "Bernal", "Caseros",
    "Castelar", "Escobar", "Ezeiza", "Florencio Varela", "Haedo", "Hurlingham",
    "Ituzaingó", "La Matanza", "La Plata", "Lanús", "Lomas de Zamora",
    "Martínez", "Merlo", "Monte Grande", "Moreno", "Morón", "Munro", "Olivos",
    "Pilar", "Quilmes", "Ramos Mejía", "San Fernando", "San Isidro",
    "San Justo", "San Martín", "Temperley", "Tigre", "Vicente López", "Wilde",
]
BARRIOS = BARRIOS_CABA + CONURBANO


def zona(lugar: str) -> str | None:
    """"caba", "conurbano", o None si el lugar no esta en la lista."""
    if lugar in BARRIOS_CABA:
        return "caba"
    if lugar in CONURBANO:
        return "conurbano"
    return None


_CANONICO = {plano(b): b for b in BARRIOS}
_LUGAR = "(" + "|".join(re.escape(b) for b in sorted(_CANONICO, key=len, reverse=True)) + ")"

_DE_A = re.compile(rf"\b(?:de|desde)\s+{_LUGAR}\s+(?:a|hasta|para)\s+{_LUGAR}\b")
_NUEVO_DESTINO = re.compile(rf"\b(?:es|ahora|mejor|seria|va)\s+(?:a|para|hasta)\s+{_LUGAR}\b")
_NUEVO_ORIGEN = re.compile(rf"\b(?:sale|salgo|retiran?|buscan?|es)\s+(?:de|desde|en)\s+{_LUGAR}\b")
# Lugares que no estan en la lista: se aceptan con duda si vienen con mayuscula.
_DE_A_LIBRE = re.compile(
    r"\b(?:de|desde)\s+([A-ZÁÉÍÓÚÑ][\wáéíóúñ]*(?:\s+[A-ZÁÉÍÓÚÑ][\wáéíóúñ]*)*)"
    r"\s+(?:a|hasta|para)\s+([A-ZÁÉÍÓÚÑ][\wáéíóúñ]*(?:\s+[A-ZÁÉÍÓÚÑ][\wáéíóúñ]*)*)"
)


#: Lo que parece un lugar pero no lo es. Sale de mensajes reales: "de lunes
#: a viernes", "de 9 a 17h", "de los tuyos no se va a sumar ninguno".
_DIAS_Y_MESES = re.compile(
    r"^(?:lunes|martes|miercoles|jueves|viernes|sabado|domingo|enero|febrero|"
    r"marzo|abril|mayo|junio|julio|agosto|septiembre|setiembre|octubre|"
    r"noviembre|diciembre|manana|hoy|ayer|ahora|aca|alla|ahi)\b"
)
_SOLO_HORA = re.compile(r"^\d{1,2}(?:[:.]\d{2})?\s*(?:hs?|horas?|am|pm)?$")
_PRONOMBRES = re.compile(r"^(?:los|las|el|la|mi|tu|su|vos|nos|ellos|eso|esto|ahi)\b")
#: Palabras que aparecen en "de X a Y" sin que X sea un lugar: "de nuevo a
#: casa de mi mama", "de vuelta a la obra".
_NO_LUGAR = re.compile(r"^(?:nuevo|nueva|vuelta|paso|ultima|una|un|otro|otra)\b")

#: "de alberti y santa fe hasta rufino inda y vertiz": esquinas y direcciones
#: en minuscula, que es como escribe la gente. Sin mayusculas obligatorias y
#: sin la lista de barrios, que es de otra ciudad.
#: Se acepta el punto de una abreviatura ("b.goñi") pero no el que cierra
#: una oracion, para no cruzar de una frase a la siguiente.
_TROZO = r"(?:(?!\.\s)[^,;:!?\n]){3,45}?"
_DE_A_SUELTO = re.compile(
    rf"\b(?:de|desde)\s+({_TROZO})\s+(?:a|hasta|para)\s+({_TROZO})"
    r"(?=\s*(?:[,;.:!?\n]|$|\bel\b|\bpara\b|\bque\b|\bes\b|\bson\b|\bcon\b))",
    re.IGNORECASE,
)


def _parece_lugar(texto: str) -> bool:
    """Si eso puede ser una direccion o una esquina, y no otra cosa.

    "de lunes a viernes" y "de 9 a 17h" tienen la misma forma que "de
    Alberti a Colon": lo unico que las separa es mirar que hay adentro.
    """
    limpio = plano(texto).strip()
    if len(limpio) < 3 or _SOLO_HORA.match(limpio):
        return False
    if _DIAS_Y_MESES.match(limpio) or _PRONOMBRES.match(limpio) or _NO_LUGAR.match(limpio):
        return False
    # Al menos una palabra de verdad: "9 y 17" no es una esquina.
    return any(len(t) >= 3 and t.isalpha() for t in re.split(r"[^\wáéíóúñ]+", limpio))


def lugares(texto: str) -> list[Hallazgo]:
    p = plano(texto)
    if m := _DE_A.search(p):
        c = cita(texto, m)
        return [
            Hallazgo("origin", _CANONICO[m.group(1)], 0.9, c),
            Hallazgo("destination", _CANONICO[m.group(2)], 0.9, c),
        ]
    hallados = []
    if m := _NUEVO_DESTINO.search(p):
        hallados.append(Hallazgo("destination", _CANONICO[m.group(1)], 0.85, cita(texto, m)))
    if m := _NUEVO_ORIGEN.search(p):
        hallados.append(Hallazgo("origin", _CANONICO[m.group(1)], 0.85, cita(texto, m)))
    if hallados:
        return hallados
    if m := _DE_A_LIBRE.search(texto):
        duda = f"'{m.group(0)}': lugar que no esta en la lista de barrios"
        return [
            Hallazgo("origin", m.group(1), 0.6, m.group(0), duda),
            Hallazgo("destination", m.group(2), 0.6, m.group(0), duda),
        ]
    # Ultimo recurso: direcciones y esquinas escritas como se escriben, en
    # minuscula. Confianza baja y con duda: hay que mirarlo.
    if m := _DE_A_SUELTO.search(texto):
        origen, destino = m.group(1).strip(), m.group(2).strip()
        if _parece_lugar(origen) and _parece_lugar(destino):
            duda = "dirección escrita a mano: conviene confirmarla"
            return [
                Hallazgo("origin", origen, 0.55, m.group(0), duda),
                Hallazgo("destination", destino, 0.55, m.group(0), duda),
            ]
    return []


# --------------------------------------------------------------------------
# Que se lleva
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Item:
    nombre: str  # singular canonico: es la clave para juntar menciones
    plural: str
    cantidad: int | None
    en_plural: bool

    def __str__(self) -> str:
        if self.cantidad is not None and self.cantidad > 1:
            return f"{self.cantidad} {self.plural}"
        return self.plural if self.en_plural and self.cantidad is None else self.nombre


# singular, plural, otras formas de decirlo
_MUEBLES = [
    ("juego de comedor", "juegos de comedor", []),
    ("heladera", "heladeras", []),
    ("lavarropas", "lavarropas", ["lavarropa"]),
    ("secarropas", "secarropas", []),
    ("freezer", "freezers", []),
    ("microondas", "microondas", []),
    ("televisor", "televisores", ["tele", "teles", "tv"]),
    ("sillon", "sillones", ["sofa", "sofas"]),
    ("cama", "camas", []),
    ("colchon", "colchones", []),
    ("sommier", "sommiers", []),
    ("placard", "placares", ["placards", "ropero", "roperos"]),
    ("mesa", "mesas", []),
    ("silla", "sillas", []),
    ("escritorio", "escritorios", []),
    ("biblioteca", "bibliotecas", []),
    ("cajonera", "cajoneras", []),
    ("comoda", "comodas", []),
    ("rack", "racks", []),
    ("caja", "cajas", []),
    ("bicicleta", "bicicletas", ["bici", "bicis"]),
]
_FORMA = {}
for singular, plural, otras in _MUEBLES:
    # Primero el plural: si se escribe igual ("lavarropas"), cuenta como singular.
    _FORMA[plural] = (singular, plural, True)
    _FORMA[singular] = (singular, plural, False)
    for o in otras:
        _FORMA[o] = (singular, plural, o.endswith("s") and not singular.endswith("s"))
_MUEBLE = re.compile(
    rf"(?:\b({NUMERO})\s+)?\b("
    + "|".join(re.escape(f) for f in sorted(_FORMA, key=len, reverse=True))
    + r")\b"
)


def leer_item(texto: str) -> Item | None:
    """La vuelta de `str(item)`: "15 cajas" -> Item("caja", ..., 15, True)."""
    m = _MUEBLE.fullmatch(plano(texto.strip()))
    if m is None:
        return None
    singular, plural, en_plural = _FORMA[m.group(2)]
    cantidad = a_numero(m.group(1)) if m.group(1) else None
    return Item(singular, plural, cantidad, en_plural)


def items(texto: str) -> list[Hallazgo]:
    p = plano(texto)
    encontrados: dict[str, Item] = {}
    citas = []
    for m in _MUEBLE.finditer(p):
        singular, plural, en_plural = _FORMA[m.group(2)]
        cantidad = a_numero(m.group(1)) if m.group(1) else None
        previo = encontrados.get(singular)
        if previo is not None and cantidad is None:
            continue
        encontrados[singular] = Item(singular, plural, cantidad, en_plural)
        citas.append(cita(texto, m))
    if not encontrados:
        return []
    return [Hallazgo("items", list(encontrados.values()), 0.8, " ... ".join(citas))]


# --------------------------------------------------------------------------
# Pisos y ascensor
# --------------------------------------------------------------------------

_ORDINALES = {
    "primer": 1, "primero": 1, "segundo": 2, "tercer": 3, "tercero": 3,
    "cuarto": 4, "quinto": 5, "sexto": 6, "septimo": 7, "octavo": 8,
    "noveno": 9, "decimo": 10,
}
_PISO = re.compile(
    r"\b(?:(\d{1,2})\s*(?:er|ro|do|to|vo|no|mo|°|º)?\s*piso|piso\s*(\d{1,2})|("
    + "|".join(_ORDINALES)
    + r")\b|(planta\s+baja|pb)\b)"
)
_SIN_ASCENSOR = re.compile(r"\b(?:sin|no\s+(?:hay|tiene|tengo|tenemos))\s+ascensor\b")
_CON_ASCENSOR = re.compile(r"\b(?:hay|con|tiene|tengo|tenemos)\s+ascensor\b")


def pisos(texto: str) -> list[Hallazgo]:
    """Pisos por escalera. Solo cuenta si se dijo algo del ascensor."""
    p = plano(texto)
    if m := _SIN_ASCENSOR.search(p):
        piso = _PISO.search(p)
        if piso is None:
            return [Hallazgo("floors_no_lift", None, 0.0, cita(texto, m),
                             "dijo que no hay ascensor pero no en que piso")]
        if piso.group(4):
            valor = 0
        elif piso.group(3):
            valor = _ORDINALES[piso.group(3)]
        else:
            valor = int(piso.group(1) or piso.group(2))
        inicio, fin = min(piso.start(), m.start()), max(piso.end(), m.end())
        return [Hallazgo("floors_no_lift", valor, 0.85, texto[inicio:fin].strip())]
    if m := _CON_ASCENSOR.search(p):
        return [Hallazgo("floors_no_lift", 0, 0.8, cita(texto, m))]
    return []


# --------------------------------------------------------------------------
# Ayudantes
# --------------------------------------------------------------------------

_AYUDANTES = re.compile(rf"\b({NUMERO})\s+ayudantes?\b")
_CON_AYUDANTE = re.compile(r"\bcon\s+(?:un\s+)?ayudante\b")
_SIN_AYUDANTE = re.compile(r"\bsin\s+ayudantes?\b")
_SOMOS = re.compile(rf"\bsomos\s+({NUMERO})\b")


def ayudantes(texto: str) -> list[Hallazgo]:
    p = plano(texto)
    if (m := _AYUDANTES.search(p)) and (n := a_numero(m.group(1))) is not None:
        return [Hallazgo("helpers", n, 0.9, cita(texto, m))]
    if m := _CON_AYUDANTE.search(p):
        return [Hallazgo("helpers", 1, 0.85, cita(texto, m))]
    if m := _SIN_AYUDANTE.search(p):
        return [Hallazgo("helpers", 0, 0.85, cita(texto, m))]
    if (m := _SOMOS.search(p)) and (n := a_numero(m.group(1))) is not None:
        return [Hallazgo("helpers", n, 0.7, cita(texto, m),
                         f"'{cita(texto, m)}': gente que carga o que viaja?")]
    return []


# --------------------------------------------------------------------------
# Precio: solo lo dice el fletero
# --------------------------------------------------------------------------

#: Como se dice un precio de verdad. La lista corta de antes solo agarraba
#: "sale 45 mil"; el fletero escribe "esta 35 mil el flete", "son 40 mil",
#: "desde 35mil". El verbo va siempre antes del numero, a proposito: asi
#: "15 mil sale el ayudante" no se lee como el precio del flete.
_PRECIO = re.compile(
    r"\b(?:sale|salen|seria|serian|cuesta|cuestan|cobro|cobrarian|precio|total"
    r"|esta|estan|es|son|queda|quedan|desde|arranca|te\s+lo\s+dejo\s+en|te\s+hago)\b"
    r"[^\d$]{0,12}(\$)?\s*(\d{1,3}(?:[.,]\d{3})+|\d+)\s*(mil|k|lucas)?\b"
)

#: Si el numero es lo que sale el ayudante, no es el precio del flete.
_ES_DEL_AYUDANTE = re.compile(r"\bayudante")


def precio(texto: str) -> list[Hallazgo]:
    p = plano(texto)
    m = _PRECIO.search(p)
    if not m:
        return []
    signo, numero, escala = m.group(1), m.group(2), m.group(3)
    # Un precio se escribe de una de tres formas: "$45.000", "45.000" o
    # "45 mil". Un numero pelado de cuatro cifras es un año, un codigo o
    # cualquier cosa: "es 2026" no es un precio de flete.
    if not (signo or escala or re.search(r"[.,]", numero)):
        return []
    valor = int(re.sub(r"[.,]", "", numero))
    if escala:
        valor *= 1000
    if valor < 1000:  # "salen 2 viajes" no es un precio
        return []
    # "el ayudante son 15 mil" es un extra, no lo que sale el flete.
    if _ES_DEL_AYUDANTE.search(p) and "flete" not in p:
        return []
    return [Hallazgo("quoted_price", valor, 0.95, cita(texto, m))]
