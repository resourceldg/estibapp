"""La publicidad cuenta lo que salio del bolsillo, sin pagar dos veces los impuestos.

Hasta la migracion 022 cada gasto se multiplicaba por (1 + impuestos) sin
saber si lo cargado ya era el total de la factura. Estos tests fijan los
cuatro casos: neto con impuestos aparte, total con impuestos incluidos,
sin impuestos, y que nunca se dupliquen.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from estiba.finanzas.costos import Costos, GastoPublicidad, balance
from estiba.storage.db import connect
from estiba.storage.repositories import GastoRepository
from estiba.web.app import create_app

D = Decimal
DESDE, HASTA = date(2026, 9, 16), date(2026, 9, 29)
C = Costos(impuestos_publicidad=D(25))


def _pagado(g: GastoPublicidad) -> Decimal:
    return balance(C, [], [g], DESDE, HASTA).publicidad


class TestLoPagado:
    def test_neto_con_impuestos_separados_suma_los_de_la_factura(self):
        g = GastoPublicidad(DESDE, HASTA, D(40000), impuestos=D(10000))
        assert _pagado(g) == D(50000)
        assert g.neto(C.impuestos_publicidad) == D(40000)
        assert not g.impuestos_estimados(C.impuestos_publicidad)

    def test_total_con_impuestos_incluidos_no_se_toca(self):
        g = GastoPublicidad(DESDE, HASTA, D(50000), incluye_impuestos=True)
        assert _pagado(g) == D(50000)
        # El neto se deduce con la tasa, y se dice que es una estimacion.
        assert g.neto(C.impuestos_publicidad) == D(40000)
        assert g.impuestos_estimados(C.impuestos_publicidad)

    def test_total_con_impuestos_conocidos_da_el_neto_exacto(self):
        g = GastoPublicidad(DESDE, HASTA, D(50000), impuestos=D(8000), incluye_impuestos=True)
        assert _pagado(g) == D(50000)
        assert g.neto(C.impuestos_publicidad) == D(42000)

    def test_sin_impuestos_es_lo_cargado(self):
        g = GastoPublicidad(DESDE, HASTA, D(40000))
        assert balance(Costos(impuestos_publicidad=D(0)), [], [g], DESDE, HASTA).publicidad == D(40000)
        assert not g.impuestos_estimados(D(0))

    def test_neto_sin_impuestos_cargados_usa_la_tasa_como_antes(self):
        """Las cargas viejas (migradas) se cuentan igual que antes de la 022."""
        g = GastoPublicidad(DESDE, HASTA, D("53608.62"))
        assert _pagado(g) == D("53608.62") * D("1.25")
        assert g.impuestos_estimados(C.impuestos_publicidad)

    @pytest.mark.parametrize("g", [
        GastoPublicidad(DESDE, HASTA, D(50000), incluye_impuestos=True),
        GastoPublicidad(DESDE, HASTA, D(50000), impuestos=D(10000), incluye_impuestos=True),
        GastoPublicidad(DESDE, HASTA, D(40000), impuestos=D(10000)),
    ])
    def test_nunca_se_duplica_el_impuesto(self, g):
        """Tres formas de cargar la misma factura de $50.000: las tres pagan $50.000."""
        assert _pagado(g) == D(50000)

    def test_el_prorrateo_corta_lo_pagado_no_lo_neto(self):
        g = GastoPublicidad(date(2026, 9, 1), date(2026, 9, 10), D(10000), incluye_impuestos=True)
        assert balance(C, [], [g], date(2026, 9, 6), date(2026, 9, 10)).publicidad == D(5000)


@pytest.fixture
def web(tmp_path):
    ruta = tmp_path / "publi.db"
    connect(ruta).close()
    with TestClient(create_app(ruta, ahora=lambda: datetime(2026, 9, 29, 18)), follow_redirects=False) as c:
        yield c, ruta


class TestCarga:
    def test_la_factura_se_guarda_como_total(self, web):
        c, ruta = web
        c.post("/numeros/objetivos/gasto", data={"desde": "2026-09-16", "hasta": "2026-09-29",
                                                 "monto": "50.000", "tipo": "total"})
        conn = connect(ruta)
        (g,) = GastoRepository(conn).todos()
        conn.close()
        assert g["incluye_impuestos"] == 1 and g["impuestos"] is None
        assert "total pagado, impuestos incluidos" in c.get("/numeros/objetivos").text

    def test_el_neto_con_impuestos_de_la_factura(self, web):
        c, ruta = web
        c.post("/numeros/objetivos/gasto", data={"desde": "2026-09-16", "hasta": "2026-09-29",
                                                 "monto": "40.000", "impuestos": "10.000"})
        conn = connect(ruta)
        (g,) = GastoRepository(conn).todos()
        conn.close()
        assert g["incluye_impuestos"] == 0 and D(g["impuestos"]) == D(10000)
        assert "lo pagado a Meta, impuestos incluidos" in c.get("/numeros/objetivos?periodo=mes").text

    def test_sin_decir_nada_es_neto_con_impuestos_estimados(self, web):
        c, _ = web
        c.post("/numeros/objetivos/gasto", data={"desde": "2026-09-16", "hasta": "2026-09-29", "monto": "40.000"})
        html = c.get("/numeros/objetivos").text
        assert "de impuestos estimados" in html
