-- Posponer no es cerrar ni archivar: la conversacion deja de estar entre las
-- urgentes y vuelve sola cuando corresponde. Si el cliente dijo "manana te
-- confirmo", no tiene sentido que moleste hasta manana.
CREATE TABLE snoozed_chats (
    chat_id     TEXT PRIMARY KEY,
    hasta       TEXT NOT NULL,
    motivo      TEXT,
    at          TEXT NOT NULL
);
