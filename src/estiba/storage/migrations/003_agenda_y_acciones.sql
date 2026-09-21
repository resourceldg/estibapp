-- Lo que pasa con un flete fuera de la conversacion: lo marca una persona
-- ("realizado", "perdido") o el calendario ("agendado"). El JobEngine las
-- aplica en orden, junto con los mensajes.
CREATE TABLE job_actions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id          TEXT NOT NULL,
    action          TEXT NOT NULL,   -- agendado | realizado | perdido
    at              TEXT NOT NULL,
    actor           TEXT NOT NULL,   -- humano | calendario
    note            TEXT
);
CREATE INDEX idx_job_actions_job ON job_actions (job_id, at);

-- La agenda propia. Se publica como .ics y, si hay CalDAV, se refleja ahi.
CREATE TABLE calendar_events (
    id              TEXT PRIMARY KEY,         -- tambien es el UID del VEVENT
    job_id          TEXT NOT NULL UNIQUE,
    starts_at       TEXT NOT NULL,            -- fecha (todo el dia) o fecha y hora
    all_day         INTEGER NOT NULL CHECK (all_day IN (0, 1)),
    approximate     INTEGER NOT NULL CHECK (approximate IN (0, 1)),  -- hora deducida de la franja
    title           TEXT NOT NULL,
    description     TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'activo',  -- activo | cancelado
    mirrored        INTEGER NOT NULL DEFAULT 0,      -- 1 si ya esta en el CalDAV
    updated_at      TEXT NOT NULL
);

-- Ajustes chicos que genera el sistema (el token del .ics, por ejemplo).
CREATE TABLE settings (
    key             TEXT PRIMARY KEY,
    value           TEXT NOT NULL
);
