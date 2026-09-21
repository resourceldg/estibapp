"""AudioPipeline: vacia la cola de audios sin transcribir.

No decide nada del negocio. Toma cada audio pendiente, lo pasa por el
`Transcriber` que le den y guarda el resultado. Si un audio no se puede
transcribir, queda pendiente y se reporta: nunca se inventa un texto.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from estiba.conversation.store import ConversationStore
from estiba.core.models import Message, Transcript
from estiba.core.ports import Transcriber


@dataclass
class ReporteAudio:
    transcriptos: list[Transcript] = field(default_factory=list)
    sin_archivo: list[Message] = field(default_factory=list)
    fallidos: list[tuple[Message, str]] = field(default_factory=list)


class AudioPipeline:
    def __init__(
        self,
        store: ConversationStore,
        transcriber: Transcriber,
        base_dir: Path | str = ".",
    ) -> None:
        self.store = store
        self.transcriber = transcriber
        self.base_dir = Path(base_dir)

    def run(self, limite: int | None = None, modelo: str | None = None) -> ReporteAudio:
        """Transcribe los audios pendientes.

        Con `modelo`, en cambio, toma los que ese modelo no transcribio aunque
        ya tengan transcripcion de otro: asi se comparan modelos con `wer`.
        """
        reporte = ReporteAudio()
        if modelo is None:
            pendientes = self.store.pending_audio()
        else:
            pendientes = self.store.mensajes.audio_without_model(modelo)
        if limite is not None:
            pendientes = pendientes[:limite]

        for m in pendientes:
            ruta = self.base_dir / (m.media_path or "")
            if not ruta.is_file():
                reporte.sin_archivo.append(m)
                continue
            try:
                t = self.transcriber.transcribe(str(ruta), m.id)
            except Exception as e:  # un audio roto no frena al resto
                reporte.fallidos.append((m, str(e)))
                continue
            if t.message_id != m.id:
                reporte.fallidos.append((m, f"el transcriptor devolvio {t.message_id}"))
                continue
            self.store.transcripciones.save(t)
            reporte.transcriptos.append(t)

        return reporte
