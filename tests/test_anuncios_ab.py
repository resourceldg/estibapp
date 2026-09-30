"""El test A/B de octubre: cada anuncio nuevo deja su propio mensaje inicial."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from estiba.bandeja.automatico import sale_sola
from estiba.bandeja.redactor import APERTURA
from estiba.conversation.clasificacion import anuncio_de
from estiba.core.models import Conversation, Message, MessageKind
from estiba.economia.historia import por_anuncio
from estiba.economia.mes import FleteDelMes
from estiba.extraction.texto import plano

D = Decimal
AHORA = datetime(2026, 10, 1, 12)


@pytest.mark.parametrize("texto, anuncio", [
    ("¡Hola! Quiero cotizar un flete", "B1 ¿Qué hay que mover?"),
    ("¡Hola! Necesito un flete para hoy", "B2 Te lo llevamos hoy"),
    ("¡Hola! Necesito un flete para algo de Marketplace", "B3 Marketplace"),
    ("¡Hola! Vi el video, necesito un flete", "B4 Reel"),
    ("¡Hola! Quiero más información", "A (anteriores)"),
    ("hola, cuánto sale un flete a Batán?", None),
])
def test_cada_mensaje_inicial_dice_de_que_anuncio_vino(texto, anuncio):
    assert anuncio_de(plano(texto)) == anuncio


def _conv(texto):
    return Conversation("c", [Message("c:1", "c", True, AHORA - timedelta(minutes=5), MessageKind.TEXT, text=texto)])


def test_el_saludo_automatico_sigue_para_los_anuncios_viejos():
    assert sale_sola(_conv("¡Hola! Quiero más información"), APERTURA, AHORA, ya_mando=False)


def test_los_anuncios_nuevos_no_reciben_un_segundo_saludo():
    """Meta ya le pidio la foto: no se le pide lo mismo dos veces."""
    assert not sale_sola(_conv("¡Hola! Quiero cotizar un flete"), APERTURA, AHORA,
                         ya_mando=False)


def test_por_anuncio_solo_cuenta_los_que_vinieron_de_uno():
    hoy = date(2026, 10, 5)
    fletes = [FleteDelMes(f"j{i}", hoy - timedelta(days=i), D(40000), D(30), D(20), D(2), medido=True)
              for i in range(4)]
    grupos = por_anuncio(fletes, {"j0": "B1 ¿Qué hay que mover?", "j1": "B1 ¿Qué hay que mover?",
                                  "j2": "A (anteriores)"}, D(162), {}, hoy)
    assert {g.nombre: g.n for g in grupos} == {"B1 ¿Qué hay que mover?": 2, "A (anteriores)": 1}


def test_la_apertura_dice_que_es_un_flete_y_no_pide_fotos_ni_audios():
    assert "Flete Surfero" in APERTURA
    assert "audio" not in APERTURA and "foto" not in APERTURA
