-- Fotos que entraron como texto: la miniatura en base64 que trae el cuerpo
-- del mensaje ("/9j/4AAQ..."). No la escribio nadie, y el extractor la leia
-- como si fuera un mensaje del cliente. Pasan a ser fotos sin texto.
-- Lo que se pierde es la miniatura codificada, que no se usaba para nada.
UPDATE messages SET kind = 'image', text = NULL
 WHERE kind = 'text' AND (text LIKE '/9j/%' OR text LIKE 'iVBOR%');
