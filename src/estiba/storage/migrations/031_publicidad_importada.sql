-- Gasto de Meta importado por campania y dia, al lado de lo cargado a mano.
--
-- Hasta aca `campaigns` solo tenia cargas manuales de un periodo entero
-- ("del 16 al 29, tanto"). La importacion trae una fila por campania y por
-- dia, con la cuenta, la moneda, la zona horaria de la cuenta y cuando se
-- leyo. Dos reglas:
--
--   * Reimportar no duplica: una fila importada se identifica por
--     (plataforma, cuenta, campania, dia) y se actualiza en el lugar.
--   * Lo manual no se borra: si la importacion cubre todos sus dias, la carga
--     manual queda `vigente = 0` con `reemplazado_por` diciendo por que. Si
--     los cubre a medias, la manual sigue valiendo y los dias importados que
--     se pisan con ella quedan `vigente = 0`. Nunca cuentan las dos.
--
-- `spend` sigue siendo lo que Meta muestra como gastado, sin impuestos: la
-- importacion no inventa impuestos (ver 022).
--
-- Rollback: columnas nuevas con valor por defecto y un indice; el codigo
-- anterior las ignora y ve las filas importadas como cargas de un dia.
ALTER TABLE campaigns ADD COLUMN origen TEXT NOT NULL DEFAULT 'manual';
ALTER TABLE campaigns ADD COLUMN cuenta TEXT;
ALTER TABLE campaigns ADD COLUMN nombre TEXT;
ALTER TABLE campaigns ADD COLUMN moneda TEXT;
ALTER TABLE campaigns ADD COLUMN zona_horaria TEXT;
ALTER TABLE campaigns ADD COLUMN actualizado TEXT;
ALTER TABLE campaigns ADD COLUMN vigente INTEGER NOT NULL DEFAULT 1;
ALTER TABLE campaigns ADD COLUMN reemplazado_por TEXT;

CREATE UNIQUE INDEX campaigns_importadas
    ON campaigns (platform, cuenta, external_id, period_start)
    WHERE origen = 'importado';
