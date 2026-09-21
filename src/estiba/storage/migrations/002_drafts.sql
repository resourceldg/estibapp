-- Bandeja de aprobacion: lo que Estiba propone decirle al cliente.
-- Nada de aca sale sin que una persona lo apruebe.
CREATE TABLE drafts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id          TEXT NOT NULL REFERENCES jobs (id),
    chat_id         TEXT NOT NULL,
    kind            TEXT NOT NULL,   -- respuesta | preguntas | cotizacion | confirmacion | seguimiento
    reason          TEXT NOT NULL,   -- por que Estiba propone escribir ahora
    text            TEXT NOT NULL,   -- lo que propone Estiba
    final_text      TEXT,            -- lo que se aprobo, editado o no
    -- Ultimo mensaje de la conversacion cuando se genero. Si llega uno
    -- nuevo, el borrador viejo queda reemplazado.
    basis_message_id TEXT NOT NULL REFERENCES messages (id),
    status          TEXT NOT NULL DEFAULT 'pendiente',  -- pendiente | aprobado | descartado | reemplazado
    created_at      TEXT NOT NULL,
    decided_at      TEXT,
    UNIQUE (job_id, kind, basis_message_id)
);
CREATE INDEX idx_drafts_status ON drafts (status, created_at);
