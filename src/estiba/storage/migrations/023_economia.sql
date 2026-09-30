-- El motor economico (estiba/economia): lo que el fletero quiere que la
-- camioneta haga por el, y el precio base con su historia.
--
-- Los costos por km, los fijos y la cuota siguen viviendo en settings
-- ("costos"): no se duplican. Aca va solo lo nuevo.
--
-- economia_config: cada guardado es una fila nueva, nunca se pisa. La
-- configuracion vigente de una clave es la de mayor id. Claves: camioneta,
-- credito, disponibilidad, fondos, objetivo. Volver atras = borrar la
-- ultima fila de esa clave.
CREATE TABLE economia_config (
    id          INTEGER PRIMARY KEY,
    clave       TEXT NOT NULL,
    valor       TEXT NOT NULL,          -- JSON
    at          TEXT NOT NULL
);
CREATE INDEX idx_economia_config ON economia_config (clave, id);

-- El precio base: el valor comercial de referencia de un flete. No es el
-- minimo ni el ticket promedio. Cada cambio es una fila con desde cuando
-- rige y cual reemplazo.
CREATE TABLE precio_base (
    id          INTEGER PRIMARY KEY,
    monto       TEXT NOT NULL,          -- Decimal como texto
    desde       TEXT NOT NULL,          -- fecha ISO desde la que rige
    anterior    TEXT,                   -- el monto que reemplazo
    nota        TEXT,
    at          TEXT NOT NULL
);
CREATE INDEX idx_precio_base_desde ON precio_base (desde, id);

-- El de hoy, $40.000, lo dijo el fletero el 2026-09-29. Rige desde el
-- principio de la historia para que los fletes viejos tengan referencia.
INSERT INTO precio_base (monto, desde, anterior, nota, at)
VALUES ('40000', '2026-01-01', NULL, 'precio base inicial', datetime('now'));
