"""Fase 1 del motor economico: estructura, capacidad deseada, precios y cuentas.

Los numeros de referencia: cuota de $420.000, estructura de $143.000
(seguro 60, patente 5, VTV 8, monotributo 50, celular 20), 4 dias por
semana a 2 fletes comodos de 3 h = 104 h deseadas por mes.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal

import pytest

from estiba.economia.cargar import economia
from estiba.economia.config import (
    CONSERVACION,
    PERSONAL,
    Camioneta,
    Credito,
    Disponibilidad,
    Economia,
    Fondo,
)
from estiba.economia.cuentas import Actividad, cuentas
from estiba.economia.estructura import (
    contribucion_por_hora,
    horas_de,
    horas_deseadas,
    precios,
    rinde_base,
)
from estiba.economia.modelo import Tipo, combinar
from estiba.finanzas.costos import Costos
from estiba.storage.db import connect
from estiba.storage.repositories import EconomiaRepository, SettingsRepository

D = Decimal
SEP = date(2026, 9, 15)
TODOS = frozenset(Costos().a_dict())


def _e(**cambios) -> Economia:
    base = Economia(costos=Costos(credito=D(420000), ganancia_objetivo=D(0)), precio_base=D(40000),
                    declarados=TODOS)
    return replace(base, **cambios)


class TestCapacidadDeseada:
    def test_las_horas_deseadas_son_el_ritmo_comodo(self):
        assert horas_deseadas(_e()).monto == D("104.0")

    def test_el_pico_no_se_vuelve_la_expectativa(self):
        """Poder hacer 3 por dia no sube las horas deseadas."""
        mas_pico = _e(disponibilidad=Disponibilidad(fletes_dia_pico=D(5)))
        assert horas_deseadas(mas_pico).monto == horas_deseadas(_e()).monto

    def test_un_tope_de_horas_manda(self):
        e = _e(disponibilidad=Disponibilidad(tope_horas_mes=D(60)))
        assert horas_deseadas(e).monto == D(60)

    def test_la_jornada_maxima_por_dia_limita(self):
        e = _e(disponibilidad=Disponibilidad(horas_dia=D(4)))
        assert horas_deseadas(e).monto < D(104)

    def test_la_capacidad_tecnica_no_es_la_deseada(self):
        e = _e()
        assert e.costos.fletes_por_mes_max == D(48)
        assert e.disponibilidad.fletes_mes() < D(48)

    def test_sin_dias_no_hay_contribucion_posible(self):
        e = _e(disponibilidad=Disponibilidad(dias_semana=D(0)))
        w = contribucion_por_hora(e, SEP)
        assert w.objetivo is None and w.horas.tipo is Tipo.FALTA


class TestContribucionPorHora:
    def test_la_minima_cubre_estructura_y_cuota(self):
        w = contribucion_por_hora(_e(), SEP)
        assert w.total_minima == D(143000 + 420000)
        assert w.minima == D(563000) / D("104.0")

    def test_los_fondos_y_la_ganancia_libre_solo_suben_la_objetivo(self):
        fondos = (Fondo("rep", "Reposición", CONSERVACION, D(100000)),
                  Fondo("viajes", "Viajes", PERSONAL, D(150000)))
        e = _e(fondos=fondos, costos=Costos(credito=D(420000), ganancia_objetivo=D(50000)))
        w = contribucion_por_hora(e, SEP)
        assert w.total_minima == D(563000)
        assert w.total_objetivo == D(563000 + 100000 + 150000 + 50000)

    def test_menos_horas_deseadas_exigen_mas_por_hora(self):
        pocas = _e(disponibilidad=Disponibilidad(tope_horas_mes=D(52)))
        assert contribucion_por_hora(pocas, SEP).minima == 2 * contribucion_por_hora(_e(), SEP).minima


class TestCredito:
    CREDITO = Credito(cuotas=72, primera=date(2026, 1, 1))

    def test_termina_en_la_cuota_72(self):
        assert self.CREDITO.ultima() == date(2031, 12, 1)
        assert self.CREDITO.restantes(date(2026, 9, 15)) == 63

    def test_fin_del_credito_saca_la_cuota_de_la_exigencia(self):
        e = _e(credito=self.CREDITO)
        antes = contribucion_por_hora(e, date(2031, 12, 10))
        despues = contribucion_por_hora(e, date(2032, 1, 10))
        assert antes.total_minima - despues.total_minima == D(420000)

    def test_sin_fecha_de_inicio_la_cuota_se_cuenta(self):
        """Lo conservador: no hacer desaparecer una obligacion desconocida."""
        assert Credito().vigente(date(2040, 1, 1))


class TestCuentas:
    ACT = Actividad(ingresos=D(800000), km=D(1000), publicidad_pagada=D(60000))

    def test_la_cuota_es_caja_no_costo_economico(self):
        c = cuentas(_e(), SEP, self.ACT)
        assert any(p.nombre == "Cuota del crédito" for p in c.caja)
        assert not any("Cuota" in p.nombre for p in c.economico)
        assert c.saldo_caja == c.resultado_economico - D(420000)

    def test_sin_interes_cargado_se_dice_y_no_se_inventa(self):
        c = cuentas(_e(), SEP, self.ACT)
        assert any("interés" in s for s in c.sin_contar)
        assert c.capital_amortizado is None

    def test_con_interes_solo_el_interes_es_costo_y_el_resto_es_patrimonio(self):
        e = _e(credito=Credito(interes_mes=D(100000)))
        c = cuentas(e, SEP, self.ACT)
        assert c.capital_amortizado == D(320000)
        assert cuentas(_e(), SEP, self.ACT).resultado_economico - c.resultado_economico == D(100000)

    def test_la_reserva_existe_mientras_se_paga_el_credito(self):
        """Pagar capital no deja plata para arreglar la camioneta: el fondo va igual."""
        e = _e(fondos=(Fondo("rep", "Reposición", CONSERVACION, D(80000)),))
        c = cuentas(e, SEP, self.ACT)
        assert c.aportado == D(80000)
        assert any("reposición" in p.nombre.lower() for p in c.caja)
        # El aporte es apartar plata, no un costo: el resultado economico no cambia.
        assert c.resultado_economico == cuentas(_e(), SEP, self.ACT).resultado_economico

    def test_la_depreciacion_es_opcional_y_se_marca_como_supuesto(self):
        sin = cuentas(_e(), SEP, self.ACT)
        assert any("depreciación" in s for s in sin.sin_contar)
        con = cuentas(_e(camioneta=Camioneta(depreciacion_mes=D(50000))), SEP, self.ACT)
        (dep,) = [p for p in con.economico if "Depreciación" in p.nombre]
        assert dep.tipo is Tipo.SUPUESTO and dep.monto == D(-50000)
        # Y nunca entra en la caja: no sale plata por depreciar.
        assert con.saldo_caja == sin.saldo_caja

    def test_resultado_real_no_es_la_contribucion_objetivo(self):
        """Uno dice cuanto se gano; el otro, cuanto habria que aportar."""
        e = _e(fondos=(Fondo("viajes", "Viajes", PERSONAL, D(150000)),),
               costos=Costos(credito=D(420000), ganancia_objetivo=D(200000)))
        c = cuentas(e, SEP, self.ACT)
        w = contribucion_por_hora(e, SEP)
        assert not any("Ganancia libre" in p.nombre for p in c.economico)
        assert any("Ganancia libre" in p.nombre for p in w.partes_objetivo)


class TestPrecios:
    def test_el_recomendado_nunca_baja_del_base(self):
        p = precios(_e(), SEP, D(10), horas_de(_e(), D(10)))
        assert p.recomendado == D(40000)

    def test_un_viaje_largo_pide_mas_que_el_base(self):
        e = _e(fondos=(Fondo("viajes", "Viajes", PERSONAL, D(150000)),))
        p = precios(e, SEP, D(120), horas_de(e, D(120)))
        assert p.recomendado > D(40000)
        assert p.recomendado % 1000 == 0
        assert p.minimo < p.recomendado

    def test_el_minimo_cubre_estructura_y_cuota_pero_no_los_objetivos(self):
        e = _e(fondos=(Fondo("viajes", "Viajes", PERSONAL, D(300000)),))
        sin_fondo = precios(_e(), SEP, D(120), D(5))
        con_fondo = precios(e, SEP, D(120), D(5))
        assert con_fondo.minimo == sin_fondo.minimo
        assert con_fondo.recomendado > sin_fondo.recomendado

    def test_el_cac_se_suma_cuando_se_pasa(self):
        e = _e()
        assert precios(e, SEP, D(60), D(3), cac=D(15000)).minimo > precios(e, SEP, D(60), D(3)).minimo

    def test_el_precio_base_es_mutable(self):
        assert precios(_e(precio_base=D(45000)), SEP, D(10), D(1)).recomendado == D(45000)

    def test_costos_por_defecto_hacen_una_estimacion(self):
        e = _e(declarados=frozenset())
        assert precios(e, SEP, D(60), D(3)).tipo is Tipo.ESTIMADO
        assert precios(_e(), SEP, D(60), D(3)).tipo is Tipo.CALCULADO


class TestRindeBase:
    def test_en_el_km_maximo_el_costo_iguala_al_base(self):
        e = _e()
        r = rinde_base(e, SEP)
        km = r.km_max
        w = contribucion_por_hora(e, SEP).objetivo
        costo = km * e.costos.costo_km + horas_de(e, km) * w
        assert abs(costo - e.precio_base) < D(1)

    def test_subir_el_base_estira_los_km(self):
        assert rinde_base(_e(precio_base=D(50000)), SEP).km_max > rinde_base(_e(), SEP).km_max

    def test_un_cliente_nuevo_acorta_los_km(self):
        assert rinde_base(_e(), SEP, cac=D(10000)).km_max < rinde_base(_e(), SEP).km_max

    def test_espera_cac_y_adicionales(self):
        r = rinde_base(_e(), SEP)
        assert r.adicional_hora == contribucion_por_hora(_e(), SEP).objetivo
        assert r.adicional_km > _e().costos.costo_km
        # Espera admisible = horas maximas menos las de un flete tipico.
        assert r.espera_max == max(D(0), r.horas_max - r.horas_tipico)
        assert r.km_vacios_max(D(10)) == r.km_max - D(10)

    def test_un_base_bajo_no_banca_clientes_nuevos(self):
        r = rinde_base(_e(precio_base=D(15000)), SEP)
        assert r.cac_max < 0


class TestTipos:
    def test_una_cuenta_vale_lo_que_su_insumo_mas_debil(self):
        assert combinar(Tipo.DECLARADO, Tipo.MEDIDO) is Tipo.CALCULADO
        assert combinar(Tipo.DECLARADO, Tipo.SUPUESTO) is Tipo.ESTIMADO
        assert combinar(Tipo.CALCULADO, Tipo.FALTA) is Tipo.FALTA


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "eco.db")
    yield c
    c.close()


class TestPersistencia:
    def test_precio_base_inicial_y_su_historial(self, conn):
        repo = EconomiaRepository(conn)
        assert repo.precio_base(SEP) == D(40000)
        repo.cambiar_precio_base(D(45000), date(2026, 10, 1), datetime(2026, 9, 29, 18))
        assert repo.precio_base(date(2026, 9, 30)) == D(40000)
        assert repo.precio_base(date(2026, 10, 1)) == D(45000)
        ultimo = repo.historial_precio_base()[0]
        assert D(ultimo["monto"]) == D(45000) and D(ultimo["anterior"]) == D(40000)

    def test_la_config_se_versiona_y_se_puede_deshacer(self, conn):
        repo = EconomiaRepository(conn)
        repo.guardar("disponibilidad", {"dias_semana": "4"}, datetime(2026, 9, 29))
        repo.guardar("disponibilidad", {"dias_semana": "3"}, datetime(2026, 9, 30))
        assert economia(conn, SEP).disponibilidad.dias_semana == D(3)
        assert repo.deshacer("disponibilidad")
        assert economia(conn, SEP).disponibilidad.dias_semana == D(4)

    def test_los_costos_salen_de_la_misma_fuente_de_siempre(self, conn):
        SettingsRepository(conn).guardar_json("costos", {"credito": "420000"})
        e = economia(conn, SEP)
        assert e.costos.credito == D(420000)
        assert e.declarados == {"credito"}
