# Estiba

Convierte conversaciones de WhatsApp en fletes agendados. Corre entero en tu
máquina, con software libre y sin costo de licencias.

El nombre es el oficio de acomodar la carga para que entre, no se mueva y
llegue entera. Es lo que el sistema hace con conversaciones desordenadas.

## Estado

**Fase 2 de 7: terminada.** A lo de la Fase 1 (SQLite, ingesta de fixtures,
reconstrucción de conversaciones) se suma la transcripción local de audios con
faster-whisper y la medición del error contra lo que el audio de verdad dice.

| Fase | Qué trae | Estado |
| --- | --- | --- |
| 1 | Esqueleto, SQLite, ingesta de fixtures, ConversationStore | Listo |
| 2 | AudioPipeline con Whisper local | Listo |
| 3 | Intención y extracción de datos por reglas | Pendiente |
| 4 | JobEngine, datos faltantes, PricingEngine | Pendiente |
| 5 | API y dashboard con bandeja de aprobación | Pendiente |
| 6 | Calendario CalDAV, CRM y funnel | Pendiente |
| 7 | WAHA en Docker, ingesta real de WhatsApp | Pendiente |

## Cómo se usa hoy

```bash
uv sync --all-groups --extra audio     # sin --extra audio no se instala Whisper
uv run estiba ingest                    # carga las conversaciones de fixtures/
uv run estiba list                      # qué conversaciones hay
uv run estiba show 5491133334444        # el hilo entero reconstruido
uv run estiba pending-audio             # audios sin transcribir
uv run estiba transcribe                # los pasa por Whisper (--modelo small por defecto)
uv run estiba wer                       # error contra transcripcion_esperada
uv run pytest                           # 63 tests, no necesitan Whisper
```

## Cómo está armado

El núcleo es la transformación `Conversation → FreightJob`. No importa nada de
WhatsApp, Whisper, Ollama ni CalDAV: todo eso vive detrás de los puertos de
[`core/ports.py`](src/estiba/core/ports.py).

```
src/estiba/
  core/models.py      Tipos del dominio. Slot lleva confianza y evidencia.
  core/ports.py       Protocols: MessageSource, Transcriber, CalendarProvider…
  adapters/fixtures.py  Lee conversaciones de JSON. En Fase 7 lo reemplaza WAHA.
  adapters/whisper.py   Transcriber con faster-whisper. Carga el modelo al primer audio.
  audio/pipeline.py     Vacía la cola de audios. Si uno falla, queda pendiente.
  audio/wer.py          Tasa de error por palabra, sin contar tildes ni puntuación.
  storage/            SQLite, migraciones versionadas, repositorios.
  conversation/store.py  Reconstruye el hilo con las transcripciones pegadas.
  cli.py
```

Tres reglas que no se negocian:

1. **Nada sale hacia el cliente sin aprobación humana.** Estiba redacta y deja
   la respuesta en una bandeja.
2. **Ningún dato existe sin evidencia.** Cada campo sabe de qué mensaje salió,
   con qué cita textual y si lo puso una regla o un modelo.
3. **Nada se inventa.** Si falta un dato, se marca y se pregunta.

## Conversaciones de prueba

En [`fixtures/conversaciones/`](fixtures/conversaciones/). Cada una tiene una
`descripcion` que dice qué caso difícil cubre. Las tres actuales:

- **Datos fuera de orden**: el ascensor y los ayudantes llegan cuatro mensajes
  después de la dirección. Rompe cualquier extractor incremental.
- **"Mañana te confirmo"**: posponer no es confirmar, y el silencio posterior
  es una cancelación implícita.
- **Cambio de destino**: el destino cambia a mitad de conversación y la fecha
  viene como "el jueves que viene".

Los audios reales van en `fixtures/audios/` y no se versionan. Para elegir
modelo, poné ahí tus audios, cargá su `transcripcion_esperada` en el fixture y
corré `estiba transcribe --modelo small`, después
`estiba transcribe --modelo medium --comparar`, y mirá `estiba wer`. Cada modelo guarda su propia transcripción y gana la de mayor
confianza.

## Decisiones tomadas

- Python 3.12 fijado con `uv`.
- Puente de WhatsApp: WAHA, que desde la versión 2026.6.1 es gratis y
  Apache-2.0. Plan B: wppconnect.
- Audio: faster-whisper para arrancar, whisper.cpp como plan B.
- Fechas: dateparser más reglas propias para giros rioplatenses.
- Calendario: Radicale por CalDAV, para no depender de Google.
