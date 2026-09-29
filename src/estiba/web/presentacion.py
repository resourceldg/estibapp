"""Del dominio a lo que se muestra.

Las paginas HTML y la API JSON salen de las mismas funciones: lo que ve el
fletero en el celular y lo que devuelve /api son siempre la misma cosa.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta
from urllib.parse import quote

from estiba.core.models import Conversation, JobState
from estiba.extraction.fechas import hora_legible, nombre_dia
from estiba.formato import pesos, telefono
from estiba.jobs.engine import Evaluacion
from estiba.jobs.preguntas import ETIQUETAS

#: Como se llama cada estado en la pantalla, y con que tono.
#:
#: Una palabra por cosa, y la misma en toda la app. Antes el paso del
#: precio se llamaba "Presupuestado" en el chip, "Cotizado" en el grupo y
#: "Cotizar" en la pestaña, y esas dos ultimas estaban a un caracter de
#: distancia significando lo contrario: una queria decir "ya se cotizo" y
#: la otra "hay que cotizar".
#:
#: El tono tampoco puede significar dos cosas. "Nuevo" y "Realizado"
#: compartian `neutro`, asi que un flete que no habia empezado y uno
#: terminado se veian igual. Un trabajo hecho es la unica buena noticia
#: del embudo: lleva `hecho`, que es suyo y de nadie mas.
ESTADOS = {
    JobState.DRAFT: ("Nuevo", "neutro"),
    JobState.QUOTED: ("Cotizado", "info"),
    JobState.WAITING_CUSTOMER: ("Esperando al cliente", "aviso"),
    JobState.CONFIRMED: ("Confirmado", "ok"),
    JobState.SCHEDULED: ("Agendado", "ok"),
    JobState.DONE: ("Realizado", "hecho"),
    JobState.CANCELLED: ("Perdido", "mal"),
}

TIPOS_BORRADOR = {
    "respuesta": "Te toca responder",
    "preguntas": "Faltan datos",
    "cotizacion": "Para cotizar",
    "confirmacion": "Para confirmar",
    "seguimiento": "Seguimiento",
}

#: Lo que no es texto ni audio: la burbuja no puede quedar en blanco.
ETIQUETAS_MEDIO = {"image": "Imagen", "other": "Adjunto"}

#: Mas que esto no se lee de un vistazo: se pliega y se abre si hace falta.
LARGO_PARA_PLEGAR = 320

#: La proxima accion: una sola por conversacion, la que el contexto pide.
#: Estiba propone, decide una persona. (etiqueta, icono)
ACCIONES = {
    "respuesta": ("Responder", "chat"),
    "preguntas": ("Pedir datos", "chat"),
    "cotizacion": ("Preparar cotización", "precio"),
    "confirmacion": ("Confirmar flete", "hecho"),
    "seguimiento": ("Hacer seguimiento", "chat"),
}
#: Cuando no hay nada que mandar, igual hay algo que decir de la conversacion.
ESPERANDO = ("Esperar respuesta", "reloj")

_ORDEN_DATOS = ("origin", "destination", "when", "hour", "daypart", "items",
                "floors_no_lift", "helpers", "quoted_price")


def puede_realizarse(ev: Evaluacion) -> bool:
    """Abierto, o perdido solo porque el chat se callo.

    El flete de Alex del 21/09 se hizo, pero despues de reabrirlo para
    corregir lo cobrado ya no se podia volver a cerrar: cuatro dias sin
    mensajes lo daban por perdido, y un perdido no se marcaba realizado.
    """
    if ev.job.state not in (JobState.DONE, JobState.CANCELLED):
        return True
    return ev.job.state is JobState.CANCELLED and ev.perdido_por_silencio


def fecha_corta(d: date) -> str:
    return f"{nombre_dia(d)[:3]} {d:%d/%m}"


def hace(desde: datetime, ahora: datetime) -> str:
    segundos = int((ahora - desde).total_seconds())
    if segundos < 60:
        return "recién"
    if segundos < 3600:
        return f"hace {segundos // 60} min"
    if segundos < 86400:
        return f"hace {segundos // 3600} h"
    dias = segundos // 86400
    return "ayer" if dias == 1 else f"hace {dias} días"


def cuando_vuelve(hasta: datetime, ahora: datetime) -> str:
    """"vuelve manana 09:00", "vuelve en 2 h": cuando reaparece una pospuesta."""
    minutos = int((hasta - ahora).total_seconds() // 60)
    if minutos < 60:
        return f"en {max(minutos, 1)} min"
    if hasta.date() == ahora.date():
        return f"hoy {hasta:%H:%M}"
    if hasta.date() == (ahora + timedelta(days=1)).date():
        return f"mañana {hasta:%H:%M}"
    return f"el {fecha_corta(hasta.date())} {hasta:%H:%M}"


def ancla(message_id: str) -> str:
    """Id de HTML para un mensaje: los ':' no se llevan bien con los selectores."""
    return "m-" + re.sub(r"[^A-Za-z0-9_-]", "-", message_id)


def whatsapp(chat_id: str, texto: str) -> str:
    return f"https://wa.me/{chat_id}?text={quote(texto)}"


def _mostrar(campo: str, valor: object) -> str:
    if isinstance(valor, list):
        return ", ".join(map(str, valor))
    if isinstance(valor, date):
        return f"{nombre_dia(valor)} {valor:%d/%m}"
    if isinstance(valor, time):
        return f"{hora_legible(valor)} h"
    if campo == "daypart":
        return {"manana": "mañana"}.get(str(valor), str(valor))
    if campo == "quoted_price":
        return pesos(valor)
    if campo == "floors_no_lift":
        return "con ascensor" if valor == 0 else f"{valor} por escalera"
    return str(valor)


@dataclass
class Dato:
    campo: str
    etiqueta: str
    valor: str
    confianza: float
    cita: str
    message_id: str
    ancla: str
    duda: str | None
    #: El dato salio de un audio que Whisper no entendio bien. Es el caso
    #: peligroso: en la base real hay un flete agendado el 30/09 a las 11, y
    #: las dos cosas salieron de audios con 0.46 y 0.51 de confianza, rotos
    #: los dos. Una transcripcion mala se nota leyendola; una fecha mal
    #: entendida se nota el dia que vas.
    de_audio_dudoso: bool = False


@dataclass
class Evento:
    cuando: str
    hacia: str
    tono: str
    causa: str
    ancla: str | None


@dataclass
class Mensaje:
    id: str
    ancla: str
    del_cliente: bool
    hora: str
    texto: str | None
    es_audio: bool
    medio: str | None = None  # "Imagen", "Adjunto"...: para lo que no es texto ni audio
    #: Un muro de texto pegado o un audio largo tapan la conversacion entera:
    #: se muestran cortados y se abren si hace falta.
    largo: bool = False
    #: Hay archivo de audio guardado: se puede escuchar el original.
    escuchable: bool = False
    #: Whisper no entendio bien: lo que dice la burbuja puede no ser lo que
    #: se dijo. El original esta al lado para salir de la duda.
    transcripcion_dudosa: bool = False
    datos: list[str] = field(default_factory=list)  # que datos salieron de este mensaje


@dataclass
class Borrador:
    id: int
    job_id: str
    chat_id: str
    tipo: str
    tipo_etiqueta: str
    razon: str
    texto: str
    estado: str
    texto_final: str | None
    whatsapp: str | None
    enviado: bool = False
    #: Salio sin que nadie lo aprobara. Es la unica excepcion a la regla de
    #: la app, asi que se dice en la pantalla en vez de disimularse.
    automatico: bool = False
    error_envio: str | None = None
    #: Lo que dice el mensaje que sale, para poder tocarlo sin editar texto.
    monto: int | None = None

    @property
    def accion(self) -> str:
        """Que haria Estiba con esto. Una sola, la del contexto."""
        return ACCIONES.get(self.tipo, (self.tipo_etiqueta, "chat"))[0]

    @property
    def accion_icono(self) -> str:
        return ACCIONES.get(self.tipo, (self.tipo_etiqueta, "chat"))[1]


@dataclass
class Flete:
    job_id: str
    #: Para nombrarlo en voz alta: "el 12". None mientras es una consulta.
    numero: int | None
    chat_id: str
    nombre: str | None
    telefono: str
    estado: str
    estado_etiqueta: str
    tono: str
    origen: str | None
    destino: str | None
    fecha: str | None
    fecha_iso: str | None
    dia: str | None
    dia_semana: str | None
    hora: str | None
    cosas: str | None
    cotizado: str | None
    #: El mismo numero sin formato, para precargar el campo de "cuanto
    #: cobraste". Antes el precio cotizado se mostraba como `placeholder`:
    #: parecia estar cargado y se mandaba vacio, asi que lo cobrado se
    #: perdia en silencio y el precio aprendido se quedaba sin su dato.
    cotizado_crudo: str | None
    sugerido: str | None
    sugerido_completo: bool
    alertas: list[str]
    ultimo: str
    ultimo_texto: str | None
    ultimo_del_cliente: bool
    ultimo_at: str
    pasado: bool

    @property
    def ruta(self) -> str:
        return f"{self.origen or '¿?'} → {self.destino or '¿?'}"

    @property
    def quien(self) -> str:
        return self.nombre or self.telefono

    @property
    def etapa(self) -> str:
        """En que punto de la venta esta, dicho como lo diria el fletero.

        El estado interno dice "borrador" o "esperando_cliente"; lo que
        importa de un vistazo es si hay plata en juego y de que lado.
        """
        if self.estado == JobState.DONE.value:
            return "vendido"
        if self.estado == JobState.CANCELLED.value:
            return "perdido"
        if self.estado in (JobState.CONFIRMED.value, JobState.SCHEDULED.value):
            return "cerrado-falta-hacer"
        if self.estado in (JobState.QUOTED.value, JobState.WAITING_CUSTOMER.value):
            return "cotizado"
        return "sin-cotizar"

    @property
    def cerrado(self) -> bool:
        """El flete termino: realizado o perdido.

        La conversacion sigue existiendo y el cliente puede volver, pero
        esto ya no es trabajo pendiente y no tiene que pesar en la lista.
        """
        return self.estado in (JobState.DONE.value, JobState.CANCELLED.value)

    @property
    def titulo(self) -> str:
        """Para listas: la ruta si ya se sabe, si no quien escribe.

        "¿? → ¿?" no dice nada de un lead nuevo, y son la mayoria: con
        varios en la lista, todos se ven iguales.
        """
        return self.ruta if (self.origen or self.destino) else self.quien


@dataclass
class DetalleFlete:
    flete: Flete
    datos: list[Dato]
    faltan: list[str]
    dudas: list[str]
    preguntas: list[str]
    desglose: list[tuple[str, str]]
    sin_contar: list[str]
    historia: list[Evento]
    mensajes: list[Mensaje]
    borradores: list[Borrador]
    otros: list[Flete]
    #: Cerrarlo o darlo por perdido se puede casi siempre: hay fletes que se
    #: arreglan por telefono y nunca dejan rastro en el chat.
    puede_realizar: bool
    puede_perder: bool
    #: Pero que se pueda no quiere decir que sea lo que sigue. Cerrar es el
    #: final del embudo: solo es la accion principal cuando el cliente ya
    #: dijo que si. Antes la accion principal de un lead que acababa de
    #: escribir "hola" era "Marcar realizado", medido en 2 conversaciones
    #: reales. Cuando no es lo que sigue, la accion existe igual, plegada
    #: mas abajo junto con "Dar por perdido".
    cerrar_es_lo_que_sigue: bool
    #: Esta cerrado, asi que lo unico que queda es poder deshacerlo. Cerrar
    #: era irreversible: un toque equivocado en la agenda dejaba el flete
    #: fuera del embudo para siempre.
    puede_reabrir: bool


def flete(job_id: str, ev: Evaluacion, conv: Conversation, ahora: datetime,
          nombre: str | None = None, numero: int | None = None) -> Flete:
    job = ev.job
    etiqueta, tono = ESTADOS[job.state]
    mensajes = [m for m in conv.messages if m.sent_at <= ahora]
    ultimo = mensajes[-1]
    cuando = job.when.value if job.when else None
    hora = None
    if job.hour:
        hora = f"{hora_legible(job.hour.value)} h"
    elif job.daypart:
        hora = _mostrar("daypart", job.daypart.value)
    return Flete(
        job_id=job_id,
        numero=numero,
        chat_id=conv.chat_id,
        nombre=nombre,
        telefono=telefono(conv.chat_id),
        estado=job.state.value,
        estado_etiqueta=etiqueta,
        tono=tono,
        origen=job.origin.value if job.origin else None,
        destino=job.destination.value if job.destination else None,
        fecha=fecha_corta(cuando) if cuando else None,
        fecha_iso=cuando.isoformat() if cuando else None,
        dia=f"{cuando.day}" if cuando else None,
        dia_semana=nombre_dia(cuando)[:3] if cuando else None,
        hora=hora,
        cosas=", ".join(job.items.value) if job.items else None,
        cotizado=pesos(job.quoted_price.value) if job.quoted_price else None,
        cotizado_crudo=f"{int(job.quoted_price.value)}" if job.quoted_price else None,
        sugerido=pesos(ev.presupuesto.total) if ev.presupuesto else None,
        sugerido_completo=bool(ev.presupuesto and ev.presupuesto.completo),
        alertas=ev.alertas,
        ultimo=hace(ultimo.sent_at, ahora),
        ultimo_texto=conv.text_of(ultimo),
        ultimo_del_cliente=ultimo.from_customer,
        ultimo_at=ultimo.sent_at.isoformat(),
        pasado=bool(cuando and cuando < ahora.date()),
    )


def monto_del_texto(texto: str | None) -> int | None:
    """El precio que dice el mensaje, para poder tocarlo sin editar texto."""
    if not texto:
        return None
    m = re.search(r"\$\s?([\d.]+)", texto)
    if not m:
        return None
    try:
        return int(m.group(1).replace(".", ""))
    except ValueError:
        return None


def borrador(fila: sqlite3.Row) -> Borrador:
    texto_final = fila["final_text"]
    return Borrador(
        id=fila["id"],
        job_id=fila["job_id"],
        chat_id=fila["chat_id"],
        tipo=fila["kind"],
        tipo_etiqueta=TIPOS_BORRADOR.get(fila["kind"], fila["kind"]),
        razon=fila["reason"],
        texto=fila["text"],
        estado=fila["status"],
        texto_final=texto_final,
        whatsapp=whatsapp(fila["chat_id"], texto_final) if texto_final else None,
        enviado=bool(fila["sent_at"]),
        automatico=bool(fila["auto"]) if "auto" in fila.keys() else False,
        error_envio=fila["send_error"],
        monto=monto_del_texto(fila["text"]),
    )


def detalle(
    job_id: str,
    ev: Evaluacion,
    conv: Conversation,
    ahora: datetime,
    borradores: list[sqlite3.Row],
    nombre: str | None = None,
    otros: list[Flete] | None = None,
) -> DetalleFlete:
    ex = ev.extraccion
    datos = []
    por_mensaje: dict[str, list[str]] = {}
    for campo in _ORDEN_DATOS:
        c = ex.ganadores.get(campo)
        if c is None:
            continue
        mid = c.slot.evidence.message_id
        datos.append(Dato(
            campo=campo,
            etiqueta=ETIQUETAS[campo].capitalize(),
            valor=_mostrar(campo, c.slot.value),
            confianza=c.slot.confidence,
            cita=c.slot.evidence.quote,
            message_id=mid,
            ancla=ancla(mid),
            duda=c.duda,
            de_audio_dudoso=(t.dudosa if (t := conv.transcripts.get(mid)) else False),
        ))
        por_mensaje.setdefault(mid, []).append(ETIQUETAS[campo])

    historia = []
    for e in ev.eventos:
        etiqueta, tono = ESTADOS[e.hacia]
        historia.append(Evento(
            cuando=f"{fecha_corta(e.at.date())} {e.at:%H:%M}",
            hacia=etiqueta,
            tono=tono,
            causa=e.causa,
            ancla=ancla(e.message_id) if e.message_id else None,
        ))

    mensajes = []
    for m in conv.messages:
        if m.sent_at > ahora:
            continue
        mensajes.append(Mensaje(
            id=m.id,
            ancla=ancla(m.id),
            del_cliente=m.from_customer,
            hora=f"{fecha_corta(m.sent_at.date())} {m.sent_at:%H:%M}",
            texto=conv.text_of(m),
            es_audio=m.kind.value == "audio",
            medio=ETIQUETAS_MEDIO.get(m.kind.value),
            largo=len(conv.text_of(m) or "") > LARGO_PARA_PLEGAR,
            escuchable=bool(m.media_path),
            transcripcion_dudosa=(t.dudosa if (t := conv.transcripts.get(m.id)) else False),
            datos=por_mensaje.get(m.id, []),
        ))

    p = ev.presupuesto
    return DetalleFlete(
        flete=flete(job_id, ev, conv, ahora, nombre),
        datos=datos,
        faltan=[ETIQUETAS[f] for f in ev.job.missing],
        dudas=ex.dudas,
        preguntas=ev.preguntas,
        desglose=[(c, pesos(m)) for c, m in p.desglose] if p else [],
        sin_contar=p.sin_contar if p else [],
        historia=historia,
        mensajes=mensajes,
        borradores=[borrador(b) for b in borradores],
        otros=otros or [],
        puede_realizar=puede_realizarse(ev),
        puede_perder=ev.job.state not in (JobState.DONE, JobState.CANCELLED),
        cerrar_es_lo_que_sigue=ev.job.state in (JobState.CONFIRMED, JobState.SCHEDULED),
        # Perdido por silencio no tiene un cierre que deshacer: lo que se
        # ofrece es marcarlo realizado, que es lo que paso si se hizo.
        puede_reabrir=ev.job.state is JobState.DONE
        or (ev.job.state is JobState.CANCELLED and not ev.perdido_por_silencio),
    )


# --------------------------------------------------------------------------
# Agenda
# --------------------------------------------------------------------------


@dataclass
class ItemAgenda:
    job_id: str
    hora: str
    aproximado: bool
    ruta: str
    cosas: str | None
    precio: str | None
    #: Sin formato: precarga el campo al cerrar el flete desde la agenda.
    precio_crudo: str | None
    quien: str
    estado_etiqueta: str
    tono: str
    #: La fecha se habló pero el cliente no la cerró. Esta en la agenda
    #: para no perderla, no porque este vendida.
    tentativo: bool = False


@dataclass
class DiaAgenda:
    etiqueta: str
    fecha: str
    items: list[ItemAgenda]
    #: Para que la grilla del mes pueda llevar hasta este dia.
    iso: str = ""


@dataclass
class CeldaMes:
    """Un dia en la grilla del mes."""

    fecha: date
    dia: int
    #: De un mes vecino: se dibuja apagado para que la grilla quede entera.
    de_otro_mes: bool
    es_hoy: bool
    pasado: bool
    #: Cuantos fletes cerrados y cuantas fechas que nadie confirmo.
    confirmados: int
    tentativos: int

    @property
    def total(self) -> int:
        return self.confirmados + self.tentativos

    @property
    def enlazable(self) -> bool:
        """Si tocar el dia lleva a algun lado.

        La lista de abajo solo tiene los dias que vienen, asi que un dia
        pasado con marca enlazaba a un ancla que no existe: se tocaba y no
        pasaba nada. La marca se sigue viendo —el mes cuenta lo que pasó
        tambien— pero no promete un salto que no puede cumplir.
        """
        return self.total > 0 and not self.pasado

    @property
    def iso(self) -> str:
        return self.fecha.isoformat()


@dataclass
class Mes:
    """El mes que se esta mirando, y como moverse a los de al lado."""

    etiqueta: str          # "septiembre 2026"
    semanas: list[list[CeldaMes]]
    anterior: str          # "2026-08", para el enlace
    siguiente: str
    #: El mes de hoy, para el boton de volver. None si ya se esta mirando.
    hoy: str | None


@dataclass
class Agenda:
    para_cerrar: list[ItemAgenda]
    dias: list[DiaAgenda]
    url_ics: str
    url_webcal: str
    mes: Mes | None = None


#: La semana empieza el lunes, como el calendario de pared de cualquier casa.
DIAS_SEMANA = ["L", "M", "M", "J", "V", "S", "D"]

MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
         "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


def mes_de(eventos, cual: date, hoy: date) -> Mes:
    """La grilla del mes, con cuantos fletes cae en cada dia.

    Una lista de dias dice que viene; una grilla dice donde estan los
    huecos, que es lo que se mira cuando alguien pregunta "¿el jueves
    podes?". Por eso la grilla no reemplaza la lista: van juntas.
    """
    porque: dict[date, list[bool]] = {}
    for e, cancelado in eventos:
        if cancelado:
            continue
        dia = e.inicio if not isinstance(e.inicio, datetime) else e.inicio.date()
        porque.setdefault(dia, []).append(e.tentativo)

    primero = cual.replace(day=1)
    # Lunes de la semana en la que cae el 1: la grilla arranca ahi para que
    # cada columna sea siempre el mismo dia de la semana.
    arranque = primero - timedelta(days=primero.weekday())
    siguiente_mes = (primero + timedelta(days=32)).replace(day=1)
    ultimo = siguiente_mes - timedelta(days=1)
    cierre = ultimo + timedelta(days=6 - ultimo.weekday())

    semanas, semana, d = [], [], arranque
    while d <= cierre:
        marcas = porque.get(d, [])
        semana.append(CeldaMes(
            fecha=d,
            dia=d.day,
            de_otro_mes=d.month != primero.month,
            es_hoy=d == hoy,
            pasado=d < hoy,
            confirmados=sum(1 for t in marcas if not t),
            tentativos=sum(1 for t in marcas if t),
        ))
        if len(semana) == 7:
            semanas.append(semana)
            semana = []
        d += timedelta(days=1)

    anterior = (primero - timedelta(days=1)).replace(day=1)
    este_mes = f"{hoy:%Y-%m}"
    return Mes(
        etiqueta=f"{MESES[primero.month - 1]} {primero.year}",
        semanas=semanas,
        anterior=f"{anterior:%Y-%m}",
        siguiente=f"{siguiente_mes:%Y-%m}",
        hoy=None if f"{primero:%Y-%m}" == este_mes else este_mes,
    )


def agenda(eventos, fletes: dict[str, Flete], ahora: datetime, url_ics: str,
           mirando: date | None = None) -> Agenda:
    hoy = ahora.date()
    para_cerrar: list[ItemAgenda] = []
    por_dia: dict[date, list[ItemAgenda]] = {}
    for e, _cancelado in eventos:
        f = fletes.get(e.job_id)
        if f is None:
            continue
        dia = e.inicio if not isinstance(e.inicio, datetime) else e.inicio.date()
        if e.todo_el_dia:
            hora = "Sin hora"
        else:
            hora = f"{'~' if e.aproximado else ''}{hora_legible(e.inicio.time())} h"
        item = ItemAgenda(
            job_id=e.job_id, hora=hora, aproximado=e.aproximado, ruta=f.ruta, cosas=f.cosas,
            precio=f.cotizado, precio_crudo=f.cotizado_crudo,
            quien=f.quien, estado_etiqueta=f.estado_etiqueta, tono=f.tono,
            tentativo=e.tentativo,
        )
        if dia < hoy:
            if f.estado == JobState.SCHEDULED.value:
                para_cerrar.append(item)
            continue
        por_dia.setdefault(dia, []).append(item)

    dias = []
    for dia in sorted(por_dia):
        if dia == hoy:
            etiqueta = "Hoy"
        elif dia == hoy + timedelta(days=1):
            etiqueta = "Mañana"
        else:
            etiqueta = nombre_dia(dia).capitalize()
        dias.append(DiaAgenda(etiqueta, f"{dia:%d/%m}",
                              sorted(por_dia[dia], key=lambda i: i.hora), dia.isoformat()))
    webcal = "webcal://" + url_ics.split("://", 1)[1]
    return Agenda(para_cerrar, dias, url_ics, webcal,
                  mes=mes_de(eventos, mirando or hoy, hoy))


# --------------------------------------------------------------------------
# Numeros
# --------------------------------------------------------------------------


@dataclass
class Kpi:
    etiqueta: str
    valor: str
    nota: str


@dataclass
class BarraEmbudo:
    etiqueta: str
    cantidad: int
    ancho: float  # 0..100, relativo a las consultas
    porcentaje: str  # del total
    paso: str | None  # conversion desde la etapa anterior


@dataclass
class BarraMotivo:
    motivo: str
    cantidad: int
    ancho: float


@dataclass
class Numeros:
    consultas: int
    kpis: list[Kpi]
    embudo: list[BarraEmbudo]
    perdidos: int
    motivos: list[BarraMotivo]
    clientes: int
    recurrentes: int


def _duracion(d: timedelta | None) -> str:
    if d is None:
        return "—"
    minutos = int(d.total_seconds() // 60)
    if minutos < 60:
        return f"{minutos} min"
    horas, resto = divmod(minutos, 60)
    return f"{horas} h {resto:02d}" if horas < 24 else f"{horas // 24} días"


def numeros(e) -> Numeros:
    consultas = e.pasos[0].cantidad if e.pasos else 0
    kpis = [
        Kpi("Facturado", pesos(e.facturado), "fletes realizados"),
        Kpi("En agenda", pesos(e.comprometido), "confirmados por hacer"),
        Kpi("Ticket promedio", pesos(e.ticket_promedio) or "—", "por flete vendido"),
        Kpi("Primera respuesta", _duracion(e.respuesta_mediana), "mediana"),
    ]
    if e.desvio_precio is not None:
        signo = "+" if e.desvio_precio >= 0 else "−"
        kpis.append(Kpi("Tu precio vs. sugerido", f"{signo}{abs(e.desvio_precio):.0%}",
                        "cotizaste más caro" if e.desvio_precio > 0.02
                        else "cotizaste más barato" if e.desvio_precio < -0.02 else "casi igual"))
    embudo = [
        BarraEmbudo(
            etiqueta=p.etiqueta,
            cantidad=p.cantidad,
            ancho=100 * p.cantidad / consultas if consultas else 0,
            porcentaje=f"{p.del_total:.0%}",
            paso=f"{p.del_anterior:.0%} de la etapa anterior" if p.del_anterior is not None else None,
        )
        for p in e.pasos
    ]
    mayor = max((n for _, n in e.motivos), default=0)
    motivos = [BarraMotivo(m, n, 100 * n / mayor if mayor else 0) for m, n in e.motivos]
    return Numeros(consultas, kpis, embudo, e.perdidos, motivos, e.clientes, e.recurrentes)


# --------------------------------------------------------------------------
# Clientes
# --------------------------------------------------------------------------


@dataclass
class Cliente:
    chat_id: str
    nombre: str | None
    telefono: str
    fletes: int
    realizados: int
    facturado: str
    ultimo: str
    ultimo_job_id: str
    ultimo_ruta: str
    ultimo_estado: str
    tono: str


def clientes(leads, fletes: dict[str, Flete], nombres: dict[str, str], ahora: datetime) -> list[Cliente]:
    from estiba.core.models import Stage

    por_chat: dict[str, list] = {}
    for l in leads:
        por_chat.setdefault(l.chat_id, []).append(l)
    salida = []
    for chat_id, suyos in por_chat.items():
        suyos.sort(key=lambda l: l.creado)
        ultimo = suyos[-1]
        f = fletes.get(ultimo.job_id)
        hechos = [l for l in suyos if l.etapa is Stage.DONE]
        salida.append(Cliente(
            chat_id=chat_id,
            nombre=nombres.get(chat_id),
            telefono=telefono(chat_id),
            fletes=len(suyos),
            realizados=len(hechos),
            facturado=pesos(sum((l.cotizado or 0 for l in hechos), 0)),
            ultimo=hace(f.ultimo_at and datetime.fromisoformat(f.ultimo_at), ahora) if f else "",
            ultimo_job_id=ultimo.job_id,
            ultimo_ruta=f.ruta if f else "",
            ultimo_estado=f.estado_etiqueta if f else "",
            tono=f.tono if f else "neutro",
        ))
    return sorted(salida, key=lambda c: fletes[c.ultimo_job_id].ultimo_at if c.ultimo_job_id in fletes else "",
                  reverse=True)


# --------------------------------------------------------------------------
# WhatsApp
# --------------------------------------------------------------------------

SESIONES = {
    "WORKING": ("Conectado", "ok"),
    "SCAN_QR_CODE": ("Escaneá el QR", "aviso"),
    "STARTING": ("Arrancando…", "aviso"),
    "PASSKEY_REQUIRED": ("Confirmá en el celular", "aviso"),
    "PASSKEY_CONFIRMATION_REQUIRED": ("Confirmá en el celular", "aviso"),
    "STOPPED": ("Desconectado", "mal"),
    "FAILED": ("Falló la conexión", "mal"),
    "SIN_SESION": ("Sin conectar", "neutro"),
    "SIN_WAHA": ("WAHA no responde", "mal"),
}


@dataclass
class SesionWhatsApp:
    estado: str
    etiqueta: str
    tono: str


def sesion_whatsapp(estado: str | None) -> SesionWhatsApp:
    estado = estado or "SIN_SESION"
    etiqueta, tono = SESIONES.get(estado, (estado.title(), "neutro"))
    return SesionWhatsApp(estado, etiqueta, tono)


@dataclass
class VistaWhatsApp:
    configurado: bool
    sesion: SesionWhatsApp
    numero: str | None
    nombre: str | None
    url_waha: str | None
    #: Mientras no este conectado, la pagina se refresca sola.
    esperando: bool


def vista_whatsapp(whatsapp, info: dict | None, estado: str | None) -> VistaWhatsApp:
    s = sesion_whatsapp(estado)
    me = (info or {}).get("me") or {}
    numero = me.get("id", "").split("@")[0] or None
    return VistaWhatsApp(
        configurado=whatsapp is not None,
        sesion=s,
        numero=telefono(numero) if numero else None,
        nombre=me.get("pushName"),
        url_waha=whatsapp.cliente.config.url if whatsapp else None,
        esperando=s.estado in ("STARTING", "SCAN_QR_CODE", "PASSKEY_REQUIRED", "PASSKEY_CONFIRMATION_REQUIRED"),
    )


def a_json(obj) -> dict:
    datos = asdict(obj)
    if isinstance(obj, Flete):
        datos["ruta"] = obj.ruta
    return datos
