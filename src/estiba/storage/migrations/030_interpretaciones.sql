-- Por que Estiba entendio lo que entendio, guardado cuando cambia.
--
-- La interpretacion se recalcula en cada sincronizacion y se tiraba: no se
-- podia responder "por que el martes decia X". Una fila por cambio de lo
-- que se entiende de un pedido (campos con su evidencia, intenciones,
-- estado y causa), con la version de las reglas que lo produjo y cuanto
-- tardo. Sin secretos ni credenciales: solo lo que ya esta en la base.
-- Rollback: tabla nueva.
CREATE TABLE interpretaciones (
    id          INTEGER PRIMARY KEY,
    trace_id    TEXT NOT NULL,
    job_id      TEXT NOT NULL,
    chat_id     TEXT NOT NULL,
    reglas      TEXT NOT NULL,
    at          TEXT NOT NULL,
    huella      TEXT NOT NULL,
    contenido   TEXT NOT NULL,
    latencia_ms INTEGER
);
CREATE INDEX interpretaciones_job ON interpretaciones (job_id, at);
