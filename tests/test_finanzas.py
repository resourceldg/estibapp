"""Plata y objetivos: el costo real de un flete, que cobrar y cuanto vender.

Los numeros de los tests son los de la primera campaña: cinco fletes (35,
35, 40, 20 y 45 mil), $53.608 en Meta entre el 16 y el 29 de septiembre,
GNC a $8.700 cada 100 km y 64 km por flete.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from estiba.core.models import Conversation, JobState, Message, MessageKind
from estiba.finanzas.costos import (
    Costos,
    FleteHecho,
    GastoPublicidad,
    balance,
    plan_a_capacidad,
    plan_con_precio,
    precio_minimo,
    ritmo_del_mes,
)
from estiba.jobs.engine import REALIZADO, Accion, JobEngine
from estiba.storage.db import connect
from estiba.storage.repositories import GastoRepository, SettingsRepository
from estiba.web import presentacion as P
from estiba.web.app import create_app

D = Decimal
SEPTIEMBRE = [
    FleteHecho("agus", date(2026, 9, 19), D(20000)),
    FleteHecho("alex1", date(2026, 9, 21), D(35000)),
    FleteHecho("alex2", date(2026, 9, 22), D(35000)),
    FleteHecho("dami", date(2026, 9, 22), D(45000)),
    FleteHecho("alex3", date(2026, 9, 25), D(40000)),
]
META = GastoPublicidad(date(2026, 9, 16), date(2026, 9, 29), D("53608.62"))


class TestCostos:
    def test_el_costo_por_km_suma_el_gnc_y_lo_que_se_gasta_andando(self):
        c = Costos()
        assert c.costo_km == D(162)                  # 87 de GNC + 20 + 15 + 40
        assert c.camioneta_por_flete == D(10368)     # 64 km

    def test_la_publicidad_por_flete_cuenta_las_consultas_que_no_cierran(self):
        """Cada flete paga tambien las cinco conversaciones que no se hicieron."""
        c = Costos(costo_por_consulta=D(1000), cierre=D(20), impuestos_publicidad=D(0))
        assert c.publicidad_por_flete == D(5000)

    def test_sin_saber_cuanto_cierra_no_hay_costo_de_publicidad(self):
        """Cero por ciento no es un costo infinito: es que falta el dato."""
        assert Costos(cierre=D(0)).publicidad_por_flete is None

    def test_lo_guardado_manda_y_lo_roto_se_ignora(self):
        c = Costos.desde_dict({"seguro": "90000", "patente": "no se", "inventado": "1"})
        assert c.seguro == D(90000) and c.patente == Costos().patente


class TestBalance:
    def test_septiembre_con_todos_los_costos(self):
        """La cuenta de la charla, hecha por Estiba y no a mano."""
        c = Costos()
        b = balance(c, SEPTIEMBRE, [META], date(2026, 9, 16), date(2026, 9, 29))
        assert b.fletes == 5 and b.ingresos == D(175000)
        assert b.camioneta == D(51840)                         # 320 km
        assert b.publicidad == D("53608.62") * D("1.25")
        assert b.fijos == D(143000) * 14 / 30
        assert b.ticket == D(35000)

    def test_un_periodo_que_corta_el_gasto_cuenta_solo_sus_dias(self):
        """Del 16 al 29 son 14 dias: una semana es la mitad del gasto."""
        c = Costos(impuestos_publicidad=D(0))
        b = balance(c, SEPTIEMBRE, [META], date(2026, 9, 23), date(2026, 9, 29))
        assert b.publicidad == D("53608.62") / 2
        assert b.fletes == 1                                   # los bultos de Alex

    def test_un_flete_sin_monto_se_cuenta_pero_no_suma(self):
        c = Costos()
        b = balance(c, [*SEPTIEMBRE, FleteHecho("x", date(2026, 9, 26), None)], [],
                    date(2026, 9, 1), date(2026, 9, 30))
        assert b.fletes == 6 and b.sin_monto == 1
        assert b.ingresos == D(175000) and b.ticket == D(35000)

    def test_desde_siempre_arranca_con_lo_primero_que_paso(self):
        """Sin fecha de inicio, los fijos no se cobran desde el año cero."""
        b = balance(Costos(), SEPTIEMBRE, [META], None, date(2026, 9, 29))
        assert b.dias == 14


class TestQueCobrar:
    def test_el_minimo_baja_con_el_volumen(self):
        c = Costos()
        assert precio_minimo(c, 11) > precio_minimo(c, 30) > precio_minimo(c, 48)

    def test_a_su_ritmo_de_hoy_el_flete_de_35_mil_no_cubre(self):
        """Once fletes por mes con todo contado: el minimo pasa los $35.000."""
        assert precio_minimo(Costos(), 11) > D(35000)

    def test_la_tarifa_es_base_mas_kilometros_redondeada(self):
        assert Costos().tarifa(D(64)) == D(59000)              # 30.000 + 64 × 450


class TestObjetivo:
    def test_a_pleno_dice_cuanto_tiene_que_valer_cada_flete(self):
        p = plan_a_capacidad(Costos())
        assert p.fletes == 48 and p.entra
        assert D(65000) < p.precio < D(72000)
        assert p.consultas == 283                              # 48 / 17 %

    def test_cobrando_35_mil_no_entra_en_un_mes(self):
        """El hallazgo de la charla: con este precio no se llega con volumen."""
        p = plan_con_precio(Costos(), D(35000))
        assert p.fletes > 150 and not p.entra

    def test_si_cada_flete_pierde_no_hay_plan(self):
        assert plan_con_precio(Costos(), D(20000)) is None

    def test_el_presupuesto_diario_es_sin_impuestos(self):
        """Es lo que se escribe en Meta, que cobra los impuestos aparte."""
        p = plan_a_capacidad(Costos())
        assert p.publicidad_dia == Costos().costo_por_consulta * p.consultas / 30
        assert p.publicidad_mes == Costos().costo_por_consulta * p.consultas * D("1.25")

    def test_el_ritmo_proyecta_el_mes_entero(self):
        c = Costos(ganancia_objetivo=D(300000))
        b = balance(c, SEPTIEMBRE, [], date(2026, 9, 1), date(2026, 9, 15))
        r = ritmo_del_mes(c, b, date(2026, 9, 15))
        assert r.esperado_a_hoy == D(150000)
        assert r.proyectado == b.ganancia * 2


# --------------------------------------------------------------------------
# El flete que se hizo aunque el chat se callo
# --------------------------------------------------------------------------


def _callado(dias: int) -> tuple[Conversation, datetime]:
    """Cotizado y despues silencio: lo que queda de un flete arreglado por telefono."""
    inicio = datetime(2026, 9, 20, 21)
    conv = Conversation("x", [
        Message("x:0", "x", True, inicio, MessageKind.TEXT,
                text="llevo dos alacenas de Hipolito Yrigoyen 3751 a Catamarca 1185"),
        Message("x:1", "x", False, inicio + timedelta(minutes=5), MessageKind.TEXT,
                text="sin ayudante esta 35 mil el flete"),
    ])
    return conv, inicio + timedelta(days=dias)


class TestPerdidoPorSilencio:
    def test_el_silencio_lo_da_por_perdido_y_lo_dice(self):
        conv, ahora = _callado(9)
        ev = JobEngine().evaluar(conv, ahora)
        assert ev.job.state is JobState.CANCELLED and ev.perdido_por_silencio

    def test_marcarlo_realizado_pisa_el_silencio(self):
        """El flete de Alex del 21/09: se hizo, y el chat no lo dice."""
        conv, ahora = _callado(9)
        ev = JobEngine().evaluar(conv, ahora, [Accion(ahora, REALIZADO, monto=D(35000))])
        assert ev.job.state is JobState.DONE and not ev.perdido_por_silencio

    def test_perdido_por_silencio_se_ofrece_cerrar_y_no_reabrir(self):
        """Reabrir no tenia nada que deshacer: el boton no hacia nada."""
        conv, ahora = _callado(9)
        ev = JobEngine().evaluar(conv, ahora)
        assert P.puede_realizarse(ev)

    def test_un_perdido_que_dijo_alguien_no_se_marca_realizado(self):
        conv, ahora = _callado(1)
        conv.messages.append(Message("x:2", "x", True, ahora, MessageKind.TEXT,
                                     text="no gracias, ya consegui otro"))
        ev = JobEngine().evaluar(conv, ahora + timedelta(minutes=1))
        assert ev.job.state is JobState.CANCELLED
        assert not ev.perdido_por_silencio and not P.puede_realizarse(ev)


# --------------------------------------------------------------------------
# La pestaña
# --------------------------------------------------------------------------


@pytest.fixture
def web(tmp_path):
    ruta = tmp_path / "plata.db"
    connect(ruta).close()
    with TestClient(create_app(ruta, ahora=lambda: datetime(2026, 9, 29, 18)),
                    follow_redirects=False) as c:
        yield c, ruta


class TestPestana:
    def test_sin_datos_arranca_con_la_estimacion_y_lo_avisa(self, web):
        c, _ = web
        html = c.get("/numeros/objetivos").text
        assert "Costos y metas" in html
        assert 'value="60.000"' in html                        # el seguro por defecto
        assert "No cargaste gasto en publicidad" in html

    def test_guardar_los_costos_recalcula(self, web):
        c, ruta = web
        r = c.post("/numeros/objetivos", data={"seguro": "90.000", "aceite_km": "25", "cierre": "12,5"})
        assert r.status_code == 303
        conn = connect(ruta)
        guardado = Costos.desde_dict(SettingsRepository(conn).json("costos"))
        conn.close()
        assert guardado.seguro == D(90000)
        assert guardado.aceite_km == D(25)                     # pesos, no miles
        assert guardado.cierre == D("12.5")
        assert guardado.monotributo == Costos().monotributo    # lo que no vino, queda
        assert "$173.000 por mes" in c.get("/numeros/objetivos").text

    def test_un_campo_ilegible_no_pisa_lo_que_habia(self, web):
        c, ruta = web
        c.post("/numeros/objetivos", data={"seguro": "mucho"})
        conn = connect(ruta)
        assert Costos.desde_dict(SettingsRepository(conn).json("costos")).seguro == D(60000)
        conn.close()

    def test_cargar_y_borrar_un_gasto(self, web):
        c, ruta = web
        r = c.post("/numeros/objetivos/gasto",
                   data={"desde": "2026-09-16", "hasta": "2026-09-29", "monto": "53.608,62"})
        assert r.status_code == 303
        conn = connect(ruta)
        (g,) = GastoRepository(conn).todos()
        conn.close()
        assert D(g["spend"]) == D("53608.62")
        assert "16/09 al 29/09/2026" in c.get("/numeros/objetivos").text
        assert c.post(f"/numeros/objetivos/gasto/{g['id']}/borrar").status_code == 303
        assert c.post(f"/numeros/objetivos/gasto/{g['id']}/borrar").status_code == 404

    def test_un_gasto_al_reves_no_entra(self, web):
        c, _ = web
        r = c.post("/numeros/objetivos/gasto",
                   data={"desde": "2026-09-29", "hasta": "2026-09-16", "monto": "1000"})
        assert r.status_code == 422

    def test_la_pestana_vive_dentro_de_numeros(self, web):
        c, _ = web
        assert 'href="/numeros/objetivos"' in c.get("/numeros").text
