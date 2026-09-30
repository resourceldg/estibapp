"""La pantalla Camioneta: configurar lo que el negocio tiene que hacer por vos."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from estiba.economia.cargar import economia
from estiba.storage.db import connect
from estiba.storage.repositories import EconomiaRepository
from estiba.web.app import create_app

D = Decimal
HOY = date(2026, 9, 29)


@pytest.fixture
def web(tmp_path):
    ruta = tmp_path / "cam.db"
    connect(ruta).close()
    with TestClient(create_app(ruta, ahora=lambda: datetime(2026, 9, 29, 18)), follow_redirects=False) as c:
        yield c, ruta


def _e(ruta):
    conn = connect(ruta)
    try:
        return economia(conn, HOY)
    finally:
        conn.close()


class TestPantalla:
    def test_responde_primero_hasta_donde_rinde_el_base(self, web):
        c, _ = web
        html = c.get("/numeros/camioneta").text
        assert "¿Hasta dónde rinde tu precio base?" in html
        assert "$40.000" in html
        assert "es saludable hasta unos" in html
        assert "Es una exigencia, no una ganancia" in html

    def test_avisa_lo_que_falta_en_vez_de_inventarlo(self, web):
        c, _ = web
        html = c.get("/numeros/camioneta").text
        assert "Falta valor de mercado" in html
        assert "Sin la fecha de la primera cuota no se sabe cuándo termina" in html


class TestGuardar:
    def test_cambiar_el_precio_base_deja_historial(self, web):
        c, ruta = web
        r = c.post("/numeros/camioneta/precio-base", data={"monto": "45.000", "desde": "2026-09-29", "nota": "GNC"})
        assert r.status_code == 303
        assert _e(ruta).precio_base == D(45000)
        conn = connect(ruta)
        (nuevo, viejo) = EconomiaRepository(conn).historial_precio_base()
        conn.close()
        assert D(nuevo["anterior"]) == D(40000)
        assert "antes $40.000" in c.get("/numeros/camioneta").text

    def test_un_precio_base_invalido_no_entra(self, web):
        c, _ = web
        assert c.post("/numeros/camioneta/precio-base", data={"monto": "cero", "desde": "2026-09-29"}).status_code == 422

    def test_la_disponibilidad_en_minutos_y_el_pico_nunca_debajo_del_comodo(self, web):
        c, ruta = web
        c.post("/numeros/camioneta/disponibilidad", data={"dias_semana": "3", "fletes_dia_comodo": "2",
                                                          "fletes_dia_pico": "1", "coordinacion_min": "30"})
        d = _e(ruta).disponibilidad
        assert d.dias_semana == D(3)
        assert d.fletes_dia_pico == D(2)
        assert d.h_coordinacion == D("0.5")

    def test_la_camioneta_y_el_credito(self, web):
        c, ruta = web
        c.post("/numeros/camioneta/activo", data={"valor_mercado": "12.000.000", "primera": "2026-01",
                                                  "cuotas": "72", "depreciacion_mes": ""})
        e = _e(ruta)
        assert e.camioneta.valor_mercado == D(12000000)
        assert e.camioneta.depreciacion_mes is None
        assert e.credito.primera == date(2026, 1, 1)
        assert "Quedan 63 cuotas; la última es en 12/2031" in c.get("/numeros/camioneta").text

    def test_los_fondos(self, web):
        c, ruta = web
        c.post("/numeros/camioneta/fondos", data={"reposicion__aporte_mes": "80.000", "viajes__aporte_mes": "150.000",
                                                  "viajes__objetivo": "600.000"})
        fondos = {f.clave: f for f in _e(ruta).fondos}
        assert fondos["reposicion"].aporte_mes == D(80000)
        assert fondos["viajes"].objetivo == D(600000)
        assert fondos["mantenimiento"].aporte_mes == D(0)
        assert "No hay aporte a los fondos" not in c.get("/numeros/camioneta").text
