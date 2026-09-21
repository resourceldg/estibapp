"""Puertos: lo unico que el nucleo conoce del mundo exterior.

Cada uno tiene al menos dos implementaciones: una real y una de prueba. Si
manana WAHA muere o faster-whisper queda congelado del todo, se reescribe el
adaptador y el nucleo no se entera.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from datetime import date, datetime
from typing import Protocol, runtime_checkable

from estiba.core.models import Campaign, FreightJob, Message, Transcript


@runtime_checkable
class MessageSource(Protocol):
    """De aca entran los mensajes. Fixtures en Fase 1, WAHA en Fase 7."""

    def poll(self) -> Iterator[Message]:
        """Devuelve los mensajes nuevos desde la ultima llamada."""
        ...


@runtime_checkable
class Transcriber(Protocol):
    """Audio a texto, siempre local."""

    def transcribe(self, audio_path: str, message_id: str) -> Transcript: ...


@runtime_checkable
class CalendarProvider(Protocol):
    """Calendario. El evento se crea al final, nunca antes de confirmar."""

    def create(self, job: FreightJob) -> str:
        """Devuelve el id del evento creado."""
        ...

    def move(self, event_id: str, when: datetime) -> None: ...

    def cancel(self, event_id: str) -> None: ...


@runtime_checkable
class AdSpendProvider(Protocol):
    """Gasto publicitario. Puerto definido, sin implementacion todavia.

    Cuando el acceso a la API de Meta este resuelto, se enchufa un adaptador
    aca y nada mas cambia.
    """

    def spend(self, since: date, until: date) -> Iterable[Campaign]: ...
