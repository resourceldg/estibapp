-- Reparaciones, dias parada y los movimientos de los fondos.
--
-- reparaciones: lo unico nuevo que se carga a mano. Ordinaria (frenos,
-- aceite: ya esta estimada en el desgaste por km) o extraordinaria (caja,
-- motor, embrague: la que tiene que salir del fondo). Los dias fuera de
-- servicio se muestran como capacidad perdida; no se monetizan solos.
--
-- fondo_movimientos: el saldo de un fondo es la suma de sus movimientos,
-- nunca un numero pisado. aporte (+), gasto (-). Una reparacion pagada con
-- un fondo deja su gasto aca.
--
-- Doble conteo que se evita: una reparacion es caja (o sale del fondo),
-- nunca un costo economico extra: el economico ya la tiene como desgaste
-- por km. Rollback: tablas nuevas.
CREATE TABLE reparaciones (
    id              INTEGER PRIMARY KEY,
    fecha           TEXT NOT NULL,
    componente      TEXT NOT NULL,
    tipo            TEXT NOT NULL,      -- ordinaria | extraordinaria
    costo           TEXT NOT NULL,
    km              TEXT,               -- odometro al momento de la reparacion
    dias_parada     INTEGER NOT NULL DEFAULT 0,
    fondo           TEXT,               -- clave del fondo que la pago, o NULL si salio de la caja
    nota            TEXT,
    at              TEXT NOT NULL
);
CREATE INDEX idx_reparaciones_fecha ON reparaciones (fecha);

CREATE TABLE fondo_movimientos (
    id              INTEGER PRIMARY KEY,
    fondo           TEXT NOT NULL,      -- clave del fondo
    fecha           TEXT NOT NULL,
    monto           TEXT NOT NULL,      -- positivo: aporte; negativo: gasto
    origen          TEXT NOT NULL,      -- aporte | reparacion | ajuste
    nota            TEXT,
    at              TEXT NOT NULL
);
CREATE INDEX idx_fondo_movimientos ON fondo_movimientos (fondo, fecha);
