-- Que es cada conversacion, y quien lo decidio.
--
-- La identidad vive aca: si alguien vuelve a escribir dentro de tres meses,
-- no renace como un desconocido. Se guarda tambien lo que Estiba habia
-- inferido cuando una persona la corrigio, que es la materia prima para
-- mejorar las reglas: interpretacion original -> correccion humana.
CREATE TABLE chat_clase (
    chat_id     TEXT PRIMARY KEY,
    clase       TEXT NOT NULL,
    porque      TEXT,
    -- "estiba" o "humano": la correccion humana manda y no se pisa.
    quien       TEXT NOT NULL,
    -- Lo que Estiba habia dicho antes de que la corrigieran.
    clase_previa TEXT,
    at          TEXT NOT NULL
);
