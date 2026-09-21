-- Fuente unica de verdad. Nunca se edita ni se borra una fila de aca.
CREATE TABLE messages (
    id              TEXT PRIMARY KEY,
    chat_id         TEXT NOT NULL,
    from_customer   INTEGER NOT NULL CHECK (from_customer IN (0, 1)),
    sent_at         TEXT NOT NULL,
    kind            TEXT NOT NULL,
    text            TEXT,
    media_path      TEXT,
    source          TEXT NOT NULL,
    ingested_at     TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_messages_chat ON messages (chat_id, sent_at);

-- Separado de messages para poder re-transcribir con mejor modelo
-- sin perder lo que ya habia.
CREATE TABLE transcripts (
    message_id      TEXT NOT NULL REFERENCES messages (id),
    text            TEXT NOT NULL,
    confidence      REAL NOT NULL,
    model           TEXT NOT NULL,
    duration_s      REAL NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (message_id, model)
);

-- La persona, estable en el tiempo.
CREATE TABLE contacts (
    id              TEXT PRIMARY KEY,
    chat_id         TEXT NOT NULL UNIQUE,
    display_name    TEXT,
    first_seen      TEXT NOT NULL
);

-- Un intento de venta. Un contacto puede tener varios.
CREATE TABLE leads (
    id              TEXT PRIMARY KEY,
    contact_id      TEXT NOT NULL REFERENCES contacts (id),
    conversation_id TEXT NOT NULL,
    stage           TEXT NOT NULL,
    click_id        TEXT,            -- NULL = sin atribuir, nunca se reparte
    lost_reason     TEXT,
    created_at      TEXT NOT NULL
);

CREATE TABLE lead_stage_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id         TEXT NOT NULL REFERENCES leads (id),
    from_stage      TEXT,
    to_stage        TEXT NOT NULL,
    at              TEXT NOT NULL,
    reason          TEXT
);

CREATE TABLE jobs (
    id                 TEXT PRIMARY KEY,
    conversation_id    TEXT NOT NULL,
    state              TEXT NOT NULL,
    calendar_event_id  TEXT,
    created_at         TEXT NOT NULL
);

-- Trazabilidad campo por campo: un dato no existe sin su evidencia.
CREATE TABLE job_slots (
    job_id          TEXT NOT NULL REFERENCES jobs (id),
    field           TEXT NOT NULL,
    value_json      TEXT NOT NULL,
    confidence      REAL NOT NULL,
    message_id      TEXT NOT NULL REFERENCES messages (id),
    quote           TEXT NOT NULL,
    method          TEXT NOT NULL,   -- regex | dateparser | llm | human
    updated_at      TEXT NOT NULL,
    PRIMARY KEY (job_id, field)
);

CREATE TABLE job_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id          TEXT NOT NULL REFERENCES jobs (id),
    from_state      TEXT,
    to_state        TEXT NOT NULL,
    at              TEXT NOT NULL,
    cause           TEXT NOT NULL,
    message_id      TEXT REFERENCES messages (id)
);

CREATE TABLE campaigns (
    id              TEXT PRIMARY KEY,
    platform        TEXT NOT NULL,
    external_id     TEXT NOT NULL,
    period_start    TEXT NOT NULL,
    period_end      TEXT NOT NULL,
    spend           TEXT NOT NULL    -- Decimal como texto, sin float
);
