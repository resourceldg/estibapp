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
    #: La fecha se dijo pero el cliente todavia no confirmo. Va al
    #: calendario igual, como TENTATIVE: una fecha que alguien nombro y no
    #: esta en ningun lado es una fecha que se pasa. Pero no suena, porque
    #: no se madruga por un quizas.
    tentativo: bool = False

    @property
    def todo_el_dia(self) -> bool:
        return not isinstance(self.inicio, datetime)

    @property
    def fin(self) -> date | datetime:
        return self.inicio + (timedelta(days=1) if self.todo_el_dia else DURACION)


def uid_de(job_id: str) -> str:
    return f"{job_id.replace(':', '-')}@estiba"


def evento_de(job: FreightJob, tentativo: bool = False) -> Evento:
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

    # Sin ruta, "Flete ¿? → ¿?" en el calendario del celular no dice nada.
    # El telefono al menos deja saber de quien se trata.
    if job.origin or job.destination:
        que = (f"Flete {job.origin.value if job.origin else '¿?'} → "
               f"{job.destination.value if job.destination else '¿?'}")
    else:
        que = f"Flete de {telefono(job.conversation_id)}"
    # El titulo lo dice con palabras, porque el calendario del celular no
    # siempre distingue un TENTATIVE de un CONFIRMED de un vistazo.
    titulo = f"{'Sin confirmar · ' if tentativo else ''}{'~' if aproximado else ''}{que}"

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

    if tentativo:
        lineas.insert(0, "SIN CONFIRMAR: la fecha se habló pero el cliente no la cerró.")
    return Evento(uid_de(job.id), job.id, inicio, aproximado, titulo, "\n".join(lineas), tentativo)


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


#: Cuanto antes avisa el celular. Un flete no se prepara con cinco minutos:
#: hay que cargar, salir y llegar. La vispera a la tarde es para acomodar el
#: dia siguiente; las dos horas, para salir a tiempo.
AVISO_VISPERA = "-PT15H"   # desde un evento de todo el dia: 18 h del dia anterior
AVISO_ANTES = "-PT2H"
#: Lo que no se confirmo tambien avisa, pero distinto: la vispera a las 10,
#: para escribirle al cliente con tiempo, no para salir. Antes no avisaba
#: nada, y una fecha nombrada en un audio se pasaba sin que nadie la viera.
AVISO_TENTATIVO_DIA = "-PT14H"   # todo el dia: 10 h del dia anterior
AVISO_TENTATIVO_HORA = "-P1D"    # con hora: la misma hora del dia anterior


def _valarm(disparo: str, texto: str) -> list[str]:
    return [
        "BEGIN:VALARM",
        "ACTION:DISPLAY",
        f"TRIGGER:{disparo}",
        f"DESCRIPTION:{_escapar(texto)}",
        "END:VALARM",
    ]


def vevent(e: Evento, estampa: datetime, cancelado: bool = False) -> list[str]:
    """Un VEVENT, con alarma si es un flete de verdad.

    El .ics al que se suscribe el celular no tenia ninguna alarma: estaba
    suscripto y no sonaba nunca. Ahora suena. Lo tentativo avisa aparte:
    la vispera a la mañana, para confirmar con el cliente, no para salir.
    """
    tipo = ";VALUE=DATE" if e.todo_el_dia else ""
    estado = "CANCELLED" if cancelado else ("TENTATIVE" if e.tentativo else "CONFIRMED")
    lineas = [
        "BEGIN:VEVENT",
        f"UID:{e.uid}",
        f"DTSTAMP:{estampa.strftime('%Y%m%dT%H%M%S')}",
        f"DTSTART{tipo}:{_fecha_ical(e.inicio)}",
        f"DTEND{tipo}:{_fecha_ical(e.fin)}",
        f"SUMMARY:{_escapar(e.titulo)}",
        f"DESCRIPTION:{_escapar(e.descripcion)}",
        f"STATUS:{estado}",
    ]
    if not cancelado and not e.tentativo:
        lineas += _valarm(AVISO_VISPERA if e.todo_el_dia else AVISO_ANTES, e.titulo)
    elif not cancelado:
        texto = f"¿Se confirma? {e.titulo.removeprefix('Sin confirmar · ')}: escribile al cliente"
        lineas += _valarm(AVISO_TENTATIVO_DIA if e.todo_el_dia else AVISO_TENTATIVO_HORA, texto)
    lineas.append("END:VEVENT")
    return lineas


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
