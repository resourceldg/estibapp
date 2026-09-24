-- Chats marcados "no es cliente": charla personal, spam, numero equivocado.
-- No se borra ningun mensaje; sincronizar() los salta y asi desaparecen de
-- la bandeja, los fletes y el CRM.
CREATE TABLE excluded_chats (
    chat_id     TEXT PRIMARY KEY,
    motivo      TEXT,
    at          TEXT NOT NULL
);
