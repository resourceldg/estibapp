# Estiba

Convierte conversaciones de WhatsApp en fletes agendados. Corre entero en tu
máquina, con software libre y sin costo de licencias.

El nombre es el oficio de acomodar la carga para que entre, no se mueva y
llegue entera. Es lo que el sistema hace con conversaciones desordenadas.

## Estado

**Fase 4 de 7: terminada.** Estiba ingiere conversaciones, transcribe los
audios con Whisper local, saca los datos del flete por reglas (cada uno con la
cita de donde salió) y arma el flete: en qué estado está y por qué, qué
precio sugiere la tarifa y qué falta preguntarle al cliente.

| Fase | Qué trae | Estado |
| --- | --- | --- |
| 1 | Esqueleto, SQLite, ingesta de fixtures, ConversationStore | Listo |
| 2 | AudioPipeline con Whisper local | Listo |
| 3 | Intención y extracción de datos por reglas | Listo |
| 4 | JobEngine, datos faltantes, PricingEngine | Listo |
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
uv run estiba extract                   # datos del flete con su evidencia, dudas y faltantes
uv run estiba jobs                      # estado, historia, precio sugerido, qué preguntar
uv run estiba jobs --ahora 2026-09-21T12:00   # evaluar como si fuera otra fecha
uv run pytest                           # 135 tests, no necesitan Whisper
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
  extraction/fechas.py  "el sábado", "el jueves que viene", "9 de la mañana"…
  extraction/reglas.py  Barrios, muebles, pisos, ayudantes, precio.
  extraction/intencion.py  Consulta, acepta, pospone, corrige, cancela.
  extraction/extractor.py  Junta todo: gana la última mención, las otras quedan.
  jobs/engine.py        Estado del flete, recalculado mensaje a mensaje.
  jobs/pricing.py       Precio sugerido con desglose. Montos en tarifa.toml.
  jobs/preguntas.py     Qué preguntarle al cliente. Borrador: no se envía.
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

## Cómo se mueve un flete

El estado no se edita a mano: se recalcula recorriendo la conversación, y
cada cambio queda con su causa y el mensaje que lo provocó.

- **borrador → presupuestado**: el fletero dice un precio ("te sale 45 mil").
- **presupuestado → esperando cliente**: el cliente pospone ("mañana te confirmo").
- **→ confirmado**: el cliente acepta *y* origen, destino, fecha y qué se
  lleva están sin dudas. Si acepta con la fecha ambigua, queda una alerta y
  se confirma recién cuando se aclara.
- **→ cancelado**: el cliente cancela, o pasan 4 días sin respuesta después
  de cotizar. Si el último que escribió fue el cliente, no se cancela: la
  alerta es que está esperando respuesta.

## Tarifa

Los montos por defecto están ajustados a las tres cotizaciones de prueba
(sugiere $46.000, $57.000 y $70.000 donde cotizaste $45.000, $60.000 y
$70.000). Para usar los tuyos, creá `tarifa.toml` con los que quieras cambiar:

```toml
base = 30000               # todo flete
conurbano = 15000          # si origen o destino es del conurbano
grande = 8000              # heladera, lavarropas, placard, sillón, cama...
mediano = 3000             # mesa, escritorio, tele...
chico = 1000               # silla, microondas
caja = 700
juego_comedor = 12000      # incluye la mesa y las sillas
piso_sin_ascensor = 7000   # por piso
ayudante = 10000
```

Lo que no se sabe no se cobra ni se esconde: el presupuesto dice "o más" y
lista qué quedó sin contar.

## Decisiones tomadas

- Python 3.12 fijado con `uv`.
- Puente de WhatsApp: WAHA, que desde la versión 2026.6.1 es gratis y
  Apache-2.0. Plan B: wppconnect.
- Audio: faster-whisper para arrancar, whisper.cpp como plan B.
- Fechas: reglas propias. Se pensó en dateparser, pero los casos reales
  ("a la mañana" vs "mañana", "mañana te confirmo", "el jueves que viene"
  dicho un jueves) necesitaban reglas a mano igual. Se suma si aparece un
  formato que las reglas no cubran.
- Fecha, hora y franja van en campos separados: llegan en mensajes distintos
  y juntarlas obligaría a inventar la hora.
- Los datos del flete salen del cliente. Del fletero solo el precio, y las
  fechas u horas que propone con una pregunta y el cliente acepta.
- Calendario: Radicale por CalDAV, para no depender de Google.
