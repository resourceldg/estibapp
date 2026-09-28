-- Cual de los mensajes que salieron los mando Estiba por su cuenta.
--
-- Es la unica excepcion a "nada sale sin tu aprobacion", asi que tiene que
-- poder mirarse. Sin esto, el fletero abre una conversacion, ve un mensaje
-- suyo que no recuerda haber mandado, y no tiene como saber si lo aprobo
-- distraido o si lo mando la app. Esa duda es peor que la funcion.
ALTER TABLE drafts ADD COLUMN auto INTEGER NOT NULL DEFAULT 0;
