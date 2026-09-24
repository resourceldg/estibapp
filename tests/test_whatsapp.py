"""Fase 7: WhatsApp por WAHA.

WAHA se simula con httpx.MockTransport, con las formas de la API de WAHA
2026.8.2 (verificadas contra el contenedor real). La firma de los webhooks
se prueba contra un evento firmado por WAHA de verdad.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

import estiba.adapters.waha as waha
from estiba.adapters.waha import ConfigWaha, ErrorWaha, WahaClient, firma_valida, normalizar
from estiba.conversation.store import ConversationStore
from estiba.core.models import MessageKind
from estiba.entorno import cargar_env
from estiba.storage.db import connect
from estiba.storage.repositories import DraftRepository, SettingsRepository
from estiba.web.app import create_app
from estiba.whatsapp import WhatsApp

DATOS = Path(__file__).parent / "datos"
T = int(datetime(2026, 9, 18, 10, 0).timestamp())
CLIENTE = "5491144445555"


def mensaje(**cambios) -> dict:
    """Un WAMessage como los manda WAHA."""
    base = {
        "id": "false_5491144445555@c.us_3EB0AAAA", "timestamp": T,
        "from": f"{CLIENTE}@c.us", "fromMe": False, "source": "app", "to": "5491100000000@c.us",
        "participant": None, "body": "hola, necesito llevar una heladera de Caballito a Flores, hay ascensor",
        "hasMedia": False, "mediaUrl": None, "ack": 1, "ackName": "SERVER",
    }
    base.update(cambios)
    return base


class WahaFalso:
    """Un WAHA en memoria, con las rutas que usa Estiba."""

    def __init__(self) -> None:
        self.sesion: dict | None = None
        self.enviados: list[dict] = []
        self.falla_envio = False
        self.pedidos: list[tuple[str, str]] = []
        self.lids = {"99887766@lid": "5491122223333@c.us"}
        self.historial = {f"{CLIENTE}@c.us": [mensaje()]}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        ruta, metodo = request.url.path, request.method
        self.pedidos.append((metodo, ruta))
        if request.headers.get("X-Api-Key") != "clave":
            return httpx.Response(401, json={"message": "Unauthorized"})
        if ruta == "/api/sessions/default" and metodo == "GET":
            return httpx.Response(200, json=self.sesion) if self.sesion else httpx.Response(404, json={})
        if ruta == "/api/sessions" and metodo == "POST":
            cuerpo = json.loads(request.content)
            self.sesion = {"name": cuerpo["name"], "status": "SCAN_QR_CODE", "config": cuerpo["config"], "me": None}
            return httpx.Response(201, json=self.sesion)
        if ruta == "/api/sessions/default" and metodo == "PUT":
            self.sesion["config"] = json.loads(request.content)["config"]
            return httpx.Response(200, json=self.sesion)
        if ruta == "/api/sessions/default/start":
            self.sesion["status"] = "STARTING"
            return httpx.Response(201, json=self.sesion)
        if ruta == "/api/default/auth/qr":
            if not self.sesion or self.sesion["status"] != "SCAN_QR_CODE":
                return httpx.Response(422, json={"error": "no qr"})
            return httpx.Response(200, content=b"\x89PNG", headers={"content-type": "image/png"})
        if ruta == "/api/sendText":
            cuerpo = json.loads(request.content)
            if self.falla_envio:
                return httpx.Response(422, json={"error": "Session status is not as expected", "status": "STARTING"})
            enviado = mensaje(id=f"true_{cuerpo['chatId']}_OUT{len(self.enviados)}", fromMe=True,
                              **{"from": "5491100000000@c.us", "to": cuerpo["chatId"]}, body=cuerpo["text"],
                              timestamp=T + 60)
            self.enviados.append(cuerpo)
            return httpx.Response(201, json=enviado)
        if ruta.startswith("/api/default/lids/"):
            pn = self.lids.get(ruta.rsplit("/", 1)[1])
            return httpx.Response(200, json={"lid": ruta.rsplit("/", 1)[1], "pn": pn}) if pn else httpx.Response(404)
        if ruta.startswith("/api/files/"):
            return httpx.Response(200, content=b"OggS audio")
        if ruta == "/api/default/chats":
            return httpx.Response(200, json=[{"id": jid} for jid in [*self.historial, "12036@g.us"]])
        if ruta.startswith("/api/default/chats/") and ruta.endswith("/messages"):
            return httpx.Response(200, json=self.historial.get(ruta.split("/")[4], []))
        return httpx.Response(404, json={"ruta": ruta})


@pytest.fixture
def falso():
    return WahaFalso()


@pytest.fixture
def cliente_waha(falso):
    config = ConfigWaha("http://waha:3000", "clave", hmac_key="firma", url_estiba="http://host.docker.internal:8000")
    return WahaClient(config, transport=httpx.MockTransport(falso))


@pytest.fixture(autouse=True)
def media_temporal(tmp_path, monkeypatch):
    monkeypatch.setattr(waha, "MEDIA_DIR", tmp_path / "audios")


# --------------------------------------------------------------------------
# Normalizar
# --------------------------------------------------------------------------


class TestNormalizar:
    def test_texto_del_cliente(self):
        m = normalizar(mensaje())
        assert (m.chat_id, m.from_customer, m.kind, m.source) == (CLIENTE, True, MessageKind.TEXT, "waha")
        assert m.sent_at == datetime(2026, 9, 18, 10, 0)

    def test_lo_que_mandas_vos_va_al_chat_del_destinatario(self):
        m = normalizar(mensaje(fromMe=True, **{"from": "5491100000000@c.us", "to": f"{CLIENTE}@c.us"}))
        assert m.chat_id == CLIENTE and not m.from_customer

    @pytest.mark.parametrize("jid", ["12036302@g.us", "status@broadcast", "1203@newsletter"])
    def test_grupos_estados_y_canales_no_son_clientes(self, jid):
        assert normalizar(mensaje(**{"from": jid})) is None

    def test_lid_se_traduce_a_telefono(self):
        m = normalizar(mensaje(**{"from": "99887766@lid"}), resolver_lid=lambda jid: "5491122223333")
        assert m.chat_id == "5491122223333"

    def test_lid_sin_traduccion_queda_como_identidad(self):
        m = normalizar(mensaje(**{"from": "99887766@lid"}), resolver_lid=lambda jid: None)
        assert m.chat_id == "99887766@lid"

    def test_audio_con_archivo(self):
        m = normalizar(mensaje(hasMedia=True, body="", media={"mimetype": "audio/ogg; codecs=opus"}),
                       media_path="data/audios/a.ogg")
        assert m.kind is MessageKind.AUDIO and m.media_path == "data/audios/a.ogg" and m.text is None

    def test_audio_que_no_se_pudo_bajar_queda_como_otro(self):
        m = normalizar(mensaje(hasMedia=True, body="", media={"mimetype": "audio/ogg"}))
        assert m.kind is MessageKind.OTHER

    def test_si_la_descarga_falla_el_mensaje_igual_entra(self):
        """WhatsApp caduca los audios viejos: WAHA contesta 404.

        Antes la excepcion subia hasta el webhook, que devolvia 500, y WAHA
        terminaba descartando el mensaje entero despues de reintentar.
        """
        def rompe(request):
            return httpx.Response(404, json={"error": "not found"})

        cliente = WahaClient(
            ConfigWaha("http://waha:3000", "clave"), transport=httpx.MockTransport(rompe)
        )
        m = waha.ingresar(cliente, mensaje(
            hasMedia=True, body="",
            media={"url": "http://waha:3000/api/files/default/x.oga", "mimetype": "audio/ogg"},
        ))
        assert m is not None and m.kind is MessageKind.OTHER and m.media_path is None

    def test_foto_con_epigrafe_se_lee_como_texto(self):
        m = normalizar(mensaje(hasMedia=True, body="esta es la heladera", media={"mimetype": "image/jpeg"}))
        assert m.kind is MessageKind.TEXT and m.text == "esta es la heladera"


class TestFirma:
    def test_contra_un_webhook_firmado_por_waha(self):
        vector = json.loads((DATOS / "waha_webhook_firmado.json").read_text())
        cuerpo = vector["cuerpo"].encode()
        assert firma_valida(cuerpo, vector["hmac"], vector["clave"])
        assert not firma_valida(cuerpo, vector["hmac"], "otra-clave")
        assert not firma_valida(cuerpo + b" ", vector["hmac"], vector["clave"])
        assert not firma_valida(cuerpo, None, vector["clave"])


# --------------------------------------------------------------------------
# Cliente
# --------------------------------------------------------------------------


class TestCliente:
    def test_conectar_crea_la_sesion_con_el_webhook_de_estiba(self, cliente_waha, falso):
        cliente_waha.conectar("SECRETO")
        webhook = falso.sesion["config"]["webhooks"][0]
        assert webhook["url"] == "http://host.docker.internal:8000/webhooks/waha/SECRETO"
        assert webhook["events"] == ["message.any", "session.status"]
        assert webhook["hmac"] == {"key": "firma"}
        assert falso.sesion["config"]["ignore"]["groups"] is True

    def test_conectar_de_nuevo_actualiza_y_arranca_si_estaba_parada(self, cliente_waha, falso):
        cliente_waha.conectar("A")
        falso.sesion["status"] = "STOPPED"
        cliente_waha.conectar("B")
        assert falso.sesion["config"]["webhooks"][0]["url"].endswith("/B")
        assert ("POST", "/api/sessions/default/start") in falso.pedidos

    def test_api_key_mala(self, falso):
        malo = WahaClient(ConfigWaha("http://waha:3000", "otra"), transport=httpx.MockTransport(falso))
        with pytest.raises(ErrorWaha, match="API key"):
            malo.sesion()

    def test_error_de_envio_legible(self, cliente_waha, falso):
        falso.falla_envio = True
        with pytest.raises(ErrorWaha, match="Session status is not as expected"):
            cliente_waha.enviar_texto(CLIENTE, "hola")

    def test_manda_al_jid_del_telefono(self, cliente_waha, falso):
        cliente_waha.enviar_texto(CLIENTE, "hola")
        assert falso.enviados[0]["chatId"] == f"{CLIENTE}@c.us"

    def test_baja_archivos_por_la_url_configurada(self, cliente_waha, falso, tmp_path):
        destino = cliente_waha.bajar("http://localhost:3000/api/files/default/a.oga", tmp_path / "a.ogg")
        assert destino.read_bytes() == b"OggS audio"
        assert ("GET", "/api/files/default/a.oga") in falso.pedidos

    def test_lid_se_consulta_una_sola_vez(self, cliente_waha, falso):
        assert cliente_waha.telefono_de_lid("99887766@lid") == "5491122223333"
        cliente_waha.telefono_de_lid("99887766@lid")
        assert falso.pedidos.count(("GET", "/api/default/lids/99887766@lid")) == 1


# --------------------------------------------------------------------------
# Servicio
# --------------------------------------------------------------------------


@pytest.fixture
def conn():
    return connect(":memory:")


class TestServicio:
    def test_recibir_guarda_una_sola_vez(self, cliente_waha, conn):
        w = WhatsApp(cliente_waha)
        evento = {"event": "message.any", "payload": mensaje()}
        assert w.recibir(conn, evento) is not None
        assert w.recibir(conn, evento) is None
        assert len(ConversationStore(conn).get(CLIENTE).messages) == 1

    def test_recibir_un_audio_lo_baja_y_queda_para_transcribir(self, cliente_waha, conn):
        w = WhatsApp(cliente_waha)
        m = w.recibir(conn, {"event": "message.any", "payload": mensaje(
            id="false_x_AUDIO", hasMedia=True, body="",
            media={"url": "http://localhost:3000/api/files/default/x.oga", "mimetype": "audio/ogg; codecs=opus"})})
        assert m.kind is MessageKind.AUDIO and Path(m.media_path).read_bytes() == b"OggS audio"
        assert [p.id for p in ConversationStore(conn).pending_audio()] == ["false_x_AUDIO"]

    def test_otros_eventos_se_ignoran(self, cliente_waha, conn):
        assert WhatsApp(cliente_waha).recibir(conn, {"event": "message.ack", "payload": mensaje()}) is None

    def test_importar_historial_sin_grupos_y_sin_duplicar(self, cliente_waha, conn):
        w = WhatsApp(cliente_waha)
        assert w.importar(conn, datetime(2026, 9, 1)) == 1
        assert w.importar(conn, datetime(2026, 9, 1)) == 0

    def _aprobado(self, conn, texto="Te sale $40.000") -> int:
        ConversationStore(conn).ingest([normalizar(mensaje())])
        with conn:
            conn.execute("INSERT INTO jobs (id, conversation_id, state, created_at) VALUES ('j', ?, 'borrador', 'x')",
                         (CLIENTE,))
        repo = DraftRepository(conn)
        with conn:
            conn.execute("INSERT INTO drafts (job_id, chat_id, kind, reason, text, basis_message_id, created_at) "
                         "VALUES ('j', ?, 'cotizacion', 'r', ?, ?, 'x')", (CLIENTE, texto, mensaje()["id"]))
        draft_id = repo.pending()[0]["id"]
        repo.decide(draft_id, True, texto, datetime(2026, 9, 18, 10, 5))
        return draft_id

    def test_enviar_marca_y_mete_el_mensaje_en_el_hilo(self, cliente_waha, falso, conn):
        draft_id = self._aprobado(conn)
        envio = WhatsApp(cliente_waha).enviar(conn, draft_id, datetime(2026, 9, 18, 10, 6))
        assert envio.ok and falso.enviados[0]["text"] == "Te sale $40.000"
        assert DraftRepository(conn).get(draft_id)["sent_at"] is not None
        hilo = ConversationStore(conn).get(CLIENTE).messages
        assert hilo[-1].text == "Te sale $40.000" and not hilo[-1].from_customer

    def test_si_falla_queda_el_error_para_reintentar(self, cliente_waha, falso, conn):
        draft_id = self._aprobado(conn)
        falso.falla_envio = True
        envio = WhatsApp(cliente_waha).enviar(conn, draft_id, datetime(2026, 9, 18, 10, 6))
        assert not envio.ok
        assert [b["id"] for b in DraftRepository(conn).unsent()] == [draft_id]
        falso.falla_envio = False
        assert WhatsApp(cliente_waha).enviar(conn, draft_id, datetime(2026, 9, 18, 10, 7)).ok
        assert DraftRepository(conn).unsent() == []

    def test_no_manda_nada_que_no_este_aprobado(self, cliente_waha, conn):
        ConversationStore(conn).ingest([normalizar(mensaje())])
        with conn:
            conn.execute("INSERT INTO jobs (id, conversation_id, state, created_at) VALUES ('j', ?, 'borrador', 'x')",
                         (CLIENTE,))
            conn.execute("INSERT INTO drafts (job_id, chat_id, kind, reason, text, basis_message_id, created_at) "
                         "VALUES ('j', ?, 'cotizacion', 'r', 'hola', ?, 'x')", (CLIENTE, mensaje()["id"]))
        with pytest.raises(ValueError, match="no esta aprobado"):
            WhatsApp(cliente_waha).enviar(conn, 1, datetime(2026, 9, 18))


# --------------------------------------------------------------------------
# Dashboard: webhook, envio al aprobar, pantalla de WhatsApp
# --------------------------------------------------------------------------


@pytest.fixture
def app_con_waha(tmp_path, cliente_waha):
    app = create_app(tmp_path / "w.db", ahora=lambda: datetime(2026, 9, 18, 10, 30),
                     whatsapp=WhatsApp(cliente_waha))
    with TestClient(app) as tc:
        tc.app_estado = app.state.estado
        yield tc


def _secreto(tc) -> str:
    conn = tc.app_estado.conectar()
    try:
        return SettingsRepository(conn).secreto("webhook_waha")
    finally:
        conn.close()


def _firmado(evento: dict) -> tuple[bytes, dict]:
    import hashlib
    import hmac

    cuerpo = json.dumps(evento).encode()
    return cuerpo, {"X-Webhook-Hmac": hmac.new(b"firma", cuerpo, hashlib.sha512).hexdigest(),
                    "Content-Type": "application/json"}


class TestWebhook:
    def test_secreto_equivocado(self, app_con_waha):
        assert app_con_waha.post("/webhooks/waha/otro", json={}).status_code == 404

    def test_firma_equivocada(self, app_con_waha):
        r = app_con_waha.post(f"/webhooks/waha/{_secreto(app_con_waha)}", json={"event": "message.any"},
                              headers={"X-Webhook-Hmac": "00"})
        assert r.status_code == 401

    def test_un_mensaje_llega_a_la_bandeja(self, app_con_waha):
        cuerpo, headers = _firmado({"event": "message.any", "session": "default", "payload": mensaje()})
        r = app_con_waha.post(f"/webhooks/waha/{_secreto(app_con_waha)}", content=cuerpo, headers=headers)
        assert r.json() == {"ok": True, "nuevo": True}
        [item] = app_con_waha.get("/api/bandeja").json()
        assert item["flete"]["chat_id"] == CLIENTE and item["borrador"]["tipo"] == "cotizacion"

    def test_el_estado_de_la_sesion_se_actualiza(self, app_con_waha):
        cuerpo, headers = _firmado({"event": "session.status", "payload": {"status": "WORKING"}})
        app_con_waha.post(f"/webhooks/waha/{_secreto(app_con_waha)}", content=cuerpo, headers=headers)
        assert app_con_waha.app_estado.sesion == "WORKING"

    def test_la_puerta_de_docker_solo_atiende_webhooks(self, tmp_path, cliente_waha):
        app = create_app(tmp_path / "p.db", whatsapp=WhatsApp(cliente_waha))
        with TestClient(app) as tc:
            app.state.puertas_webhook = {"testserver"}
            assert tc.get("/").status_code == 404
            assert tc.post("/webhooks/waha/x", json={}).status_code == 404  # llega, pero el secreto no es


class TestAprobarYEnviar:
    def _llega(self, tc):
        cuerpo, headers = _firmado({"event": "message.any", "payload": mensaje()})
        tc.post(f"/webhooks/waha/{_secreto(tc)}", content=cuerpo, headers=headers)
        return tc.get("/api/bandeja").json()[0]["borrador"]["id"]

    def test_aprobar_manda_por_whatsapp(self, app_con_waha, falso):
        draft_id = self._llega(app_con_waha)
        r = app_con_waha.post(f"/borradores/{draft_id}/aprobar", data={"texto": "Te sale $40.000"},
                              headers={"HX-Request": "true"})
        assert "Enviado por WhatsApp" in r.text and "data-abre-whatsapp" not in r.text
        assert falso.enviados == [{"session": "default", "chatId": f"{CLIENTE}@c.us", "text": "Te sale $40.000"}]
        assert app_con_waha.get("/api/bandeja").json() == []

    def test_si_no_sale_queda_arriba_de_la_bandeja_para_reintentar(self, app_con_waha, falso):
        draft_id = self._llega(app_con_waha)
        falso.falla_envio = True
        r = app_con_waha.post(f"/borradores/{draft_id}/aprobar", data={"texto": "hola"}, headers={"HX-Request": "true"})
        assert "Aprobado, pero no salió" in r.text and "Reintentar" in r.text
        assert "No salieron" in app_con_waha.get("/").text
        falso.falla_envio = False
        r = app_con_waha.post(f"/borradores/{draft_id}/reenviar", headers={"HX-Request": "true"})
        assert "Enviado por WhatsApp" in r.text
        assert "No salieron" not in app_con_waha.get("/").text

    def test_sin_waha_aprobar_abre_whatsapp_en_el_celular(self, tmp_path):
        conn = connect(tmp_path / "s.db")
        ConversationStore(conn).ingest([normalizar(mensaje())])
        conn.close()
        with TestClient(create_app(tmp_path / "s.db", ahora=lambda: datetime(2026, 9, 18, 10, 30))) as tc:
            assert "data-abre-whatsapp" in tc.get("/").text


class TestPantalla:
    def test_sin_configurar_explica_como(self, tmp_path):
        with TestClient(create_app(tmp_path / "n.db")) as tc:
            html = tc.get("/whatsapp").text
            assert "docker compose up -d" in html

    def test_conectar_y_mostrar_el_qr(self, app_con_waha, falso):
        app_con_waha.post("/whatsapp/conectar")
        html = app_con_waha.get("/whatsapp").text
        assert "Escaneá el QR" in html and "/whatsapp/qr.png" in html
        assert 'hx-trigger="every 3s"' in html
        assert app_con_waha.get("/whatsapp/qr.png").content == b"\x89PNG"

    def test_conectado_ofrece_importar(self, app_con_waha, falso):
        app_con_waha.post("/whatsapp/conectar")
        falso.sesion.update(status="WORKING", me={"id": "5491100000000@c.us", "pushName": "Fletes Juan"})
        html = app_con_waha.get("/whatsapp").text
        assert "Fletes Juan" in html and "+54 9 11 0000-0000" in html and "Importar" in html
        app_con_waha.post("/whatsapp/importar", data={"dias": "30"})
        assert any(i["flete"]["chat_id"] == CLIENTE for i in app_con_waha.get("/api/bandeja").json())

    def test_waha_apagado(self, tmp_path):
        def caido(request):
            raise httpx.ConnectError("refused")

        w = WhatsApp(WahaClient(ConfigWaha("http://waha:3000", "clave"), transport=httpx.MockTransport(caido)))
        with TestClient(create_app(tmp_path / "c.db", whatsapp=w)) as tc:
            assert "WAHA no responde" in tc.get("/whatsapp").text


def test_env(tmp_path, monkeypatch):
    archivo = tmp_path / ".env"
    archivo.write_text("# comentario\nESTIBA_PRUEBA_A=uno\nESTIBA_PRUEBA_B='dos tres'\nESTIBA_PRUEBA_C=pisada\n")
    monkeypatch.setenv("ESTIBA_PRUEBA_C", "del entorno")
    monkeypatch.delenv("ESTIBA_PRUEBA_A", raising=False)
    monkeypatch.delenv("ESTIBA_PRUEBA_B", raising=False)
    assert sorted(cargar_env(archivo)) == ["ESTIBA_PRUEBA_A", "ESTIBA_PRUEBA_B"]
    import os

    assert os.environ["ESTIBA_PRUEBA_B"] == "dos tres" and os.environ["ESTIBA_PRUEBA_C"] == "del entorno"
    monkeypatch.delenv("ESTIBA_PRUEBA_A")
    monkeypatch.delenv("ESTIBA_PRUEBA_B")
