"""SlotExtractor: de una conversacion entera a un FreightJob con evidencia.

Corre sobre el hilo completo cada vez, nunca de forma incremental: los datos
llegan fuera de orden y se corrigen ("no es mas a San Telmo, es a Barracas").
Para cada campo gana la ultima mencion; las anteriores quedan en
`candidatos` para poder ver que se dijo y cuando.

De quien sale cada dato:

- Lo del flete (lugares, cosas, fecha, pisos, ayudantes) sale del cliente.
- El precio sale del fletero: es quien cotiza.
- Una fecha u hora que propone el fletero con una pregunta ("9 de la manana
  te queda bien?") cuenta solo si el cliente la acepta en su respuesta.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from estiba.core.models import (
    Conversation,
    Evidence,
    ExtractionMethod,
    FreightJob,
    Message,
    Slot,
)
from estiba.extraction import fechas, reglas
from estiba.extraction.texto import sin_conversacion_pegada
from estiba.extraction.intencion import Intencion, IntencionDetectada, intencion
from estiba.extraction.texto import Hallazgo

_REGLAS_CLIENTE = (reglas.lugares, reglas.items, reglas.pisos, reglas.ayudantes, fechas.horarios)
_PROPONIBLES = {"when", "hour", "daypart"}


@dataclass(frozen=True)
class Candidato:
    """Una mencion de un dato, gane o no."""

    campo: str
    slot: Slot
    duda: str | None = None


@dataclass
class Extraccion:
    job: FreightJob
    candidatos: list[Candidato] = field(default_factory=list)
    intenciones: list[IntencionDetectada] = field(default_factory=list)
    #: Audios sin transcribir: puede faltar un dato que esta ahi adentro.
    sin_texto: list[str] = field(default_factory=list)
    #: El candidato que gano en cada campo.
    ganadores: dict[str, Candidato] = field(default_factory=dict)
    #: Cosas que se dijeron pero no alcanzan para llenar un campo.
    sin_resolver: list[Candidato] = field(default_factory=list)

    @property
    def dudas(self) -> list[str]:
        """Lo que hay que preguntarle al cliente antes de dar un dato por bueno."""
        dudas = [c.duda for c in self.ganadores.values() if c.duda]
        dudas += [c.duda for c in self.sin_resolver if c.duda]
        return dudas


def _slot(h: Hallazgo, m: Message, confianza: float | None = None) -> Slot:
    return Slot(
        value=h.valor,
        confidence=h.confianza if confianza is None else confianza,
        evidence=Evidence(message_id=m.id, quote=h.cita, method=ExtractionMethod.REGEX),
    )


class SlotExtractor:
    def extract(self, conv: Conversation, job_id: str | None = None) -> Extraccion:
        ex = Extraccion(job=FreightJob(id=job_id or f"{conv.chat_id}:job", conversation_id=conv.chat_id))
        # Lo que alguien reenvio o pego de otro chat no es lo que este cliente
        # esta pidiendo: la marca de hora de un chat exportado se leia como la
        # fecha del flete (un "[15/8, 12:24]" termino siendo 15/08/2027).
        textos = {
            m.id: sin_conversacion_pegada(t) if (t := conv.text_of(m)) else t
            for m in conv.messages
        }
        ex.sin_texto = [m.id for m in conv.messages if textos[m.id] is None]

        # Primero las intenciones: hacen falta para saber si el cliente
        # acepto lo que propuso el fletero.
        intenciones: dict[str, Intencion] = {}
        for m in conv.messages:
            texto = textos[m.id]
            if m.from_customer and texto and (hallada := intencion(texto)):
                intenciones[m.id] = hallada[0]
                ex.intenciones.append(IntencionDetectada(m.id, hallada[0], hallada[1]))

        for i, m in enumerate(conv.messages):
            texto = textos[m.id]
            if not texto:
                continue
            if m.from_customer:
                hallazgos = [h for regla in _REGLAS_CLIENTE for h in regla(texto)]
                hallazgos += fechas.fechas(texto, m.sent_at)
                for h in hallazgos:
                    self._anotar(ex, h, m)
                continue

            for h in reglas.precio(texto):
                self._anotar(ex, h, m, valor=Decimal(h.valor))
            if texto.rstrip().endswith("?") and self._acepta_lo_siguiente(conv, i, intenciones):
                propuestos = fechas.horarios(texto) + fechas.fechas(texto, m.sent_at)
                for h in propuestos:
                    if h.campo in _PROPONIBLES:
                        self._anotar(ex, h, m)

        self._resolver(ex)
        return ex

    @staticmethod
    def _acepta_lo_siguiente(conv: Conversation, i: int, intenciones: dict[str, Intencion]) -> bool:
        """El primer mensaje del cliente despues del `i` es una aceptacion."""
        for m in conv.messages[i + 1 :]:
            if m.from_customer:
                return intenciones.get(m.id) is Intencion.ACEPTA
        return False

    @staticmethod
    def _anotar(ex: Extraccion, h: Hallazgo, m: Message, valor: object = None) -> None:
        if h.valor is None:
            ex.sin_resolver.append(Candidato(h.campo, _slot(h, m), h.duda))
            return
        if valor is not None:
            h = Hallazgo(h.campo, valor, h.confianza, h.cita, h.duda)
        ex.candidatos.append(Candidato(h.campo, _slot(h, m), h.duda))

    @staticmethod
    def _resolver(ex: Extraccion) -> None:
        cosas: dict[str, reglas.Item] = {}
        ultimo_items: Candidato | None = None
        for c in ex.candidatos:
            if c.campo == "items":
                # Las cosas se suman entre mensajes; una cantidad nueva
                # reemplaza a la vieja ("unas cajas" -> "como quince cajas").
                for item in c.slot.value:
                    previo = cosas.get(item.nombre)
                    if previo is None or item.cantidad is not None:
                        cosas[item.nombre] = item
                ultimo_items = c
            else:
                ex.ganadores[c.campo] = c

        if ultimo_items is not None:
            ex.ganadores["items"] = Candidato(
                "items",
                Slot(
                    value=[str(i) for i in cosas.values()],
                    confidence=ultimo_items.slot.confidence,
                    evidence=ultimo_items.slot.evidence,
                ),
                ultimo_items.duda,
            )
        for campo, c in ex.ganadores.items():
            setattr(ex.job, campo, c.slot)
