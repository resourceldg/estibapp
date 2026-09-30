"""Avisos en el escritorio, con sonido.

Estiba corre en la misma compu donde se usa, asi que el aviso lo manda el
servidor al escritorio de Linux (notify-send) y suena (canberra o
pipewire). Aparece aunque el navegador este cerrado o en otra pestaña, que
es justo cuando hace falta.

Si no hay escritorio (un servidor, los tests), no pasa nada: avisar nunca
puede romper la sincronizacion. Se apaga con ESTIBA_AVISOS=0.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess

log = logging.getLogger("estiba")

VARIABLE = "ESTIBA_AVISOS"
SONIDOS = {
    "normal": "message-new-instant",
    "urgente": "alarm-clock-elapsed",
}
_CARPETA_SONIDOS = "/usr/share/sounds/freedesktop/stereo"


def encendido() -> bool:
    return os.environ.get(VARIABLE, "1").strip().lower() not in ("0", "no", "off", "false")


class AvisoEscritorio:
    """notify-send + un sonido del tema freedesktop."""

    def __init__(self, ejecutar=subprocess.Popen) -> None:
        self._ejecutar = ejecutar

    def disponible(self) -> bool:
        return shutil.which("notify-send") is not None

    def avisar(self, titulo: str, cuerpo: str, urgente: bool = False, enlace: str | None = None) -> bool:
        if not encendido() or not self.disponible():
            return False
        texto = cuerpo + (f"\n{enlace}" if enlace else "")
        try:
            self._ejecutar(["notify-send", "--app-name=Estiba", "--icon=mail-message-new",
                            f"--urgency={'critical' if urgente else 'normal'}", titulo, texto],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self._sonar("urgente" if urgente else "normal")
            return True
        except OSError as e:
            log.warning("no se pudo avisar en el escritorio: %s", e)
            return False

    def _sonar(self, tipo: str) -> None:
        nombre = SONIDOS[tipo]
        if shutil.which("canberra-gtk-play"):
            orden = ["canberra-gtk-play", "-i", nombre, "-d", "Estiba"]
        elif shutil.which("pw-play"):
            orden = ["pw-play", f"{_CARPETA_SONIDOS}/{nombre}.oga"]
        else:
            return
        self._ejecutar(orden, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
