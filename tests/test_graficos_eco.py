"""Los graficos del motor economico: SVG honesto, sin nada fuera del marco."""

from __future__ import annotations

import re
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from estiba.storage.db import connect
from estiba.web import graficos_eco as G
from estiba.web.app import create_app

f = G.pesos_cortos


class TestEjes:
    def test_el_ultimo_tick_cubre_el_maximo(self):
        """Una referencia de $563 mil no puede quedar arriba del eje."""
        ticks = G._nice(-20_000, 563_000)
        assert ticks[-1] >= 563_000 and ticks[0] <= -20_000

    def test_un_negativo_chico_no_se_come_medio_grafico(self):
        ticks = G._nice(-20_000, 563_000)
        assert ticks[0] > -200_000


class TestFormas:
    def test_sin_datos_no_hay_grafico(self):
        assert G.linea([], [], f) == "" and G.barras([], f) == "" and G.apilada([], f) == ""
        assert G.escalera([("a", 0)], 10, f) == "" and G.histograma([], f) == ""

    def test_barras_con_negativos_y_su_tooltip(self):
        svg = G.barras([("Base", -448_000, "g-s2"), ("Otro", 120_000, "g-s1")], f)
        assert svg.count("<path") == 2 and "<title>Base: −$448 mil</title>" in svg

    def test_la_escalera_numera_como_la_leyenda(self):
        """Un escalon sin monto no ocupa lugar pero conserva su numero."""
        svg = G.escalera([("Uso", 100_000), ("Estructura", 300_000), ("Cuota", 0), ("Libre", 400_000)], 150_000, f)
        numeros = re.findall(r"class='g-sobre'[^>]*>(\d)</text>", svg)
        assert "3" not in numeros and "4" in numeros

    def test_apilada_hasta_cinco_partes_con_leyenda(self):
        svg = G.apilada([(str(i), 10) for i in range(7)], f)
        assert svg.count("<rect") == 10       # 5 segmentos + 5 muestras de leyenda

    def test_lineas_con_leyenda_solo_si_hay_mas_de_una_serie(self):
        una = G.linea([1, 2, 3], [("A", [1, 2, 3], "g-s1")], f)
        dos = G.linea([1, 2, 3], [("A", [1, 2, 3], "g-s1"), ("B", [3, 2, 1], "g-s2")], f)
        assert "g-linea'/>" not in una.split("<line")[0] and dos.count("x2=") > una.count("x2=")

    def test_el_histograma_no_se_estira_por_los_extremos(self):
        valores = [0.0] * 990 + [-3_000_000.0] * 10
        svg = G.histograma(valores, f, ref=(0.0, "Se paga sola"))
        assert "−$3 M" not in svg

    def test_intervalos_apagan_lo_debil(self):
        svg = G.intervalos([("Firme", 10.0, 9.0, 11.0, True, ""), ("Débil", 8.0, 2.0, 14.0, False, "")], f)
        assert "g-s1 g-punto" in svg and "g-apagado g-punto" in svg


@pytest.fixture
def web(tmp_path):
    ruta = tmp_path / "g.db"
    connect(ruta).close()
    with TestClient(create_app(ruta, ahora=lambda: datetime(2026, 9, 29, 18))) as c:
        yield c


class TestPantallas:
    @pytest.mark.parametrize("ruta", ["/numeros/camioneta", "/numeros/plan"])
    def test_las_pantallas_dibujan(self, web, ruta):
        assert "<svg class='grafico'" in web.get(ruta).text

    def test_sin_fletes_no_se_inventa_un_grafico(self, web):
        html = web.get("/numeros/validacion").text
        assert "<svg class='grafico'" not in html and "Sin fletes todavía" in html
