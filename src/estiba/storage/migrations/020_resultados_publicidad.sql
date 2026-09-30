-- Cuantas conversaciones dice Meta que trajo cada gasto.
--
-- Estiba cuenta como "de la publicidad" solo lo que arranca con el texto del
-- boton del anuncio. Meta cuenta todas las conversaciones que abrio el
-- anuncio: en la primera campaña fueron 29 contra 19. Para el costo por
-- conversacion el numero bueno es el de Meta. NULL: no se cargo.
ALTER TABLE campaigns ADD COLUMN resultados INTEGER;
