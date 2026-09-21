"""Formatos que se muestran igual en todos lados: CLI, web, calendario, mensajes."""

from __future__ import annotations

import re
from decimal import Decimal


def pesos(monto: Decimal | int | None) -> str | None:
    """45000 -> "$45.000"."""
    if monto is None:
        return None
    return f"${int(monto):,}".replace(",", ".")


def telefono(chat_id: str) -> str:
    """5491133334444 -> +54 9 11 3333-4444. Si no lo reconoce, lo deja como esta."""
    m = re.fullmatch(r"549(11)(\d{4})(\d{4})", chat_id)
    if m:
        return f"+54 9 {m.group(1)} {m.group(2)}-{m.group(3)}"
    return f"+{chat_id}" if chat_id.isdigit() else chat_id
