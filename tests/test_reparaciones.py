"""Reparaciones, dias parada, saldos de fondos, despues del credito y minima intervencion."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from estiba.economia.config import Credito, Disponibilidad, Economia
from estiba.economia.mes import FleteDelMes, calcular
from estiba.economia.reparaciones import (
    EXTRAORDINARIA,
    ORDINARIA,
    Reparacion,
    del_periodo,
    extraordinarias,
    saldo,
    tipo_sugerido,
)
from estiba.finanzas.costos import Costos
from estiba.storage.db import connect
from estiba.storage.repositories import ReparacionRepository
from estiba.web.app import create_app
from estiba.web.economia_vista import despues_del_credito

D = Decimal
HOY = date(2026, 9, 30)
E = Economia(costos=Costos(credito=D(420000), ganancia_objetivo=D(0)), precio_base=D(40000),
             declarados=frozenset(Costos().a_dict()))


class TestReparaciones:
    def test_el_tipo_se_deduce_del_componente(self):
        assert tipo_sugerido("Embrague") == EXTRAORDINARIA
        assert tipo_sugerido("caja de cambios") == EXTRAORDINARIA
        assert tipo_sugerido("pastillas de freno") == ORDINARIA

    def test_downtime_es_capacidad_perdida_no_plata(self):
        reps = [Reparacion(date(2026, 9, 10), "embrague", EXTRAORDINARIA, D(600000), dias_parada=7)]
        p = del_periodo(reps, date(2026, 9, 1), HOY, Disponibilidad())
        assert p.dias == 7
        assert p.fletes_perdidos == D(7) * 4 / 7 * 2      # 4 dias por semana, 2 comodos
        assert p.costo_caja == D(600000) and p.costo_fondos == 0

    def test_pagada_con_un_fondo_no_sale_de_la_caja(self):
        reps = [Reparacion(date(2026, 9, 10), "motor", EXTRAORDINARIA, D(900000), fondo="mantenimiento")]
        p = del_periodo(reps, date(2026, 9, 1), HOY, Disponibilidad())
        assert p.costo_caja == 0 and p.costo_fondos == D(900000)

    def test_cuenta_las_extraordinarias(self):
        reps = [Reparacion(HOY, "caja", EXTRAORDINARIA, D(1)), Reparacion(HOY, "aceite", ORDINARIA, D(1))]
        assert extraordinarias(reps) == 1

    def test_una_reparacion_de_la_caja_baja_lo_disponible_y_se_avisa(self):
        fletes = [FleteDelMes(f"j{i}", date(2026, 9, 1) + timedelta(days=i), D(50000), D(40), D(30), D("2.5"))
                  for i in range(20)]
        sin = calcular(E, HOY, fletes, {})
        con = calcular(E, HOY, fletes, {}, reparaciones_caja=D(300000), dias_parada=5, fletes_perdidos=D(6))
        assert sin.disponible - con.disponible == D(300000)
        assert any("parada 5 días" in a for a in con.avisos)
        assert any("de reparaciones de la caja" in a for a in con.avisos)


class TestFondos:
    def test_el_saldo_es_la_suma_de_movimientos(self):
        movs = [(date(2026, 8, 1), D(100000)), (date(2026, 9, 1), D(100000)), (date(2026, 9, 10), D(-150000))]
        assert saldo(movs) == D(50000)
        assert saldo(movs, hasta=date(2026, 8, 31)) == D(100000)

    def test_una_reparacion_con_fondo_deja_su_gasto(self, tmp_path):
        conn = connect(tmp_path / "r.db")
        repo = ReparacionRepository(conn)
        repo.mover("mantenimiento", date(2026, 9, 1), D(200000), datetime(2026, 9, 1))
        repo.cargar(Reparacion(date(2026, 9, 10), "embrague", EXTRAORDINARIA, D(150000), fondo="mantenimiento"),
                    datetime(2026, 9, 10))
        assert saldo(repo.movimientos("mantenimiento")) == D(50000)
        conn.close()


class TestDespuesDelCredito:
    def test_sin_fechas_pide_el_dato(self):
        assert "primera cuota" in despues_del_credito(E, HOY)["frase"]

    def test_con_fechas_muestra_la_estructura_nueva_y_un_destino(self):
        e = replace(E, credito=Credito(cuotas=72, primera=date(2026, 1, 1)))
        v = despues_del_credito(e, HOY)
        assert v["frase"].startswith("Desde 01/2032")
        assert "fondo de reposición" in v["sugerencia"]

    def test_sin_credito_no_hay_nada_que_decir(self):
        assert despues_del_credito(replace(E, costos=Costos(credito=D(0))), HOY) is None


@pytest.fixture
def web(tmp_path):
    ruta = tmp_path / "rep.db"
    connect(ruta).close()
    with TestClient(create_app(ruta, ahora=lambda: datetime(2026, 9, 29, 18)), follow_redirects=False) as c:
        yield c, ruta


class TestWeb:
    def test_anotar_una_reparacion_y_verla_en_el_mes(self, web):
        c, _ = web
        r = c.post("/numeros/camioneta/reparacion", data={"fecha": "2026-09-20", "componente": "Embrague",
                                                          "costo": "600.000", "dias_parada": "4"})
        assert r.status_code == 303
        assert "extraordinaria" in c.get("/numeros/camioneta").text
        assert "estuvo parada 4 días" in c.get("/numeros/mes").text

    def test_anotar_un_aporte_sube_el_saldo(self, web):
        c, _ = web
        c.post("/numeros/camioneta/fondos/viajes/mover", data={"monto": "50.000", "tipo": "aporte"})
        assert "Saldo: $50.000" in c.get("/numeros/camioneta").text

    def test_minima_intervencion_abre_hoy_en_para_decidir(self, web):
        import re

        c, _ = web
        activo = lambda html: re.search(r'class="filtro activo"[^>]*>\s*([^<]+)', html).group(1).strip()
        assert activo(c.get("/").text).startswith("Todos")
        c.post("/numeros/camioneta/modo", data={"minima": "1"})
        assert activo(c.get("/").text).startswith("Para decidir")
        # Elegir otro filtro a mano sigue funcionando.
        assert activo(c.get("/?f=todos").text).startswith("Todos")
