"""Como se escribe una calle y como la tiene el INDEC.

El cliente escribe "11 de setiembre", "b.goñi", "araoz"; el INDEC tiene
"11 DE SEPTIEMBRE", "GOÑI BENIGNO" o "P M ARAOZ". La clave de una calle son
sus palabras importantes, sin tildes, sin mayusculas y sin lo que sobra
("avenida", "calle", iniciales sueltas): con eso se comparan.
"""

from __future__ import annotations

import re
import unicodedata

#: Palabras que no distinguen una calle de otra.
_SOBRAN = {
    "av", "avda", "avenida", "calle", "cl", "pje", "pasaje", "diag", "diagonal",
    "bv", "bvard", "boulevard", "bulevar", "ruta", "rn", "rp",
    "dr", "doctor", "gral", "general", "ing", "ingeniero", "pte", "pres", "presidente",
    "tte", "teniente", "cnel", "coronel", "sgto", "sargento", "cap", "capitan",
    "int", "intendente", "gdor", "gobernador", "cmte", "comandante", "alte", "almirante",
    "sta", "santa", "san", "sto", "santo", "fray", "padre", "pbro", "monseñor", "mons",
    "de", "del", "la", "las", "los", "el", "y", "e",
}
#: Como la escribe la gente -> como la escribe el INDEC.
_IGUALES = {"setiembre": "septiembre", "irigoyen": "yrigoyen"}


def plano(texto: str) -> str:
    sin_tildes = unicodedata.normalize("NFKD", texto.lower())
    sin_tildes = "".join(c for c in sin_tildes if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", sin_tildes).strip()


#: Mar del Plata le pone a cada calle su numero municipal adelante: "C 148 -
#: CATAMARCA", "AV 210 - TETAMANTI". Nadie la nombra asi, y el numero hacia
#: que "Catamarca" encontrara antes la de Madariaga, que no lo tiene.
_CODIGO = re.compile(r"^\s*[a-z]{1,6}\.?\s*\d+\s*(?:bis)?\s*-\s*(?=\S)", re.I)


def palabras(nombre: str) -> tuple[str, ...]:
    """Lo que identifica a la calle. "Av. Pedro M. Aráoz" -> ("pedro", "araoz")."""
    salida = []
    for p in plano(_CODIGO.sub("", nombre)).split():
        p = _IGUALES.get(p, p)
        if p in _SOBRAN or (len(p) == 1 and not p.isdigit()):
            continue
        salida.append(p)
    return tuple(salida)


def clave(nombre: str) -> str:
    """La forma de guardar y buscar: palabras en orden alfabetico."""
    return " ".join(sorted(palabras(nombre)))


def para_mostrar(nombre: str) -> str:
    """"C 273 - RAWSON" -> "RAWSON": el codigo municipal no le dice nada a nadie."""
    return _CODIGO.sub("", nombre).strip()
