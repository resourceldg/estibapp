"""El tablero de decisiones, la calculadora, los escenarios y el informe.

Los numeros de base son los de la primera campaña: 3 fletes de 29
conversaciones de Meta, $1.849 por conversacion, $35.000 por flete y unos
4 fletes por mes que llegan sin publicidad.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from estiba.finanzas.costos import Costos
from estiba.finanzas.decision import Modelo, equilibrio, evaluar, montecarlo, optimizar, sensibilidad
from estiba.finanzas.simular import simular
from estiba.storage.db import connect
from estiba.storage.repositories import DecisionRepository, GastoRepository, SettingsRepository
from estiba.web.app import create_app

D = Decimal
HOY = Modelo(precio=35000, gasto_dia=3829, costo_consulta=1849, cierre=3 / 29, solos_mes=4.3)


class TestModelo:
    def test_mas_publicidad_rinde_cada_vez_menos(self):
        """Duplicar el gasto no duplica las consultas: 2^0,8 = 1,74 veces."""
        una, doble = HOY.consultas(3829), HOY.consultas(7658)
        assert abs(doble / una - 2 ** 0.8) < 1e-9

    def test_subir_el_precio_baja_el_cierre(self):
        assert HOY.cierre_a(70000) == pytest.approx(HOY.cierre / 2)       # elasticidad 1
        assert replace(HOY, elasticidad=0).cierre_a(70000) == pytest.approx(HOY.cierre)

    def test_la_capacidad_es_un_techo(self):
        c = Costos(fletes_por_mes_max=D(10))
        r = evaluar(c, HOY, 35000, 40000)
        assert r.fletes == 10 and r.pedidos > 10 and r.sobran > 0

    def test_el_optimo_es_el_mejor_de_la_grilla(self):
        o = optimizar(Costos(), HOY)
        assert o.mejor.ganancia == max(g for _, _, g in o.grilla)
        assert o.mejor.fletes <= 48

    def test_sin_elasticidad_conviene_cobrar_mas(self):
        """Si el precio no espanta a nadie, el optimo esta en el precio mas alto."""
        o = optimizar(Costos(), replace(HOY, elasticidad=0))
        assert o.mejor.precio == max(o.precios)

    def test_una_meta_chica_se_alcanza_y_dice_como(self):
        o = optimizar(Costos(ganancia_objetivo=D(20000)), HOY)
        assert o.meta is not None and o.meta.ganancia >= 20000

    def test_el_tornado_va_de_mayor_a_menor(self):
        _, palancas = sensibilidad(Costos(), HOY, 35000, 3829)
        rangos = [p.rango for p in palancas]
        assert rangos == sorted(rangos, reverse=True) and len(palancas) == 7

    def test_equilibrio(self):
        c = Costos()
        n = equilibrio(c, 35000, 3829)
        # A n fletes, lo que deja cada uno paga exactamente fijos y publicidad.
        assert (35000 - float(c.camioneta_por_flete)) * n == pytest.approx(
            float(c.fijos_mes) + 3829 * 30 * float(c.con_impuestos))
        assert equilibrio(c, 5000, 3829) is None       # cada flete pierde: no hay cantidad que alcance

    def test_montecarlo_es_reproducible(self):
        """La misma semilla, el mismo numero: un test lo puede comprobar."""
        a = montecarlo(Costos(), HOY, 35000, 3829, exitos=3, casos=29, cobrados=[35000.0], meses=500)
        b = montecarlo(Costos(), HOY, 35000, 3829, exitos=3, casos=29, cobrados=[35000.0], meses=500)
        assert a.ganancias == b.ganancias and a.p10 <= a.p50 <= a.p90
        assert 0 <= a.p_ganar <= 1 and a.p_meta == 0

    def test_montecarlo_ve_la_incertidumbre_del_cierre(self):
        """Con 3 de 29 el cierre puede ser muy distinto: el rango es ancho."""
        s = montecarlo(Costos(), HOY, 35000, 3829, exitos=3, casos=29, cobrados=[35000.0], meses=2000)
        assert s.p90 - s.p10 > 150000


class TestCalculadora:
    def test_la_cuenta_del_mes(self):
        c = Costos()
        s = simular(c, precio=D(35000), gasto_dia=D(4000), costo_consulta=D(2000), cierre=D(10), solos_mes=D(0))
        assert s.consultas == 60 and s.fletes == 6
        assert s.ganancia == D(35000) * 6 - c.camioneta_por_flete * 6 - D(4000) * 30 * D("1.25") - c.fijos_mes
        assert s.minimo == s.costos / 6

    def test_avisa_cuando_la_publicidad_trae_mas_de_lo_que_entra(self):
        s = simular(Costos(), precio=D(35000), gasto_dia=D(60000), costo_consulta=D(1000), cierre=D(20), solos_mes=D(0))
        assert s.fletes == 48 and s.sobran > 0


# --------------------------------------------------------------------------
# La pantalla
# --------------------------------------------------------------------------


@pytest.fixture
def web(tmp_path):
    ruta = tmp_path / "t.db"
    conn = connect(ruta)
    GastoRepository(conn).cargar(date(2026, 9, 16), date(2026, 9, 29), D("53608.62"), resultados=29)
    conn.close()
    with TestClient(create_app(ruta, ahora=lambda: datetime(2026, 9, 29, 18)), follow_redirects=False) as c:
        yield c, ruta


CALC = {"precio": "55000", "gasto_dia": "6000", "costo_consulta": "1849", "cierre": "10,3", "solos": "4,3",
        "meta": "2000000", "capacidad": "48"}


class TestTablero:
    def test_estan_todas_las_preguntas(self, web):
        c, _ = web
        html = c.get("/numeros/tablero").text
        for pregunta in ("Lo importante", "¿A dónde se va la plata?", "¿Cómo viene?", "¿Dónde se pierden los clientes?",
                         "¿Cómo termina el mes?", "¿Qué palanca pesa más?", "¿Cuál es la mejor combinación?",
                         "¿Y si…? Calculadora", "Escenarios", "¿Cuánto cobrar?", "Qué hacer esta semana"):
            assert pregunta in html, pregunta
        assert "<svg" in html

    @pytest.mark.parametrize("consulta", [
        "periodo=7d", "periodo=mes", "periodo=todo", "origen=publicidad", "origen=vuelven",
        "periodo=fechas&desde=2026-09-01&hasta=2026-09-20", "periodo=fechas&desde=cualquiera",
        "periodo=inventado&origen=inventado", "elasticidad=2.5&saturacion=0.5&tope=20000",
        "elasticidad=nan&saturacion=99",
    ])
    def test_los_filtros_no_rompen(self, web, consulta):
        c, _ = web
        assert c.get(f"/numeros/tablero?{consulta}").status_code == 200

    def test_la_calculadora_se_recalcula(self, web):
        c, _ = web
        r = c.get("/numeros/tablero/simular", params=CALC)
        assert r.status_code == 200 and "Si cambiás el precio" in r.text and "Si cambiás la publicidad" in r.text

    def test_un_decimal_con_punto_no_es_un_numero_de_miles(self, web):
        """"10.3" en el cierre es 10,3 %, no 103 %: eso llenaba la capacidad sola."""
        c, _ = web
        con_punto = c.get("/numeros/tablero/simular", params={**CALC, "cierre": "10.3"}).text
        con_coma = c.get("/numeros/tablero/simular", params={**CALC, "cierre": "10,3"}).text
        assert con_punto == con_coma


class TestDecidir:
    def _guardar(self, c, nombre, **cambios):
        return c.post("/numeros/tablero/escenario", data={**CALC, "nombre": nombre, **cambios})

    def test_guardar_y_comparar(self, web):
        c, _ = web
        self._guardar(c, "Subo precio")
        self._guardar(c, "Más publicidad", precio="35000", gasto_dia="12000")
        html = c.get("/numeros/tablero").text
        assert "Subo precio" in html and "Más publicidad" in html

    def test_aplicar_cambia_la_configuracion(self, web):
        """Decidir mueve la meta y la capacidad: con eso cambia el precio de cada flete."""
        c, ruta = web
        self._guardar(c, "Meta realista", meta="600000", capacidad="30", cierre="12", costo_consulta="1700")
        conn = connect(ruta)
        (e,) = DecisionRepository(conn).todas()
        conn.close()
        assert c.post(f"/numeros/tablero/escenario/{e['id']}/aplicar").status_code == 303
        conn = connect(ruta)
        costos = Costos.desde_dict(SettingsRepository(conn).json("costos"))
        vigente = DecisionRepository(conn).vigente()
        conn.close()
        assert costos.ganancia_objetivo == D(600000) and costos.fletes_por_mes_max == D(30)
        assert costos.cierre == D(12) and costos.costo_por_consulta == D(1700)
        assert vigente["nombre"] == "Meta realista"
        html = c.get("/numeros/tablero").text
        assert "Tu decisión vigente" in html and "Meta realista" in html

    def test_una_decision_nueva_reemplaza_a_la_anterior_sin_borrarla(self, web):
        c, ruta = web
        self._guardar(c, "Primera")
        self._guardar(c, "Segunda")
        conn = connect(ruta)
        primera, segunda = DecisionRepository(conn).todas()
        conn.close()
        c.post(f"/numeros/tablero/escenario/{primera['id']}/aplicar")
        c.post(f"/numeros/tablero/escenario/{segunda['id']}/aplicar")
        conn = connect(ruta)
        estados = {f["nombre"]: f["estado"] for f in DecisionRepository(conn).todas()}
        conn.close()
        assert estados == {"Primera": "reemplazada", "Segunda": "aplicada"}
        assert "Decisiones anteriores" in c.get("/numeros/tablero").text

    def test_una_decision_aplicada_no_se_borra(self, web):
        c, ruta = web
        self._guardar(c, "Aplicada")
        conn = connect(ruta)
        (e,) = DecisionRepository(conn).todas()
        conn.close()
        c.post(f"/numeros/tablero/escenario/{e['id']}/aplicar")
        assert c.post(f"/numeros/tablero/escenario/{e['id']}/borrar").status_code == 409

    def test_aplicar_algo_que_no_existe(self, web):
        c, _ = web
        assert c.post("/numeros/tablero/escenario/999/aplicar").status_code == 404


class TestInforme:
    def test_el_pdf(self, web):
        pytest.importorskip("weasyprint")
        c, _ = web
        r = c.get("/numeros/informe.pdf?periodo=todo")
        assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
        assert r.content.startswith(b"%PDF") and len(r.content) > 20000
        assert "attachment" in r.headers["content-disposition"]

    def test_el_nombre_del_negocio_va_en_el_informe(self, web):
        c, ruta = web
        c.post("/numeros/objetivos", data={"negocio": "Flete Surfero"})
        conn = connect(ruta)
        assert SettingsRepository(conn).json("negocio") == {"nombre": "Flete Surfero"}
        conn.close()
