"""Transcriptor local con faster-whisper.

Implementa el puerto `Transcriber`. El modelo se carga recien en el primer
audio: importar este modulo no cuesta nada y los tests no necesitan tener
faster-whisper instalado.

Si faster-whisper queda congelado, el plan B es whisper.cpp con otro adaptador
que cumpla el mismo puerto.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Protocol

from estiba.core.models import Transcript

#: Vocabulario del oficio. Ayuda a Whisper con palabras que en espanol
#: rioplatense suenan parecido a otras mas comunes.
PROMPT_FLETES = (
    "Flete, mudanza, heladera, lavarropas, placard, sillon, colchon, "
    "ascensor, ayudantes, tercer piso, cajas."
)


class _Segmento(Protocol):
    start: float
    end: float
    avg_logprob: float


def confianza(segmentos: Sequence[_Segmento]) -> float:
    """Probabilidad media por token, pesada por la duracion de cada segmento.

    `avg_logprob` es el logaritmo de la probabilidad media de los tokens del
    segmento. No es una probabilidad calibrada, pero ordena bien: sirve para
    decidir que transcripcion gana si hay varias y cual mirar a mano.
    """
    total = 0.0
    peso = 0.0
    for s in segmentos:
        dur = max(s.end - s.start, 0.0)
        total += math.exp(s.avg_logprob) * dur
        peso += dur
    if peso == 0.0:
        return 0.0
    return min(max(total / peso, 0.0), 1.0)


def _falta_cuda(e: Exception) -> bool:
    texto = str(e).lower()
    return any(p in texto for p in ("cublas", "cudnn", "cuda"))


class FasterWhisperTranscriber:
    """Audio a texto con faster-whisper, siempre en la maquina local."""

    def __init__(
        self,
        model_size: str = "small",
        device: str = "auto",
        compute_type: str = "int8",
        language: str = "es",
        beam_size: int = 5,
        initial_prompt: str | None = PROMPT_FLETES,
    ) -> None:
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self.language = language
        self.beam_size = beam_size
        self.initial_prompt = initial_prompt
        self._modelo = None

    @property
    def nombre(self) -> str:
        """Lo que se guarda en `transcripts.model`, para poder re-transcribir."""
        return f"faster-whisper/{self.model_size}"

    def _cargar(self):
        if self._modelo is None:
            try:
                from faster_whisper import WhisperModel
            except ImportError as e:
                raise RuntimeError(
                    "falta faster-whisper. Instalalo con: uv sync --extra audio"
                ) from e
            self._modelo = WhisperModel(
                self.model_size, device=self.device, compute_type=self.compute_type
            )
        return self._modelo

    def _segmentos(self, audio_path: str):
        segmentos, info = self._cargar().transcribe(
            audio_path,
            language=self.language,
            beam_size=self.beam_size,
            initial_prompt=self.initial_prompt,
            vad_filter=True,
        )
        return list(segmentos), info

    def transcribe(self, audio_path: str, message_id: str) -> Transcript:
        try:
            segmentos, info = self._segmentos(audio_path)
        except RuntimeError as e:
            # Con "auto", CTranslate2 elige la GPU si ve una, pero las
            # librerias de CUDA (cuBLAS, cuDNN) pueden no estar: recien falla
            # al transcribir. Se sigue en CPU, que es mas lento pero anda.
            if self.device == "cpu" or not _falta_cuda(e):
                raise
            self.device, self._modelo = "cpu", None
            segmentos, info = self._segmentos(audio_path)
        texto = " ".join(s.text.strip() for s in segmentos).strip()
        if not texto:
            # Un texto vacio se leeria como "el cliente no dijo nada".
            # Mejor que el audio quede pendiente y lo escuche una persona.
            raise ValueError(f"no se detecto voz en {audio_path}")
        return Transcript(
            message_id=message_id,
            text=texto,
            confidence=confianza(segmentos),
            model=self.nombre,
            duration_s=float(info.duration),
        )
