-- Los precios que quedaron anotados en pesos sueltos.
--
-- El fletero dice "treinta y cinco" y escribe "35". Hasta ahora eso se
-- guardaba tal cual, asi que hay un flete anotado en $35 conviviendo con
-- otro de $40.000 del mismo cliente. Esos numeros son el material con el
-- que Estiba aprende a sugerir precios: uno mal corre la mediana entera.
--
-- No existe un flete de menos de mil pesos, asi que debajo de eso la unica
-- lectura posible es que son miles. El parser ya lo hace para lo que se
-- cargue de ahora en mas; esto arregla lo que ya estaba.
UPDATE job_actions
   SET monto = CAST(CAST(monto AS INTEGER) * 1000 AS TEXT)
 WHERE monto IS NOT NULL
   AND CAST(monto AS REAL) > 0
   AND CAST(monto AS REAL) < 1000;
