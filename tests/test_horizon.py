"""Vinculo con Horizon: Estiba manda sus fletes resumidos a la nube.

Horizon se simula con httpx.MockTransport con la forma real de /api/estiba.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace

import httpx

from estiba.adapters.horizon import ConfigHorizon, HorizonClient
from estiba.horizon import HUELLAS, ULTIMO, flete_para_horizon, mes_para_horizon, sincronizar
from estiba.storage.db import connect
from estiba.storage.repositories import SettingsRepository

AHORA = datetime(2026, 11, 16, 19, 0)


def flete(job_id="549223@c.us:job1", **cambios):
    base = dict(job_id=job_id, numero=12, chat_id="549223@c.us", nombre="Marcela", telefono="+54 9 223 512-3344",
                estado="presupuestado", etapa="cotizado", origen="Punta Mogotes", destino="Batán",
                fecha_iso="2026-11-20", hora="10:00", cosas="heladera y sillón", km="18 km",
                cotizado_crudo="45000", ultimo_at="2026-11-16T17:39:53", ultimo_del_cliente=True,
                ultimo_texto="¿sigue en pie lo del sábado?")
    base.update(cambios)
    return SimpleNamespace(**base)


class HorizonFalso:
    def __init__(self, status=200, fallar_ids=()):
        self.pedidos: list[dict] = []
        self.headers: list[str] = []
        self.status = status
        self.fallar_ids = set(fallar_ids)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        cuerpo = json.loads(request.content)
        self.pedidos.append(cuerpo)
        self.headers.append(request.headers.get("authorization"))
        if self.status != 200:
            return httpx.Response(self.status, json={"error": "no"})
        errores = [{"id": f["id"], "error": "falló"} for f in cuerpo["fletes"] if f["id"] in self.fallar_ids]
        ok = len(cuerpo["fletes"]) - len(errores)
        return httpx.Response(200, json={"ok": True, "creados": ok, "actualizados": 0, "precios": ok, "errores": errores})


def cliente(falso):
    return HorizonClient(ConfigHorizon("https://horizon.test", "t" * 40), transport=httpx.MockTransport(falso))


class TestLoQueSeManda:
    def test_un_flete_va_resumido_sin_la_conversacion(self):
        f = flete_para_horizon(flete(), {"sugerido": "48000", "cotizado": "45000", "cobrado": None})
        assert f["etapa"] == "cotizado" and f["origen"] == "Punta Mogotes"
        assert f["km"] == 18.0 and f["sugerido"] == 48000.0 and f["cotizado"] == 45000.0 and f["cobrado"] is None
        assert "ultimo_texto" not in f and "mensajes" not in f

    def test_las_fechas_viajan_con_zona_para_no_correrse_un_dia(self):
        f = flete_para_horizon(flete())
        assert f["fecha_iso"].startswith("2026-11-20T10:00") and f["fecha_iso"][-6] in "+-"
        sin_hora = flete_para_horizon(flete(hora=None))
        assert sin_hora["fecha_iso"].startswith("2026-11-20T12:00")

    def test_sin_montos_usa_lo_cotizado_de_la_fila(self):
        assert flete_para_horizon(flete())["cotizado"] == 45000.0

    def test_el_resumen_del_mes(self):
        m = SimpleNamespace(desde=date(2026, 11, 1), hasta=date(2026, 11, 16), ingresos=Decimal("230000"),
                            publicidad=Decimal("53608.62"), disponible=Decimal("150000"), km=Decimal("410"),
                            horas=Decimal("22.5"), fletes=[1, 2, 3], nivel=2, autonomia=0.7311,
                            falta_para_pagarse=Decimal("180000"), fletes_que_faltan=4, margen_tipico=Decimal("38000"),
                            objetivo_cumplido=False, ticket=Decimal("76666.67"), capacidad_restante=Decimal("9"),
                            obligacion=Decimal("563000"), avisos=["a", "b", "c", "d"])
        r = mes_para_horizon(m)
        assert r["fletes"] == 3 and r["nivel_texto"] == "Paga su estructura" and r["autonomia"] == 0.731
        assert r["publicidad"] == 53608.62 and len(r["avisos"]) == 3


class TestSincronizar:
    def test_la_primera_vez_manda_todo_con_el_token(self, tmp_path):
        conn, falso = connect(tmp_path / "e.db"), HorizonFalso()
        fletes = [flete_para_horizon(flete(f"c{i}:job1")) for i in range(3)]
        r = sincronizar(conn, cliente(falso), fletes, {"fletes": 3}, AHORA)
        assert r["ok"] and r["enviados"] == 3
        assert falso.headers == ["Bearer " + "t" * 40]
        assert falso.pedidos[0]["mes"] == {"fletes": 3}

    def test_despues_solo_viaja_lo_que_cambio(self, tmp_path):
        conn, falso = connect(tmp_path / "e.db"), HorizonFalso()
        fletes = [flete_para_horizon(flete(f"c{i}:job1")) for i in range(3)]
        sincronizar(conn, cliente(falso), fletes, None, AHORA)
        sincronizar(conn, cliente(falso), fletes, {"fletes": 3}, AHORA)
        assert falso.pedidos[1]["fletes"] == [] and "mes" in falso.pedidos[1]
        fletes[1] = flete_para_horizon(flete("c1:job1", etapa="vendido"), {"cobrado": "45000"})
        sincronizar(conn, cliente(falso), fletes, None, AHORA)
        assert [f["id"] for f in falso.pedidos[2]["fletes"]] == ["c1:job1"]

    def test_lo_que_horizon_no_guardo_se_vuelve_a_mandar(self, tmp_path):
        conn = connect(tmp_path / "e.db")
        fletes = [flete_para_horizon(flete(f"c{i}:job1")) for i in range(2)]
        r = sincronizar(conn, cliente(HorizonFalso(fallar_ids={"c0:job1"})), fletes, None, AHORA)
        assert r["fallidos"] == 1 and not r["ok"]
        falso = HorizonFalso()
        sincronizar(conn, cliente(falso), fletes, None, AHORA)
        assert [f["id"] for f in falso.pedidos[0]["fletes"]] == ["c0:job1"]

    def test_muchos_fletes_van_de_a_tandas(self, tmp_path):
        conn, falso = connect(tmp_path / "e.db"), HorizonFalso()
        fletes = [flete_para_horizon(flete(f"c{i}:job1")) for i in range(90)]
        sincronizar(conn, cliente(falso), fletes, {"fletes": 90}, AHORA)
        assert [len(p["fletes"]) for p in falso.pedidos] == [40, 40, 10]
        assert ["mes" in p for p in falso.pedidos] == [False, False, True]

    def test_horizon_caido_no_rompe_estiba_y_queda_anotado(self, tmp_path):
        conn = connect(tmp_path / "e.db")
        fletes = [flete_para_horizon(flete())]
        r = sincronizar(conn, cliente(HorizonFalso(status=401)), fletes, None, AHORA)
        assert r["ok"] is False and "token" in r["error"]
        ajustes = SettingsRepository(conn)
        assert ajustes.json(ULTIMO)["ok"] is False
        assert not (ajustes.json(HUELLAS) or {})  # nada se dio por enviado

    def test_sin_configuracion_no_hay_cliente(self, monkeypatch):
        monkeypatch.delenv("ESTIBA_HORIZON_URL", raising=False)
        monkeypatch.delenv("ESTIBA_HORIZON_TOKEN", raising=False)
        assert ConfigHorizon.desde_entorno() is None


class TestCambiosDesdeHorizon:
    """El usuario cambia el estado de un flete desde Horizon; Estiba lo aplica con sus reglas."""

    def test_se_aplica_y_se_avisa(self):
        from estiba.horizon import aplicar_comandos

        hechos = []
        acks = aplicar_comandos([{"id": "c1", "tipo": "realizado", "flete": "x:job1", "datos": {"monto": 45000}}],
                                lambda tipo, job, datos: hechos.append((tipo, job, datos["monto"])))
        assert hechos == [("realizado", "x:job1", 45000)]
        assert acks == [{"id": "c1", "ok": True}]

    def test_si_la_regla_de_estiba_dice_que_no_vuelve_el_motivo(self):
        from fastapi import HTTPException

        from estiba.horizon import aplicar_comandos

        def ejecutar(tipo, job, datos):
            raise HTTPException(409, "un flete borrador no se puede marcar confirmado")

        acks = aplicar_comandos([{"id": "c1", "tipo": "confirmar", "flete": "x:job1"}], ejecutar)
        assert acks[0]["ok"] is False and "borrador" in acks[0]["error"]

    def test_un_cambio_desconocido_no_se_ejecuta(self):
        from estiba.horizon import aplicar_comandos

        llamado = []
        acks = aplicar_comandos([{"id": "c1", "tipo": "borrar-todo", "flete": "x"}], lambda *a: llamado.append(a))
        assert not llamado and acks[0]["ok"] is False

    def test_el_flete_con_pedido_de_cambio_viaja_aunque_no_haya_cambiado(self, tmp_path):
        conn, falso = connect(tmp_path / "e.db"), HorizonFalso()
        fletes = [flete_para_horizon(flete("c1:job1"))]
        sincronizar(conn, cliente(falso), fletes, None, AHORA)
        sincronizar(conn, cliente(falso), fletes, None, AHORA, acks=[{"id": "x", "ok": False, "error": "no"}],
                    forzar={"c1:job1"})
        assert [f["id"] for f in falso.pedidos[1]["fletes"]] == ["c1:job1"]
        assert falso.pedidos[1]["acks"] == [{"id": "x", "ok": False, "error": "no"}]

    def test_los_motivos_de_perdida_viajan_como_catalogo(self, tmp_path):
        conn, falso = connect(tmp_path / "e.db"), HorizonFalso()
        sincronizar(conn, cliente(falso), [], {"fletes": 0}, AHORA, catalogos={"motivos_perdida": [["precio", "Precio alto"]]})
        assert falso.pedidos[-1]["catalogos"]["motivos_perdida"] == [["precio", "Precio alto"]]
