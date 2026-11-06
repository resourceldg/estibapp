-- De donde viene cada consulta: piezas, atribuciones y pauta por anuncio.
--
-- Hasta aca el origen de una consulta se deducia cada vez con reglas escritas
-- en el codigo (clasificacion.ANUNCIOS) y lo organico no existia en la base.
-- Esto agrega tres tablas:
--
--   * piezas: cada cosa publicada, paga u organica. Un anuncio de Meta, un
--     posteo en un grupo, la ficha de Google, un flyer. `marcador` es la
--     frase que deja el mensaje precargado (regex sobre el texto `plano`) y
--     `codigo` el que va en un link de WhatsApp. `anuncio_id` es el link a la
--     pauta: con eso se cruza el gasto por anuncio.
--   * atribuciones: que pieza trajo cada oportunidad (un flete), como se supo
--     y con que evidencia. Una sola vigente por flete; corregir a mano deja
--     la anterior en `vigente = 0`. Lo que no se sabe NO tiene fila: queda
--     "sin atribuir", jamas se reparte a ojo.
--   * pauta_diaria: gasto, impresiones y conversaciones por anuncio y por dia,
--     importado de Meta. Sin impuestos, como lo muestra Meta (ver 022).
--
-- Rollback: tablas nuevas que nada anterior lee; se pueden borrar sin perder
-- datos de antes.

CREATE TABLE piezas (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    clave        TEXT NOT NULL UNIQUE,      -- estable, para la CLI y los tests
    nombre       TEXT NOT NULL,             -- como se muestra en Numeros
    tipo         TEXT NOT NULL,             -- anuncio | posteo_grupo | marketplace | pagina | ficha_google | flyer | otro
    medio        TEXT NOT NULL,             -- pago | organico
    canal        TEXT NOT NULL,             -- registro.CANALES: meta, google, marketplace, organico...
    lugar        TEXT,                      -- nombre del grupo, barrio del flyer...
    campania     TEXT,                      -- nombre legible de la campania o tanda
    campania_id  TEXT,                      -- id externo (Meta)
    anuncio_id   TEXT,                      -- id externo del anuncio: el link a la pauta
    marcador     TEXT,                      -- regex sobre el primer mensaje `plano`
    codigo       TEXT UNIQUE,               -- codigo corto para links de WhatsApp
    texto        TEXT,                      -- lo publicado
    url          TEXT,
    desde        TEXT,                      -- fecha ISO de publicacion
    hasta        TEXT,
    costo_fijo   TEXT NOT NULL DEFAULT '0', -- lo pagado por fuera de la pauta (imprenta, etc.)
    notas        TEXT,
    vigente      INTEGER NOT NULL DEFAULT 1,
    creada       TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE atribuciones (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id      TEXT NOT NULL,
    pieza_id    INTEGER NOT NULL REFERENCES piezas (id),
    metodo      TEXT NOT NULL,              -- mensaje_precargado | codigo | declarado | manual
    evidencia   TEXT,                       -- el texto que lo prueba
    at          TEXT NOT NULL,
    vigente     INTEGER NOT NULL DEFAULT 1
);
CREATE UNIQUE INDEX atribucion_vigente ON atribuciones (job_id) WHERE vigente = 1;

CREATE TABLE pauta_diaria (
    plataforma      TEXT NOT NULL,
    cuenta          TEXT NOT NULL,
    anuncio_id      TEXT NOT NULL,
    anuncio         TEXT,
    campania_id     TEXT,
    dia             TEXT NOT NULL,
    gasto           TEXT NOT NULL,          -- sin impuestos
    impresiones     INTEGER,
    conversaciones  INTEGER,
    actualizado     TEXT NOT NULL,
    PRIMARY KEY (plataforma, cuenta, anuncio_id, dia)
);

-- Las piezas que ya existen. Los marcadores son los mismos que tenia
-- clasificacion.ANUNCIOS; los de noviembre van con su anuncio de Meta (v2).
INSERT INTO piezas (clave, nombre, tipo, medio, canal, campania, marcador, desde, hasta) VALUES
 ('a-anteriores', 'A (anteriores)', 'anuncio', 'pago', 'meta', 'Septiembre-octubre 2026',
  '\b(?:quiero|quisiera|necesito|me\s+gustaria|me\s+interesa)\s+(?:\w+\s+){0,2}m[aá]s\s+informaci|\bme\s+interesa\s+(?:este|esta|lo\s+que)\b',
  '2026-09-16', '2026-10-16'),
 ('b1', 'B1 ¿Qué hay que mover?', 'anuncio', 'pago', 'meta', 'Test A/B octubre 2026',
  '\bquiero\s+cotizar\s+un\s+flete\b', '2026-10-01', '2026-10-06'),
 ('b2', 'B2 Te lo llevamos hoy', 'anuncio', 'pago', 'meta', 'Test A/B octubre 2026',
  '\bnecesito\s+un\s+flete\s+para\s+hoy\b', '2026-10-01', '2026-10-06'),
 ('b3', 'B3 Marketplace', 'anuncio', 'pago', 'meta', 'Test A/B octubre 2026',
  '\bflete\s+para\s+algo\s+de\s+marketplace\b', '2026-10-01', '2026-10-06'),
 ('b4', 'B4 Reel', 'anuncio', 'pago', 'meta', 'Test A/B octubre 2026',
  '\bvi\s+el\s+video,?\s+necesito\s+un\s+flete\b', '2026-10-01', '2026-10-16');

INSERT INTO piezas (clave, nombre, tipo, medio, canal, campania, campania_id, anuncio_id, marcador, desde, hasta) VALUES
 ('nov-hogar', 'Nov Hogar', 'anuncio', 'pago', 'meta', 'Temporada noviembre 2026', '120252742957830431',
  '120252742958560431', '\bvi\s+el\s+anuncio\s+de\s+mudanzas\s+y\s+muebles\b', '2026-11-06', '2026-11-30'),
 ('nov-marketplace', 'Nov Marketplace', 'anuncio', 'pago', 'meta', 'Temporada noviembre 2026', '120252742957830431',
  '120252742958590431', '\bcompre\s+algo\s+en\s+marketplace\s+y\s+necesito\s+traerlo\b', '2026-11-06', '2026-11-30'),
 ('nov-cursada', 'Nov Fin de cursada', 'anuncio', 'pago', 'meta', 'Temporada noviembre 2026', '120252742957830431',
  '120252742958690431', '\btermine\s+de\s+cursar\s+y\s+quiero\s+llevar\s+mis\s+cosas\b', '2026-11-06', '2026-11-30'),
 ('nov-temporada', 'Nov Temporada', 'anuncio', 'pago', 'meta', 'Temporada noviembre 2026', '120252742957830431',
  '120252742959040431', '\bpreparando\s+un\s+alquiler\s+para\s+la\s+temporada\b', '2026-11-06', '2026-11-30');

INSERT INTO piezas (clave, nombre, tipo, medio, canal, marcador, url, desde, notas) VALUES
 ('google-ficha', 'Ficha de Google', 'ficha_google', 'organico', 'google',
  '\bte\s+encontre\s+en\s+google\b',
  'https://wa.me/5492235128209?text=%C2%A1Hola!%20Te%20encontr%C3%A9%20en%20Google%2C%20quiero%20cotizar',
  '2026-11-06', 'Link de WhatsApp de la ficha. Ver ~/FleteSurfero/FICHA-GOOGLE.md');
