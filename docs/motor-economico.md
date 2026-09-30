# Estiba: motor económico de la camioneta

Documento de diseño, 29 de septiembre de 2026. Todavía no implementado: es la
propuesta a aprobar antes de escribir código. Versión editable y comentable:
[Claude Docs](https://claude.ai/code/artifact/98008bba-10dd-48d9-aa69-cbe0ae529e2d).

## 1. Qué entendí del objetivo

Estiba tiene que encontrar la **mínima cantidad de trabajo que hace que la camioneta se pague sola, se conserve y financie parte de tu vida**, y avisarte cuando ya llegaste. No es maximizar fletes ni facturación.

Lo que cambia respecto de hoy:

- **La unidad de éxito es el mes cumplido, no el mes máximo.** Hoy Estiba reparte una meta de $2.000.000 entre 48 fletes. El nuevo modelo parte de una lista corta de prioridades ordenadas (cuota, costos, fondos, viajes, tope de horas) y dice cuánto falta para cada una.
- **Dos recursos escasos, no uno.** Tu tiempo (con un ritmo cómodo distinto del pico posible) y la vida de la camioneta (cada km consume capital). Todo flete se mide contra los dos: ganancia por hora y ganancia por 100 km.
- **Caja y rentabilidad son dos cuentas separadas.** La cuota del crédito es plata que sale; el desgaste es valor que se consume. Se muestran lado a lado y nunca se suman sin decirlo.
- **Decidir por excepción.** Lo normal se resuelve solo y queda listo para aprobar. Solo pide criterio cuando un pedido sale de tus reglas o hay un trade-off real.
- **Honestidad sobre lo que no se sabe.** Cada número dice si es medido, calculado, estimado o supuesto, con una confianza que sube con la muestra.

La pregunta final del módulo: *¿cuál es la forma más simple de alcanzar mis objetivos este mes?* Si la respuesta es «ya está», Estiba lo dice y no empuja a trabajar más.

## 2. Qué ya existe y se reutiliza

La base es sólida: casi todo lo que el módulo necesita *calcular* ya tiene una pieza parecida, y todo es determinístico, con `Decimal` y con tests (599). Lo que falta es sobre todo *datos por flete* y *un modelo de la camioneta*.

| Pieza actual | Dónde | Qué aporta al módulo nuevo |
| --- | --- | --- |
| Mapa propio (INDEC) + ruta base → origen → destino → base | `mapa/`, `finanzas/precio.py` | Km reales por tramo. Ya separa ida, carga y vuelta: falta etiquetarlos como vacíos o cargados. |
| Precio por objetivos con desglose y supuestos | `finanzas/precio.py` | Esqueleto del «precio desde abajo» y del «qué se supuso». Se extiende, no se reemplaza. |
| `Costos` configurables desde la app | `finanzas/costos.py`, settings `costos` | Montos de partida (crédito $420.000, seguro, GNC, aceite, cubiertas, arreglos). Se reparten en el modelo nuevo. |
| Balance, precio mínimo, plan, ritmo del mes | `finanzas/costos.py` | La cuenta de caja del período. Se mantiene como vista «caja». |
| Pronóstico con Wilson y «medido vs configurado» | `finanzas/pronostico.py` | El patrón de honestidad que pide el punto 24 ya existe (`Dato(valor, medido, detalle)`). Se generaliza a todo el módulo. |
| Optimizador de grilla, tornado, Monte Carlo con semilla fija | `finanzas/decision.py`, `simular.py` | Motor de escenarios y de incertidumbre. Se cambia su función objetivo (hoy: ganancia). |
| Escenarios guardables y aplicables | tabla `decisiones`, `/numeros/tablero` | Base del «¿qué pasa si…?» del punto 23. |
| Detección de lead por anuncio | `_DESDE_PUBLI` en `web/app.py` | Primer clasificador de canal (Meta vs el resto). |
| Clase `cliente` y conteo de recurrentes | `chat_clase`, `crm/embudo.py` | Semilla de CAC vs recurrencia y de LTV. |
| Aprendido: mediana de precios parecidos, mínimo 3 casos | `jobs/aprendido.py` | Patrón para segmentos y precio de mercado propio. |
| Acciones con monto (`realizado`, `ajustado`) y agenda | `job_actions`, `calendar_events` | Cobrado real y fecha/hora: la materia prima para horas por día y capacidad. |
| Bandeja con aprobación y envío automático acotado | `bandeja/` | El mecanismo de «preparar y dejar para aprobar» (punto 39.D) ya existe. |

Datos reales hoy: **6 fletes realizados**, 10 montos cobrados, **1 campaña** (16 al 29 de septiembre, $53.608, 29 conversaciones), 3 clientes. Todo lo que dependa de historia arranca con confianza baja.

## 3. Qué está mal o no alcanza

El error de fondo: **el modelo actual supone que vas a trabajar a capacidad máxima y optimiza ganancia**. Eso empuja justo en la dirección contraria a la que pedís.

| Hoy | Por qué está mal | Consecuencia |
| --- | --- | --- |
| `valor_hora = (meta + fijos) / (48 fletes × 3 h)` | Reparte los fijos entre 144 horas que no querés trabajar. | Cada hora parece barata; el precio sugerido queda bajo y solo cierra si llenás la semana. |
| `fijos_mes` suma crédito, seguro, monotributo, celular | Mezcla deuda (caja), costo de la camioneta y costo del negocio o personal. | No se puede decir «la camioneta se pagó»; al terminar el crédito, $420.000 desaparecen sin plan. |
| Desgaste = `arreglos_km` fijo ($40/km) | No hay depreciación, ni eventos grandes (caja, embrague), ni fondos. | La reparación de $900.000 llega como sorpresa, no como fondo que faltaba. |
| Balance usa `km_por_flete` promedio (64 km) para todos | `precio.py` ya calcula los km reales de cada flete, pero el balance no los usa. | El costo del mes no refleja si hiciste viajes largos o cortos. |
| Publicidad promedio sumada al precio de **todo** flete | `precio.py` agrega `publicidad_por_flete` aunque el cliente sea recurrente. | Al que vuelve se le cobra un CAC que ya pagó; el tablero sí separa, el precio no. |
| Canal binario: anuncio sí/no | Detectado por el texto del botón de Meta. | No hay referido, convenio, Marketplace; no hay rentabilidad por canal. |
| Tiempo = manejo + carga | Faltan espera, coordinación, mensajes, cobro. Nada se mide después. | $/hora sobreestimado, y nunca se corrige con lo real. |
| Todo flete sale y vuelve a la base | No hay encadenamiento ni etiqueta de km vacíos. | No se puede medir ni reducir deadhead. |
| Capacidad = `fletes_por_mes_max` (48) | Un solo número, técnico. Sin días, horas, ritmo cómodo ni pico. | El optimizador llena hasta 48. |
| Objetivo del optimizador = máxima ganancia | La grilla precio × publicidad busca el máximo absoluto. | Recomienda más publicidad y más fletes aunque la meta ya esté cumplida. |
| Monte Carlo sin roturas | Sortea cierre, consultas y ticket; los costos de la camioneta son fijos. | Subestima el riesgo real del mes. |
| Al marcar realizado solo se guarda el monto | Sin km reales, horas reales, ayudante pagado, espera. | El sistema no puede aprender; todo queda en estimación. |
| `tarifa_base` $30.000 existe pero no es «precio base» | Se usa solo como relleno del ticket; no tiene historial; no hay precio base de $40.000. | Precio base, mínimo y ticket no se distinguen en pantalla. |

Lo que **no** está mal y se conserva: el mapa, el patrón `Dato(medido/supuesto)`, Wilson, la semilla fija, las reglas «nada se inventa» y «nada sale sin aprobación».

## 4. Arquitectura propuesta

Un paquete nuevo, `estiba/economia/`, de **funciones puras**: no lee la base ni arma HTML. Los repositorios le pasan datos y `web/presentacion.py` convierte sus resultados en frases. Es el mismo corte que ya usan `finanzas/` y `jobs/`, y es lo que lo hace testeable y auditable.

```
 ENTRA                        MOTOR ECONÓMICO · estiba/economia          SALE
                             ┌──────────────────────────────────┐
 Conversaciones y mapa  ───► │ Costo y capacidad                │ ───►  Tarjeta del pedido
   km por tramo, cosas,      │   uso, desgaste y riesgo por km  │         en la conversación
   pisos                     │   horas deseadas, no máximas     │
                             │ Precio                           │
 Lo que querés          ───► │   recomendado y mínimo           │ ───►  Borrador con precio
   días, horas, prioridades  │   hasta dónde rinde el base      │         listo para aprobar
                             │ Evaluar pedido                   │
 La camioneta           ───► │   veredicto, razones, capa       │ ───►  Hoy
   costos, crédito, fondos   │   CAC solo al cliente nuevo      │         una línea: cómo va el mes
                             │ El mes                           │
 Lo que pasó            ───► │   cascada y niveles              │ ───►  ¿Cómo estoy?
   cobrado, km y horas       │   autonomía, día en que se pagó  │         escalera y 7 indicadores
   reales                    │ Optimizar y simular              │
 Publicidad             ───► │   el plan de menos horas         │ ───►  ¿Qué pasa si…?
   gasto y clientes por      │   que cumple tus prioridades     │         escenarios del mes
   canal                     │                                  │
                             │  Funciones puras, con Decimal;   │
                             │  cada valor con tipo y confianza │
                             └──────────────────────────────────┘
```

Cómo entra sin romper lo que funciona:

- **Precio.** `PrecioPorObjetivos.estimate(job)` mantiene su interfaz y pasa a delegar en `economia`. `Presupuesto.detalle` suma la evaluación; la cotización actual la muestra sin cambiar de lugar.
- **Sincronizar.** `bandeja/servicio.py` ya evalúa todo al sincronizar: ahí se calcula y se guarda la evaluación de cada pedido abierto, igual que hoy el precio en `estimaciones`.
- **Capas de decisión.** Automático, atención y decisión se traducen a los filtros de la bandeja que ya existen; no hay bandeja nueva.
- **Lo viejo.** `finanzas/` queda como la vista de caja. `decision.py` y `simular.py` se reusan cambiando la función objetivo y agregando roturas. `Costos` se migra una vez a camioneta, crédito, negocio y fondos.
- **Módulos.** `modelo.py` (tipos y `Dato` con tipo y confianza, generalizando el de `pronostico.py`), `camioneta.py`, `capacidad.py`, `precio.py`, `evaluar.py`, `mes.py`, `clientes.py`, `optimizar.py`, `escenarios.py`.

## 5. Entidades nuevas

Pocas entidades guardadas y muchas derivadas: lo que se puede calcular no se carga a mano (punto 39.E y 45).

| Entidad | Qué es | Se guarda o se deriva |
| --- | --- | --- |
| **Disponibilidad** | Días por semana, horas por día, fletes por día cómodo (2) y pico (3), días que no, franjas preferidas, tope de horas semanal y mensual. Separa *capacidad técnica* de *capacidad deseada*. | Guardada, versionada por fecha |
| **Precio base** | Valor comercial de referencia ($40.000). Cada cambio es una fila: desde, monto anterior, monto nuevo. | Guardada, con historial |
| **Camioneta** | Valor de mercado hoy, valor al reponerla, km actuales (odómetro), vida útil restante en km. | Guardada; el odómetro se actualiza al cargar un flete o una reparación |
| **Crédito** | Cuota, cuotas totales (72), primera cuota, tasa si se conoce. Deriva cuotas restantes y fecha de fin. | Guardado; separado de la camioneta |
| **Costos de la camioneta** | Por km: GNC, aceite y filtros, cubiertas, desgaste ordinario. Por mes: seguro, patente, VTV/GNC. | Guardados (salen de los `Costos` actuales) |
| **Costos del negocio** | Monotributo, celular, otros. No son de la camioneta. | Guardados |
| **Fondo** | Mantenimiento, extraordinario, cubiertas, reposición, viajes. Aporte por km o por mes, objetivo, prioridad, saldo. | Guardado; movimientos: aporte y gasto |
| **Reparación** | Fecha, componente, ordinaria o extraordinaria, costo, km, días fuera de servicio, fondo que la pagó. | Guardada (lo único nuevo que se carga a mano) |
| **Prioridades del mes** | De 1 a 3 activas, ordenadas: cubrir cuota, costos, fondo X, viajes $Y, no pasar Z horas. | Guardadas por mes; el mes siguiente hereda |
| **Registro real del flete** | Km totales y vacíos, horas por etapa, espera, ayudante pagado, canal, cobrado. Arranca con la estimación; al marcar realizado se confirma con un toque o se corrige. | Guardado al cerrar |
| **Canal** | Meta, recurrente, referido, orgánico, Google, Marketplace, WhatsApp directo, convenio, otro. Se infiere y se puede corregir. | Guardado por lead |
| **Evaluación del pedido** | Veredicto, capa de decisión, precio recomendado, razones y el desglose con su tipo de dato. | En `estimaciones` (tipo `evaluacion`), como hoy el precio |
| Cliente (LTV) | Trabajos, ingreso y margen acumulados, canal de origen, CAC asignado. | Derivado |
| Jornada | Día, fletes, horas, km, días seguidos trabajados. | Derivada de agenda y registros |
| Segmento | Urbano corto/medio/largo, Santa Clara, Miramar, interurbano, mudanza, carga especial, comercial recurrente. | Derivado: primero por reglas, después agrupando |
| Nivel del mes | En qué escalón de éxito está el mes. | Derivado |

## 6. Métricas y niveles de éxito

El corazón es una **cascada del mes**: lo que entra baja por escalones en el orden de tus prioridades, y cada escalón se llena o no. De ahí salen el nivel, la autonomía y el día de autofinanciamiento, sin fórmulas aparte.

**Niveles** (nombres propuestos, en tus palabras de la pantalla):

| Nivel | Nombre | Se alcanza cuando lo que dejaron los fletes cubre… |
| --- | --- | --- |
| 0 | Pierde | ni el uso de la camioneta (GNC, aceite, cubiertas, desgaste) |
| 1 | Cubre el uso | el uso, pero no los fijos ni la cuota |
| 2 | Se paga sola | uso + seguro, patente, VTV + cuota del crédito |
| 3 | Se conserva | lo anterior + los fondos de mantenimiento, extraordinario, cubiertas y reposición |
| 4 | Te da vida | lo anterior + el fondo de viajes u objetivo personal |
| 5 | Deja libre | lo anterior + ganancia neta sin destino |

**Pantalla principal** (cambian decisiones; pocas):

- **Autonomía de la camioneta**: qué porcentaje de su costo total del mes pagó su trabajo. 118% = se pagó y sobró 18%.
- **Horas para que se pague sola**: horas trabajadas hasta llegar al nivel 2 este mes.
- **Día en que se pagó**: la fecha en que la cascada cruzó el nivel 2 (o la proyectada, marcada como estimación).
- **Ganancia económica por hora**: lo que queda después de todos los costos económicos, dividido por horas reales.
- **Ganancia por 100 km**: cuánto valor dejaste por cada 100 km de vida de la camioneta.
- **Km vacíos**: porcentaje de km sin carga sobre km totales.
- **Horas usadas / tope**: contra tu capacidad deseada, no la técnica.

**Vista semanal y mensual** (se abren, no dominan): ingreso bruto por hora, margen de contribución por hora y por km, ticket medio, precio base vigente, % recurrentes, CAC por canal, trabajos por cliente, LTV y LTV/CAC, rentabilidad por canal y por segmento, jornadas de 2 vs 3 fletes, días fuera de servicio.

**Índice de eficiencia del flete** (0 a 100): suma ponderada de cinco componentes normalizados contra tus umbrales: margen/hora, margen por 100 km, km vacíos, recurrencia y horas usadas. Siempre se muestra con sus cinco barras; nunca el número solo. Los pesos se exponen y se pueden mover. Arranca como vista secundaria: el veredicto del punto 14 no depende de él, depende de umbrales que entendés.

**Cada número lleva su tipo**: *medido* (salió de un registro real), *calculado* (cuenta sobre medidos), *estimado* (sobre supuestos del mapa o de promedios), *supuesto* (valor configurado sin dato). Y una confianza: baja con menos de 8 fletes comparables, media de 8 a 29, alta desde 30. Umbrales configurables.

## 7. Fórmulas

Todas determinísticas, en `Decimal`, y cada una devuelve su valor junto con su tipo de dato y su explicación.

**Km del flete.** Tramos del recorrido; si dos fletes se encadenan, el tramo entre ellos reemplaza una vuelta y una ida.

```math
km_{total} = km_{ida} + km_{carga} + km_{vuelta} \qquad km_{vacíos} = km_{ida} + km_{vuelta} \qquad deadhead = km_{vacíos} / km_{total}
```

**Horas del flete.** Coordinación y cobro arrancan como supuesto configurable (por ejemplo 20 y 10 minutos) y se reemplazan por lo medido.

```math
h = h_{coord} + \frac{km_{total}}{v} + h_{carga}(\text{cosas, pisos}) + h_{espera} + h_{cobro}
```

**Costo de la camioneta por km, en tres capas que no se pisan:**

```math
c_{uso} = \frac{GNC}{100} + aceite + cubiertas + desgaste \qquad c_{dep} = \frac{V_{hoy} - V_{residual}}{km_{vida\ restante}} \qquad c_{riesgo} = \frac{\text{fondo extraordinario anual}}{km_{año}}
```

**Margen y ganancia de un flete.** El CAC solo se imputa al primer trabajo de un cliente nuevo de canal pago. Los fijos se reparten entre tus **horas deseadas**, no las máximas: esa es la corrección central.

```math
MC = P - km_{total} \cdot c_{uso} - directos - CAC \qquad G_{eco} = MC - km_{total}(c_{dep} + c_{riesgo}) - h \cdot \frac{fijos_{mes}}{H_{deseadas}}
```

**Hora objetivo y hora mínima.** La objetivo cubre todo lo que pediste que el negocio haga por vos; la mínima, solo hasta «se paga sola».

```math
w_{obj} = \frac{fijos_{cam} + cuota + fondos + objetivo_{personal} + costos_{negocio}}{H_{deseadas}} \qquad w_{min} = \frac{fijos_{cam} + cuota + costos_{negocio}}{H_{deseadas}}
```

**Precio recomendado y mínimo** (desde abajo, redondeado a $1.000; nunca por debajo del precio base salvo regla explícita):

```math
P_{rec} = \max\left(P_{base},\ km_{total} \cdot c_{uso} + directos + CAC + h \cdot w_{obj}\right) \qquad P_{min} = km_{total} \cdot c_{uso} + directos + CAC + h \cdot w_{min}
```

**Hasta dónde rinde el precio base** (punto 5). Con h = h₀ + km/v, despejando km:

```math
km_{max} = \frac{P_{base} - CAC - h_0 \cdot w_{obj}}{c_{uso} + w_{obj}/v} \qquad adicional\ por\ km = c_{uso} + \frac{w_{obj}}{v}
```

Lo mismo da horas máximas, km vacíos admisibles y CAC admisible: cada uno despejando su variable con las otras en su valor típico.

**Cascada, nivel y autonomía del mes.**

```math
D = \sum ingresos - directos - publicidad - costos_{negocio} \qquad Autonomía = \frac{D}{uso_{mes} + fijos_{cam} + cuota + fondos_{cam}}
```

El denominador es el costo de *sostener y conservar* la camioneta (nivel 3). **Día de autofinanciamiento** = primer día en que la suma acumulada de D cruza el nivel 2; **horas para autofinanciar** = horas de los fletes hasta ese día.

**Cuota vs depreciación, sin doble conteo.** La cascada es de caja: usa la cuota. La ganancia económica usa depreciación e interés. Mientras dura el crédito, el fondo de reposición arranca en cero (la cuota ya es capital). Al terminar, la cuota sale y el fondo de reposición (u otro destino que elijas) toma su lugar: el modelo no deja que los $420.000 desaparezcan sin decisión.

**CAC y LTV.**

```math
CAC_{canal} = \frac{gasto_{canal}(1 + imp)}{clientes\ nuevos_{canal}} \qquad LTV = \sum_{trabajos} MC - CAC_{origen}
```

**Costo de oportunidad** (solo con 8 semanas o más de historia en esa franja; si no, no se menciona):

```math
CO = h_{desplazadas} \cdot \text{mediana}(MC/h)_{franja} \cdot p(\text{otro pedido en la franja})
```

**Encadenar B y C** (ventanas horarias compatibles):

```math
ahorro_{km} = km(B_{dest} \to base) + km(base \to C_{orig}) - km(B_{dest} \to C_{orig})
```

**Función objetivo del optimizador**, lexicográfica y no una suma mágica: (1) alcanzar las prioridades activas del mes en su orden; (2) entre los planes que lo logran, el de **menos horas**; (3) a igualdad, el de menos km vacíos; (4) a igualdad, el de más ganancia. Sujeto a días, horas y fletes por día deseados. Si ningún plan alcanza, dice cuál prioridad queda afuera y qué haría falta, sin subir el tope solo.

**Veredicto del pedido** (umbrales tuyos, ninguno escondido):

| Veredicto | Regla | Capa |
| --- | --- | --- |
| Conviene | `P ≥ P_rec`, margen/hora ≥ `w_obj`, horas y deadhead dentro de tus topes | Automático: queda listo para aprobar |
| Conviene con ajuste | `P_min ≤ P < P_rec` | Atención: muestra el precio que lo vuelve «conviene» |
| No conviene | `P < P_min`, o margen/hora < `w_min` | Atención o decisión |
| Cualquiera + trade-off | Ocupa más del umbral de la jornada, cae en día ya en pico, o CO con confianza media o alta | Decisión: pide criterio |

Si el mes ya alcanzó tus prioridades, la razón lo dice («no lo necesitás para tu objetivo») y el pedido se evalúa solo por lo que suma, nunca como «podrías ganar más».

## 8. Pantallas

Cada pantalla es una pregunta tuya, no una entidad del sistema. Lo diario vive donde ya trabajás (la conversación); lo mensual, en Números.

| Ritmo | Dónde | Pregunta que responde | Qué muestra primero |
| --- | --- | --- | --- |
| Cada pedido | Tarjeta en la conversación y en la bandeja, junto a la cotización actual | ¿Este flete me conviene? ¿Cuánto cobro? | Una línea: *Tomaría este flete a partir de $48.000*. Veredicto y hasta 3 razones. «¿Por qué?» abre el desglose con el tipo de cada dato. El borrador de respuesta ya lleva ese precio, para aprobar. |
| Al cerrar | Botón Realizado | ¿Fue como estimé? | *38 km · 2 h 10 · $40.000.* [Así fue] [Corregir]. Un toque: así se aprende sin cargar datos. |
| Diario | Hoy (arriba, una línea) | ¿Cómo voy? | *La camioneta se pagó el 21. Lo que venga es para tus fondos.* o *Faltan unos 4 fletes como los últimos.* |
| Semanal | Números → **¿Cómo estoy?** | ¿Ya se pagó? ¿Cuánto trabajé para pagarla? ¿Cuánto puedo guardar para viajar? ¿Puedo no trabajar esta semana? | La escalera de niveles del mes llenándose, y los 7 indicadores del punto 6. Nada más arriba. |
| Semanal | Números → **¿Qué me conviene?** | ¿Qué tipo de flete? ¿Qué canal? ¿2 o 3 por día? | Frases con su confianza: *Tus jornadas de dos fletes dejan $X/h más que las de tres (confianza baja: 4 jornadas).* Debajo, segmentos y canales. |
| Mensual | Números → **¿Qué pasa si…?** | ¿Y si subo el base 10%? ¿Y si no trabajo el viernes? | Palancas del punto 23 como interruptores; cambian al instante ganancia, horas, km, desgaste, cuota cubierta, fondo viajes y $/hora. Reusa los escenarios guardables de hoy. |
| Mensual | Números → **Mi camioneta** | ¿La estoy cuidando? ¿Qué pasa cuando termine el crédito? | Fondos con saldo y objetivo, reparaciones y días parada, km, cuotas restantes y la estructura *después del crédito*. |
| Una vez, y cuando cambie | Configuración → **Lo que quiero que haga por mí** | ¿Cuánto quiero trabajar y para qué? | Disponibilidad, precio base con historial, 1 a 3 prioridades ordenables, umbrales «si A, hago B», modo *mínima intervención*. |

El tablero de hoy (Monte Carlo, tornado, mapa de calor) pasa a una pestaña **Análisis**, secundaria. No se borra: sirve para explorar, no para decidir el día.

**Modo mínima intervención** (activable): los pedidos «Conviene» dejan la cotización redactada con el precio recomendado y no generan aviso; solo avisan «atención» y «decisión». Nunca se envía un precio, se acepta o se rechaza un cliente sin tu aprobación.

## 9. Cambios de base de datos

Cinco migraciones aditivas: no se borra ni se renombra nada, así lo actual sigue funcionando mientras se construye lo nuevo. Montos como texto `Decimal`, igual que hoy.

| Migración | Crea o cambia | Para qué |
| --- | --- | --- |
| `022_precio_base.sql` | `precio_base(id, monto, desde, anterior, nota, at)` | Historial del precio base. La fila vigente es la de `desde` más reciente ≤ hoy. |
| `023_camioneta.sql` | `camioneta(clave, valor, desde)` (valor de mercado, residual, odómetro, vida útil) · `credito(id, cuota, cuotas, primera, tasa)` · `reparaciones(id, fecha, componente, tipo, costo, km, dias_parada, fondo_id, nota)` | Activo, deuda y eventos, separados. |
| `024_fondos.sql` | `fondos(id, nombre, tipo, aporte_km, aporte_mes, objetivo, prioridad, activo)` · `fondo_movimientos(id, fondo_id, fecha, monto, origen, nota)` | Saldos por movimientos, nunca un número pisado. |
| `025_operacion.sql` | `disponibilidad(desde, json)` · `prioridades(mes, orden, tipo, valor)` · `lead_canal(job_id, canal, quien, at)` | Capacidad deseada versionada, prioridades del mes, canal inferido o corregido. |
| `026_registro_flete.sql` | `flete_real(job_id, km_ida, km_carga, km_vuelta, km_entre, h_coord, h_manejo, h_carga, h_espera, h_cobro, ayudante_pagado, directos, confirmado, at)` | Lo que pasó, con la estimación como punto de partida. `confirmado=0` = todavía es estimación. |

La evaluación del pedido reusa `estimaciones` con `tipo='evaluacion'`, que ya guarda valor y supuestos en JSON. Los `Costos` actuales en `settings` se leen una vez para sembrar camioneta, crédito ($420.000 × 72) y fondos; después quedan como vista de compatibilidad hasta migrar sus pantallas. Antes de aplicar, backup automático del `.db` como las veces anteriores.

## 10. Riesgos

El riesgo mayor no es técnico: es **que el módulo pida más carga de datos de la que devuelve** y se abandone. Todo el diseño apunta a que el único dato manual nuevo sea la reparación.

| Riesgo | Mitigación |
| --- | --- |
| Precisión falsa con 6 fletes | Cada número lleva tipo y confianza; bajo 8 casos no hay recomendaciones estratégicas, solo reglas. Tests específicos de «confianza baja con poca muestra». |
| Supuestos iniciales malos (valor residual, vida útil, riesgo de rotura) | Se muestran como *supuesto* con su fuente, en un solo lugar editable. El veredicto de un pedido usa sobre todo `c_uso` y `w_obj`, que son los más medibles. |
| Horas reales nunca registradas | Confirmación de un toque al marcar realizado; sin confirmar, queda como estimación y no suma confianza. |
| Canal mal inferido | Hoy solo Meta es detectable con seguridad. El resto arranca como «otro» y se corrige con un toque; un recurrente se infiere porque ya es `cliente`. |
| Doble conteo cuota / depreciación / fondo reposición | Dos vistas explícitas (caja y económica) y una regla escrita (sección 7), con test. |
| Romper el precio actual | El motor nuevo se agrega detrás de la misma interfaz `estimate(job)`; el viejo sigue hasta que los tests nuevos y los 599 actuales pasen. |
| El sistema empuja a trabajar más | Función objetivo lexicográfica con «menos horas» antes que «más ganancia», y test de que con objetivo cumplido no recomienda más trabajo. |
| Pantallas llenas de números | Regla de la sección 8: primero la frase, después el porqué. Cada métrica nueva tiene que nombrar la decisión que cambia. |
| Encadenamiento y costo de oportunidad sin datos | Se construyen al final y se apagan solos por debajo de su umbral de historia. |
| Fallas silenciosas del sistema (pendiente previo) | Un módulo que decide precios no puede fallar callado. Conviene cerrar antes, o en paralelo, supervisión con systemd, errores visibles y backup diario. |

## 11. Orden sugerido de implementación

Seis etapas; cada una deja algo usable y sus tests antes de pasar a la siguiente. Las cuatro primeras sirven desde el primer día con reglas; las dos últimas se encienden con historia.

1. **Motor núcleo, sin pantallas nuevas.** Paquete `economia/`: camioneta en tres capas, crédito separado, precio base con historial, disponibilidad deseada vs técnica, hora objetivo y mínima con horas deseadas, precio recomendado y mínimo, «hasta dónde rinde el base». Migraciones 022 a 025 y configuración en «Lo que quiero que haga por mí».
    - Tests: precio base configurable, histórico, cuota, TCO, costo variable, mantenimiento, precio mínimo, capacidad deseada, máximo diario, fin del crédito.
2. **Evaluación del pedido.** Km por tramos con vacíos, CAC solo al cliente nuevo, veredicto con razones y capa, tarjeta en la conversación, precio recomendado en el borrador.
    - Tests: cliente nuevo, recurrente, CAC, km vacíos, margen/hora, margen/km, recomendación de trabajo.
3. **Lo que pasó y el mes.** Confirmación de un toque al realizar (migración 026), fondos, reparaciones y días parada, cascada, niveles, autonomía, día y horas de autofinanciamiento; «¿Cómo estoy?» y la línea de Hoy.
    - Tests: fondos, cálculo de autofinanciamiento, downtime, reparación extraordinaria.
4. **Prioridades, optimizador y escenarios.** 1 a 3 prioridades, función objetivo lexicográfica, curvas ganancia vs horas y $/hora vs fletes, «¿Qué pasa si…?», estructura después del crédito, «objetivo cumplido».
    - Tests: escenarios, optimización con restricción horaria, fin del crédito en escenarios, que con objetivo cumplido no recomiende más trabajo.
5. **Clientes, canales y segmentos.** Canal por lead, LTV y LTV/CAC, rentabilidad por canal, segmentos por reglas (Santa Clara, Miramar, bandas de km), «¿Qué me conviene?» con confianza.
    - Tests: LTV, baja confianza con poca muestra, aumento de confianza con más datos.
6. **Con historia suficiente.** Costo de oportunidad por franja, encadenamiento de fletes, ritmo sostenible (jornadas de 2 vs 3), Monte Carlo con roturas de baja probabilidad y alto impacto, índice de eficiencia desplegable. Segmentos automáticos por agrupamiento solo si mejoran a las reglas.

IA generativa: ninguna en estas etapas. Si después se agrega, es para redactar la explicación a partir de razones ya calculadas, nunca para calcular.

**Para arrancar necesito que confirmes o corrijas cuatro supuestos:** valor de mercado actual de la camioneta, cuántas cuotas de las 72 ya pagaste, km actuales del odómetro, y horas por mes que querés trabajar como máximo (propuesta inicial: 4 días × 2 fletes × ~2,5 h ≈ 80 h/mes como techo, con prioridad de usar menos).
