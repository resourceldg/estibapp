-- El nombre que tiene el contacto en WhatsApp. Va aparte del que pone el
-- fletero a mano: si escribe "Sabi (vecina)" eso manda, y una importacion
-- posterior no se lo pisa.
ALTER TABLE contacts ADD COLUMN wa_name TEXT;
