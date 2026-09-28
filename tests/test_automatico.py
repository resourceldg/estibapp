"""La unica cosa que sale por WhatsApp sin aprobacion.

Toda la app se apoya en que nada sale sin que el fletero lo decida. Esta es
la excepcion, asi que los tests importan mas de lo habitual: cada uno prueba
que una de las cinco condiciones alcanza, sola, para que NO salga nada.

Un mensaje mandado no se puede desmandar. El costo de equivocarse para el
lado de no mandar es que el fletero aprueba un saludo a mano; para el otro
lado es que un desconocido recibe un mensaje del negocio sin motivo.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from estiba.bandeja.automatico import VARIABLE, sale_sola
from estiba.bandeja.redactor import APERTURA
from estiba.core.models import Conversation, Message, MessageKind

AHORA = datetime(2026, 9, 20, 12, 0)
DE_LA_PAUTA = "¡Hola! Quiero más información"


def _chat(*lineas: tuple[str, str]) -> Conversation:
    inicio = datetime(2026, 9, 20, 10)
    return Conversation("x", [
        Message(f"x:{i:03d}", "x", quien == "c", inicio + timedelta(minutes=i),
                MessageKind.TEXT, text=t)
        for i, (quien, t) in enumerate(lineas)
    ])


@pytest.fixture(autouse=True)
def encendida(monkeypatch):
    monkeypatch.setenv(VARIABLE, "1")


class TestCuandoSaleSola:
    def test_el_que_llego_por_la_publicidad_y_no_dijo_nada_mas(self):
        """15 de las 58 conversaciones reales empiezan exactamente asi."""
        assert sale_sola(_chat(("c", DE_LA_PAUTA)), APERTURA, AHORA, ya_mando=False)

    def test_tambien_con_la_otra_forma_que_pone_meta(self):
        conv = _chat(("c", "¡Hola! Me gustaría conseguir más información sobre esto."))
        assert sale_sola(conv, APERTURA, AHORA, ya_mando=False)


class TestCuandoNo:
    def test_si_escribio_a_mano_espera(self):
        """Un "hola" suelto puede ser cualquier cosa: un cliente, la madre,
        un numero equivocado. Ese lo mira una persona."""
        assert not sale_sola(_chat(("c", "hola, buenas")), APERTURA, AHORA, ya_mando=False)

    def test_si_ademas_conto_algo_hay_con_que_contestar_mejor(self):
        conv = _chat(("c", DE_LA_PAUTA), ("c", "necesito llevar una heladera a Güemes"))
        assert not sale_sola(conv, APERTURA, AHORA, ya_mando=False)

    def test_si_el_fletero_ya_escribio_no_se_mete(self):
        conv = _chat(("c", DE_LA_PAUTA), ("v", "ahí te contesto"))
        assert not sale_sola(conv, APERTURA, AHORA, ya_mando=False)

    def test_no_saluda_dos_veces(self):
        assert not sale_sola(_chat(("c", DE_LA_PAUTA)), APERTURA, AHORA, ya_mando=True)

    def test_solo_sale_la_apertura_tal_cual(self):
        """Si el redactor escribio otra cosa es porque entendio algo del
        pedido, y eso ya es una respuesta con contenido."""
        conv = _chat(("c", DE_LA_PAUTA))
        assert not sale_sola(conv, "¡Buenas! ¿De dónde a dónde?", AHORA, ya_mando=False)
        assert not sale_sola(conv, APERTURA + " ¿te sirve el jueves?", AHORA, ya_mando=False)

    def test_se_apaga_sin_tocar_codigo(self, monkeypatch):
        monkeypatch.setenv(VARIABLE, "0")
        assert not sale_sola(_chat(("c", DE_LA_PAUTA)), APERTURA, AHORA, ya_mando=False)

    def test_un_mensaje_que_todavia_no_llego_no_cuenta(self):
        """El reloj manda: si se evalua con una fecha anterior, para Estiba
        ese mensaje no existe todavia."""
        conv = _chat(("c", DE_LA_PAUTA))
        assert not sale_sola(conv, APERTURA, datetime(2026, 9, 20, 9, 0), ya_mando=False)
