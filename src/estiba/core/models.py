"""Tipos del dominio.

Este modulo no importa nada de WhatsApp, Whisper, Ollama ni CalDAV: esas cosas
viven detras de los puertos de `estiba.core.ports`.

La pieza que sostiene todo lo demas es `Slot`: ningun dato del negocio existe
sin saber de que mensaje salio y quien lo puso.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import Enum


# --------------------------------------------------------------------------
# Entrada normalizada: el nucleo nunca ve WhatsApp
# --------------------------------------------------------------------------


class MessageKind(Enum):
    TEXT = "text"
    AUDIO = "audio"
    IMAGE = "image"
    OTHER = "other"


@dataclass(frozen=True)
class Message:
    """Un mensaje, venga de donde venga, ya normalizado."""

    id: str
    chat_id: str
    from_customer: bool
    sent_at: datetime
    kind: MessageKind
    text: str | None = None
    media_path: str | None = None
    source: str = "fixture"  # "waha" | "fixture" | "cli"

    def __post_init__(self) -> None:
        if self.kind is MessageKind.TEXT and not self.text:
            raise ValueError(f"mensaje de texto {self.id} sin texto")
        if self.kind is MessageKind.AUDIO and not self.media_path:
            raise ValueError(f"mensaje de audio {self.id} sin media_path")


@dataclass(frozen=True)
class Transcript:
    """Resultado de pasar un audio por el AudioPipeline."""

    message_id: str
    text: str
    confidence: float
    model: str  # que modelo y version lo produjo, para poder re-transcribir
    duration_s: float


# --------------------------------------------------------------------------
# Trazabilidad
# --------------------------------------------------------------------------


class ExtractionMethod(Enum):
    REGEX = "regex"
    DATEPARSER = "dateparser"
    LLM = "llm"
    HUMAN = "human"


@dataclass(frozen=True)
class Evidence:
    """De donde salio un dato. Sin esto, un campo no se guarda."""

    message_id: str
    quote: str
    method: ExtractionMethod


@dataclass(frozen=True)
class Slot[T]:
    value: T
    confidence: float
    evidence: Evidence

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confianza fuera de rango: {self.confidence}")


# --------------------------------------------------------------------------
# El corazon del dominio
# --------------------------------------------------------------------------


class JobState(Enum):
    DRAFT = "borrador"
    QUOTED = "presupuestado"
    WAITING_CUSTOMER = "esperando_cliente"
    CONFIRMED = "confirmado"
    SCHEDULED = "agendado"
    DONE = "realizado"
    CANCELLED = "cancelado"


#: Transiciones permitidas. El JobEngine (Fase 4) es el unico que las aplica.
ALLOWED_TRANSITIONS: dict[JobState, frozenset[JobState]] = {
    JobState.DRAFT: frozenset({JobState.QUOTED, JobState.CANCELLED}),
    JobState.QUOTED: frozenset(
        {JobState.WAITING_CUSTOMER, JobState.CONFIRMED, JobState.CANCELLED}
    ),
    # El silencio no es ausencia de estado: es un estado con un reloj corriendo.
    JobState.WAITING_CUSTOMER: frozenset(
        {JobState.CONFIRMED, JobState.QUOTED, JobState.CANCELLED}
    ),
    JobState.CONFIRMED: frozenset({JobState.SCHEDULED, JobState.CANCELLED}),
    JobState.SCHEDULED: frozenset({JobState.DONE, JobState.CANCELLED}),
    JobState.DONE: frozenset(),
    JobState.CANCELLED: frozenset(),
}


def can_transition(origen: JobState, destino: JobState) -> bool:
    return destino in ALLOWED_TRANSITIONS[origen]


#: Campos sin los cuales un flete no se puede confirmar.
REQUIRED_FOR_CONFIRMATION = ("origin", "destination", "when", "items")


@dataclass
class FreightJob:
    id: str
    conversation_id: str
    state: JobState = JobState.DRAFT
    origin: Slot[str] | None = None
    destination: Slot[str] | None = None
    items: Slot[list[str]] | None = None
    when: Slot[datetime] | None = None
    floors_no_lift: Slot[int] | None = None
    helpers: Slot[int] | None = None
    quoted_price: Slot[Decimal] | None = None
    calendar_event_id: str | None = None
    created_at: datetime | None = None

    @property
    def missing(self) -> list[str]:
        """Que falta para poder confirmar. Nunca se inventa: se pregunta."""
        return [f for f in REQUIRED_FOR_CONFIRMATION if getattr(self, f) is None]


# --------------------------------------------------------------------------
# CRM, al costado del nucleo
# --------------------------------------------------------------------------


class Stage(Enum):
    NEW = "nuevo"
    QUALIFIED = "calificado"
    QUOTED = "presupuestado"
    CONFIRMED = "confirmado"
    DONE = "realizado"
    LOST = "perdido"


@dataclass
class Contact:
    """La persona. Estable en el tiempo, aunque te pida diez fletes.

    Separado de Lead a proposito: sin esto, el cliente que vuelve son tres
    leads sin relacion entre si y el ticket por cliente no existe.
    """

    id: str
    chat_id: str
    display_name: str | None = None
    first_seen: datetime | None = None


@dataclass
class Lead:
    """Un intento de venta. Un Contact puede tener varios a lo largo del ano."""

    id: str
    contact_id: str
    conversation_id: str
    stage: Stage = Stage.NEW
    click_id: str | None = None  # None = sin atribuir, jamas se reparte a ojo
    lost_reason: str | None = None
    created_at: datetime | None = None


@dataclass
class Campaign:
    id: str
    platform: str  # "meta"
    external_id: str
    period_start: date
    period_end: date
    spend: Decimal


# --------------------------------------------------------------------------
# Conversacion
# --------------------------------------------------------------------------


@dataclass
class Conversation:
    """El hilo con un contacto, reconstruido desde el log de mensajes."""

    chat_id: str
    messages: list[Message] = field(default_factory=list)
    transcripts: dict[str, Transcript] = field(default_factory=dict)

    def text_of(self, message: Message) -> str | None:
        """El texto utilizable de un mensaje: el propio, o el transcripto."""
        if message.text:
            return message.text
        t = self.transcripts.get(message.id)
        return t.text if t else None

    def as_plain_text(self) -> str:
        """La conversacion entera como texto.

        El SlotExtractor (Fase 3) corre sobre esto completo cada vez, no de
        forma incremental: los datos llegan fuera de orden ("somos 2, hay
        ascensor" tres mensajes despues de la direccion).
        """
        lineas = []
        for m in self.messages:
            quien = "cliente" if m.from_customer else "vos"
            texto = self.text_of(m)
            if texto is None:
                texto = f"[{m.kind.value} sin transcribir]"
            lineas.append(f"{quien}: {texto}")
        return "\n".join(lineas)
