-- El mensaje de WhatsApp tal como llego, antes de convertirlo.
--
-- La ingesta se quedaba con lo minimo (texto o audio) y tiraba el resto:
-- una ubicacion, un contacto o una foto sin epigrafe quedaban como "otro"
-- vacio, y WhatsApp no los guarda para siempre (de 29 mensajes asi en charlas
-- de negocio, a la semana quedaban 5). Ver audit/AUDIT_REPORT.md.
--
-- Una fila por mensaje (el mismo id que messages), sin lo binario (se recorta).
-- Rollback: tabla nueva, no se toca nada existente.
CREATE TABLE mensajes_crudos (
    message_id TEXT PRIMARY KEY,
    chat_id    TEXT NOT NULL,
    payload    TEXT NOT NULL,
    recibido   TEXT NOT NULL
);
