"""Fase 3: al cerrar un flete queda lo que paso de verdad, con el menor esfuerzo."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from estiba.adapters.fixtures import FixtureMessageSource
from estiba.conversation.store import ConversationStore
from estiba.economia.registro import (
    CONFIRMADO,
    CORREGIDO,
    ESTIMADO,
    Registro,
    canal_inferido,
    cerrar,
    desvio,
)
from estiba.finanzas.precio import PrecioPorObjetivos
from estiba.jobs.engine import JobEngine
from estiba.storage.db import connect
from estiba.storage.repositories import FleteRealRepository
from estiba.web.app import create_app

D = Decimal
EST = Registro(km=D(40), km_vacios=D(30), horas=D("2.5"), canal="meta")


class TestCerrar:
    def test_visto_y_sin_tocar_es_asi_fue(self):
        r = cerrar(EST, visto=True, cambios={})
        assert r.fuente == CONFIRMADO and r.medido and r.km == D(40)

    def test_sin_ver_la_estimacion_no_cuenta_como_medido(self):
        r = cerrar(EST, visto=False, cambios={})
        assert r.fuente == ESTIMADO and not r.medido

    def test_corregir_un_numero_lo_marca_corregido(self):
        r = cerrar(EST, visto=True, cambios={"horas": D(4), "espera_h": D("0.5")})
        assert r.fuente == CORREGIDO and r.horas == D(4) and r.km == D(40)

    def test_escribir_el_mismo_numero_no_es_una_correccion(self):
        assert cerrar(EST, visto=True, cambios={"km": D(40)}).fuente == CONFIRMADO

    def test_cambiar_el_canal_no_corrige_la_medicion(self):
        r = cerrar(EST, visto=True, cambios={"canal": "referido"})
        assert r.fuente == CONFIRMADO and r.canal == "referido"

    def test_un_canal_inventado_se_ignora(self):
        assert cerrar(EST, visto=True, cambios={"canal": "tiktok"}).canal == "meta"

    def test_ayudante_y_otros_son_costos_directos(self):
        r = cerrar(EST, visto=True, cambios={"ayudante_pagado": D(15000), "otros_gastos": D(2000)})
        assert r.directos == D(17000)

    def test_el_desvio_es_real_menos_estimado(self):
        d = desvio(EST, cerrar(EST, visto=True, cambios={"horas": D(3), "km": D(35)}))
        assert d.horas == D("0.5") and d.km == D(-5)


class TestCanal:
    def test_lo_que_se_sabe_con_seguridad(self):
        assert canal_inferido(True, True) == "recurrente"
        assert canal_inferido(False, True) == "meta"
        assert canal_inferido(False, False) == "otro"


@pytest.fixture
def web(tmp_path):
    ruta = tmp_path / "real.db"
    conn = connect(ruta)
    ConversationStore(conn).ingest(list(FixtureMessageSource().poll()))
    conn.close()
    app = create_app(ruta, engine=JobEngine(pricing=PrecioPorObjetivos(None)),
                     ahora=lambda: datetime(2026, 9, 25, 18))
    with TestClient(app, follow_redirects=False) as c:
        yield c, ruta


def _cerrable(c) -> str:
    """Un flete que se puede marcar realizado y tiene evaluacion."""
    for job in ("5491177778888:job", "5491133334444:job", "5491122223333:job"):
        if "Se anota así" in c.get(f"/fletes/{job}").text:
            return job
    pytest.skip("ningun flete de las fixtures tiene estimacion para cerrar")


class TestCierreWeb:
    def test_asi_fue_con_un_toque(self, web):
        c, ruta = web
        job = _cerrable(c)
        assert c.post(f"/fletes/{job}/realizado", data={"cobrado": "40.000", "visto": "1"}).status_code in (200, 303)
        conn = connect(ruta)
        r = FleteRealRepository(conn).ultimo(job)
        conn.close()
        assert r.fuente == CONFIRMADO and r.km is not None and r.horas is not None

    def test_corregir_horas_y_espera(self, web):
        c, ruta = web
        job = _cerrable(c)
        c.post(f"/fletes/{job}/realizado", data={"cobrado": "40.000", "visto": "1", "horas": "3:30",
                                                 "espera_min": "30", "canal": "referido"})
        conn = connect(ruta)
        r = FleteRealRepository(conn).ultimo(job)
        conn.close()
        assert r.fuente == CORREGIDO and r.horas == D("3.5") and r.espera_h == D("0.5") and r.canal == "referido"

    def test_desde_la_agenda_compacta_queda_como_estimado(self, web):
        c, ruta = web
        job = _cerrable(c)
        c.post(f"/fletes/{job}/realizado", data={"cobrado": "40.000"})
        conn = connect(ruta)
        assert FleteRealRepository(conn).ultimo(job).fuente == ESTIMADO
        conn.close()
