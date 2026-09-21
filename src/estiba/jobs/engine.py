"""JobEngine: el estado del flete, reconstruido desde la conversacion.

El estado no se guarda a mano: se recalcula recorriendo el hilo mensaje por
mensaje. En cada paso se extraen los datos de lo dicho hasta ese momento, y
la intencion del mensaje decide si el flete cambia de estado. Asi cada
transicion queda con su causa y el mensaje que la provoco, y reprocesar la
misma conversacion da siempre la misma historia.

Es el unico lugar que aplica transiciones, y solo las de ALLOWED_TRANSITIONS.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from estiba.core.models import (
    REQUIRED_FOR_CONFIRMATION,
    Conversation,
    FreightJob,
    JobState,
    can_transition,
)
from estiba.extraction.extractor import Extraccion, SlotExtractor
from estiba.extraction.intencion import Intencion
from estiba.jobs.preguntas import ETIQUETAS, preguntas
from estiba.jobs.pricing import PricingEngine, Presupuesto

_ABIERTOS = (JobState.QUOTED, JobState.WAITING_CUSTOMER)
_CERRADOS = (JobState.DONE, JobState.CANCELLED)


@dataclass(frozen=True)
class Politica:
    #: Sin respuesta del cliente despues de esto: hay que escribirle.
    seguimiento: timedelta = timedelta(days=1)
    #: Sin respuesta despues de esto: el flete se da por perdido.
    abandono: timedelta = timedelta(days=4)
    #: Por debajo de esta confianza, un dato obligatorio no alcanza para confirmar.
    confianza_minima: float = 0.6


@dataclass(frozen=True)
class Evento:
    desde: JobState | None
    hacia: JobState
    at: datetime
    causa: str
    message_id: str | None


@dataclass
class Evaluacion:
    job: FreightJob
    extraccion: Extraccion
    eventos: list[Evento] = field(default_factory=list)
    #: Lo que el fletero tiene que mirar: no cambia el estado por si solo.
    alertas: list[str] = field(default_factory=list)
    #: Borrador de lo que habria que preguntarle al cliente.
    preguntas: list[str] = field(default_factory=list)
    presupuesto: Presupuesto | None = None


class JobEngine:
    def __init__(
        self,
        extractor: SlotExtractor | None = None,
        pricing: PricingEngine | None = None,
        politica: Politica | None = None,
    ) -> None:
        self.extractor = extractor or SlotExtractor()
        self.pricing = pricing or PricingEngine()
        self.politica = politica or Politica()

    def evaluar(self, conv: Conversation, ahora: datetime) -> Evaluacion:
        mensajes = [m for m in conv.messages if m.sent_at <= ahora]
        if not mensajes:
            raise ValueError(f"la conversacion {conv.chat_id} no tiene mensajes hasta {ahora}")

        estado = JobState.DRAFT
        eventos: list[Evento] = []
        alertas: list[str] = []
        aceptacion_trabada: str | None = None

        def mover(hacia: JobState, at: datetime, causa: str, message_id: str | None) -> None:
            nonlocal estado
            if not can_transition(estado, hacia):
                raise ValueError(f"transicion no permitida: {estado.value} -> {hacia.value}")
            eventos.append(Evento(estado, hacia, at, causa, message_id))
            estado = hacia

        for i, m in enumerate(mensajes):
            ex = self.extractor.extract(Conversation(conv.chat_id, mensajes[: i + 1], conv.transcripts))
            if estado in _CERRADOS:
                continue

            if not m.from_customer:
                precio = ex.job.quoted_price
                if precio is not None and precio.evidence.message_id == m.id:
                    if estado in (JobState.DRAFT, JobState.WAITING_CUSTOMER):
                        mover(JobState.QUOTED, m.sent_at, f"cotizo ${precio.value:,}".replace(",", "."), m.id)
                    elif estado is JobState.CONFIRMED:
                        alertas.append(f"{m.id}: nueva cotizacion con el flete ya confirmado")
                continue

            intencion = next((x.intencion for x in ex.intenciones if x.message_id == m.id), None)
            if intencion is Intencion.CANCELA:
                mover(JobState.CANCELLED, m.sent_at, "el cliente cancelo", m.id)
            elif intencion is Intencion.POSPONE and estado is JobState.QUOTED:
                mover(JobState.WAITING_CUSTOMER, m.sent_at, "el cliente pospuso la decision", m.id)
            elif intencion is Intencion.ACEPTA and estado in _ABIERTOS:
                impide = self._impide_confirmar(ex)
                if impide:
                    aceptacion_trabada = f"{m.id}: acepto, pero {', '.join(impide)}"
                else:
                    mover(JobState.CONFIRMED, m.sent_at, "el cliente acepto", m.id)
                    aceptacion_trabada = None
            elif intencion is Intencion.CORRIGE and estado not in (JobState.DRAFT, *_CERRADOS):
                alertas.append(f"{m.id}: el cliente corrigio datos despues de cotizar, revisar precio")

        if aceptacion_trabada:
            alertas.append(aceptacion_trabada)

        ultimo = mensajes[-1]
        silencio = ahora - ultimo.sent_at
        dias = silencio.days
        if estado in _ABIERTOS or estado is JobState.DRAFT:
            if ultimo.from_customer and estado is not JobState.WAITING_CUSTOMER:
                alertas.append(f"el cliente escribio hace {_hace(silencio)} y espera respuesta")
            elif estado in _ABIERTOS and silencio >= self.politica.abandono:
                mover(
                    JobState.CANCELLED,
                    ultimo.sent_at + self.politica.abandono,
                    f"{_plural(dias, 'dia')} sin respuesta del cliente",
                    ultimo.id,
                )
            elif estado in _ABIERTOS and silencio >= self.politica.seguimiento:
                alertas.append(f"hace {_hace(silencio)} que el cliente no responde: hacer seguimiento")

        final = self.extractor.extract(Conversation(conv.chat_id, mensajes, conv.transcripts))
        job = final.job
        job.state = estado
        job.created_at = mensajes[0].sent_at
        return Evaluacion(
            job=job,
            extraccion=final,
            eventos=eventos,
            alertas=alertas,
            preguntas=preguntas(final, estado),
            presupuesto=self.pricing.estimate(job),
        )

    def _impide_confirmar(self, ex: Extraccion) -> list[str]:
        """Por que no se puede confirmar todavia. Vacio = se puede."""
        motivos = []
        for campo in REQUIRED_FOR_CONFIRMATION:
            ganador = ex.ganadores.get(campo)
            if ganador is None:
                motivos.append(f"falta {ETIQUETAS[campo]}")
            elif ganador.duda or ganador.slot.confidence < self.politica.confianza_minima:
                motivos.append(f"{ETIQUETAS[campo]} con duda")
        return motivos


def _hace(delta: timedelta) -> str:
    if delta.days >= 1:
        return _plural(delta.days, "dia")
    horas = delta.seconds // 3600
    return _plural(horas, "hora") if horas else _plural(delta.seconds // 60, "minuto")


def _plural(n: int, palabra: str) -> str:
    return f"{n} {palabra}" if n == 1 else f"{n} {palabra}s"
