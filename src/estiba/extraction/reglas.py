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
#: Los nombres pueden llevar "de", "del" o "y" en el medio: "Ortiz de Zárate y
#: Edison", "Mar del Plata". Antes se cortaba en "Ortiz".
_NOMBRE_PROPIO = r"[A-ZÁÉÍÓÚÑ][\wáéíóúñ]*(?:\s+(?:(?:de|del|y)\s+)?[A-ZÁÉÍÓÚÑ][\wáéíóúñ]*)*"
_DE_A_LIBRE = re.compile(
    rf"\b(?:de|desde)\s+({_NOMBRE_PROPIO})"
    rf"\s+(?:a|hasta|para)\s+({_NOMBRE_PROPIO})"
)


#: Lo que parece un lugar pero no lo es. Sale de mensajes reales: "de lunes
#: a viernes", "de 9 a 17h", "de los tuyos no se va a sumar ninguno".
_DIAS_Y_MESES = re.compile(
    r"^(?:lunes|martes|miercoles|jueves|viernes|sabado|domingo|enero|febrero|"
    r"marzo|abril|mayo|junio|julio|agosto|septiembre|setiembre|octubre|"
    r"noviembre|diciembre|manana|hoy|ayer|ahora|aca|alla|ahi)\b"
)
_SOLO_HORA = re.compile(r"^\d{1,2}(?:[:.]\d{2})?\s*(?:hs?|horas?|am|pm)?$")
#: Pronombres, no lugares. Un articulo solo es pronombre si sigue un
#: posesivo ("de los tuyos"): "La Rioja", "Las Dalias" o "El Alfar" son calles
#: y barrios, y antes se descartaban (auditoria: dos pedidos sin origen ni destino).
_PRONOMBRES = re.compile(
    r"^(?:mi|tu|su|vos|nos|ellos|eso|esto|ahi"
    r"|(?:los|las|el|la)\s+(?:tuy|mi|su|nuestr|otr|dema|que\b|cual)\w*)\b")
#: Palabras que aparecen en "de X a Y" sin que X sea un lugar: "de nuevo a
#: casa de mi mama", "de vuelta a la obra".
_NO_LUGAR = re.compile(r"^(?:nuevo|nueva|vuelta|paso|ultima|una|un|otro|otra|donde|adonde|cuando|como)\b")

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


#: Palabras que dicen que es una frase y no una direccion.
_FRASE = re.compile(r"\b(?:no|que|porque|pero|manana|hoy|ayer|cuando|si|tambien|despues|trabaja\w*|tengo|tiene)\b")
_INFINITIVO = re.compile(r"^[a-z]{3,}(?:ar|er|ir)$")


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
    palabras_ = limpio.split()
    # Una frase no es un lugar: "de cruzar a la encargada y mañana no trabaja".
    if _FRASE.search(limpio) or (len(palabras_) == 1 and _INFINITIVO.match(limpio)):
        return False
    # Lo que nombra una cosa que se lleva no es un lugar: "a Tonal unos
    # tirantes de aluminio hasta" (un audio real) era el destino.
    if any(w in _FORMA for w in palabras_):
        return False
    # Con articulo adelante ("la rioja y alberti", "las dalias") solo si es
    # corto o es una esquina: "las materias del segundo cuatrimestre" no.
    if palabras_[0] in ("la", "las", "el", "los") and len(palabras_) > 3 and " y " not in f" {limpio} ":
        return False
    # Al menos una palabra de verdad: "9 y 17" no es una esquina.
    return any(len(t) >= 3 and t.isalpha() for t in re.split(r"[^\wáéíóúñ]+", limpio))


#: «Moreno 2234 (e/ Entre Ríos y Corrientes)»: las entrecalles entre paréntesis cortaban el «desde … hasta …».
_ENTRECALLES = re.compile(r"\s*\((?:e/|/e|entre\b)[^)]{0,80}\)", re.IGNORECASE)


def lugares(texto: str) -> list[Hallazgo]:
    texto = _ENTRECALLES.sub("", texto)
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
    # Se sigue buscando si el primer «de … a …» no es un lugar: en «de la escalera … a ver si … desde Moreno 2234
    # hasta Misiones 1179» (18-nov-2026) el recorrido es el segundo y antes se perdía. Más allá del primero solo
    # cuentan calles con altura en las dos puntas: «de aluminio hasta el barrio» no es un recorrido.
    inicio = 0
    while m := _DE_A_SUELTO.search(texto, inicio):
        origen, destino = m.group(1).strip(), m.group(2).strip()
        con_altura = inicio == 0 or all(re.search(r"[A-Za-zÁÉÍÓÚáéíóúñÑ]\s+\d{2,5}\b", x) for x in (origen, destino))
        if con_altura and _parece_lugar(origen) and _parece_lugar(destino):
            duda = "dirección escrita a mano: conviene confirmarla"
            return [
                Hallazgo("origin", origen, 0.55, m.group(0), duda),
                Hallazgo("destination", destino, 0.55, m.group(0), duda),
            ]
        inicio = m.start() + 1
    return _direccion_sola(texto) or _direcciones_sueltas(texto)


#: "Garcia Lorca al 1140", "García Lorca, al 1140": una calle con su altura,
#: dicha en un audio. Whisper escribe los nombres propios con mayuscula.
_AL_NUMERO = re.compile(
    r"\b((?:[A-ZÁÉÍÓÚÑ][\wáéíóúñ]+\s+){0,3}[A-ZÁÉÍÓÚÑ][\wáéíóúñ]+)\s*,?\s+(?:al|n[°º]?)\s*(\d{2,5})\b")
#: Una calle que se llama como una fecha, despues de "de/desde" o "a/hasta":
#: "de 12 de octubre y tal", "hasta 25 de mayo al 300".
_CALLE_FECHA = re.compile(
    r"\b(de|desde|a|hasta|para)\s+(\d{1,2}\s+de\s+(?:enero|febrero|marzo|abril|mayo|junio|julio|agosto|"
    r"septiembre|setiembre|octubre|noviembre|diciembre))((?:\s+(?:y|e)\s+[a-z]+)|(?:\s+al\s+\d{2,5}))?\b")
_HACIA_ACA = re.compile(r"\b(?:a|hasta|para)\s+(?:aca|mi\s+casa|casa|lo\s+de)\b|\bhasta\b|\bpara\b")


#: Con esta confianza sale una direccion suelta. Completa lo que falta, pero
#: no pisa un lugar dicho con mas claridad (ver `extractor._resolver`):
#: "Aguirre al 500" despues de "de Villa Crespo a Caballito" precisa, no cambia.
CONFIANZA_SUELTA = 0.5


#: Un mensaje que es solo una direccion: "Hipólito Yrigoyen 3751", "Araoz
#: 2619", "Y vamos a Catamarca 1185", "El Alberti 2359 entre santa fe y
#: corrientes". Antes hacia falta el "al" y se perdian (auditoria: 5 casos).
_SOLO_DIRECCION = re.compile(
    r"^\W*(?P<a>(?:y\s+)?(?:vamos|vamos\s+a|va|van|a|hasta|para)\s+(?:a\s+)?)?(?:el|la|en|es\s+en|calle)?\s*"
    r"(?P<calle>[a-záéíóúñ][\wáéíóúñ.]*(?:\s+[a-záéíóúñ][\wáéíóúñ.]*){0,3})\s+(?P<num>\d{3,5})"
    r"(?P<resto>\s+entre\s+.+)?\W*$", re.I)
#: Con esta confianza sale una direccion con altura: mas que una esquina
#: dicha suelta (0,55), menos que un "de Palermo a Belgrano" (0,9).
CONFIANZA_ALTURA = 0.6


#: Palabras con las que no empieza el nombre de una calle: "son 1500" es un
#: precio, "tengo 2000" otra cosa, "dni 30123" un documento.
_NO_ES_CALLE = {
    "son", "es", "sale", "salen", "cuesta", "tengo", "hay", "total", "precio", "pague", "pago", "cobro", "seria",
    "serian", "mas", "menos", "unos", "unas", "como", "cerca", "peso", "pesos", "codigo", "dni", "cuit",
    "numero", "nro", "tel", "telefono", "piso", "depto", "dpto", "km", "kilometros", "metros", "cuadras", "dias",
    "horas", "llego", "llegamos", "salgo", "salimos", "hace", "tipo", "aprox", "son", "quedan", "queda", "esta",
}


def _direccion_sola(texto: str) -> list[Hallazgo]:
    m = _SOLO_DIRECCION.match(texto.strip())
    if not m or len(texto) > 90:
        return []
    calle = m.group("calle").strip()
    if not _parece_lugar(calle) or re.search(r"\d", calle) or _DIAS_Y_MESES.match(plano(calle)):
        return []
    if plano(calle).split()[0] in _NO_ES_CALLE or re.search(r"\bal$", plano(calle)):
        return []   # "Aguirre al 500" lo lee la regla de siempre
    campo = "destination" if m.group("a") else "origin"
    return [Hallazgo(campo, f"{calle} {m.group('num')}", CONFIANZA_ALTURA, m.group(0).strip(),
                     "dirección suelta: conviene confirmarla")]


def _direcciones_sueltas(texto: str) -> list[Hallazgo]:
    """Lo que queda cuando no hay un "de X a Y" limpio: lo que se dice en un audio.

    "De ahi, de 12 de octubre y tal, a aca de donde me viniste a buscar...
    la Garcia Lorca, al 1140": el origen es una calle con nombre de fecha y
    el destino una calle con altura, con charla en el medio. Confianza baja
    y con duda: se ve en la cotizacion y se confirma.
    """
    p = plano(texto)
    hallados: list[Hallazgo] = []
    duda = "salió de un audio o un mensaje suelto: conviene confirmarla"
    for m in _CALLE_FECHA.finditer(p):
        campo = "origin" if m.group(1) in ("de", "desde") else "destination"
        if any(h.campo == campo for h in hallados):
            continue
        valor = m.group(2) + (m.group(3) or "")
        hallados.append(Hallazgo(campo, valor, CONFIANZA_SUELTA, cita(texto, m), duda))
    for m in _AL_NUMERO.finditer(texto):
        calle = m.group(1)
        if _DIAS_Y_MESES.match(plano(calle)) or _PRONOMBRES.match(plano(calle)):
            continue
        antes = plano(texto[max(0, m.start() - 120): m.start()])
        tiene = {h.campo for h in hallados}
        if "destination" not in tiene and (_HACIA_ACA.search(antes) or "origin" in tiene):
            campo = "destination"
        elif "origin" not in tiene:
            campo = "origin"
        else:
            continue
        hallados.append(Hallazgo(campo, f"{calle} {m.group(2)}", CONFIANZA_SUELTA, m.group(0), duda))
    return hallados


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
    ("freezer", "freezers", ["frezer", "frezers", "friser", "frisers", "frizer", "frizers"]),
    ("microondas", "microondas", []),
    ("televisor", "televisores", ["tele", "teles", "tv"]),
    ("sillon", "sillones", ["sofa", "sofas"]),
    ("cama", "camas", []),
    ("colchon", "colchones", []),
    ("sommier", "sommiers", []),
    ("placard", "placares", ["placards", "ropero", "roperos"]),
    # "mesa de luz" va antes que "mesa": se busca primero lo mas largo.
    ("mesa de luz", "mesas de luz", ["mesita de luz", "mesitas de luz"]),
    ("mesita", "mesitas", []),
    ("mesa", "mesas", []),
    ("silla", "sillas", []),
    ("escritorio", "escritorios", []),
    ("biblioteca", "bibliotecas", []),
    ("cajonera", "cajoneras", []),
    ("comoda", "comodas", []),
    ("rack", "racks", []),
    ("caja", "cajas", []),
    ("bicicleta", "bicicletas", ["bici", "bicis"]),
    # De conversaciones reales de la costa: "la maquina", "dos alacenas y un
    # baul", "unas ventanas", "tengo unos bultos".
    ("maquina", "maquinas", []),
    ("cocina", "cocinas", []),
    ("alacena", "alacenas", []),
    ("baul", "baules", []),
    ("ventana", "ventanas", []),
    ("termotanque", "termotanques", []),
    ("bulto", "bultos", []),
    # Auditoria de octubre 2026: cosas reales que no se reconocian.
    ("mueble", "muebles", []),
    ("valija", "valijas", []),
    ("bolsa", "bolsas", []),
    ("tabla", "tablas", []),
    ("biombo", "biombos", []),
    ("placa de durlock", "placas de durlock", ["durlock"]),
    ("tirante", "tirantes", []),
    ("herramienta", "herramientas", []),
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


#: "cama no", "sin la cama", "no es una cama": lo que se saca de la lista.
def _negada(p: str, forma: str) -> bool:
    f = re.escape(forma)
    return bool(re.search(rf"\b{f}\s+no\b|\bsin\s+(?:el\s+|la\s+|los\s+|las\s+)?{f}\b"
                          rf"|\bno\s+(?:es|era|son|eran|va|van)\s+(?:un\s+|una\s+|el\s+|la\s+)?{f}\b", p))


def items(texto: str) -> list[Hallazgo]:
    """Las cosas que se llevan. Lo negado ("cama no") sale aparte para restarse."""
    p = plano(texto)
    encontrados: dict[str, Item] = {}
    negados: set[str] = set()
    citas = []
    for m in _MUEBLE.finditer(p):
        singular, plural, en_plural = _FORMA[m.group(2)]
        # "alacenas de cocina": la cocina describe a la alacena, no es otra cosa.
        antes = p[: m.start()]
        if re.search(r"\b(\w+)\s+(?:de|del)\s+$", antes) and \
                re.search(r"\b(\w+)\s+(?:de|del)\s+$", antes).group(1) in _FORMA:
            continue
        if _negada(p, m.group(2)):
            negados.add(singular)
            continue
        cantidad = a_numero(m.group(1)) if m.group(1) else None
        previo = encontrados.get(singular)
        if previo is not None and cantidad is None:
            continue
        if previo is not None and previo.cantidad and cantidad and previo.cantidad > 1 and cantidad > 1:
            cantidad += previo.cantidad   # "6 cajas de plastico ... 10 cajas de carton": 16
        elif previo is not None and previo.cantidad and cantidad:
            cantidad = max(cantidad, previo.cantidad)   # "una mesa, una mesa": la repite, no son dos
        encontrados[singular] = Item(singular, plural, cantidad, en_plural)
        citas.append(cita(texto, m))
    salida = []
    for n in negados:
        encontrados.pop(n, None)
    if encontrados:
        salida.append(Hallazgo("items", list(encontrados.values()), 0.8, " ... ".join(citas)))
    if negados:
        salida.append(Hallazgo("items_no", sorted(negados), 0.8, ", ".join(sorted(negados))))
    return salida


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
    # "Es planta baja": cero pisos por escalera. "Tengo escalera, 1 piso":
    # uno. Antes hacia falta nombrar el ascensor (auditoria: 3 casos).
    if m := re.search(r"\b(?:planta\s+baja|pb)\b", p):
        return [Hallazgo("floors_no_lift", 0, 0.8, cita(texto, m))]
    if (e := re.search(r"\bescaleras?\b", p)) and (piso := _PISO.search(p)) and not piso.group(4):
        valor = _ORDINALES[piso.group(3)] if piso.group(3) else int(piso.group(1) or piso.group(2))
        inicio, fin = min(piso.start(), e.start()), max(piso.end(), e.end())
        return [Hallazgo("floors_no_lift", valor, 0.75, texto[inicio:fin].strip())]
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

#: Como se dice un precio de verdad. El fletero escribe en miles y casi
#: nunca pone "mil": "te sale 40", "35 le sale", "25 mil sale", "esta 35 mil
#: el flete", "$45.000". Antes solo se leia "verbo + numero con mil o $", y
#: la mitad de los precios reales (11 de 23) quedaban afuera: esos pedidos
#: figuraban como nunca cotizados. Ver audit/AUDIT_REPORT.md.
_VERBO_PRECIO = (r"sale|salen|seria|serian|cuesta|cuestan|cobro|cobrarian|precio|total|valor|esta|estan|es|son"
                 r"|queda|quedan|desde|arranca|te\s+lo\s+dejo\s+en|te\s+hago")
_MONTO = r"(?P<signo>\$)?\s*(?P<numero>\d{1,3}(?:[.,]\d{3})+|\d+)\s*(?P<escala>mil|k|lucas|luca)?"
#: Verbo y despues el numero: "te sale 40", "son 40 mil", "esta 35 mil el flete".
_PRECIO = re.compile(rf"\b(?:{_VERBO_PRECIO})\b(?P<medio>[^\d$]{{0,12}}){_MONTO}\b")
#: Numero y despues el verbo: "35 le sale", "25 mil sale".
_PRECIO_ANTES = re.compile(rf"(?<![\d.,]){_MONTO}\s+(?:le\s+|te\s+)?(?:sale|salen|cuesta|queda)\b")
#: "$190.000" suelto: con el signo no hace falta verbo.
_CON_SIGNO = re.compile(r"(?P<signo>\$)\s*(?P<numero>\d{1,3}(?:[.,]\d{3})+|\d+)\s*(?P<escala>mil|k)?\b")

#: Lo que viene despues de un numero que no es un precio.
_UNIDAD = re.compile(r"^\s*(?:viajes?|vueltas?|cuadras?|km|kilometros?|metros?|mts?|horas?|hs?\b|h\b|minutos?|min\b"
                     r"|pisos?|dias?|personas?|fletes?|cajas?|bultos?|:|\.\d|de\s+la\s+(?:manana|tarde|noche))")
#: Lo que esta cerca de un monto que no es el precio del flete.
_NO_ES_EL_FLETE = re.compile(r"\b(?:ayudantes?|sena|adelanto|propina)\b")


def _monto(signo, numero, escala) -> int | None:
    if not (signo or escala or re.search(r"[.,]", numero)):
        # Numero pelado: en miles si tiene 2 o 3 cifras ("te sale 40").
        n = int(numero)
        return n * 1000 if 10 <= n <= 999 else None
    valor = int(re.sub(r"[.,]", "", numero))
    if escala:
        valor *= 1000
    return valor if valor >= 1000 else None


def precio(texto: str) -> list[Hallazgo]:
    """El precio del flete que dice el fletero. Si da varias opciones, la primera (la base)."""
    p = plano(texto)
    candidatos = []
    for patron in (_PRECIO, _PRECIO_ANTES, _CON_SIGNO):
        for m in patron.finditer(p):
            signo, numero, escala = m.group("signo"), m.group("numero"), m.group("escala")
            if patron is _PRECIO and re.search(r"\b(?:las|la|a\s+las)\b", m.group("medio")):
                continue  # "son las 19": una hora
            if _UNIDAD.match(p[m.end():]):
                continue  # "salen 2 viajes", "son 6 cuadras", "esta 20 minutos"
            alrededor = p[max(0, m.start() - 25): m.end() + 25]
            if _NO_ES_EL_FLETE.search(alrededor) and "flete" not in alrededor:
                continue  # "15 mil sale el ayudante", "una seña de 10 mil"
            valor = _monto(signo, numero, escala)
            if valor is not None:
                candidatos.append((m.start(), valor, m))
    if not candidatos:
        return []
    inicio, valor, m = min(candidatos, key=lambda c: c[0])
    pelado = not (m.group("signo") or m.group("escala") or re.search(r"[.,]", m.group("numero")))
    return [Hallazgo("quoted_price", valor, 0.85 if pelado else 0.95, cita(texto, m))]
