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


# --------------------------------------------------------------------------
# Personas: situacion, reputacion, historial (dominio puro)
# --------------------------------------------------------------------------

from decimal import Decimal  # noqa: E402

from estiba.core.models import Stage  # noqa: E402
from estiba.crm import personas as PS  # noqa: E402
from estiba.crm.leads import Paso  # noqa: E402
from estiba.crm.perdida import Perdida  # noqa: E402


def _op(n, etapa, dia, alcanzo=None, perdida=None, cobrado=None, cosas=None):
    at = datetime(2026, 9, dia, 10)
    historia = (Paso(Stage.NEW, at, ""),) + ((Paso(etapa, at.replace(hour=12), ""),) if etapa is not Stage.NEW else ())
    return PS.Oportunidad(f"549:job{n}", at, etapa, alcanzo or etapa, historia, ruta="A → B", cosas=cosas,
                          cotizado=Decimal(40000), cobrado=cobrado, perdida=perdida)


def _perdida(codigo, silencio=False, decidida=True):
    return Perdida(codigo, codigo, None, None, datetime(2026, 9, 1), decidida, silencio)


def _p(ops, eventos=()):
    return PS.Persona("549", None, "+549", ops, [PS.Evento(t, datetime(2026, 9, 30, i), v, n)
                                               for i, (t, v, n) in enumerate(eventos)])


class TestSituacion:
    def test_lead_cliente_recurrente(self):
        assert _p([_op(1, Stage.LOST, 1)]).situacion == PS.LEAD
        assert _p([_op(1, Stage.DONE, 1)]).situacion == PS.CLIENTE
        assert _p([_op(1, Stage.DONE, 1), _op(2, Stage.DONE, 5)]).situacion == PS.RECURRENTE

    def test_varias_oportunidades_en_el_mismo_perfil_y_recuperado(self):
        p = _p([_op(1, Stage.LOST, 1, perdida=_perdida("otro-flete")), _op(2, Stage.DONE, 10),
                _op(3, Stage.DONE, 20)])
        assert (p.consultas, p.perdidos, p.completados) == (3, 1, 2)
        assert p.recuperado and p.situacion == PS.RECURRENTE

    def test_perder_no_es_recuperar_si_no_contrato_despues(self):
        assert not _p([_op(1, Stage.DONE, 1), _op(2, Stage.LOST, 10)]).recuperado

    def test_no_contactar_vale_el_ultimo(self):
        assert _p([_op(1, Stage.LOST, 1)], [("no_contactar", None, "me pidio")]).no_contactar
        assert not _p([_op(1, Stage.LOST, 1)], [("no_contactar", None, None), ("contactar", None, None)]).no_contactar


class TestReputacion:
    def test_un_lead_sin_historia_queda_normal(self):
        r = PS.reputacion(_p([_op(1, Stage.LOST, 1, perdida=_perdida("precio"))]))
        assert r.valor == 60 and r.nivel == "Normal"

    def test_explica_por_que(self):
        p = _p([_op(1, Stage.DONE, 1), _op(2, Stage.DONE, 5), _op(3, Stage.DONE, 9),
                _op(4, Stage.LOST, 20, perdida=_perdida("cancelo"))], [("pago_ok", None, None)])
        r = PS.reputacion(p)
        textos = [t for _, t in r.motivos]
        assert "3 fletes completados" in textos and "cliente recurrente" in textos and "1 cancelación" in textos
        assert r.valor == 60 + 30 + 10 + 8 - 8 and r.nivel == "Excelente"

    def test_problemas_de_pago_pesan(self):
        assert PS.reputacion(_p([_op(1, Stage.DONE, 1)], [("pago_problema", None, None)])).nivel == "Atención"

    def test_la_correccion_manual_no_borra_la_cuenta(self):
        r = PS.reputacion(_p([_op(1, Stage.DONE, 1)], [("reputacion", "40", "me dejo plantado")]))
        assert (r.valor, r.calculado, r.nota_manual) == (40, 70, "me dejo plantado")

    def test_el_silencio_sin_presupuesto_no_resta(self):
        hola = _op(1, Stage.LOST, 1, alcanzo=Stage.NEW, perdida=_perdida("no-respondio", True, False))
        assert PS.reputacion(_p([hola])).valor == 60


class TestEtiquetasEHistorial:
    def test_las_automaticas_respetan_lo_que_sacaste(self):
        p = _p([_op(1, Stage.LOST, 1, perdida=_perdida("precio"))])
        assert "precio_sensible" in p.etiquetas
        p = _p([_op(1, Stage.LOST, 1, perdida=_perdida("precio"))], [("etiqueta-", "precio_sensible", None)])
        assert "precio_sensible" not in p.etiquetas

    def test_el_historial_va_en_orden(self):
        p = _p([_op(2, Stage.DONE, 10), _op(1, Stage.LOST, 1, perdida=_perdida("precio"))])
        textos = [h.texto for h in PS.historial(p)]
        assert textos[0].startswith("Consultó") and any(t.startswith("Perdido: precio") for t in textos)
        assert [h.at for h in PS.historial(p)] == sorted(h.at for h in PS.historial(p))


class TestFicha:
    def test_la_ficha_muestra_y_anota(self, web):
        chat = "5491155556666"
        html = web.get(f"/clientes/{chat}").text
        assert "Historial" in html and "/100" in html
        web.post(f"/clientes/{chat}/evento", data={"tipo": "etiqueta+", "valor": "Buen cliente"})
        web.post(f"/clientes/{chat}/evento", data={"tipo": "no_contactar", "nota": "no me escribas más"})
        html = web.get(f"/clientes/{chat}").text
        assert "buen cliente" in html and "No contactar" in html
        assert web.post(f"/clientes/{chat}/evento", data={"tipo": "inventado"}).status_code == 422
        assert web.post(f"/clientes/{chat}/evento", data={"tipo": "reputacion", "valor": "300"}).status_code == 422
        assert "no-contactar" in web.get("/clientes?situacion=no-contactar").text
