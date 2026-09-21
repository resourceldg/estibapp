# Estiba

Convierte conversaciones de WhatsApp en fletes agendados. Corre entero en tu
máquina, con software libre y sin costo de licencias.

El nombre es el oficio de acomodar la carga para que entre, no se mueva y
llegue entera. Es lo que el sistema hace con conversaciones desordenadas.

## Estado

**Las 7 fases están terminadas.** Estiba lee tu WhatsApp por WAHA (en tu
máquina), transcribe los audios con Whisper local, saca los datos del flete
por reglas (cada uno con la cita de donde salió), arma el flete, te deja en
una bandeja el mensaje que le escribiría al cliente y, cuando lo aprobás, lo
manda. Agenda solo lo confirmado (en tu celular por .ics y, si querés, en
Radicale) y lleva el CRM con el embudo de ventas.

| Fase | Qué trae | Estado |
| --- | --- | --- |
| 1 | Esqueleto, SQLite, ingesta de fixtures, ConversationStore | Listo |
| 2 | AudioPipeline con Whisper local | Listo |
| 3 | Intención y extracción de datos por reglas | Listo |
| 4 | JobEngine, datos faltantes, PricingEngine | Listo |
| 5 | API y dashboard con bandeja de aprobación | Listo |
| 6 | Calendario CalDAV, CRM y funnel | Listo |
| 7 | WAHA en Docker, ingesta real de WhatsApp | Listo |

## Cómo se usa hoy

```bash
uv sync --all-groups --extra audio --extra calendario   # Whisper y CalDAV son opcionales
uv run estiba ingest                    # carga las conversaciones de fixtures/
uv run estiba list                      # qué conversaciones hay
uv run estiba show 5491133334444        # el hilo entero reconstruido
uv run estiba pending-audio             # audios sin transcribir
uv run estiba transcribe                # los pasa por Whisper (--modelo small por defecto)
uv run estiba wer                       # error contra transcripcion_esperada
uv run estiba extract                   # datos del flete con su evidencia, dudas y faltantes
uv run estiba jobs                      # estado, historia, precio sugerido, qué preguntar
uv run estiba jobs --ahora 2026-09-21T12:00   # evaluar como si fuera otra fecha
uv run estiba serve                     # dashboard en http://127.0.0.1:8000, API en /docs
uv run estiba serve --ahora 2026-09-18T12:00  # demo con las conversaciones de prueba
uv run estiba sync                      # evaluar todo, agendar y dejar la bandeja al día
uv run estiba waha estado|conectar|importar   # WhatsApp desde la terminal
uv run pytest                           # 236 tests, no necesitan Whisper, CalDAV ni WAHA
```

## Cómo está armado

El núcleo es la transformación `Conversation → FreightJob`. No importa nada de
WhatsApp, Whisper, Ollama ni CalDAV: todo eso vive detrás de los puertos de
[`core/ports.py`](src/estiba/core/ports.py).

```
src/estiba/
  core/models.py      Tipos del dominio. Slot lleva confianza y evidencia.
  core/ports.py       Protocols: MessageSource, Transcriber, CalendarProvider…
  adapters/fixtures.py  Lee conversaciones de JSON: los casos de prueba.
  adapters/waha.py      WhatsApp por WAHA: cliente, normalización, firma.
  whatsapp.py           Recibir, importar historial, mandar lo aprobado.
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
  bandeja/redactor.py   El mensaje que Estiba propone, a lo sumo uno por flete.
  bandeja/servicio.py   Sincronizar: evaluar todo y dejar la bandeja al día.
  jobs/episodios.py     Un cliente, varios fletes: parte la conversación.
  agenda/               Agenda propia (SQLite), .ics y espejo CalDAV.
  crm/                  Contactos, leads con su historia de etapas, embudo.
  web/                  FastAPI + htmx: dashboard para el celular y API JSON.
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

## WhatsApp

Estiba habla con WhatsApp por [WAHA](https://waha.devlike.pro), que corre en
tu máquina en Docker. Tus conversaciones no pasan por ningún servicio externo.

```bash
cp .env.example .env        # cambiá las claves
docker compose up -d        # WAHA, solo en 127.0.0.1:3000
uv run estiba serve         # y en el dashboard: WhatsApp → Conectar WhatsApp
```

Aparece un código QR: en el celular, *WhatsApp → Dispositivos vinculados →
Vincular un dispositivo*. Desde ahí:

- **Entra solo**: cada mensaje llega por webhook, los audios se bajan y se
  transcriben, y la bandeja se actualiza. Grupos, estados y canales ni llegan.
- **Sale solo lo que aprobás**: *Aprobar y enviar* lo manda desde tu número.
  Si WhatsApp no lo acepta, queda arriba de la bandeja en *No salieron*, con el
  motivo, para reintentar o mandarlo a mano.
- **Historial**: *Importar* trae las conversaciones de los últimos días para
  arrancar con lo que ya venías hablando. Se puede repetir: no duplica.

Sin WAHA, todo lo demás funciona igual y *Aprobar* abre WhatsApp en el celular
con el texto listo.

**Cómo está protegido.** WAHA escucha solo en tu máquina y pide API key. Para
los webhooks, `estiba serve` abre una segunda puerta en la interfaz de Docker
(`172.17.0.1`, configurable con `ESTIBA_IP_DOCKER`) que atiende solo
`/webhooks/` y nada más; la URL lleva un secreto y cada evento viene firmado
con HMAC-SHA512 (`ESTIBA_WAHA_HMAC`), que Estiba verifica.

**Números nuevos de WhatsApp.** WhatsApp está reemplazando el teléfono por un
identificador (`@lid`) en algunos chats. Estiba le pide a WAHA el teléfono; si
WAHA no lo sabe, usa el identificador, que es estable pero no es un número.

Probado contra WAHA 2026.8.2: la API, la firma de los webhooks y la entrega al
host. Lo que no se pudo probar acá es vincular un teléfono real.

## El dashboard

Pensado para el celular, con una mano. Cuatro pestañas, y arriba el estado de
WhatsApp (el punto verde es que está conectado):

- **Bandeja**: una tarjeta por cada cliente al que le toca tu respuesta, con
  el mensaje ya escrito. Lo corregís si hace falta y tocás *Aprobar y
  enviar*: se abre WhatsApp con el texto listo. Nada sale sin ese toque.
  Si el cliente preguntó algo que Estiba no sabe contestar, la tarjeta viene
  vacía para que escribas vos.
- **Agenda**: lo que viene, día por día, y lo que ya pasó y falta marcar
  como realizado. Desde acá te suscribís con el calendario del celular.
- **Fletes**: los próximos por fecha, los que están en conversación y los
  pasados o perdidos.
- **Detalle**: ruta, fecha, precio cotizado contra sugerido con su desglose,
  historia de estados y la conversación. Cada dato muestra la cita de donde
  salió; tocándolo te lleva a ese mensaje. Desde acá le ponés nombre al
  cliente, marcás el flete como realizado o lo das por perdido.
- **Números**: facturado, lo que tenés en agenda, ticket promedio, cuánto
  tardás en contestar, cuánto se aparta tu precio del sugerido, y el embudo:
  de las consultas, cuántas llegan a presupuesto, a confirmar y a hacerse, y
  por qué se pierden las que se pierden. De ahí, la lista de clientes.

La misma información está en la API JSON (`/api/bandeja`, `/api/fletes`,
`/api/agenda`, `/api/embudo`, `/api/clientes`...), documentada en `/docs`.

### Desde el celular

Por defecto escucha solo en tu máquina. Para abrirlo desde el teléfono en la
misma red hay que ponerle clave (sin clave, se niega a arrancar):

```bash
ESTIBA_CLAVE='una-clave-larga' uv run estiba serve --host 0.0.0.0
```

y entrás a `http://<ip-de-tu-máquina>:8000`. Desde fuera de tu casa, mejor
por una VPN como Tailscale que abriendo un puerto. En el celular, "Agregar a
la pantalla de inicio" la deja como una app.

## Agenda

Cada flete confirmado con fecha se agenda solo; si la fecha u hora cambian en
la conversación, el evento se mueve, y si el flete se pierde, se borra. Con
hora exacta el evento dura 3 horas; si solo se dijo "a la tarde", se agenda a
una hora típica de la franja y el título lleva "~"; sin hora, es de todo el día.

- **En el celular**: en *Agenda → Suscribirme* (o copiá el link a Google
  Calendar, *Otros calendarios → Desde URL*). El link lleva un secreto propio,
  así que funciona aunque el dashboard tenga clave; no lo compartas.
- **En Radicale u otro CalDAV** (opcional):

  ```bash
  export ESTIBA_CALDAV_URL=http://localhost:5232/fletero/fletes/
  export ESTIBA_CALDAV_USUARIO=fletero ESTIBA_CALDAV_CLAVE=...
  ```

  Se copia solo lo que cambió. Si el servidor no responde no se pierde nada:
  el dashboard avisa y se reintenta en la próxima actualización.

## Un cliente, varios fletes

WhatsApp es un solo hilo por persona. Estiba lo parte: si el cliente vuelve a
consultar después de un flete realizado o perdido, o después de un mes sin
escribir, es un flete nuevo del mismo contacto. El nuevo no hereda datos del
anterior, y en Números cuenta como un cliente que volvió.

## Cómo se mueve un flete

El estado no se edita a mano: se recalcula recorriendo la conversación, y
cada cambio queda con su causa y el mensaje que lo provocó.

- **borrador → presupuestado**: el fletero dice un precio ("te sale 45 mil").
- **presupuestado → esperando cliente**: el cliente pospone ("mañana te confirmo").
- **→ confirmado**: el cliente acepta *y* origen, destino, fecha y qué se
  lleva están sin dudas. Si acepta con la fecha ambigua, queda una alerta y
  se confirma recién cuando se aclara.
- **confirmado → agendado**: automático, al entrar en la agenda.
- **agendado → realizado**: lo marcás vos, desde la agenda o el flete.
- **→ perdido**: el cliente cancela, lo das por perdido vos (con el motivo,
  si querés), o pasan 4 días sin respuesta después de tu último mensaje. Si
  el último que escribió fue el cliente, no se cancela: la alerta es que está
  esperando respuesta.

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
- Puente de WhatsApp: WAHA en Docker (imagen fijada a `latest-2026.8.2`, la
  versión probada). Plan B: wppconnect.
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
