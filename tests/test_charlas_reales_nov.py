"""Cómo lee Estiba las respuestas de los clientes: casos reales de noviembre 2026 (anonimizados).

Lucas dudó de cómo Estiba categorizaba lo cotizado (18-nov-2026). La revisión mostró que Estiba sí detectaba
aceptaciones pero no las mostraba, que contaba un «Genial gracias!!» como «espera respuesta», que no reconocía el
«no por ahora» amable y que no preguntaba «¿se hizo?» por un cotizado con la fecha cumplida.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from estiba.core.models import Conversation, JobState, Message, MessageKind
from estiba.extraction.intencion import Intencion, es_cortesia, intencion
from estiba.jobs.engine import JobEngine

INICIO = datetime(2026, 11, 10, 10)


def _chat(*lineas: tuple[str, str], cada: timedelta = timedelta(minutes=5)) -> Conversation:
    msgs = [Message(f"n:{i:03d}", "n", q == "c", INICIO + cada * i, MessageKind.TEXT, text=t) for i, (q, t) in enumerate(lineas)]
    return Conversation("n", msgs)


class TestNoPorAhora:
    def test_el_no_amable_es_posponer(self):
        for t in ("Te agendo por si necesito", "estoy buscando presupuestos .. si lo necesito le aviso",
                  "cualquier cosa le aviso", "ando comparando precios"):
            assert intencion(t)[0] is Intencion.POSPONE, t

    def test_no_se_confunde_con_aceptar_ni_cancelar(self):
        assert intencion("me sirve, el sábado entonces")[0] is Intencion.ACEPTA
        assert intencion("ya conseguí, gracias")[0] is Intencion.CANCELA
        assert intencion("te confirmo para hoy a las 15")[0] is Intencion.ACEPTA

    def test_despues_de_cotizar_pasa_a_esperando(self):
        ev = JobEngine().evaluar(_chat(("c", "de Constitución a Centro una heladera"), ("v", "te sale 35 mil"),
                                       ("c", "estoy buscando presupuestos .. si lo necesito le aviso")), INICIO + timedelta(hours=2))
        assert ev.job.state is JobState.WAITING_CUSTOMER


class TestCortesia:
    def test_que_es_cortesia(self):
        for t in ("Genial gracias!!", "Genial Buenisimo!", "👍", "Tranqui se entiende", "ok", "Dale gracias"):
            assert es_cortesia(t), t
        for t in ("gracias, y el sábado?", "Gracias! te paso la dirección: Colón 1234", "Hola, necesito un flete", "", None):
            assert not es_cortesia(t), t

    def test_despues_de_cotizar_un_gracias_no_espera_respuesta(self):
        ev = JobEngine().evaluar(_chat(("c", "de Constitución a Centro una heladera"), ("v", "te sale 35 mil"),
                                       ("c", "Genial gracias!!")), INICIO + timedelta(hours=3))
        assert not any("espera respuesta" in a for a in ev.alertas)

    def test_antes_de_cotizar_un_ok_sigue_esperando_tu_precio(self):
        ev = JobEngine().evaluar(_chat(("c", "Compré algo en Marketplace y necesito traerlo de La Plata"),
                                       ("v", "se paga 50% al principio y 50 en la entrega"), ("c", "Ok")), INICIO + timedelta(hours=3))
        assert any("espera respuesta" in a for a in ev.alertas)


class TestAceptoYFaltaConfirmar:
    def test_no_se_da_por_perdido_por_silencio(self):
        # «Genial Buenisimo!» después de cotizar, sin fecha ni direcciones claras: aceptó pero no se puede confirmar.
        ev = JobEngine().evaluar(_chat(("c", "necesito llevar unas cosas"), ("v", "te sale 25 mil"),
                                       ("c", "Tiene el carro para prestarme?"), ("v", "Si si claro"), ("c", "Genial Buenisimo!")),
                                 INICIO + timedelta(days=8))
        assert ev.job.state is not JobState.CANCELLED
        assert any("aceptó" in a and "falta confirmarlo" in a for a in ev.alertas)


class TestFechaCumplida:
    def test_cotizado_con_la_fecha_cumplida_y_charla_ese_dia_pregunta_si_se_hizo(self):
        # Caso real: cotizado, el dia del flete escribiste «Ahí estoy adentro del edificio» y nadie lo marcó hecho.
        msgs = [Message("f:0", "f", True, datetime(2026, 11, 13, 9), MessageKind.TEXT, text="necesito llevar un sillón el 18/11"),
                Message("f:1", "f", False, datetime(2026, 11, 13, 9, 30), MessageKind.TEXT, text="te sale 40 mil"),
                Message("f:2", "f", False, datetime(2026, 11, 18, 10, 33), MessageKind.TEXT, text="Ahí estoy adentro del edificio")]
        ev = JobEngine().evaluar(Conversation("f", msgs), datetime(2026, 11, 18, 22))
        assert ev.job.state in (JobState.QUOTED, JobState.WAITING_CUSTOMER)
        assert any("¿se hizo el flete?" in a and "cuánto cobraste" in a for a in ev.alertas)
