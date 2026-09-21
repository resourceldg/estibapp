"""El evento de calendario de un flete, y como se escribe en iCalendar.

Si se sabe la hora, el evento dura DURACION desde esa hora. Si solo se sabe
la franja ("a la mañana"), se agenda a una hora tipica de esa franja y el
titulo lo avisa con "~". Si no hay ni hora ni franja, es de todo el dia: el
calendario no inventa una hora que nadie dijo.

Las horas van sin zona horaria ("flotantes"): el calendario del celular las
muestra en la hora local, que es la del negocio.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from estiba.core.models import FreightJob
from estiba.extraction.fechas import hora_legible
from estiba.formato import pesos, telefono

DURACION = timedelta(hours=3)
HORA_DE_FRANJA = {"manana": time(9), "tarde": time(14), "noche": time(19)}


@dataclass(frozen=True)
class Evento:
    uid: str
    job_id: str
    inicio: date | datetime  # date = todo el dia
    aproximado: bool
    titulo: str
    descripcion: str

    @property
    def todo_el_dia(self) -> bool:
        return not isinstance(self.inicio, datetime)

    @property
    def fin(self) -> date | datetime:
        return self.inicio + (timedelta(days=1) if self.todo_el_dia else DURACION)


def uid_de(job_id: str) -> str:
    return f"{job_id.replace(':', '-')}@estiba"


def evento_de(job: FreightJob) -> Evento:
    if job.when is None:
        raise ValueError(f"el flete {job.id} no tiene fecha: no se puede agendar")
    dia = job.when.value
    aproximado = False
    if job.hour is not None:
        inicio: date | datetime = datetime.combine(dia, job.hour.value)
    elif job.daypart is not None and job.daypart.value in HORA_DE_FRANJA:
        inicio = datetime.combine(dia, HORA_DE_FRANJA[job.daypart.value])
        aproximado = True
    else:
        inicio = dia

    ruta = f"{job.origin.value if job.origin else '¿?'} → {job.destination.value if job.destination else '¿?'}"
    titulo = f"{'~' if aproximado else ''}Flete {ruta}"

    lineas = []
    if job.items:
        lineas.append(f"Lleva: {', '.join(job.items.value)}")
    if job.floors_no_lift is not None:
        pisos = job.floors_no_lift.value
        lineas.append("Con ascensor" if pisos == 0 else f"{pisos} pisos por escalera")
    if job.helpers is not None and job.helpers.confidence >= 0.8:
        lineas.append(f"Ayudantes: {job.helpers.value}")
    if job.quoted_price:
        lineas.append(f"Precio: {pesos(job.quoted_price.value)}")
    if aproximado:
        lineas.append(f"Hora aproximada: dijo \"a la {job.daypart.value.replace('manana', 'mañana')}\"")
    elif job.hour is not None:
        lineas.append(f"Hora: {hora_legible(job.hour.value)} h")
    chat_id = job.conversation_id
    lineas.append(f"Cliente: {telefono(chat_id)} · https://wa.me/{chat_id}")

    return Evento(uid_de(job.id), job.id, inicio, aproximado, titulo, "\n".join(lineas))


# --------------------------------------------------------------------------
# iCalendar (RFC 5545), lo justo
# --------------------------------------------------------------------------


def _escapar(texto: str) -> str:
    return (
        texto.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")
    )


def _plegar(linea: str) -> str:
    """Las lineas de iCalendar no pasan de 75 bytes: se pliegan con CRLF + espacio."""
    crudo = linea.encode("utf-8")
    if len(crudo) <= 75:
        return linea
    partes, actual = [], b""
    for c in linea:
        b = c.encode("utf-8")
        if len(actual) + len(b) > (75 if not partes else 74):
            partes.append(actual.decode("utf-8"))
            actual = b""
        actual += b
    partes.append(actual.decode("utf-8"))
    return "\r\n ".join(partes)


def _fecha_ical(valor: date | datetime) -> str:
    if isinstance(valor, datetime):
        return valor.strftime("%Y%m%dT%H%M%S")
    return valor.strftime("%Y%m%d")


def vevent(e: Evento, estampa: datetime, cancelado: bool = False) -> list[str]:
    tipo = ";VALUE=DATE" if e.todo_el_dia else ""
    return [
        "BEGIN:VEVENT",
        f"UID:{e.uid}",
        f"DTSTAMP:{estampa.strftime('%Y%m%dT%H%M%S')}",
        f"DTSTART{tipo}:{_fecha_ical(e.inicio)}",
        f"DTEND{tipo}:{_fecha_ical(e.fin)}",
        f"SUMMARY:{_escapar(e.titulo)}",
        f"DESCRIPTION:{_escapar(e.descripcion)}",
        f"STATUS:{'CANCELLED' if cancelado else 'CONFIRMED'}",
        "END:VEVENT",
    ]


def calendario(eventos: list[tuple[Evento, bool]], estampa: datetime, nombre: str = "Fletes") -> str:
    """Un VCALENDAR con los eventos dados como (evento, cancelado)."""
    lineas = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Estiba//Fletes//ES",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_escapar(nombre)}",
    ]
    for e, cancelado in eventos:
        lineas += vevent(e, estampa, cancelado)
    lineas.append("END:VCALENDAR")
    return "\r\n".join(_plegar(x) for x in lineas) + "\r\n"
