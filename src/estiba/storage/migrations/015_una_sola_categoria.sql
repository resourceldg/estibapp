-- Un solo lugar donde vive "esto no es un flete".
--
-- Habia dos mecanismos para sacar una conversacion de la bandeja:
-- `excluded_chats` (el viejo boton "No es cliente") y la clase spam/privado.
-- El segundo tiene pestaña propia: se ve, se cuenta y se puede deshacer. El
-- primero no: `sincronizar()` saltaba esos chats antes de mirar nada, asi
-- que quedaban en ninguna pantalla, sin contador y sin forma de volver.
-- Peor: le ganaba a la clase, de modo que marcar algo como lead a mano no
-- hacia nada y la conversacion desaparecia igual, en silencio.
--
-- Aca se migra lo que habia a la clase equivalente y se vacia la tabla.
-- Los mensajes no se tocan: solo cambia donde se lee que la conversacion
-- esta afuera.

-- Lo excluido pasa a spam, que es la pestaña que le corresponde. Si una
-- persona ya dijo que esto era otra cosa, manda esa decision y no se pisa.
INSERT INTO clase_historia (chat_id, clase, clase_previa, porque, quien, at)
SELECT e.chat_id, 'spam', c.clase, 'lo sacaste con "No es cliente"', 'humano', e.at
  FROM excluded_chats e
  LEFT JOIN chat_clase c ON c.chat_id = e.chat_id
 WHERE c.chat_id IS NULL OR c.quien <> 'humano';

INSERT INTO chat_clase (chat_id, clase, porque, quien, clase_previa, at)
SELECT e.chat_id, 'spam', 'lo sacaste con "No es cliente"', 'humano', c.clase, e.at
  FROM excluded_chats e
  LEFT JOIN chat_clase c ON c.chat_id = e.chat_id
 WHERE c.chat_id IS NULL OR c.quien <> 'humano'
    ON CONFLICT (chat_id) DO UPDATE SET
       clase = excluded.clase, porque = excluded.porque, quien = 'humano',
       clase_previa = excluded.clase_previa, at = excluded.at;

-- La tabla queda vacia y sin uso. No se borra: si algo salio mal, el
-- historial de que se excluyo y cuando sigue estando en clase_historia.
DELETE FROM excluded_chats;
