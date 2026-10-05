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
