-- Difusion en grupos de Facebook, semiautomatica.
--
-- Estiba NO publica ni lee grupos: eso arriesga la cuenta de Facebook (y con
-- ella la publicidad). Arma la cola del dia con textos distintos y un link de
-- WhatsApp con codigo; la persona pega y publica. Cada posteo publicado es
-- una `pieza` (tipo posteo_grupo) con su codigo, asi Numeros > Origen cuenta
-- consultas, fletes y plata por grupo.
--
--   * grupos: el catalogo. `dias` son los dias en que el grupo deja publicar
--     (0 = lunes ... 6 = domingo, separados por coma; vacio = cualquiera).
--     `tema` elige los textos: general, estudiantes, alquileres, compraventa.
--   * pedidos_vistos: un "busco flete" que alguien vio en un grupo y pego
--     aca para no olvidarse de escribirle. No es un lead hasta que hay
--     conversacion de WhatsApp.
--
-- Rollback: tablas nuevas que nada anterior lee.

CREATE TABLE grupos (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre              TEXT NOT NULL UNIQUE,
    url                 TEXT,
    zona                TEXT NOT NULL DEFAULT 'Mar del Plata',
    tema                TEXT NOT NULL DEFAULT 'general',
    dias                TEXT NOT NULL DEFAULT '',
    acepta_publicidad   INTEGER NOT NULL DEFAULT 1,
    reglas              TEXT,
    miembros            INTEGER,
    vigente             INTEGER NOT NULL DEFAULT 1,
    creado              TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE pedidos_vistos (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    grupo_id    INTEGER REFERENCES grupos (id),
    texto       TEXT NOT NULL,
    contacto    TEXT,
    visto_at    TEXT NOT NULL,
    estado      TEXT NOT NULL DEFAULT 'pendiente',   -- pendiente | contactado | descartado
    resuelto_at TEXT
);
