"""La CLI de punta a punta: cargar fixtures y verlos reconstruidos."""

from __future__ import annotations

import pytest

from estiba.cli import main
from estiba.storage.db import connect, migrate


@pytest.fixture
def db(tmp_path):
    return str(tmp_path / "test.db")


class TestMigraciones:
    def test_corren_una_sola_vez(self, tmp_path):
        conn = connect(tmp_path / "m.db")
        assert migrate(conn) == [], "volvio a correr una migracion ya aplicada"

    def test_crean_todas_las_tablas_del_diseno(self, tmp_path):
        conn = connect(tmp_path / "m.db")
        tablas = {
            r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        esperadas = {
            "messages", "transcripts", "contacts", "leads", "lead_stage_history",
            "jobs", "job_slots", "job_events", "campaigns",
        }
        assert esperadas <= tablas


class TestCicloCompleto:
    def test_ingest_despues_list_despues_show(self, db, capsys):
        assert main(["--db", db, "ingest"]) == 0
        salida = capsys.readouterr().out
        assert "38 mensajes leidos, 38 nuevos" in salida

        assert main(["--db", db, "list"]) == 0
        assert "5491133334444" in capsys.readouterr().out

        assert main(["--db", db, "show", "5491133334444"]) == 0
        conversacion = capsys.readouterr().out
        # El hilo entero, en orden, con quien habla marcado.
        assert "cliente: hola buenas, haces fletes?" in conversacion
        assert "vos: Buenas! Si, contame que necesitas" in conversacion
        assert conversacion.index("haces fletes?") < conversacion.index("ahi te espero")

    def test_ingerir_dos_veces_no_duplica(self, db, capsys):
        main(["--db", db, "ingest"])
        capsys.readouterr()
        main(["--db", db, "ingest"])
        assert "0 nuevos" in capsys.readouterr().out

    def test_el_dato_que_llega_tarde_queda_en_el_hilo(self, db, capsys):
        """'Somos 2, hay ascensor' llega cuatro mensajes despues de la direccion.

        En la Fase 3 el extractor va a leer este texto completo, no el ultimo
        mensaje: por eso importa que el hilo se reconstruya entero.
        """
        main(["--db", db, "ingest"])
        capsys.readouterr()
        main(["--db", db, "show", "5491133334444"])
        texto = capsys.readouterr().out
        assert "Aguirre al 500" in texto
        assert "somos 2 y hay ascensor" in texto

    def test_show_de_un_chat_inexistente_falla_limpio(self, db, capsys):
        main(["--db", db, "ingest"])
        capsys.readouterr()
        assert main(["--db", db, "show", "no-existe"]) == 1

    def test_lista_los_audios_pendientes_y_avisa_si_falta_el_archivo(self, db, capsys):
        main(["--db", db, "ingest"])
        capsys.readouterr()
        assert main(["--db", db, "pending-audio"]) == 0
        salida = capsys.readouterr().out
        assert "1 audios esperando" in salida
        assert "FALTA EL ARCHIVO" in salida
