-- Fase A: cerrar el circuito y dejar de pisar lo que decide una persona.
--
-- Tres cosas que se perdian:
--
-- 1. Lo que Estiba estimo. El precio sugerido se calculaba para mostrarlo y
--    se tiraba. Sin ese primer eslabon no hay "estimacion -> correccion
--    humana -> resultado": la cadena arranca rota.
-- 2. Cada correccion de clasificacion. Se guardaba solo la ultima.
-- 3. Cada vez que se pospone. Traer una conversacion de vuelta borraba la
--    fila, asi que no quedaba que la habias pospuesto tres veces.
--
-- Todas son append-only: aca no se edita ni se borra, como en messages.

-- Lo que Estiba estimo, con que datos y cuando. El resultado real se
-- compara despues contra esto, no se pisa encima.
CREATE TABLE estimaciones (
    id          INTEGER PRIMARY KEY,
    job_id      TEXT NOT NULL,
    tipo        TEXT NOT NULL,    -- "precio" (mas adelante: duracion, costo...)
    valor       TEXT NOT NULL,
    -- Con que se calculo: el desglose tal como estaba en ese momento.
    supuestos   TEXT,             -- JSON
    -- Si Estiba sabia que le faltaban datos, el valor es un piso.
    completa    INTEGER NOT NULL DEFAULT 1,
    at          TEXT NOT NULL
);
CREATE INDEX idx_estimaciones_job ON estimaciones (job_id, tipo, at);

-- Cada decision de clasificacion, no solo la vigente.
CREATE TABLE clase_historia (
    id          INTEGER PRIMARY KEY,
    chat_id     TEXT NOT NULL,
    clase       TEXT NOT NULL,
    clase_previa TEXT,
    porque      TEXT,
    quien       TEXT NOT NULL,    -- estiba | humano
    at          TEXT NOT NULL
);
CREATE INDEX idx_clase_historia_chat ON clase_historia (chat_id, at);

-- Cada vez que se pospuso o se trajo de vuelta.
CREATE TABLE snooze_historia (
    id          INTEGER PRIMARY KEY,
    chat_id     TEXT NOT NULL,
    accion      TEXT NOT NULL,    -- pospuso | desperto
    hasta       TEXT,
    motivo      TEXT,
    at          TEXT NOT NULL
);
CREATE INDEX idx_snooze_historia_chat ON snooze_historia (chat_id, at);
