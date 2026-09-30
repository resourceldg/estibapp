"""Fase 2: ¿este flete me conviene y cuanto cobro?

Estructura de referencia: cuota $420.000 + $143.000 de fijos entre 104 h
deseadas (5.413/h), sin fondos ni ganancia libre salvo que el test los pida.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal

import pytest

from estiba.adapters.fixtures import FixtureMessageSource
from estiba.bandeja.servicio import sincronizar
from estiba.conversation.store import ConversationStore
from estiba.economia.config import PERSONAL, Disponibilidad, Economia, Fondo
from estiba.economia.cotizar import aplicar, fletes_por_dia
from estiba.economia.modelo import Tipo
from estiba.economia.pedido import Capa, Cliente, Pedido, Veredicto, cac_de, evaluar
from estiba.finanzas.costos import Costos
from estiba.finanzas.precio import PrecioPorObjetivos
from estiba.jobs.engine import JobEngine
from estiba.jobs.pricing import Presupuesto
from estiba.storage.db import connect
from estiba.storage.repositories import EstimacionRepository

D = Decimal
SEP = date(2026, 9, 29)
TODOS = frozenset(Costos().a_dict())
VIAJES = (Fondo("viajes", "Viajes", PERSONAL, D(150000)),)


def _e(**cambios) -> Economia:
    e = Economia(costos=Costos(credito=D(420000), ganancia_objetivo=D(0)), precio_base=D(40000),
                 declarados=TODOS, fondos=VIAJES)
    return replace(e, **cambios)


def _pedido(km=40, ida=15, carga=10, vuelta=15, **kw) -> Pedido:
    return Pedido(km=D(km), km_tipo=Tipo.CALCULADO,
                  tramos={"ida": D(ida), "carga": D(carga), "vuelta": D(vuelta)}, **kw)


NUEVO_META = Cliente(por_publicidad=True)
NUEVO_SOLO = Cliente()
VUELVE = Cliente(fletes_previos=3)


class TestCAC:
    def test_solo_lo_paga_el_cliente_nuevo_que_vino_por_publicidad(self):
        e = _e()
        assert cac_de(e, NUEVO_META).monto == e.costos.publicidad_por_flete > 0
        assert cac_de(e, NUEVO_SOLO).monto == 0
        assert cac_de(e, VUELVE).monto == 0

    def test_sin_saber_el_cierre_el_cac_falta_no_es_cero(self):
        e = _e(costos=Costos(credito=D(420000), cierre=D(0)))
        assert cac_de(e, NUEVO_META).tipo is Tipo.FALTA

    def test_el_mismo_flete_le_conviene_mas_con_un_cliente_que_vuelve(self):
        e = _e()
        nuevo = evaluar(e, SEP, _pedido(), NUEVO_META)
        viejo = evaluar(e, SEP, _pedido(), VUELVE)
        assert viejo.margen - nuevo.margen == e.costos.publicidad_por_flete
        assert "no carga publicidad" in " ".join(viejo.razones)


class TestKmVacios:
    def test_ida_y_vuelta_sin_carga(self):
        ev = evaluar(_e(), SEP, _pedido(km=40, ida=15, carga=10, vuelta=15), VUELVE)
        assert ev.km_vacios == D(30)
        assert ev.deadhead == D("0.75")
        assert "30 sin carga (75%)" in " ".join(ev.razones)

    def test_sin_mapa_se_dice_que_es_el_promedio(self):
        p = Pedido(km=D(64), km_tipo=Tipo.SUPUESTO, tramos=None)
        ev = evaluar(_e(), SEP, p, VUELVE)
        assert ev.km_vacios is None
        assert "es tu promedio" in " ".join(ev.razones)
        assert ev.tipo is Tipo.ESTIMADO


class TestIncertidumbre:
    def test_si_la_distancia_estimada_cambia_el_veredicto_pide_decision(self):
        """Cerca del limite, un km supuesto no alcanza para una recomendacion fuerte."""
        from estiba.economia.estructura import rinde_base
        km_max = rinde_base(_e(), SEP).km_max
        p = Pedido(km=km_max.quantize(D(1)), km_tipo=Tipo.SUPUESTO, tramos=None)
        ev = evaluar(_e(), SEP, p, VUELVE)
        assert ev.veredicto is Veredicto.DECISION
        assert "confirmá las direcciones" in " ".join(ev.razones)

    def test_lejos_del_limite_la_estimacion_alcanza(self):
        p = Pedido(km=D(15), km_tipo=Tipo.SUPUESTO, tramos=None)
        assert evaluar(_e(), SEP, p, VUELVE).veredicto is Veredicto.CONVIENE


class TestVeredicto:
    def test_corto_y_recurrente_se_cobra_a_precio_base(self):
        ev = evaluar(_e(), SEP, _pedido(km=20, ida=8, carga=4, vuelta=8), VUELVE)
        assert ev.veredicto is Veredicto.CONVIENE and ev.capa is Capa.AUTOMATICO
        assert ev.titular == "Este flete puede cobrarse a precio base."

    def test_a_precio_base_no_alcanza_y_dice_desde_cuanto(self):
        ev = evaluar(_e(), SEP, _pedido(km=110, ida=50, carga=10, vuelta=50), NUEVO_META)
        assert ev.veredicto in (Veredicto.AJUSTE, Veredicto.NO_CONVIENE)
        assert ev.recomendado > D(40000)
        assert f"${int(ev.recomendado):,}".replace(",", ".") in ev.titular

    def test_un_precio_hablado_bajo_el_minimo_no_conviene(self):
        ev = evaluar(_e(), SEP, _pedido(km=80, precio_hablado=D(15000)), NUEVO_META)
        assert ev.veredicto is Veredicto.NO_CONVIENE
        assert ev.capa is Capa.ATENCION
        assert ev.titular.startswith("A $15.000 no conviene")

    def test_entre_el_minimo_y_el_recomendado_es_con_ajuste(self):
        e = _e()
        base = evaluar(e, SEP, _pedido(km=80, ida=35, carga=10, vuelta=35), VUELVE)
        medio = (base.minimo + base.recomendado) / 2
        ev = evaluar(e, SEP, _pedido(km=80, ida=35, carga=10, vuelta=35, precio_hablado=medio), VUELVE)
        assert ev.veredicto is Veredicto.AJUSTE
        assert "entra en tus parámetros" in ev.razones[0]

    def test_una_jornada_larga_pide_decision(self):
        ev = evaluar(_e(), SEP, _pedido(km=20, horas_carga_extra=D(4)), VUELVE)
        assert ev.veredicto is Veredicto.DECISION and ev.capa is Capa.DECISION
        assert "más de media jornada" in " ".join(ev.razones)

    def test_un_dia_que_ya_tiene_el_ritmo_comodo_pide_decision(self):
        ev = evaluar(_e(), SEP, _pedido(km=20, ida=8, carga=4, vuelta=8, fletes_ese_dia=2), VUELVE)
        assert ev.veredicto is Veredicto.DECISION
        assert "lo vuelve un día pico" in " ".join(ev.razones)

    def test_el_maximo_diario_respeta_la_disponibilidad(self):
        e = _e(disponibilidad=Disponibilidad(fletes_dia_comodo=D(3), fletes_dia_pico=D(4)))
        ev = evaluar(e, SEP, _pedido(km=20, ida=8, carga=4, vuelta=8, fletes_ese_dia=2), VUELVE)
        assert ev.veredicto is Veredicto.CONVIENE

    def test_objetivo_cumplido_no_empuja(self):
        ev = evaluar(_e(), SEP, _pedido(km=20, ida=8, carga=4, vuelta=8), VUELVE, objetivo_cumplido=True)
        assert "no lo necesitás" in " ".join(ev.razones)

    def test_margen_por_hora(self):
        ev = evaluar(_e(), SEP, _pedido(), VUELVE)
        assert ev.margen_hora == ev.margen / ev.horas
        assert ev.margen == D(40000) - D(40) * _e().costos.costo_km


class TestAplicar:
    def test_la_cotizacion_toma_el_recomendado_y_el_minimo(self):
        p = Presupuesto(total=D(33000), detalle={"km": D(40)})
        ev = evaluar(_e(), SEP, _pedido(km=110, ida=50, carga=10, vuelta=50), NUEVO_META)
        aplicar(p, ev)
        assert p.total == ev.recomendado and p.detalle["minimo"] == ev.minimo
        nombres = [n for n, _ in p.detalle["partes"]]
        assert nombres[0] == "Camioneta" and "Tu trabajo" in nombres and "Publicidad" in nombres

    def test_a_precio_base_el_desglose_suma_el_base(self):
        p = Presupuesto(total=D(20000), detalle={"km": D(20)})
        ev = evaluar(_e(), SEP, _pedido(km=20, ida=8, carga=4, vuelta=8), VUELVE)
        aplicar(p, ev)
        assert p.total == D(40000)
        assert sum(m for _, m in p.desglose) == D(40000)


class TestAgenda:
    def test_cuenta_otros_fletes_activos_del_dia(self):
        from estiba.agenda.evento import Evento
        ev = lambda j, d: Evento(j, j, d, False, "t", "d")
        eventos = [(ev("a", date(2026, 10, 2)), False), (ev("b", date(2026, 10, 2)), True),
                   (ev("c", datetime(2026, 10, 2, 9)), False), (ev("yo", date(2026, 10, 2)), False)]
        assert fletes_por_dia(eventos, date(2026, 10, 2), "yo") == 2
        assert fletes_por_dia(eventos, None, "yo") == 0


class TestEnSincronizar:
    def test_cada_pedido_sale_evaluado_y_guardado(self):
        conn = connect(":memory:")
        ConversationStore(conn).ingest(list(FixtureMessageSource().poll()))
        r = sincronizar(conn, JobEngine(pricing=PrecioPorObjetivos(None)), datetime(2026, 9, 18, 12))
        assert r.evaluaciones
        job_id, ev = next(iter(r.evaluaciones.items()))
        pres = r.fletes[job_id][0].presupuesto
        assert pres.total == (ev.recomendado or pres.total)
        assert EstimacionRepository(conn).ultima(job_id, "evaluacion") is not None

    def test_el_cliente_que_vuelve_se_reconoce_en_su_segundo_flete(self):
        conn = connect(":memory:")
        ConversationStore(conn).ingest(list(FixtureMessageSource().poll()))
        r = sincronizar(conn, JobEngine(pricing=PrecioPorObjetivos(None)), datetime(2026, 9, 18, 12))
        segundos = [ev for j, ev in r.evaluaciones.items() if not j.endswith(":job")]
        assert all(ev.cliente.recurrente or ev.cliente.fletes_previos == 0 for ev in segundos)
