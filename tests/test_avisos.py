"""Avisos con sonido en el escritorio y fechas por confirmar.

Estiba se usa sobre todo en la compu, con el navegador en otra cosa: una
fecha nombrada en un audio o un mensaje nuevo tienen que hacerse notar.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from estiba.adapters.escritorio import VARIABLE, AvisoEscritorio
from estiba.conversation.store import ConversationStore
from estiba.core.models import Message, MessageKind
from estiba.storage.db import connect
from estiba.web.app import create_app


class Anotador:
    """Un escritorio de mentira: anota lo que se hubiera mostrado."""

    def __init__(self) -> None:
        self.avisos: list[tuple[str, str, bool]] = []

    def avisar(self, titulo, cuerpo, urgente=False, enlace=None):
        self.avisos.append((titulo, cuerpo, urgente))
        return True


LUNES = datetime(2026, 9, 28, 9, 0)


def _chat(conn, chat_id, lineas, inicio=datetime(2026, 9, 28, 8, 0)):
    ConversationStore(conn).ingest([
        Message(f"{chat_id}:{i}", chat_id, quien == "c", inicio.replace(minute=i), MessageKind.TEXT, text=t)
        for i, (quien, t) in enumerate(lineas)
    ])


@pytest.fixture
def armar(tmp_path):
    def _armar(lineas, ahora=LUNES):
        ruta = tmp_path / "a.db"
        conn = connect(ruta)
        _chat(conn, "5492235550000", lineas)
        conn.close()
        anotador = Anotador()
        reloj = {"t": ahora}
        app = create_app(ruta, ahora=lambda: reloj["t"], avisos=anotador)
        return app, anotador, reloj
    return _armar


POSIBLE = [("c", "hola, necesito llevar una heladera de Alberti 2359 a Catamarca 1185"),
           ("v", "te sale 55 mil"),
           ("c", "seria el martes 29 a las 11, mañana te confirmo")]


class TestFechasPosibles:
    def test_una_fecha_que_aparece_avisa_en_el_momento(self, armar):
        app, anotador, _ = armar(POSIBLE)
        with TestClient(app):
            pass
        assert any(t.startswith("Posible flete mañana 11:00") for t, _, _ in anotador.avisos)

    def test_y_se_ve_arriba_en_hoy(self, armar):
        app, _, _ = armar(POSIBLE)
        with TestClient(app) as c:
            html = c.get("/").text
        assert "Fechas por confirmar" in html and "mañana 11:00" in html

    def test_la_vispera_a_la_manana_recuerda_confirmar_una_sola_vez(self, armar):
        app, anotador, reloj = armar(POSIBLE)
        with TestClient(app) as c:
            anotador.avisos.clear()
            reloj["t"] = datetime(2026, 9, 28, 10, 5)
            # En la app corre en segundo plano cada 5 minutos: aca se llama directo.
            c.app.state.recordatorios()
            (titulo, cuerpo, urgente), = anotador.avisos
            assert titulo.startswith("¿Se confirma el flete de mañana") and urgente
            assert c.app.state.recordatorios() == 0, "no avisa dos veces lo mismo"

    def test_antes_de_las_10_no_molesta(self, armar):
        app, anotador, reloj = armar(POSIBLE)
        with TestClient(app) as c:
            anotador.avisos.clear()
            reloj["t"] = datetime(2026, 9, 28, 9, 30)
            assert c.app.state.recordatorios() == 0


class TestEscritorio:
    def test_manda_el_aviso_y_suena(self, monkeypatch):
        monkeypatch.setenv(VARIABLE, "1")
        corridos = []
        a = AvisoEscritorio(ejecutar=lambda orden, **_: corridos.append(orden))
        monkeypatch.setattr(a, "disponible", lambda: True)
        assert a.avisar("Mensaje de Agus", "cuánto me sale?")
        assert corridos[0][0] == "notify-send" and "Mensaje de Agus" in corridos[0]
        assert len(corridos) == 2, "y suena"

    def test_se_apaga_con_la_variable(self, monkeypatch):
        monkeypatch.setenv(VARIABLE, "0")
        corridos = []
        a = AvisoEscritorio(ejecutar=lambda orden, **_: corridos.append(orden))
        assert not a.avisar("x", "y") and corridos == []

    def test_sin_escritorio_no_rompe(self, monkeypatch):
        monkeypatch.setenv(VARIABLE, "1")

        def falla(orden, **_):
            raise OSError("sin escritorio")

        a = AvisoEscritorio(ejecutar=falla)
        monkeypatch.setattr(a, "disponible", lambda: True)
        assert a.avisar("x", "y") is False
