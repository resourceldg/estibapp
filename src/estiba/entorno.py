"""Lee `.env` para que Estiba y docker compose usen la misma configuracion.

Lo que ya esta en el entorno gana: el `.env` solo completa lo que falta.
"""

from __future__ import annotations

import os
from pathlib import Path


def cargar_env(ruta: Path | str = ".env") -> list[str]:
    """Devuelve las variables que cargo."""
    ruta = Path(ruta)
    if not ruta.is_file():
        return []
    cargadas = []
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        clave, valor = (x.strip() for x in linea.split("=", 1))
        if len(valor) >= 2 and valor[0] == valor[-1] and valor[0] in "'\"":
            valor = valor[1:-1]
        if clave and clave not in os.environ:
            os.environ[clave] = valor
            cargadas.append(clave)
    return cargadas
