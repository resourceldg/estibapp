-- Envio por WhatsApp de lo aprobado. Aprobar sigue siendo la unica puerta:
-- solo se manda lo que una persona aprobo, y queda registrado si salio.
ALTER TABLE drafts ADD COLUMN sent_at TEXT;
ALTER TABLE drafts ADD COLUMN sent_message_id TEXT;
ALTER TABLE drafts ADD COLUMN send_error TEXT;
