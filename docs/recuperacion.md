# Reputación, fletes perdidos y recuperación

Etapa 1: auditoría de lo que existe y diseño de integración. Fuente de verdad del módulo.

## 1. Cómo funciona hoy

Estiba no guarda el estado comercial: lo **deduce** en cada sincronización a partir de la conversación y de
lo que decidió una persona.

```
messages (WhatsApp)  ─┐
job_actions (humano) ─┼─► episodios ─► JobEngine.evaluar ─► jobs / leads / drafts / agenda
chat_clase (humano)  ─┘     (un pedido por      (estado, motivo,      (proyecciones: se
                             episodio de chat)   historia, alertas)    reescriben cada vez)
```

- **Persona** = `contacts` (una por `chat_id`), con el nombre que pone el usuario.
- **Oportunidad** = un *episodio* de la charla. Cada uno es un `job` y un `lead` (`5492...:job`, `:job2`…).
  Una persona que consulta cinco veces ya tiene cinco oportunidades (caso E).
- **Estados**: `JobState` (borrador → presupuestado → esperando → confirmado → agendado → realizado /
  cancelado) y su espejo de embudo `Stage` (nuevo → calificado → presupuestado → confirmado → realizado /
  perdido).
- **Lo que decide una persona** queda en tablas que no se recalculan:
  - `job_actions`: realizado, perdido (con motivo), cotizado, ajustado, anulado, confirmado;
  - `chat_clase`: lead, cliente, privado o spam;
  - `snoozed_chats`: posponer hasta una fecha;
  - `contacts.display_name`: el nombre.
- **Auditoría que ya existe**:
  - `job_events`: cada cambio de estado, con su causa y el mensaje que lo provocó;
  - `job_actions`: quién hizo qué y cuándo;
  - `clase_historia` y `snooze_historia`;
  - `drafts`: qué se propuso, qué se aprobó y qué salió;
  - `estimaciones`: qué precio calculó Estiba antes de saber cómo terminó.

## 2. Lo que ya existe y se reutiliza

| Pedido | Ya existe | Dónde |
|---|---|---|
| Lead / cliente / oportunidades múltiples | sí | `contacts`, `leads`, `jobs`, `jobs/episodios.py` |
| Seguimiento a las 48 h (caso A) | sí | `bandeja/accion.py`: `SEGUIR` a los 2 días de silencio, con borrador |
| Generar → mostrar → aprobar → enviar | sí | `drafts` + bandeja + `WhatsApp.enviar` |
| Motivo de pérdida | parcial | `MOTIVOS_PERDIDA` (8 motivos), en la nota de la acción `perdido` |
| Programar seguimiento | sí | posponer (`snoozed_chats`) |
| Excluir a alguien | parcial | clase privado o spam (lo saca del circuito entero) |
| Origen del lead | parcial | `canal_inferido` (anuncio / cliente que vuelve), `anuncio_de` |
| Lista de clientes | sí | `/clientes` |
| Historial de una oportunidad | sí | detalle del flete (`/fletes/{id}`): conversación + historia |
| Proveedor de mensajes | sí | `whatsapp.py` (`WhatsApp`) sobre `adapters/waha.py` |

## 3. Inconsistencias encontradas (antes de tocar nada)

1. **El motivo de pérdida se guarda como texto, no como código.** La acción `perdido` guarda la etiqueta
   («Consiguió otro»), y el embudo la vuelve a clasificar buscando palabras (`motivo_corto`). Si cambia una
   etiqueta, se pierde la relación con los datos viejos.
2. **«Perdido por silencio» no lo decidió nadie.** A los 4 días sin respuesta el pedido pasa a cancelado.
   De 36 cancelados, 30 son por silencio y solo 6 los marcó una persona. Su motivo además cambia cada día
   («4 días sin respuesta», «5 días…»), porque se recalcula con la fecha de hoy.
3. **El silencio también cierra consultas sin cotizar.** Un «hola» que nunca llegó a presupuesto cuenta como
   flete perdido e infla los perdidos.
4. **Los borradores se reemplazan en cada sincronización.** Todo borrador pendiente de un pedido que no es
   la propuesta actual pasa a «reemplazado». Un mensaje de recuperación guardado ahí se borraría solo.
5. **`leads` y `jobs` son proyecciones.** Cualquier dato humano escrito ahí se pisa en la siguiente
   sincronización. Lo humano va en tablas propias, como ya pasa con `job_actions`.
6. **No hay «no contactar».** Lo más cercano es marcar el chat como privado o spam, pero eso lo saca de los
   números y de la bandeja, que no es lo mismo.
7. **Los ids de pedido pueden cambiar** si se re-corta una charla (`cortes`, `014_renumerar`). Lo que
   pertenece a la persona se guarda por `chat_id`, no por `job_id`.

## 4. Modelo de datos propuesto

No se crean tablas de cliente ni de oportunidad: ya existen. Se agregan dos tablas, solo de alta
(migración 026, reversible porque no toca nada existente):

- **`contacto_eventos`**: todo lo que una persona decide sobre otra persona, sin borrar nunca. Cubre
  ReputationEvent, CustomerTag, Interaction manual y la auditoría de la ficha.
  - Tipos: `no_contactar`, `contactar`, `etiqueta+`, `etiqueta-`, `nota`, `reputacion` (corrección
    manual), `pago_ok`, `pago_problema`, `puntual`, `impuntual`, `facil`, `dificil`, `origen`,
    `no_interesado`, `programado`.
  - El estado actual sale de recorrer los eventos en orden.
- **`recontactos`**: los mensajes de recuperación. Cubre ScheduledMessage y RecoveryQueueItem.
  - Pasan por preparado → enviado / copiado / descartado.
  - Guardan con qué plantilla, de qué secuencia y en qué paso.
  - Viven aparte de `drafts`, así la sincronización no los reemplaza.

Lo demás se **deduce**, igual que el resto de Estiba:

- **Situación de la persona**: lead (nunca contrató), cliente (1 flete hecho), recurrente (2 o más),
  recuperado (perdió una oportunidad y después contrató).
- **Motivo de pérdida**: la acción `perdido` pasa a guardar JSON con el código, el comentario y el precio de
  la competencia. Lo viejo (la etiqueta como texto) se sigue leyendo. El catálogo es configurable (`settings`).
- **Reputación y recovery score**: funciones puras de reglas simples que devuelven el puntaje y sus motivos
  (+/−). La corrección manual es un evento: se ven las dos cosas.
- **Plantillas y secuencias**: datos en `settings` con valores iniciales, editables desde la app.
- **Cola de recuperación**: se calcula a partir de las personas, sus oportunidades, los eventos y los
  recontactos. No se guarda una cola que se desincronice.

## 5. Pantallas

- **Detalle del flete**: el formulario «perdido» con el catálogo nuevo, comentario y precio de la
  competencia.
- **Clientes**: situación, reputación y etiquetas, con filtros. Cada persona abre su **ficha 360**
  (`/clientes/{chat_id}`): encabezado, datos, resumen, reputación con motivos, etiquetas, no contactar,
  observaciones e historial cronológico.
- **Recuperación** (`/recuperacion`): la cola ordenada por prioridad, con filtros y acciones. Se llega desde
  Números → Ventas y desde Hoy, con un contador. No es otra aplicación.
- **Plantillas y secuencias** (`/recuperacion/plantillas`): textos y reglas editables.
- **Números → Recuperación**: indicadores, embudo de recuperación y motivos.

## 6. Reglas de no-spam (fijas en el código, ajustables)

- Nada se envía solo: preparar → revisar → enviar (WhatsApp) o copiar (`wa.me`).
- Hasta 3 recontactos por persona en 180 días, con al menos 7 días entre uno y otro.
- Solo entre las 9 y las 20 h.
- Se detiene si la persona respondió después de la pérdida, si contrató, si tiene «no contactar» o «no
  interesado», si es privado o spam, o si tiene una conversación abierta (eso lo maneja la bandeja).
- Una sola preparación pendiente por persona.

## 7. Riesgos de regresión

- Cambiar el formato de la nota de `perdido`: el motor y el embudo tienen que leer los dos formatos.
- La bandeja no debe ver los recontactos (tabla aparte).
- La cola puede inundarse con los 30 perdidos por silencio: se priorizan y se limitan, y una consulta que
  nunca llegó a presupuesto vale poco.
- La dirección de UX acordada es «mensajería etiquetada, no un CRM que hay que aprender». Por eso todo esto
  vive en pantallas secundarias, y la bandeja de Hoy solo suma un contador.
- Nada sale solo: el usuario apagó los envíos automáticos el 5/10/2026.
