"""Fase 7: inteligencia historica, que solo se enciende cuando la historia alcanza."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from estiba.economia.historia import (
    costo_oportunidad_disponible,
    encadenables,
    jornadas,
    ltv,
    por_canal,
    por_segmento,
    segmento,
)
from estiba.economia.mes import FleteDelMes
from estiba.economia.suficiencia import Estado

D = Decimal
HOY = date(2026, 9, 30)
USO = D(162)


def _f(i, dia, cobrado=40000, km=40, horas="2.5", medido=True):
    return FleteDelMes(f"j{i}", dia, D(cobrado), D(km), D(28), D(horas), medido=medido)


class TestSegmentos:
    def test_reglas(self):
        assert segmento(D(30), "Alberti 2350", "Catamarca 1150", ["heladera"]) == "Urbano corto"
        assert segmento(D(45), "Alberti 2350", "Colón 100", None) == "Urbano medio"
        assert segmento(D(80), "x", "y", None) == "Urbano largo"
        assert segmento(D(150), "x", "Balcarce", None) == "Interurbano"
        assert segmento(D(90), "x", "Miramar centro", None) == "Miramar"
        assert segmento(D(20), "Santa Clara del Mar", "y", None) == "Santa Clara"
        assert segmento(D(30), "x", "y", ["mudanza de 2 ambientes"]) == "Mudanza"

    def test_un_segmento_con_pocos_datos_no_concluye(self):
        fletes = [_f(i, HOY - timedelta(days=i)) for i in range(20)] + [_f(99, HOY, km=90)]
        seg = {f.job_id: ("Miramar" if f.job_id == "j99" else "Urbano corto") for f in fletes}
        grupos = {g.nombre: g for g in por_segmento(fletes, seg, USO, {f.job_id: f.job_id for f in fletes}, HOY)}
        assert grupos["Miramar"].margen_hora.estado is Estado.EXPLORATORIO
        assert grupos["Urbano corto"].margen_hora.estado.value > Estado.EXPLORATORIO.value


class TestCanalesYLTV:
    def test_un_cliente_que_volvio_cuatro_veces_vale_mas_que_cuatro_nuevos(self):
        fletes = [_f(i, HOY - timedelta(days=i * 7)) for i in range(8)]
        cliente = {f"j{i}": ("fiel" if i < 4 else f"nuevo{i}") for i in range(8)}
        cac = {"fiel": D(20000), **{f"nuevo{i}": D(20000) for i in range(4, 8)}}
        valores = {v.cliente: v for v in ltv(fletes, cliente, cac, USO)}
        fiel = valores["fiel"]
        nuevos = sum((valores[f"nuevo{i}"].ltv for i in range(4, 8)), D(0))
        assert fiel.ltv > nuevos
        assert fiel.ltv_cac > 1

    def test_rentabilidad_por_canal(self):
        fletes = [_f(i, HOY - timedelta(days=i), cobrado=40000 if i % 2 else 30000) for i in range(10)]
        canal = {f.job_id: ("meta" if i % 2 == 0 else "recurrente") for i, f in enumerate(fletes)}
        grupos = {g.nombre: g for g in por_canal(fletes, canal, USO, {}, HOY)}
        assert grupos["recurrente"].margen.valor > grupos["meta"].margen.valor


class TestRitmo:
    def test_sin_historia_no_hay_conclusion(self):
        fletes = [_f(0, HOY), _f(1, HOY), _f(2, HOY - timedelta(days=1))]
        _, conclusion = jornadas(fletes, USO, {}, HOY)
        assert conclusion is None

    def test_con_historia_el_tercero_del_dia_rinde_menos(self):
        fletes, i = [], 0
        for d in range(0, 60, 2):          # 30 dias de 2 fletes que rinden bien
            for _ in range(2):
                fletes.append(_f(i, HOY - timedelta(days=d)))
                i += 1
        for d in range(1, 61, 2):          # 30 dias de 3 fletes, el dia rinde peor por hora
            for _ in range(3):
                fletes.append(_f(i, HOY - timedelta(days=d), horas="3.5"))
                i += 1
        cliente = {f.job_id: f.job_id for f in fletes}
        _, conclusion = jornadas(fletes, USO, cliente, HOY)
        assert conclusion == "Tus jornadas de dos fletes rinden más por hora que las de tres."


class TestOportunidadYEncadenar:
    def test_el_costo_de_oportunidad_espera_ocho_semanas(self):
        ok, porque = costo_oportunidad_disponible([_f(0, HOY - timedelta(days=20))], HOY)
        assert not ok and "8 semanas" in porque
        assert costo_oportunidad_disponible([_f(0, HOY - timedelta(days=70))], HOY)[0]

    def test_dos_fletes_cercanos_el_mismo_dia_se_encadenan(self):
        dia = date(2026, 10, 2)
        a = ("a", dia, {"ida": D(20), "carga": D(5), "vuelta": D(22)},
             {"origen": (-38.00, -57.55), "destino": (-38.01, -57.56)})
        b = ("b", dia, {"ida": D(21), "carga": D(6), "vuelta": D(20)},
             {"origen": (-38.012, -57.562), "destino": (-38.03, -57.58)})
        lejos = ("c", dia + timedelta(days=1), {"ida": D(20), "carga": D(5), "vuelta": D(20)},
                 {"origen": (-38.0, -57.5), "destino": (-38.0, -57.5)})
        (x,) = encadenables([a, b, lejos], 1.3)
        assert {x.primero, x.segundo} == {"a", "b"} and x.ahorro_km > 30


class TestPantalla:
    def test_la_historia_se_muestra_con_su_suficiencia(self, tmp_path):
        from datetime import datetime

        from fastapi.testclient import TestClient

        from estiba.storage.db import connect
        from estiba.web.app import create_app

        ruta = tmp_path / "hist.db"
        connect(ruta).close()
        with TestClient(create_app(ruta, ahora=lambda: datetime(2026, 9, 29, 18))) as c:
            html = c.get("/numeros/validacion").text
        assert "Lo que se aprende de tu historia" in html
        assert "Todavía no hay jornadas suficientes" in html
        assert "Todavía no: no hay fletes todavía." in html
