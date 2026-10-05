-- Reputacion de las personas y recuperacion de fletes perdidos.
-- Ver docs/recuperacion.md.
--
-- contacto_eventos: lo que una persona decide sobre otra (no contactar,
-- etiquetas, notas, pago, puntualidad, correccion de la reputacion, origen,
-- no interesado, seguimiento programado). Solo se agrega: el estado actual
-- sale de recorrerlos en orden, y el historial que lo origino no se borra.
-- Va por chat_id y no por job_id: los pedidos se pueden re-cortar.
--
-- recontactos: los mensajes de recuperacion, preparar -> enviar/copiar o
-- descartar. Aparte de drafts a proposito: la sincronizacion reemplaza los
-- borradores pendientes de cada pedido, y estos tienen que sobrevivir.
--
-- Rollback: tablas nuevas, no se toca nada existente.
CREATE TABLE contacto_eventos (
    id      INTEGER PRIMARY KEY,
    chat_id TEXT NOT NULL,
    tipo    TEXT NOT NULL,
    valor   TEXT,
    nota    TEXT,
    at      TEXT NOT NULL,
    actor   TEXT NOT NULL DEFAULT 'humano'
);
CREATE INDEX contacto_eventos_chat ON contacto_eventos (chat_id, at);

CREATE TABLE recontactos (
    id          INTEGER PRIMARY KEY,
    chat_id     TEXT NOT NULL,
    job_id      TEXT,
    plantilla   TEXT NOT NULL,
    secuencia   TEXT,
    paso        INTEGER,
    texto       TEXT NOT NULL,
    estado      TEXT NOT NULL DEFAULT 'preparado',  -- preparado | enviado | copiado | descartado
    creado      TEXT NOT NULL,
    decidido    TEXT,
    message_id  TEXT,
    error       TEXT
);
CREATE INDEX recontactos_chat ON recontactos (chat_id, creado);
