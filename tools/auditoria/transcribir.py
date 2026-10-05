"""Segunda transcripcion de TODOS los audios, solo para la auditoria.

No toca la base ni los archivos: lee los audios y deja, por cada uno, la
transcripcion original de Estiba (faster-whisper small, prompt corto) al lado
de una nueva con el modelo medium y el vocabulario del negocio (localidades
de la costa, muebles). Comparar las dos muestra donde se equivoca el STT.

Salida: audit/audios.jsonl (una linea por audio). Se puede cortar y retomar.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

SALIDA = Path("audit/audios.jsonl")
VOCABULARIO = (
    "Flete, fletero, mudanza, cama, mesa, sillas, heladera, ropero, placard, cajonera, sommier, colchón, "
    "alacena, baúl, lavarropas, sillón, ascensor, ayudante, piso. Mar del Plata, Ostende, Pinamar, "
    "Villa Gesell, Mar Azul, San Clemente, Santa Clara del Mar, Mar de Cobo, Miramar, Camet, Batán, "
    "Mar Chiquita, Chapadmalal, Sierra de los Padres. Constitución, Colón, Independencia, Luro."
)


def main(modelo: str = "medium") -> None:
    from faster_whisper import WhisperModel

    conn = sqlite3.connect("file:estiba.db?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    filas = conn.execute(
        "SELECT m.id, m.chat_id, m.sent_at, m.media_path, t.text, t.confidence, t.model, t.duration_s "
        "FROM messages m LEFT JOIN transcripts t ON t.message_id = m.id "
        "WHERE m.kind = 'audio' ORDER BY m.sent_at").fetchall()
    hechos = set()
    if SALIDA.exists():
        hechos = {json.loads(l)["message_id"] for l in SALIDA.read_text().splitlines() if l.strip()}
    w = WhisperModel(modelo, device="cpu", compute_type="int8")
    with SALIDA.open("a") as out:
        for i, f in enumerate(filas, 1):
            if f["id"] in hechos:
                continue
            reg = {"audio_id": f["id"], "message_id": f["id"], "chat_id": f["chat_id"], "sent_at": f["sent_at"],
                   "audio_path": f["media_path"], "duration_s": f["duration_s"],
                   "transcripcion_original": f["text"], "confianza_original": f["confidence"],
                   "motor_original": f["model"], "language": "es"}
            t0 = time.time()
            try:
                seg, info = w.transcribe(f["media_path"], language="es", beam_size=5, initial_prompt=VOCABULARIO,
                                         vad_filter=True)
                seg = list(seg)
                import math
                dur = sum(max(s.end - s.start, 0) for s in seg) or 1
                reg.update(transcripcion_auditoria=" ".join(s.text.strip() for s in seg).strip(),
                           motor_auditoria=f"faster-whisper/{modelo}+vocabulario",
                           confianza_auditoria=round(sum(math.exp(s.avg_logprob) * max(s.end - s.start, 0)
                                                         for s in seg) / dur, 3),
                           duration_s=round(info.duration, 1), error=None)
            except Exception as e:  # un audio roto no frena al resto
                reg.update(transcripcion_auditoria=None, motor_auditoria=f"faster-whisper/{modelo}", error=str(e))
            reg["segundos_proceso"] = round(time.time() - t0, 1)
            out.write(json.dumps(reg, ensure_ascii=False) + "\n")
            out.flush()
            print(f"{i}/{len(filas)} {reg['segundos_proceso']}s", flush=True)


if __name__ == "__main__":
    main(*(sys.argv[1:]))
