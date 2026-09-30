"""Fase 4: el mes de la camioneta. Niveles, autonomia, dia y horas de autofinanciamiento.

Estructura: $143.000 de fijos + $420.000 de cuota. Uso: $162 por km.
Cada flete de prueba: $50.000 cobrados, 40 km, 2,5 h -> deja 50.000 - 6.480 = 43.520.
"""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from estiba.economia.config import CONSERVACION, PERSONAL, Credito, Economia, Fondo
from estiba.economia.mes import FleteDelMes, calcular, prioridades_desde, publicidad_diaria
from estiba.finanzas.costos import Costos, GastoPublicidad
from estiba.storage.db import connect
from estiba.web.app import create_app

D = Decimal
TODOS = frozenset(Costos().a_dict())
FONDOS = (Fondo("rep", "Reposición", CONSERVACION, D(100000)), Fondo("viajes", "Viajes", PERSONAL, D(150000)))


def _e(**kw) -> Economia:
    return replace(Economia(costos=Costos(credito=D(420000), ganancia_objetivo=D(0)), precio_base=D(40000),
                            declarados=TODOS, fondos=FONDOS), **kw)


def _fletes(n: int, desde: date = date(2026, 9, 1), por_dia: int = 1, recurrente=False) -> list[FleteDelMes]:
    return [FleteDelMes(f"j{i}", desde + timedelta(days=i // por_dia), D(50000), D(40), D(30), D("2.5"),
                        recurrente=recurrente) for i in range(n)]


HOY = date(2026, 9, 30)


class TestNiveles:
    def test_sin_fletes_no_cubre_nada(self):
        m = calcular(_e(), HOY, [], {})
        assert m.nivel == 0 and m.autonomia == 0
        assert m.fletes_que_faltan is not None and m.fletes_que_faltan > 0

    def test_la_cascada_sube_en_orden(self):
        m = calcular(_e(), HOY, _fletes(4), {})           # 174.080 disponibles
        assert m.nivel == 2                                 # uso + estructura, no la cuota
        m = calcular(_e(), HOY, _fletes(14), {})           # 609.280
        assert m.nivel == 3                                 # se paga sola
        m = calcular(_e(), HOY, _fletes(20), {})           # 870.400
        assert m.nivel == 5

    def test_un_fondo_sin_monto_no_se_festeja(self):
        e = _e(fondos=(Fondo("rep", "Reposición", CONSERVACION, D(0)),))
        m = calcular(e, HOY, _fletes(30), {})
        assert m.nivel == 3
        assert m.escalones[3].monto == 0

    def test_la_publicidad_pagada_resta_antes_de_subir(self):
        con = calcular(_e(), HOY, _fletes(14), {date(2026, 9, 1): D(60000)})
        sin = calcular(_e(), HOY, _fletes(14), {})
        assert sin.disponible - con.disponible == D(60000)


class TestAutonomia:
    def test_la_autonomia_es_contra_sostener_y_conservar(self):
        m = calcular(_e(), HOY, _fletes(20), {})
        base = m.km * D(162) + D(143000) + D(420000) + D(100000)
        assert abs(m.autonomia - float(m.disponible / base)) < 1e-9
        assert m.autonomia > 1

    def test_sin_cuota_la_camioneta_se_paga_antes(self):
        e = _e(credito=Credito(cuotas=12, primera=date(2025, 1, 1)))   # termino en diciembre 2025
        con = calcular(_e(), HOY, _fletes(6), {})
        sin = calcular(e, HOY, _fletes(6), {})
        assert sin.nivel >= 3 > con.nivel


class TestAutofinanciamiento:
    def test_el_dia_y_las_horas_en_que_se_pago(self):
        m = calcular(_e(), HOY, _fletes(20), {})
        # Hace falta cubrir 563.000 (estructura + cuota) con 43.520 por flete: el 13.er flete.
        assert m.dia_autofinanciado == date(2026, 9, 13)
        assert m.horas_para_autofinanciar == D("32.5")

    def test_si_no_llega_no_hay_dia(self):
        m = calcular(_e(), HOY, _fletes(5), {})
        assert m.dia_autofinanciado is None and m.falta_para_pagarse > 0

    def test_cuantos_fletes_faltan_como_los_ultimos(self):
        m = calcular(_e(), HOY, _fletes(5), {})
        assert m.margen_tipico == D(43520)
        assert m.fletes_que_faltan == math.ceil(m.falta_para_pagarse / D(43520))

    def test_sin_historia_usa_el_precio_base_y_lo_dice(self):
        m = calcular(_e(), HOY, [], {})
        assert m.margen_tipico_casos == 0
        assert m.margen_tipico == D(40000) - D(64) * D(162)


class TestPrioridades:
    def test_pocas_y_sin_repetir(self):
        assert prioridades_desde(["se_paga_sola", "se_paga_sola", "inventada", "te_da_vida", "deja_libre",
                                  "tope_horas"]) == ("se_paga_sola", "te_da_vida", "deja_libre")
        assert prioridades_desde([]) == ("se_paga_sola", "se_conserva", "tope_horas")

    def test_objetivo_cumplido_cuando_se_llega(self):
        m = calcular(_e(), HOY, _fletes(16), {}, prioridades=("se_paga_sola", "se_conserva"))
        assert m.objetivo_cumplido
        m = calcular(_e(), HOY, _fletes(10), {}, prioridades=("se_paga_sola", "se_conserva"))
        assert not m.objetivo_cumplido

    def test_pasar_las_horas_deseadas_se_avisa(self):
        m = calcular(_e(), HOY, _fletes(50, por_dia=2), {})
        assert not m.cumplidas["tope_horas"]
        assert "pasaste tus horas deseadas" in " ".join(m.avisos)


class TestNoEmpujar:
    def test_a_principio_de_mes_dice_cuantos_faltan(self):
        from estiba.web.economia_vista import frase_mes
        m = calcular(_e(), date(2026, 9, 2), _fletes(1), {})
        assert frase_mes(m).startswith("Faltan unos")

    def test_si_no_entra_en_el_ritmo_comodo_no_pide_mas_trabajo(self):
        from estiba.web.economia_vista import frase_mes
        m = calcular(_e(), date(2026, 9, 28), _fletes(2), {})
        assert "No es para compensar trabajando más" in frase_mes(m)

    def test_objetivo_cumplido_lo_dice_primero(self):
        from estiba.web.economia_vista import frase_mes
        m = calcular(_e(), HOY, _fletes(16), {}, prioridades=("se_paga_sola",))
        assert frase_mes(m).startswith("Objetivo del mes alcanzado. No necesitás tomar más trabajos")


class TestPublicidadDiaria:
    def test_reparte_lo_pagado_por_dia(self):
        g = GastoPublicidad(date(2026, 9, 1), date(2026, 9, 10), D(10000), incluye_impuestos=True)
        dias = publicidad_diaria([g], D(25), date(2026, 9, 1), date(2026, 9, 30))
        assert sum(dias.values()) == D(10000) and len(dias) == 10


@pytest.fixture
def web(tmp_path):
    ruta = tmp_path / "mes.db"
    connect(ruta).close()
    with TestClient(create_app(ruta, ahora=lambda: datetime(2026, 9, 29, 18)), follow_redirects=False) as c:
        yield c, ruta


class TestPantalla:
    def test_como_estoy_responde_primero(self, web):
        c, _ = web
        html = c.get("/numeros/mes").text
        assert "¿Cómo estoy este mes?" in html
        # El 29 de septiembre, sin fletes, no se llega: se dice sin empujar.
        assert "No es para compensar trabajando más" in html
        assert "Se paga sola" in html

    def test_hoy_muestra_una_sola_linea_del_mes(self, web):
        c, _ = web
        assert 'class="linea-mes"' in c.get("/").text

    def test_guardar_prioridades(self, web):
        c, _ = web
        r = c.post("/numeros/mes/prioridades", data={"p0": "te_da_vida", "p1": "", "p2": "tope_horas"})
        assert r.status_code == 303
        html = c.get("/numeros/mes").text
        assert "Juntar los fondos personales" in html
