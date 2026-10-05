-- jobs y leads son proyecciones: cada sincronizacion las reescribe, pero no
-- borraba las que dejaban de salir (un chat que paso a privado, un flete
-- que se re-corto). Quedaban 43 filas viejas, una de ellas "realizado".
-- No se borran: drafts, job_events y lead_stage_history apuntan a ellas y
-- son historia. Se marcan. Rollback: columnas nuevas con valor por defecto.
ALTER TABLE jobs ADD COLUMN vigente INTEGER NOT NULL DEFAULT 1;
ALTER TABLE leads ADD COLUMN vigente INTEGER NOT NULL DEFAULT 1;
