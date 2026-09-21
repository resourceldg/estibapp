"""Tasa de error por palabra (WER) contra la transcripcion esperada.

Sirve para elegir modelo con tus audios reales, no con benchmarks ajenos.
Antes de comparar se ignoran mayusculas, tildes y puntuacion: en WhatsApp
nadie escribe "mañana" igual dos veces, y eso no es un error de Whisper.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


def normalizar(texto: str) -> list[str]:
    sin_tildes = "".join(
        c
        for c in unicodedata.normalize("NFD", texto.lower())
        if unicodedata.category(c) != "Mn"
    )
    return re.findall(r"[a-z0-9]+", sin_tildes)


def distancia(referencia: list[str], hipotesis: list[str]) -> int:
    """Levenshtein por palabras: sustituciones + borrados + inserciones."""
    anterior = list(range(len(hipotesis) + 1))
    for i, r in enumerate(referencia, 1):
        actual = [i]
        for j, h in enumerate(hipotesis, 1):
            actual.append(
                min(
                    anterior[j] + 1,
                    actual[j - 1] + 1,
                    anterior[j - 1] + (r != h),
                )
            )
        anterior = actual
    return anterior[-1]


@dataclass(frozen=True)
class Comparacion:
    errores: int
    palabras: int

    @property
    def wer(self) -> float:
        if self.palabras == 0:
            return 0.0 if self.errores == 0 else 1.0
        return self.errores / self.palabras


def comparar(referencia: str, hipotesis: str) -> Comparacion:
    ref = normalizar(referencia)
    return Comparacion(errores=distancia(ref, normalizar(hipotesis)), palabras=len(ref))


def wer_total(comparaciones: list[Comparacion]) -> float:
    """WER del conjunto, pesado por palabras: un audio largo pesa mas."""
    return Comparacion(
        errores=sum(c.errores for c in comparaciones),
        palabras=sum(c.palabras for c in comparaciones),
    ).wer
