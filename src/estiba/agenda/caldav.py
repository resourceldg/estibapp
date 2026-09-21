"""Espejo de la agenda en un servidor CalDAV (Radicale, Nextcloud, ...).

Opcional: se activa con ESTIBA_CALDAV_URL (la URL del calendario),
ESTIBA_CALDAV_USUARIO y ESTIBA_CALDAV_CLAVE. La agenda propia sigue siendo
la fuente de verdad; aca solo se copia lo que cambio desde la ultima vez.
Si el servidor no responde, no se pierde nada: se reintenta en la proxima
sincronizacion.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime

from estiba.agenda.evento import Evento, calendario


@dataclass(frozen=True)
class ConfigCalDAV:
    url: str
    usuario: str | None = None
    clave: str | None = None

    @classmethod
    def desde_entorno(cls) -> ConfigCalDAV | None:
        url = os.environ.get("ESTIBA_CALDAV_URL")
        if not url:
            return None
        return cls(url, os.environ.get("ESTIBA_CALDAV_USUARIO"), os.environ.get("ESTIBA_CALDAV_CLAVE"))


class EspejoCalDAV:
    def __init__(self, config: ConfigCalDAV) -> None:
        self.config = config
        self._calendario = None

    def _cal(self):
        if self._calendario is None:
            try:
                import caldav
            except ImportError as e:
                raise RuntimeError("falta la libreria caldav: uv sync --extra calendario") from e
            cliente = caldav.DAVClient(
                url=self.config.url, username=self.config.usuario, password=self.config.clave
            )
            self._calendario = cliente.calendar(url=self.config.url)
        return self._calendario

    def reflejar(self, evento: Evento, cancelado: bool, estampa: datetime) -> None:
        """Crea, actualiza o borra el evento en el servidor. Idempotente."""
        from caldav.lib.error import NotFoundError

        cal = self._cal()
        try:
            existente = cal.event_by_uid(evento.uid)
        except NotFoundError:
            existente = None
        if cancelado:
            if existente is not None:
                existente.delete()
            return
        ical = calendario([(evento, False)], estampa)
        if existente is None:
            cal.save_event(ical)
        else:
            existente.data = ical
            existente.save()
