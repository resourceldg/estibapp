-- Plata: lo que se cotizo y lo que se termino cobrando, que no siempre
-- coinciden. Van en la accion que los produjo para saber cuando y quien.
ALTER TABLE job_actions ADD COLUMN monto TEXT;
