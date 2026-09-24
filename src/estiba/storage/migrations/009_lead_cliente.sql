-- Las clases pasan a ser de suma cero y con nombre propio: una conversacion
-- es lead, cliente, privado o spam, nunca dos cosas a la vez.
--
-- "flete" se parte en dos porque no son lo mismo: el que pregunta es un
-- lead, el que ya hizo un flete es un cliente. Al migrar todo arranca como
-- lead; sincronizar asciende solo a los que tienen un flete confirmado.
UPDATE chat_clase SET clase = 'lead'    WHERE clase = 'flete';
UPDATE chat_clase SET clase = 'privado' WHERE clase = 'personal';
UPDATE chat_clase SET clase_previa = 'lead'    WHERE clase_previa = 'flete';
UPDATE chat_clase SET clase_previa = 'privado' WHERE clase_previa = 'personal';
