"""Los tipos del dominio, incluidas las correcciones aprobadas en el diseno."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from estiba.core.models import (
    Evidence,
    ExtractionMethod,
    FreightJob,
    JobState,
    Message,
    MessageKind,
    Slot,
    can_transition,
)


def _slot(valor):
    return Slot(
        value=valor,
        confidence=0.9,
        evidence=Evidence("m1", "cita", ExtractionMethod.REGEX),
    )


class TestMensaje:
    def test_texto_sin_texto_es_invalido(self):
        with pytest.raises(ValueError, match="sin texto"):
            Message("m1", "c1", True, datetime.now(), MessageKind.TEXT, text=None)

    def test_audio_sin_archivo_es_invalido(self):
        with pytest.raises(ValueError, match="sin media_path"):
            Message("m1", "c1", True, datetime.now(), MessageKind.AUDIO)


class TestSlot:
    def test_confianza_fuera_de_rango(self):
        ev = Evidence("m1", "cita", ExtractionMethod.REGEX)
        with pytest.raises(ValueError, match="confianza"):
            Slot(value="x", confidence=1.5, evidence=ev)

    def test_todo_dato_arrastra_su_evidencia(self):
        s = _slot("Villa Crespo")
        assert s.evidence.message_id == "m1"
        assert s.evidence.method is ExtractionMethod.REGEX


class TestEstados:
    def test_esperando_cliente_existe(self):
        """Correccion aprobada: el silencio es un estado, no su ausencia.

        Sin esto, la cancelacion implicita por silencio no se puede modelar.
        """
        assert JobState.WAITING_CUSTOMER in JobState

    def test_de_presupuestado_se_puede_pasar_a_esperando(self):
        assert can_transition(JobState.QUOTED, JobState.WAITING_CUSTOMER)

    def test_esperando_puede_terminar_confirmado_o_cancelado(self):
        assert can_transition(JobState.WAITING_CUSTOMER, JobState.CONFIRMED)
        assert can_transition(JobState.WAITING_CUSTOMER, JobState.CANCELLED)

    def test_no_se_puede_agendar_sin_confirmar(self):
        assert not can_transition(JobState.DRAFT, JobState.SCHEDULED)
        assert not can_transition(JobState.QUOTED, JobState.SCHEDULED)

    def test_estados_finales_no_salen(self):
        assert not can_transition(JobState.DONE, JobState.SCHEDULED)
        assert not can_transition(JobState.CANCELLED, JobState.CONFIRMED)


class TestDatosFaltantes:
    def test_un_flete_vacio_le_falta_todo(self):
        job = FreightJob(id="j1", conversation_id="c1")
        assert set(job.missing) == {"origin", "destination", "when", "items"}

    def test_el_precio_no_es_obligatorio_para_confirmar(self):
        job = FreightJob(
            id="j1",
            conversation_id="c1",
            origin=_slot("Villa Crespo"),
            destination=_slot("Caballito"),
            when=_slot(date(2026, 9, 20)),
            items=_slot(["heladera"]),
        )
        assert job.missing == []
