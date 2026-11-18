"""Texto de las charlas de negocio para Horizon: nunca privadas ni spam; audios con su transcripcion; solo lo nuevo."""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import httpx

from estiba import charlas
from estiba.adapters.horizon import ConfigHorizon, HorizonClient
from estiba.horizon import MARCA_CHARLAS, sincronizar
from estiba.storage.db import connect
from estiba.storage.repositories import SettingsRepository

AHORA = datetime(2026, 11, 18, 12, 0)


class Base:
    def __init__(self):
        self.conn = connect(":memory:")
        self.n = 0
        self.reloj = AHORA - timedelta(hours=1)

    def chat(self, chat, nombre, clase):
        self.conn.execute("INSERT INTO contacts(id, chat_id, display_name, first_seen) VALUES(?,?,?,?)", (chat, chat, nombre, AHORA.isoformat()))
        if clase:
            self.conn.execute("INSERT INTO chat_clase(chat_id, clase, porque, quien, at) VALUES(?,?,?,?,?)", (chat, clase, "p", "estiba", AHORA.isoformat()))

    def msg(self, chat, del_cliente, texto, kind="text", hace=timedelta(hours=1)):
        self.n += 1
        self.reloj += timedelta(seconds=1)
        mid = f"m{self.n}"
        self.conn.execute("INSERT INTO messages(id, chat_id, from_customer, sent_at, kind, text, source, ingested_at) VALUES(?,?,?,?,?,?,?,?)",
                          (mid, chat, int(del_cliente), (AHORA - hace).isoformat(timespec="seconds"), kind, texto, "waha", self.reloj.isoformat(sep=" ", timespec="seconds")))
        return mid

    def transcribir(self, mid, texto):
        self.reloj += timedelta(seconds=1)
        self.conn.execute("INSERT INTO transcripts(message_id, text, confidence, model, duration_s, created_at) VALUES(?,?,?,?,?,?)",
                          (mid, texto, 0.9, "small", 3.0, self.reloj.isoformat(sep=" ", timespec="seconds")))


def base():
    b = Base()
    b.chat("549223111", "Silvia", "lead")
    b.chat("549223222", "Mamá", "privado")
    b.chat("549223333", "Promo", "spam")
    b.chat("549223444", "Nuevo sin clasificar", None)
    b.msg("549223111", True, "necesito llevar una heladera de Mogotes a Batán")
    b.msg("549223111", False, "dale, ¿qué día?")
    b.msg("549223222", True, "te mandé la receta")
    b.msg("549223333", True, "ofertas")
    b.msg("549223444", True, "hola")
    return b


def test_solo_charlas_de_negocio():
    b = base()
    msgs, _ = charlas.pendientes(b.conn, AHORA, None)
    assert [m["texto"] for m in msgs] == ["necesito llevar una heladera de Mogotes a Batán", "dale, ¿qué día?"]
    assert {m["chat"] for m in msgs} == {"549223111"} and msgs[0]["de"] == "cliente" and msgs[1]["de"] == "vos"
    assert msgs[0]["at"][-6] in "+-"
    assert set(charlas.fuera(b.conn)) == {"549223222", "549223333"}


def test_solo_lo_nuevo_y_el_audio_cuando_se_transcribe():
    b = base()
    _, marca = charlas.pendientes(b.conn, AHORA, None)
    audio = b.msg("549223111", True, None, kind="audio")
    msgs, marca = charlas.pendientes(b.conn, AHORA, marca)
    assert len(msgs) == 1 and msgs[0]["texto"] == "[audio sin transcribir todavía]" and not msgs[0]["transcripcion"]
    assert charlas.audios_sin_transcribir(b.conn) == 1
    assert charlas.pendientes(b.conn, AHORA, marca)[0] == []          # nada nuevo: no se repite
    b.transcribir(audio, "son dos cajas y un sillón")
    msgs, marca = charlas.pendientes(b.conn, AHORA, marca)
    assert len(msgs) == 1 and msgs[0]["id"] == audio and msgs[0]["texto"] == "son dos cajas y un sillón" and msgs[0]["transcripcion"]
    assert charlas.audios_sin_transcribir(b.conn) == 0


def test_ventana_de_60_dias():
    b = base()
    b.msg("549223111", True, "esto es de hace 3 meses", hace=timedelta(days=90))
    assert "esto es de hace 3 meses" not in [m["texto"] for m in charlas.pendientes(b.conn, AHORA, None)[0]]


def test_la_marca_avanza_solo_si_horizon_recibio():
    b = base()
    pedidos = []

    def ok(request):
        pedidos.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "creados": 0, "actualizados": 0, "precios": 0, "errores": []})

    def caido(request):
        return httpx.Response(500, json={"error": "caído"})

    msgs, marca = charlas.pendientes(b.conn, AHORA, None)
    cli = lambda t: HorizonClient(ConfigHorizon("https://horizon.test", "t" * 40), transport=httpx.MockTransport(t))
    sincronizar(b.conn, cli(caido), [], None, AHORA, charlas={"mensajes": msgs, "fuera": [], "marca": marca})
    assert SettingsRepository(b.conn).json(MARCA_CHARLAS) is None      # no llegó: se vuelve a mandar
    sincronizar(b.conn, cli(ok), [], None, AHORA, charlas={"mensajes": msgs, "fuera": charlas.fuera(b.conn), "marca": marca})
    assert SettingsRepository(b.conn).json(MARCA_CHARLAS) == marca
    cuerpo = json.dumps(pedidos[-1], ensure_ascii=False)
    assert "heladera" in cuerpo and "receta" not in cuerpo and "ofertas" not in cuerpo and pedidos[-1]["fuera"]
