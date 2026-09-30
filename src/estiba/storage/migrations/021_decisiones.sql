-- Escenarios de la calculadora y la decision que se aplico.
--
-- Un escenario es una combinacion que se quiso guardar: precio, publicidad
-- por dia, cierre, costo por conversacion, meta y capacidad. Aplicarlo es
-- decidir: sus valores pasan a la configuracion (y con eso cambian los
-- precios sugeridos de cada flete), y queda como la decision vigente contra
-- la que se mide lo que viene pasando. La anterior no se borra: queda
-- "reemplazada", para ver despues que se decidio y cuando.
CREATE TABLE decisiones (
    id          INTEGER PRIMARY KEY,
    nombre      TEXT NOT NULL,
    valores     TEXT NOT NULL,              -- JSON
    estado      TEXT NOT NULL DEFAULT 'guardada',  -- guardada | aplicada | reemplazada
    creada      TEXT NOT NULL,
    aplicada    TEXT
);
