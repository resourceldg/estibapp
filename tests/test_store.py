"""ConversationStore: reconstruir el hilo entero desde el log."""

from __future__ import annotations

from estiba.core.models import Transcript
from estiba.storage.repositories import TranscriptRepository

from conftest import mensaje


class TestIngesta:
    def test_guarda_y_devuelve_cuantos_eran_nuevos(self, store):
        assert store.ingest([mensaje("c1:000"), mensaje("c1:001", minuto=1)]) == 2

    def test_reingerir_lo_mismo_no_duplica(self, store):
        """Reprocesar un fixture o recibir dos veces el mismo webhook
        no puede ensuciar el historial."""
        msgs = [mensaje("c1:000"), mensaje("c1:001", minuto=1)]
        store.ingest(msgs)
        assert store.ingest(msgs) == 0
        assert len(store.get("chat1").messages) == 2


class TestReconstruccion:
    def test_devuelve_los_mensajes_en_orden_cronologico(self, store):
        store.ingest(
            [
                mensaje("c1:002", minuto=20, texto="tercero"),
                mensaje("c1:000", minuto=0, texto="primero"),
                mensaje("c1:001", minuto=10, texto="segundo"),
            ]
        )
        textos = [m.text for m in store.get("chat1").messages]
        assert textos == ["primero", "segundo", "tercero"]

    def test_no_mezcla_conversaciones(self, store):
        store.ingest([mensaje("a:000", chat_id="a"), mensaje("b:000", chat_id="b")])
        assert len(store.get("a").messages) == 1
        assert len(store.get("b").messages) == 1

    def test_pega_la_transcripcion_al_audio(self, store, conn):
        store.ingest([mensaje("c1:000", audio="fixtures/audios/x.ogg")])
        TranscriptRepository(conn).save(
            Transcript("c1:000", "son quince cajas", 0.91, "large-v3", 4.2)
        )
        conv = store.get("chat1")
        assert conv.text_of(conv.messages[0]) == "son quince cajas"

    def test_si_hay_varias_transcripciones_gana_la_de_mas_confianza(self, store, conn):
        store.ingest([mensaje("c1:000", audio="fixtures/audios/x.ogg")])
        repo = TranscriptRepository(conn)
        repo.save(Transcript("c1:000", "version mala", 0.40, "tiny", 4.2))
        repo.save(Transcript("c1:000", "version buena", 0.93, "large-v3", 4.2))
        conv = store.get("chat1")
        assert conv.text_of(conv.messages[0]) == "version buena"
        # la peor no se pierde: sirve para comparar modelos en la Fase 2
        assert len(list(repo.all_for_message("c1:000"))) == 2

    def test_audio_sin_transcribir_se_marca_no_se_inventa(self, store):
        store.ingest([mensaje("c1:000", audio="fixtures/audios/x.ogg")])
        conv = store.get("chat1")
        assert conv.text_of(conv.messages[0]) is None
        assert "[audio sin transcribir]" in conv.as_plain_text()


class TestColaDeAudio:
    def test_lista_solo_los_audios_sin_transcribir(self, store, conn):
        store.ingest(
            [
                mensaje("c1:000", texto="hola"),
                mensaje("c1:001", minuto=1, audio="fixtures/audios/a.ogg"),
                mensaje("c1:002", minuto=2, audio="fixtures/audios/b.ogg"),
            ]
        )
        TranscriptRepository(conn).save(
            Transcript("c1:001", "ya transcripto", 0.9, "large-v3", 2.0)
        )
        pendientes = [m.id for m in store.pending_audio()]
        assert pendientes == ["c1:002"]


class TestTextoPlano:
    def test_marca_quien_habla(self, store):
        store.ingest(
            [
                mensaje("c1:000", from_customer=True, texto="haces fletes?"),
                mensaje("c1:001", minuto=1, from_customer=False, texto="si, contame"),
            ]
        )
        texto = store.get("chat1").as_plain_text()
        assert texto == "cliente: haces fletes?\nvos: si, contame"


class TestDosProcesos:
    """Dos escritores a la vez: el webhook y el dashboard, o una consola."""

    def test_espera_en_vez_de_fallar_al_instante(self, tmp_path):
        """Sin busy_timeout SQLite tira "database is locked" de una, y en el
        navegador eso aparece como un error de red sin explicacion."""
        from estiba.storage.db import connect

        conn = connect(tmp_path / "x.db")
        espera = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        conn.close()
        assert espera >= 5000

    def test_un_segundo_escritor_no_se_cae(self, tmp_path):
        import threading
        from datetime import datetime

        from estiba.core.models import Message, MessageKind
        from estiba.conversation.store import ConversationStore
        from estiba.storage.db import connect

        ruta = tmp_path / "y.db"
        connect(ruta).close()
        errores = []

        def escribir(n):
            try:
                conn = connect(ruta)
                ConversationStore(conn).ingest([
                    Message(f"m{n}:{i}", f"c{n}", True, datetime(2026, 9, 20, 10),
                            MessageKind.TEXT, text="hola")
                    for i in range(40)
                ])
                conn.close()
            except Exception as e:  # noqa: BLE001
                errores.append(e)

        hilos = [threading.Thread(target=escribir, args=(n,)) for n in range(4)]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join()
        assert not errores, errores
