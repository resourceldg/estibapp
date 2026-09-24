-- Un flete arreglado en persona o por telefono no deja rastro en el chat:
-- no hay un "necesito un flete" que partir. Estiba nunca lo va a separar
-- sola, asi que el fletero tiene que poder decirlo.
--
-- Guarda desde que mensaje empieza el flete nuevo. Es una decision humana,
-- asi que no se pisa ni se recalcula: se agrega.
CREATE TABLE cortes_manuales (
    chat_id     TEXT NOT NULL,
    message_id  TEXT NOT NULL,
    at          TEXT NOT NULL,
    PRIMARY KEY (chat_id, message_id)
);
