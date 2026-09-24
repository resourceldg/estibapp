-- Un numero para nombrar un flete en voz alta: "el 12", no
-- "5492235550001:job2". Sirve para hablarlo con el cliente, buscarlo en la
-- exportacion y encontrarlo despues.
--
-- Se numera cuando deja de ser una consulta: cada conversacion crea un
-- flete en borrador, y numerarlos a todos daria decenas de numeros para
-- cosas que nunca pasaron. El numero se asigna una vez y no cambia, aunque
-- el flete despues se pierda.
ALTER TABLE jobs ADD COLUMN numero INTEGER;
CREATE UNIQUE INDEX idx_jobs_numero ON jobs (numero) WHERE numero IS NOT NULL;

-- Los que ya existen y pasaron de borrador, por orden de aparicion.
UPDATE jobs SET numero = (
    SELECT COUNT(*) FROM jobs AS previos
    WHERE previos.state != 'borrador'
      AND (previos.created_at < jobs.created_at
           OR (previos.created_at = jobs.created_at AND previos.id <= jobs.id))
) WHERE state != 'borrador';
