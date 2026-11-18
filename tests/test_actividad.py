"""Reporte de actividad de WhatsApp para Horizon: contadores y estados, nunca el texto de las charlas."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import httpx

from estiba.actividad import Rafaga, armar_reporte
from estiba.adapters.horizon import ConfigHorizon, HorizonClient
from estiba.horizon import sincronizar
from estiba.storage.db import connect

AHORA = datetime(2026, 11, 18, 12, 0)
SECRETO = "dirección secreta de la tía Marta 1234"


def _msg(conn, chat, del_cliente, cuando, texto="hola"):
    n = conn.execute("SELECT count(*) FROM messages").fetchone()[0]
    conn.execute("INSERT INTO messages(id, chat_id, from_customer, sent_at, kind, text, source) VALUES(?,?,?,?,?,?,?)",
                 (f"m{n}", chat, int(del_cliente), cuando.isoformat(timespec="seconds"), "text", texto, "waha"))


def _contacto(conn, chat, nombre, primero):
    conn.execute("INSERT INTO contacts(id, chat_id, display_name, first_seen) VALUES(?,?,?,?)",
                 (chat, chat, nombre, primero.isoformat(timespec="seconds")))


def _clase(conn, chat, clase):
    conn.execute("INSERT INTO chat_clase(chat_id, clase, porque, quien, at) VALUES(?,?,?,?,?)",
                 (chat, clase, "prueba", "estiba", AHORA.isoformat()))


def flete(chat, **cambios):
    base = dict(job_id=chat + ":job", chat_id=chat, nombre=None, etapa="sin-cotizar", origen="Mogotes", destino="Batán",
                cosas="heladera", dia_semana="jue", hora="10:00", fecha_iso=None, ultimo_at=None,
                ultimo_texto="esto no tiene que viajar")
    base.update(cambios)
    return SimpleNamespace(**base)


def escenario():
    conn = connect(":memory:")
    # Silvia escribió hace 3 h y no le respondiste (lead nuevo de hoy).
    _contacto(conn, "5492231111111", "Silvia", AHORA - timedelta(hours=4))
    _clase(conn, "5492231111111", "lead")
    _msg(conn, "5492231111111", True, AHORA - timedelta(hours=4), "necesito un flete " + SECRETO)
    _msg(conn, "5492231111111", False, AHORA - timedelta(hours=3, minutes=30), "hola Silvia, ¿qué hay que llevar?")
    _msg(conn, "5492231111111", True, AHORA - timedelta(hours=3), "una heladera")
    # Marce: cotizado hace 2 días, sin respuesta del cliente.
    _contacto(conn, "5492232222222", "Marce", AHORA - timedelta(days=5))
    _clase(conn, "5492232222222", "cliente")
    _msg(conn, "5492232222222", True, AHORA - timedelta(days=3), "¿cuánto sale?")
    _msg(conn, "5492232222222", False, AHORA - timedelta(days=2), "sale 45 mil")
    # Juan: flete mañana, cotizado, sin confirmar; le escribiste hoy.
    _contacto(conn, "5492233333333", "Juan", AHORA - timedelta(days=2))
    _clase(conn, "5492233333333", "lead")
    _msg(conn, "5492233333333", False, AHORA - timedelta(hours=1), "¿te sirve mañana?")
    # La pareja (privado) y el spam: nunca entran.
    _clase(conn, "5492234444444", "privado"); _msg(conn, "5492234444444", True, AHORA - timedelta(minutes=5), "te amo")
    _clase(conn, "5492235555555", "spam"); _msg(conn, "5492235555555", True, AHORA - timedelta(minutes=5), "oferta")
    # Algo de hace 10 días no cuenta en la semana.
    _msg(conn, "5492237777777", True, AHORA - timedelta(days=10), "viejo")
    fletes = {
        "5492232222222": flete("5492232222222", etapa="cotizado"),
        "5492233333333": flete("5492233333333", etapa="cotizado", fecha_iso=(AHORA + timedelta(days=1)).date().isoformat() + "T10:00:00"),
    }
    return conn, fletes


class TestReporte:
    def test_nunca_viaja_el_texto_de_las_charlas(self):
        conn, fletes = escenario()
        r = json.dumps(armar_reporte(conn, AHORA, fletes), ensure_ascii=False)
        for texto in (SECRETO, "una heladera", "sale 45 mil", "te amo", "esto no tiene que viajar"):
            assert texto not in r

    def test_privados_y_spam_no_entran(self):
        conn, fletes = escenario()
        r = armar_reporte(conn, AHORA, fletes)
        nombres = {c["nombre"] for c in r["conversaciones"]}
        assert nombres == {"Silvia", "Marce", "Juan"}
        assert all(c["final"] not in ("4444", "5555") for c in r["conversaciones"])

    def test_contadores_de_hoy_y_de_la_semana(self):
        conn, fletes = escenario()
        r = armar_reporte(conn, AHORA, fletes)
        assert r["hoy"] == {"personas": 2, "recibidos": 2, "enviados": 2, "nuevos": 1}
        assert r["semana"]["personas"] == 3 and r["semana"]["recibidos"] == 3 and r["semana"]["enviados"] == 3

    def test_lo_que_necesita_atencion(self):
        conn, fletes = escenario()
        a = armar_reporte(conn, AHORA, fletes)["atencion"]
        assert a == {"sin_responder": 1, "cotizados_sin_respuesta": 1, "manana_sin_confirmar": 1, "para_decidir": 0}

    def test_primero_lo_que_espera_respuesta_y_el_tema_sale_del_flete(self):
        conn, fletes = escenario()
        cs = armar_reporte(conn, AHORA, fletes)["conversaciones"]
        assert cs[0]["nombre"] == "Silvia" and cs[0]["estado"] == "esperando tu respuesta" and cs[0]["espera_horas"] == 3.0
        marce = next(c for c in cs if c["nombre"] == "Marce")
        assert marce["estado"] == "cotizado, esperando al cliente" and marce["tema"].startswith("Flete Mogotes → Batán")
        assert marce["final"] == "2222" and "telefono" not in marce
        # Las horas viajan con zona (Horizon está en UTC): nada de horas locales peladas.
        assert all(c["ultimo_at"][-6] in "+-" for c in cs)
        juan = next(c for c in cs if c["nombre"] == "Juan")
        assert juan["fecha_flete"][-6] in "+-"

    def test_sin_actividad_el_reporte_sale_vacio_pero_sale(self):
        r = armar_reporte(connect(":memory:"), AHORA, {})
        assert r["hoy"]["personas"] == 0 and r["conversaciones"] == [] and r["version"] == 1


class TestRafaga:
    def test_cinco_mensajes_seguidos_mandan_uno_solo_despues_de_la_calma(self):
        r, t = Rafaga(), AHORA
        assert not r.lista(t)
        for i in range(5):
            r.marcar(t + timedelta(seconds=3 * i))
        assert not r.lista(t + timedelta(seconds=20))      # el último fue a los 12 s: falta calma
        assert r.lista(t + timedelta(seconds=27))          # 15 s de calma
        r.mandada()
        assert not r.lista(t + timedelta(seconds=40))

    def test_si_no_paran_manda_igual_al_tope(self):
        r, t = Rafaga(), AHORA
        for i in range(30):
            r.marcar(t + timedelta(seconds=5 * i))
        assert r.lista(t + timedelta(seconds=60))


class TestEnvio:
    def test_el_reporte_viaja_con_la_sincronizacion(self):
        recibidos = []

        def falso(request: httpx.Request) -> httpx.Response:
            recibidos.append(json.loads(request.content))
            return httpx.Response(200, json={"ok": True, "creados": 0, "actualizados": 0, "precios": 0, "errores": []})

        cli = HorizonClient(ConfigHorizon("https://horizon.test", "t" * 40), transport=httpx.MockTransport(falso))
        conn, fletes = escenario()
        rep = armar_reporte(conn, AHORA, fletes)
        sincronizar(conn, cli, [], None, AHORA, actividad=rep)
        assert recibidos[-1]["actividad"]["atencion"]["sin_responder"] == 1
        assert SECRETO not in json.dumps(recibidos[-1], ensure_ascii=False)
