"""Reputacion, fletes perdidos y recuperacion. Ver docs/recuperacion.md."""

from datetime import datetime

import pytest

from estiba.storage.db import connect
from estiba.storage.repositories import ContactoEventoRepository, RecontactoRepository

T = datetime(2026, 10, 5, 10, 0)


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "r.db")
    yield c
    c.close()


class TestTablas:
    def test_los_eventos_de_una_persona_se_agregan_y_no_se_pisan(self, conn):
        r = ContactoEventoRepository(conn)
        r.add("549223", "no_contactar", T, nota="me pidio que no le escriba")
        r.add("549223", "contactar", T.replace(hour=11))
        assert [e["tipo"] for e in r.de("549223")] == ["no_contactar", "contactar"]

    def test_un_solo_recontacto_preparado_por_persona(self, conn):
        r = RecontactoRepository(conn)
        a = r.preparar("549223", "549223:job", "precio", "precio", 1, "hola", T)
        b = r.preparar("549223", "549223:job", "precio", "precio", 1, "hola de nuevo", T)
        assert a == b and r.get(a)["texto"] == "hola de nuevo"
        r.decidir(a, "enviado", T, message_id="m1")
        with pytest.raises(KeyError):
            r.decidir(a, "enviado", T)   # no se manda dos veces


class TestMotivoDePerdida:
    def test_la_nota_nueva_y_la_vieja_se_leen_igual(self):
        from estiba.jobs.engine import motivo_de_nota

        nueva = '{"motivo": "precio", "etiqueta": "Precio alto", "nota": "otra de 65 mil", "competencia": "65000"}'
        assert motivo_de_nota(nueva) == ("precio", "Precio alto", "otra de 65 mil", "65000")
        assert motivo_de_nota("Consiguió otro") == ("otro-flete", "Consiguió otro", None, None)
        assert motivo_de_nota("se arrepintio") == (None, None, "se arrepintio", None)

    def test_dar_por_perdido_guarda_codigo_comentario_y_competencia(self, web):
        job = "5491155556666:job"
        web.post(f"/fletes/{job}/perdido", data={"motivo": "precio", "nota": "le pasé 85", "competencia": "65.000"})
        html = web.get(f"/fletes/{job}").text
        assert "Perdido: Precio alto · le pasé 85" in html
        # Se corrige despues sin reabrir nada.
        web.post(f"/fletes/{job}/motivo", data={"motivo": "otro-flete"})
        assert "Perdido: Consiguió otro fletero" in web.get(f"/fletes/{job}").text
        assert web.get(f"/api/fletes/{job}").json()["flete"]["estado"] == "cancelado"

    def test_el_motivo_solo_se_anota_sobre_algo_perdido(self, web):
        assert web.post("/fletes/5491155556666:job/motivo", data={"motivo": "precio"}).status_code == 409
        assert web.post("/fletes/5491155556666:job/perdido", data={"motivo": "inventado"}).status_code == 422


JUEVES_18 = datetime(2026, 9, 18, 12)


@pytest.fixture
def web(tmp_path):
    from fastapi.testclient import TestClient

    from estiba.adapters.fixtures import FixtureMessageSource
    from estiba.conversation.store import ConversationStore
    from estiba.web.app import create_app

    ruta = tmp_path / "web.db"
    c = connect(ruta)
    ConversationStore(c).ingest(list(FixtureMessageSource().poll()))
    c.close()
    with TestClient(create_app(ruta, ahora=lambda: JUEVES_18)) as cliente:
        yield cliente
