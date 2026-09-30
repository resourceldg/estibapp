"""Confirmar el precio con el slider, y la fecha y hora tentativas.

Lo que decide el fletero le gana a lo que Estiba dedujo del chat: entra en
el mensaje de cotizacion y en la agenda.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, time
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from estiba.bandeja.redactor import proponer
from estiba.conversation.store import ConversationStore
from estiba.core.models import Conversation, ExtractionMethod, Message, MessageKind
from estiba.finanzas.precio import PrecioPorObjetivos
from estiba.jobs.engine import AJUSTADO, Accion, JobEngine
from estiba.storage.db import connect
from estiba.web.app import create_app

AHORA = datetime(2026, 9, 29, 18)
CHAT = "5492235550003"
PEDIDO = [("c", "cuanto me sale llevar una heladera de Alberti 2359 a Catamarca 1185? hay ascensor")]


def _conv(lineas=PEDIDO):
    t0 = datetime(2026, 9, 29, 17)
    return Conversation(CHAT, [Message(f"{CHAT}:{i}", CHAT, q == "c", t0.replace(minute=i), MessageKind.TEXT, text=t)
                               for i, (q, t) in enumerate(lineas)])


def _ajuste(precio=None, fecha=None, hora=None, at=AHORA):
    return Accion(at, AJUSTADO, nota=json.dumps({"fecha": fecha, "hora": hora}),
                  monto=Decimal(precio) if precio else None)


MOTOR = JobEngine(pricing=PrecioPorObjetivos(None))


class TestMotor:
    def test_lo_que_pone_el_fletero_le_gana_al_chat(self):
        ev = MOTOR.evaluar(_conv(), AHORA, [_ajuste(60000, "2026-10-01", "15:00")])
        assert ev.precio_confirmado == Decimal(60000)
        assert ev.job.when.value == date(2026, 10, 1) and ev.job.hour.value == time(15)
        assert ev.job.when.evidence.method is ExtractionMethod.HUMAN

    def test_vale_el_ultimo_ajuste(self):
        ev = MOTOR.evaluar(_conv(), AHORA, [_ajuste(60000, at=AHORA.replace(hour=17, minute=50)),
                                            _ajuste(65000, at=AHORA.replace(hour=17, minute=55))])
        assert ev.precio_confirmado == Decimal(65000)

    def test_sin_fecha_no_inventa_una(self):
        ev = MOTOR.evaluar(_conv(), AHORA, [_ajuste(60000)])
        assert ev.job.when is None

    def test_el_mensaje_de_cotizacion_lleva_el_precio_confirmado(self):
        conv = _conv()
        ev = MOTOR.evaluar(conv, AHORA, [_ajuste(61000)])
        p = proponer(ev, conv, AHORA)
        assert p.kind == "cotizacion" and "$61.000" in p.text


@pytest.fixture
def web(tmp_path):
    ruta = tmp_path / "aj.db"
    conn = connect(ruta)
    ConversationStore(conn).ingest(_conv().messages)
    conn.close()
    app = create_app(ruta, engine=JobEngine(pricing=PrecioPorObjetivos(None)), ahora=lambda: AHORA)
    with TestClient(app, follow_redirects=False) as c:
        yield c


class TestPantalla:
    def test_el_slider_va_de_a_mil_y_arranca_en_el_sugerido(self, web):
        html = web.get(f"/fletes/{CHAT}:job").text
        slider = re.search(r'<input[^>]*data-slider[^>]*>', html).group(0)
        assert 'step="1000"' in slider
        valor = int(re.search(r'value="(\d+)"', slider).group(1))
        assert valor % 1000 == 0
        assert "mínimo" in html and "meta" in html
        assert "Estiba no encontró una fecha" in html

    def test_confirmar_guarda_y_se_ve(self, web):
        r = web.post(f"/fletes/{CHAT}:job/ajustar", data={"precio": "60000", "fecha": "2026-10-01", "hora": "15:00"})
        assert r.status_code == 303
        html = web.get(f"/fletes/{CHAT}:job").text
        assert "Tu precio (confirmado)" in html and 'value="60000"' in html
        assert 'value="2026-10-01"' in html and "La fecha la pusiste vos" in html

    def test_la_fecha_confirmada_va_a_la_agenda_como_sin_confirmar(self, web):
        """El cliente todavia no acepto: entra como tentativa, con su recordatorio."""
        web.post(f"/fletes/{CHAT}:job/ajustar", data={"precio": "60000", "fecha": "2026-10-01", "hora": "15:00"})
        assert "Fechas por confirmar" in web.get("/").text

    @pytest.mark.parametrize("datos", [{"precio": "60000", "fecha": "cualquiera"},
                                       {"precio": "60000", "hora": "tarde"}, {}])
    def test_lo_que_no_se_entiende_no_entra(self, web, datos):
        assert web.post(f"/fletes/{CHAT}:job/ajustar", data=datos).status_code == 422

    def test_un_flete_hecho_no_se_ajusta(self, web):
        web.post(f"/fletes/{CHAT}:job/realizado", data={"cobrado": "55000"})
        assert web.post(f"/fletes/{CHAT}:job/ajustar", data={"precio": "60000"}).status_code == 409
