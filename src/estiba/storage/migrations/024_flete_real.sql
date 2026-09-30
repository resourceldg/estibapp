-- Lo que paso de verdad en un flete, anotado al cerrarlo.
--
-- Hasta aca al marcar realizado solo quedaba el monto cobrado: los km, las
-- horas y la espera quedaban para siempre en estimacion y Estiba no podia
-- aprender. Ahora, al cerrar, se guarda una fila con lo real.
--
-- fuente:
--   estimado    se cerro sin ver la estimacion (la agenda compacta): son
--               los numeros de Estiba, no cuentan como medidos.
--   confirmado  se cerro viendo la estimacion y sin corregirla: "asi fue".
--   corregido   el fletero cambio al menos un valor.
--
-- Una fila por cierre; si se reabre y se vuelve a cerrar, manda la ultima.
-- Rollback: tabla nueva, nadie mas la lee.
CREATE TABLE flete_real (
    id              INTEGER PRIMARY KEY,
    job_id          TEXT NOT NULL,
    km              TEXT,           -- km totales, de casa a casa
    km_vacios       TEXT,           -- los que se anduvieron sin carga
    horas           TEXT,           -- de salir a volver, con coordinar y cobrar
    espera_h        TEXT,
    ayudante_pagado TEXT,           -- lo que se le pago a ayudantes
    otros_gastos    TEXT,           -- peajes, estacionamiento...
    canal           TEXT,           -- meta | recurrente | referido | organico | google | marketplace | whatsapp | convenio | otro
    fuente          TEXT NOT NULL,  -- estimado | confirmado | corregido
    at              TEXT NOT NULL
);
CREATE INDEX idx_flete_real_job ON flete_real (job_id, id);
