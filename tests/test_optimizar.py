"""Fase 6: la forma mas simple de cumplir el mes, escenarios, curvas y Monte Carlo."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal

from estiba.economia.config import CONSERVACION, PERSONAL, Disponibilidad, Economia, Fondo
from estiba.economia.optimizar import (
    Base,
    curva,
    escenarios,
    hasta_donde_conviene,
    marginal_por_hora,
    lo_mas_simple,
    mejor_por_hora,
    plan,
    publicidad_para,
    simular_mes,
)
from estiba.economia.suficiencia import Estado
from estiba.finanzas.costos import Costos

D = Decimal
MES = date(2026, 10, 1)
E = Economia(costos=Costos(credito=D(420000), ganancia_objetivo=D(0)), precio_base=D(40000),
             declarados=frozenset(Costos().a_dict()),
             fondos=(Fondo("mantenimiento", "Mantenimiento", CONSERVACION, D(80000)),
                     Fondo("viajes", "Viajes", PERSONAL, D(150000))))
FIRME = {"margen": Estado.UTILIZABLE, "horas": Estado.UTILIZABLE, "conversión": Estado.UTILIZABLE}
B = Base(ticket=45000, km=40, km_vacios=28, horas=2.5, directos=0, organicos=6, cierre=0.15,
         costo_conversacion=2300, conversaciones_base=40, calidad=FIRME)


class TestPlan:
    def test_se_concentra_en_dias_comodos(self):
        p = plan(E, B, MES, 10, 17)
        assert p.dias == 5 and p.dias_pico == 0

    def test_si_no_entran_los_comodos_aparecen_dias_pico(self):
        p = plan(E, B, MES, 36, 17)
        assert p.dias == 17 and p.dias_pico == 2

    def test_mas_alla_del_pico_no_hay_plan(self):
        assert plan(E, B, MES, 60, 17) is None

    def test_los_que_llegan_solos_no_pagan_publicidad(self):
        assert plan(E, B, MES, 6, 17).publicidad == 0
        assert plan(E, B, MES, 7, 17).publicidad > 0

    def test_la_publicidad_satura(self):
        """El flete 20 por publicidad sale mas caro que el primero."""
        primero = publicidad_para(B, 1)
        veinte = publicidad_para(B, 20) - publicidad_para(B, 19)
        assert veinte > primero


class TestLoMasSimple:
    def test_elige_el_minimo_que_cumple_y_deja_dias_libres(self):
        r = lo_mas_simple(E, B, MES, ("se_paga_sola", "tope_horas"))
        assert r.plan is not None and not r.afuera
        menos = plan(E, B, MES, r.plan.fletes - 1, 17)
        assert menos.deja < 0           # uno menos ya no alcanza: es el minimo
        assert r.dias_libres > 0
        assert "te quedan" in r.frase

    def test_mas_prioridades_piden_mas_fletes_pero_nunca_de_mas(self):
        solo = lo_mas_simple(E, B, MES, ("se_paga_sola",))
        con_viajes = lo_mas_simple(E, B, MES, ("se_paga_sola", "te_da_vida"))
        assert con_viajes.plan.fletes > solo.plan.fletes

    def test_objetivo_cumplido_no_pide_mas(self):
        r = lo_mas_simple(E, B, MES, ("se_paga_sola",), ya_dejado=1_000_000)
        assert r.plan.fletes == 0
        assert r.frase.startswith("No necesitás más fletes")

    def test_con_restriccion_horaria_dice_que_queda_afuera(self):
        e = replace(E, disponibilidad=Disponibilidad(tope_horas_mes=D(10)))
        r = lo_mas_simple(e, B, MES, ("se_conserva", "tope_horas"))
        assert r.afuera
        assert "no para trabajar más" in r.frase

    def test_con_datos_debiles_es_exploratorio(self):
        debil = replace(B, calidad={"margen": Estado.EXPLORATORIO})
        assert "exploratorio" in lo_mas_simple(E, debil, MES, ("se_paga_sola",)).frase


class TestCurva:
    def test_las_horas_extra_valen_cada_vez_menos(self):
        """Con fijos grandes el promedio por hora sigue subiendo; la hora marginal no."""
        puntos = curva(E, B, MES, 48)
        marg = [m for m in marginal_por_hora(puntos) if m is not None]
        assert marg[-1] < max(marg)
        assert mejor_por_hora(puntos) is not None

    def test_hasta_donde_conviene_segun_tu_hora_libre(self):
        puntos = curva(E, B, MES, 48)
        assert hasta_donde_conviene(puntos, None) is None
        caro = hasta_donde_conviene(puntos, 12000)
        barato = hasta_donde_conviene(puntos, 1000)
        assert caro is not None and barato is not None and caro.fletes < barato.fletes


class TestMejorPorHora:
    def test_un_mejor_negativo_no_se_marca(self):
        perdiendo = replace(B, ticket=5000)
        assert mejor_por_hora(curva(E, perdiendo, MES, 48)) is None


class TestEscenarios:
    def test_cada_palanca_mueve_lo_que_tiene_que_mover(self):
        r = {x.nombre: x for x in escenarios(E, B, MES, 20)}
        assert escenarios(E, B, MES, 20, "Tu ritmo cómodo")[0].nombre == "Tu ritmo cómodo"
        base = r["Como vengo"]
        assert r["4 fletes menos"].horas < base.horas
        assert r["Precio base +10 %"].deja > base.deja
        assert r["GNC +20 %"].deja < base.deja
        assert r["20 % menos km vacíos"].km < base.km
        assert r["Más clientes que vuelven"].deja > base.deja
        assert r["Fondo de mantenimiento +$50.000"].viajes_cubiertos <= base.viajes_cubiertos


class TestMonteCarlo:
    def test_determinista_y_con_roturas(self):
        a = simular_mes(E, B, MES, [40000, 45000, 50000], [30, 40, 60])
        b = simular_mes(E, B, MES, [40000, 45000, 50000], [30, 40, 60])
        assert a == b
        assert a.p10 < a.p50 < a.p90
        assert 0 < a.con_rotura < 0.1

    def test_las_roturas_bajan_la_cola(self):
        sin = simular_mes(E, B, MES, [45000], [40], prob_rotura_mes=0.0)
        con = simular_mes(E, B, MES, [45000], [40], prob_rotura_mes=0.2)
        assert con.p10 < sin.p10

    def test_si_los_insumos_son_debiles_todo_es_exploratorio(self):
        debil = replace(B, calidad={"margen": Estado.EXPLORATORIO, "horas": Estado.EMERGENTE})
        s = simular_mes(E, debil, MES, [45000], [40])
        assert s.exploratoria
        assert ("roturas", "escenario (supuesto)") in s.entradas


class TestPantalla:
    def test_que_pasa_si(self, tmp_path):
        from datetime import datetime

        from fastapi.testclient import TestClient

        from estiba.storage.db import connect
        from estiba.web.app import create_app

        ruta = tmp_path / "plan.db"
        connect(ruta).close()
        with TestClient(create_app(ruta, ahora=lambda: datetime(2026, 9, 29, 18))) as c:
            html = c.get("/numeros/plan").text
        assert "¿Cuál es la forma más simple de cumplir este mes?" in html
        assert "Precio base +10 %" in html
        assert "exploración, no pronóstico" in html
        assert "Escenario exploratorio" in html
