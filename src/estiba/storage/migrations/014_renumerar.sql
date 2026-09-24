-- Numerar todo lo que dejaba de ser borrador metia en la cuenta a las
-- consultas que se cancelan solas por silencio: 26 numeros para 5 fletes
-- de verdad. "El flete 26" tiene que significar algo.
--
-- Se numera cuando hubo un precio sobre la mesa. Un flete cotizado que
-- despues se pierde conserva su numero: existio.
UPDATE jobs SET numero = NULL;

UPDATE jobs SET numero = (
    SELECT COUNT(*) FROM jobs AS previos
    WHERE previos.state IN ('presupuestado', 'esperando_cliente', 'confirmado',
                            'agendado', 'realizado')
      AND (previos.created_at < jobs.created_at
           OR (previos.created_at = jobs.created_at AND previos.id <= jobs.id))
) WHERE state IN ('presupuestado', 'esperando_cliente', 'confirmado',
                  'agendado', 'realizado');
