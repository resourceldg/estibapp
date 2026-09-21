"""Fechas, horas y franjas dichas como se dicen en el Rio de la Plata.

Toda fecha relativa se resuelve contra la hora del mensaje que la contiene,
nunca contra "hoy": "el sabado" dicho un martes es el sabado de esa semana,
aunque la conversacion se procese un mes despues.

Las trampas que estas reglas tienen que respetar:

- "a la manana" es una franja, no "manana".
- "manana te confirmo" es posponer la decision, no una fecha.
- "el jueves que viene" dicho un jueves es ambiguo: sale con duda.
"""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta

from estiba.extraction.texto import Hallazgo, cita, plano

DIAS = {
    "lunes": 0, "martes": 1, "miercoles": 2, "jueves": 3,
    "viernes": 4, "sabado": 5, "domingo": 6,
}
NOMBRE_DIA = {v: k for k, v in DIAS.items()}
_CON_TILDE = {"miercoles": "miércoles", "sabado": "sábado"}


def nombre_dia(d: date) -> str:
    """El dia de la semana como se escribe, con tilde: para mostrar, no para buscar."""
    nombre = NOMBRE_DIA[d.weekday()]
    return _CON_TILDE.get(nombre, nombre)


def hora_legible(t: time) -> str:
    """9:00 -> "9", 14:30 -> "14:30"."""
    return f"{t.hour}" if t.minute == 0 else f"{t.hour}:{t.minute:02d}"
MESES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
}

_DIA = "(" + "|".join(DIAS) + ")"
_MES = "(" + "|".join(MESES) + ")"

_POSPONE = re.compile(
    r"\bmanana\s+(?:te|le|les)\s+(?:confirmo|aviso|digo|escribo|hablo|contesto)\b"
)
_DIA_Y_NUMERO = re.compile(rf"\b{_DIA}\s+(\d{{1,2}})\b")
_NUMERO_DE_MES = re.compile(rf"\b(\d{{1,2}})\s+de\s+{_MES}\b")
_BARRA = re.compile(r"\b(\d{1,2})\s*/\s*(\d{1,2})(?:\s*/\s*(\d{2,4}))?\b")
_EL_NUMERO = re.compile(r"\bel\s+(?:dia\s+)?(\d{1,2})\b(?=\s*(?:[?!.,]|$|a\s+la|por\s+la|entonces))")
_DIA_SEMANA = re.compile(
    rf"\b(?:(proximo)\s+)?{_DIA}\b(\s+que\s+viene|\s+proximo)?(?!\s+\d)"
)
_PASADO_MANANA = re.compile(r"\bpasado\s+manana\b")
_MANANA = re.compile(r"(?<!\bla\s)(?<!pasado\s)\bmanana\b")
_HOY = re.compile(r"\bhoy\b")

_FRANJA = re.compile(r"\b(?:a|por|de|en)\s+la\s+(manana|tarde|noche)\b")
_HORA_DE_LA = re.compile(r"\b(\d{1,2})(?:[:.](\d{2}))?\s+de\s+la\s+(manana|tarde|noche)\b")
_A_LAS = re.compile(r"\ba\s+las?\s+(\d{1,2})(?:[:.](\d{2}))?\b(?!\s*(?:cajas|sillas|mil))")
_HS = re.compile(r"\b(\d{1,2})(?:[:.](\d{2}))?\s*(?:hs|hrs|horas|h)\b")


def _fecha(dia: date, texto: str, m: re.Match, confianza: float, duda: str | None = None) -> Hallazgo:
    return Hallazgo("when", dia, confianza, cita(texto, m), duda)


def _dia_del_mes(ref: date, dia: int, mes: int | None = None) -> date | None:
    """El proximo `dia` (del `mes`, si se dijo) a partir de `ref`, incluido."""
    try:
        if mes is not None:
            candidata = date(ref.year, mes, dia)
            return candidata if candidata >= ref else date(ref.year + 1, mes, dia)
        candidata = date(ref.year, ref.month, dia)
        if candidata >= ref:
            return candidata
        siguiente = ref.replace(day=1) + timedelta(days=32)
        return date(siguiente.year, siguiente.month, dia)
    except ValueError:
        return None


def fechas(texto: str, enviado: datetime) -> list[Hallazgo]:
    """La fecha mas especifica que haya en el mensaje, si hay alguna."""
    p = plano(texto)
    ref = enviado.date()

    # 1. Dia del mes explicito: lo mas confiable.
    if m := _DIA_Y_NUMERO.search(p):
        dia = _dia_del_mes(ref, int(m.group(2)))
        if dia is not None:
            dicho = DIAS[m.group(1)]
            if dia.weekday() != dicho:
                return [_fecha(dia, texto, m, 0.5,
                               f"dijo {m.group(1)} {m.group(2)}, pero el {dia:%d/%m} "
                               f"es {NOMBRE_DIA[dia.weekday()]}")]
            return [_fecha(dia, texto, m, 0.9)]
    if m := _NUMERO_DE_MES.search(p):
        if (dia := _dia_del_mes(ref, int(m.group(1)), MESES[m.group(2)])) is not None:
            return [_fecha(dia, texto, m, 0.9)]
    if m := _BARRA.search(p):
        if 1 <= int(m.group(2)) <= 12:
            if (dia := _dia_del_mes(ref, int(m.group(1)), int(m.group(2)))) is not None:
                return [_fecha(dia, texto, m, 0.9)]
    if m := _EL_NUMERO.search(p):
        if (dia := _dia_del_mes(ref, int(m.group(1)))) is not None:
            return [_fecha(dia, texto, m, 0.8)]

    # 2. Dia de la semana.
    if m := _DIA_SEMANA.search(p):
        dicho = DIAS[m.group(2)]
        faltan = (dicho - ref.weekday()) % 7
        que_viene = bool(m.group(1) or m.group(3))
        if faltan == 0:
            dia = ref + timedelta(days=7)
            otra = ref if not que_viene else ref + timedelta(days=14)
            return [_fecha(dia, texto, m, 0.5,
                           f"'{cita(texto, m)}' dicho un {m.group(2)}: "
                           f"puede ser el {dia:%d/%m} o el {otra:%d/%m}")]
        dia = ref + timedelta(days=faltan)
        if que_viene:
            otra = dia + timedelta(days=7)
            return [_fecha(dia, texto, m, 0.6,
                           f"'{cita(texto, m)}': puede ser el {dia:%d/%m} o el {otra:%d/%m}")]
        return [_fecha(dia, texto, m, 0.8)]

    # 3. Relativas. "manana te confirmo" no es una fecha.
    if m := _PASADO_MANANA.search(p):
        return [_fecha(ref + timedelta(days=2), texto, m, 0.85)]
    for m in _MANANA.finditer(p):
        if _POSPONE.match(p, m.start()):
            continue
        return [_fecha(ref + timedelta(days=1), texto, m, 0.85)]
    if m := _HOY.search(p):
        return [_fecha(ref, texto, m, 0.85)]
    return []


def _hora(h: int, minutos: str | None, franja: str | None) -> time | None:
    if franja in ("tarde", "noche") and h < 12:
        h += 12
    if not 0 <= h <= 23:
        return None
    return time(h, int(minutos or 0))


def horarios(texto: str) -> list[Hallazgo]:
    """Hora exacta y franja del dia, si se dijeron."""
    p = plano(texto)
    hallados: list[Hallazgo] = []
    franja = _FRANJA.search(p)

    if m := _HORA_DE_LA.search(p):
        if (h := _hora(int(m.group(1)), m.group(2), m.group(3))) is not None:
            hallados.append(Hallazgo("hour", h, 0.9, cita(texto, m)))
    else:
        for patron in (_A_LAS, _HS):
            if m := patron.search(p):
                del_mensaje = franja.group(1) if franja else None
                if (h := _hora(int(m.group(1)), m.group(2), del_mensaje)) is not None:
                    # "a las 3" sin franja: casi seguro 15 hs, pero no se asume.
                    duda = None
                    if h.hour < 7 and del_mensaje is None:
                        duda = f"'{cita(texto, m)}': de la manana o de la tarde?"
                    hallados.append(
                        Hallazgo("hour", h, 0.8 if duda is None else 0.5, cita(texto, m), duda)
                    )
                break

    if franja:
        hallados.append(Hallazgo("daypart", franja.group(1), 0.85, cita(texto, franja)))
    return hallados
