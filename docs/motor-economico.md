# Estiba: motor económico de la camioneta

Especificación consolidada, 30 de septiembre de 2026. Reemplaza a la
propuesta del 29 de septiembre e incorpora sus correcciones. Describe lo que
está implementado en `src/estiba/economia/`.

> Estiba no tiene que ayudar a trabajar más. Tiene que ayudar a trabajar lo
> suficiente, de la forma más rentable, sostenible y simple posible.

## 1. Qué optimiza

La pregunta que ordena todo es cuál es la forma más simple de cumplir los
objetivos del mes con la menor cantidad de horas, desgaste y decisiones.

Los recursos escasos son cuatro: el tiempo de quien maneja, la vida útil de
la camioneta, la capacidad financiera y la atención.

La camioneta cumple sus metas en este orden. Cada una es un escalón de la
cascada del mes:

1. Cubrir su uso: GNC, aceite, cubiertas y desgaste por km.
2. Pagar su estructura: seguro, patente, VTV/GNC, monotributo y celular.
3. Pagar su crédito. Hasta acá, «se paga sola».
4. Mantenerse y generar reservas, con los fondos de conservación.
5. Financiar viajes u otros objetivos personales.
6. Recién ahí, dejar ganancia libre.

El optimizador es lexicográfico, sin sumas mágicas:

1. Cumplir las prioridades activas (de 1 a 3).
2. Con menos horas.
3. Con menos km vacíos.
4. Con menos desgaste.
5. Con menos complejidad (días pico y días trabajados).
6. Recién al final, con más ganancia.

La disponibilidad es un techo, no una obligación. La capacidad deseada (el
ritmo cómodo) y la técnica (48 fletes) son conceptos distintos, y un día
pico nunca se vuelve la expectativa.

## 2. Principios de producto

- **El sistema trabaja primero y la persona decide donde aporta criterio.**
  Cada pedido sale evaluado en tres capas: automático, atención y decisión.
  En modo mínima intervención, Hoy abre en «Para decidir».
- **Recomendación breve primero, porqué después.** Por ejemplo: «Tomaría este
  flete desde $48.000». Después vienen hasta cinco razones, y los números
  quedan plegados, cada uno con su tipo.
- **Nada irreversible sale solo.** Precios, rechazos y mensajes quedan listos
  para aprobar.
- **Cumplir el objetivo puede querer decir dejar de trabajar.** Con las
  prioridades cumplidas, Estiba lo dice («No necesitás tomar más trabajos») y
  nunca muestra un «podrías ganar más».
- **Si no se llega con el ritmo cómodo, se dice como dato de estructura**
  (precio, costos o cuota), no como algo a compensar trabajando más.
- **La precisión visual nunca supera la de los datos**, y la IA generativa no
  calcula nada. Todo es determinístico, con `Decimal`, auditable y con tests.

## 3. Arquitectura

`estiba/economia/` es un conjunto de funciones puras: no lee la base ni arma
HTML. `cargar.py` junta los datos y `web/economia_vista.py` los convierte en
frases.

| Módulo | Qué resuelve |
| --- | --- |
| `modelo.py` | `Valor` y `Parte` con su tipo: declarado, medido, calculado, estimado, supuesto o falta. Una cuenta vale lo que su insumo más débil. |
| `config.py` | Camioneta, crédito, disponibilidad y fondos. Los costos por km, los fijos y la cuota siguen en `Costos` (settings): una sola fuente. |
| `estructura.py` | Contribución por hora (mínima y objetivo), precio mínimo y recomendado, y hasta dónde rinde el precio base. |
| `cuentas.py` | Caja, resultado económico, capital amortizado y reserva líquida, separados. |
| `pedido.py`, `cotizar.py` | Evaluación de cada pedido y el puente con la cotización de siempre. |
| `registro.py` | Lo que pasó realmente al cerrar un flete. |
| `mes.py` | La cascada, la autonomía, el día y las horas de autofinanciamiento, y las prioridades. |
| `suficiencia.py`, `evidencia.py` | Cuánto creerle a cada dato, y la validación del negocio. |
| `optimizar.py` | El plan más simple, la curva, los escenarios y el Monte Carlo. |
| `historia.py` | Segmentos, canales, LTV, jornadas, costo de oportunidad y encadenamiento. Solo cuando alcanza. |
| `reparaciones.py` | Reparaciones, días parada y saldos de los fondos. |

Integración sin romper lo existente:

- `bandeja/servicio.sincronizar` evalúa cada pedido con contexto: cliente que
  vuelve, si vino por un anuncio y los fletes que ya hay ese día. Deja el
  precio recomendado como precio de la cotización, así el borrador ya lo
  lleva. Con la tarifa fija de `tarifa.toml` no cambia nada.
- `finanzas/` queda como la vista de caja histórica y el tablero viejo pasa a
  la pestaña «Análisis».

## 4. Cuentas: nunca se mezclan

| Cuenta | Incluye | No incluye |
| --- | --- | --- |
| Caja | Cobrado; uso; publicidad pagada; ayudantes; estructura; la cuota entera; aportes a fondos; reparaciones pagadas de la caja | Depreciación |
| Resultado económico | Cobrado; uso (incluye desgaste por km); publicidad pagada; ayudantes; estructura; el interés del crédito, si se cargó; la depreciación, solo como supuesto opcional | El capital de la cuota; aportes a fondos; reparaciones (ya están como desgaste) |
| Patrimonio | Capital amortizado (cuota − interés), si se sabe | — |
| Reserva líquida | Saldo de los fondos (suma de movimientos) | — |

Correcciones incorporadas:

- **Pagar capital no crea liquidez.** Los fondos de conservación y reposición
  existen mientras se paga el crédito.
- **La depreciación de la C10 no se inventa.** Solo entra si se carga como
  supuesto; si no, el resultado dice que no la cuenta.
- **Publicidad** (migración 022): cada carga dice si es neto de Meta, neto
  más impuestos cargados o el total de la factura. Rentabilidad y CAC usan lo
  realmente pagado, y el impuesto nunca se cuenta dos veces.

## 5. Fórmulas

```math
c_{uso} = \frac{GNC}{100} + aceite + cubiertas + desgaste \qquad
h = h_{coord} + h_{carga} + h_{cobro} + \frac{km}{v} + extra
```

La **contribución por hora** es una exigencia, no una ganancia. Se reparte
entre las horas **deseadas**: días × fletes cómodos × horas por flete, con
un tope opcional.

```math
w_{min} = \frac{estructura + cuota}{H_{deseadas}} \qquad
w_{obj} = \frac{estructura + cuota + fondos + ganancia\ libre}{H_{deseadas}}
```

```math
P_{min} = km \cdot c_{uso} + directos + CAC + h \cdot w_{min} \qquad
P_{rec} = \max(P_{base},\ \lceil km \cdot c_{uso} + directos + CAC + h \cdot w_{obj} \rceil_{1000})
```

Hasta dónde rinde el precio base: el despeje de cada variable con las demás
en su valor típico. De ahí salen también las horas máximas, la espera, el
CAC admisible, los km vacíos admisibles y los adicionales por km y por hora.

```math
km_{max} = \frac{P_{base} - CAC - directos - h_0 \cdot w_{obj}}{c_{uso} + w_{obj}/v}
```

- **CAC:** solo lo paga el primer flete de un cliente nuevo que vino por un
  anuncio. El que vuelve y el que llegó solo pagan cero.
- **Km vacíos:** ida más vuelta (base → origen y destino → base).
  Deadhead = vacíos / totales.
- **Cascada y autonomía:**

```math
D = cobrado - ayudantes - publicidad - reparaciones_{caja} \qquad
Autonom\acute{\imath}a = \frac{D}{uso + estructura + cuota + fondos_{conservaci\acute{o}n}}
```

- **Día de autofinanciamiento:** el primer día en que lo acumulado cubre el
  escalón 3. **Horas para autofinanciar:** las horas de los fletes hasta ese
  día.
- **Rendimientos decrecientes:** los fletes que llegan solos no pagan
  publicidad; los demás sí, con saturación (duplicar el gasto trae 2^0,8 más
  conversaciones, supuesto). Importa la **hora marginal**, no el promedio: con
  fijos grandes el promedio por hora puede seguir subiendo mientras cada hora
  extra vale cada vez menos. Con un valor de hora libre (opcional), Estiba
  marca hasta dónde conviene agregar trabajo.

## 6. Evaluación de un pedido

El precio evaluado es el hablado con el cliente o, si no hay, el precio base.

| Veredicto | Regla | Capa |
| --- | --- | --- |
| Conviene | `P ≥ P_rec` | Automático: la cotización queda lista para aprobar |
| Conviene con ajuste | `P_min ≤ P < P_rec` | Atención: dice desde cuánto lo tomaría |
| No conviene | `P < P_min` | Atención |
| Requiere decisión | Un trade-off: más de media jornada, un día que ya tiene el ritmo cómodo cubierto, o km inciertos cuyo ±30 % cambia el veredicto | Decisión |

Se muestran siempre el margen, el margen por hora, los km, los km vacíos, el
tiempo, el CAC, la recurrencia y el impacto en la agenda. No hay un puntaje
que los esconda.

## 7. Suficiencia de datos

Reemplaza los umbrales fijos (menos de 8, de 8 a 29, 30 o más). La confianza
es por métrica y por decisión.

- **Técnicas:**
  - bootstrap con semilla fija (ticket, margen, horas, km);
  - Wilson (conversión);
  - cohortes (recurrencia: solo clientes con 60 días o más de observación);
  - variación real de precio (elasticidad; si no hay, es supuesto);
  - exposición en km y meses (roturas grandes; regla de tres);
  - segmentos con sus propios datos.
- **Pesos:** antigüedad (vida media de 120 días) × calidad (medido 1,
  estimado 0,6, supuesto 0,3) × vigencia (0,5 antes de un cambio de precio
  base). Se agrupan por cliente para el tamaño efectivo de muestra (Kish).
- **Estados:** exploratorio, emergente, utilizable y robusto. Combinan la
  precisión del intervalo, el n efectivo, la concentración en un cliente, los
  datos faltantes, el período observado y la antigüedad. Cada estado dice qué
  le falta para subir. Los umbrales están en `UMBRALES`.
- **Presentación:** lo exploratorio se muestra como rango y lo emergente,
  redondeado a $5.000 con «~». Un dato cuenta como evidencia solo si todo su
  intervalo queda de un lado del umbral. Si el umbral cae adentro, no hay
  recomendación fuerte.
- **Validación del negocio:** evidencia a favor, en contra, lo que no
  sabemos y el próximo dato que más reduce la incertidumbre. La pregunta de
  $1.000.000 se responde por dimensión (margen, horas, capacidad, desgaste,
  adquisición, recurrencia), nunca con un sí o no seco.

## 8. Pantallas

| Dónde | Pregunta |
| --- | --- |
| Tarjeta de cotización en el flete | ¿Me conviene? ¿Desde cuánto lo tomo? |
| Cerrar un flete | ¿Fue como estimé? («Se anota así…» / «¿Fue distinto?») |
| Hoy, una línea | ¿Cómo voy este mes? |
| Números → ¿Cómo estoy? | Escalera del mes, autonomía, horas y día en que se pagó, prioridades, resultado real, qué aprender |
| Números → ¿Vale la pena? | Validación del negocio, suficiencia de cada dato, lo que se aprende de la historia |
| Números → ¿Qué pasa si…? | El plan más simple, fletes encadenables, escenarios, curva marginal, Monte Carlo exploratorio |
| Números → Camioneta | Hasta dónde rinde el base; contribución por hora; precio base con historial; disponibilidad; activo y crédito; después del crédito; fondos con saldo; reparaciones; mínima intervención |
| Números → Análisis y Costos y metas | Lo de antes: tablero, informe PDF, costos y gasto en publicidad |

## 9. Modelo de datos

Todas las migraciones son aditivas: tablas o columnas nuevas, sin borrar ni
renombrar nada. Volver atrás es borrar lo nuevo.

| Migración | Qué agrega |
| --- | --- |
| `022_publicidad_pagado` | `campaigns.impuestos` e `incluye_impuestos` |
| `023_economia` | `economia_config` (versionada; manda la última fila por clave) y `precio_base` con historial (inicial $40.000) |
| `024_flete_real` | Lo real de cada flete cerrado, con fuente `estimado`, `confirmado` o `corregido` |
| `025_reparaciones_fondos` | `reparaciones` y `fondo_movimientos` |

Claves de `economia_config`: `camioneta`, `credito`, `disponibilidad`,
`fondos`, `prioridades` y `modo`. La evaluación de cada pedido va a
`estimaciones` con tipo `evaluacion`.

## 10. Supuestos abiertos

Solo el fletero puede cargar estos datos; mientras falten, Estiba lo dice y
no los inventa:

- valor de mercado, valor de reposición y odómetro de la camioneta;
- mes de la primera cuota (sin él, la cuota se cuenta como vigente) y la
  parte de interés;
- aportes a los fondos (hoy en cero: los escalones 4 y 5 figuran «sin
  monto»);
- valor de una hora libre;
- horas de coordinación (15 min) y cobro (6 min), que son supuestos
  configurables;
- saturación de la publicidad (0,8), probabilidad y costo de una rotura
  grande (4 % por mes, $600.000) en el Monte Carlo: son escenarios, no datos.

## 11. Deuda técnica

- `finanzas/pronostico.py` y `jobs/aprendido.py` conservan sus mínimos fijos
  (15 consultas, 3 fletes). Son la vista vieja; conviene pasarlos al motor de
  suficiencia.
- El tablero viejo (Análisis) sigue optimizando precio × publicidad por
  ganancia. Queda como exploración, pero su función objetivo no es la
  lexicográfica.
- No hay revisión semanal automática (resumen o aviso); las pantallas
  existen pero no llaman.
- El costo de oportunidad por franja está apagado hasta tener 8 semanas de
  historia y todavía no tiene cálculo: solo la compuerta.
- Siguen pendientes las fallas silenciosas: supervisión con systemd, errores
  visibles, backup diario y alerta.
