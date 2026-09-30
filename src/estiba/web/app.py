"""Dashboard y API.

Una sola app: las paginas HTML (htmx, pensadas para el celular) y la API
JSON en /api, documentada en /docs. Las dos leen lo mismo: la ultima
sincronizacion, que corre al arrancar, cada vez que se toca "actualizar" y
despues de cada accion.

Nada de aca le escribe al cliente sin aprobacion. Aprobar un borrador lo
manda por WhatsApp si WAHA esta conectado; si no, abre WhatsApp en el
celular con el texto listo.
"""

from __future__ import annotations

import asyncio
import csv
import hashlib
import hmac
import io
import json
import logging
import re
import threading
from collections.abc import Callable
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from decimal import Decimal
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Literal

from fastapi import BackgroundTasks, FastAPI, Form, HTTPException, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
)
from fastapi.concurrency import run_in_threadpool
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from estiba.adapters.waha import MEDIA_DIR, ConfigWaha, ErrorWaha, WahaClient, firma_valida
from estiba.agenda.caldav import ConfigCalDAV, EspejoCalDAV
from estiba.agenda.evento import calendario
from estiba.agenda.local import LocalCalendar
from estiba.bandeja.automatico import encendido, sale_sola
from estiba.bandeja.servicio import Resultado, sincronizar
from estiba.conversation.store import ConversationStore
from estiba.core.models import JobState
from estiba.crm import embudo
from estiba.finanzas import costos as F
from estiba.finanzas import pronostico
from estiba.finanzas.simular import simular
from estiba.informe import generar as informe
from estiba.informe import graficos
from estiba.conversation.clasificacion import _DESDE_PUBLI
from estiba.extraction.texto import plano
from estiba.finanzas.precio import BASE_DEFAULT
from estiba.formato import pesos, telefono
from estiba.jobs import aprendido, episodios
from estiba.jobs.engine import (
    AJUSTADO,
    COTIZADO,
    MOTIVOS_PERDIDA,
    PERDIDO,
    REALIZADO,
    ANULADO,
    JobEngine,
    sin_anuladas,
)
from estiba.storage.db import connect
from estiba.storage.repositories import (
    ActionRepository,
    CRMRepository,
    DraftRepository,
    ClaseRepository,
    CorteRepository,
    DecisionRepository,
    GastoRepository,
    PanelRepository,
    SettingsRepository,
    SnoozeRepository,
)
from estiba.web import presentacion as P
from estiba.whatsapp import WhatsApp

AQUI = Path(__file__).parent

#: En la bandeja, lo mas urgente primero.
_URGENCIA = ["respuesta", "confirmacion", "cotizacion", "preguntas", "seguimiento"]

#: Sin noticias por mas de esto, la conversacion deja de ser trabajo de hoy.
DIAS_FRIO = 7


def _cuando_vuelve(clave: str, ahora: datetime, fecha: str | None = None) -> datetime:
    """Los plazos de posponer, en criollo. "Hoy" es esta tarde, no medianoche."""
    if clave == "mas-tarde":
        return ahora + timedelta(hours=3)
    if clave == "hoy":
        tarde = ahora.replace(hour=18, minute=0, second=0, microsecond=0)
        return tarde if tarde > ahora else ahora + timedelta(hours=2)
    if clave == "manana":
        return (ahora + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
    if clave == "2-dias":
        return (ahora + timedelta(days=2)).replace(hour=9, minute=0, second=0, microsecond=0)
    if clave == "fecha" and fecha:
        try:
            d = datetime.fromisoformat(fecha)
        except ValueError as e:
            raise HTTPException(400, f"fecha invalida: {fecha}") from e
        return d if d.hour or d.minute else d.replace(hour=9)
    raise HTTPException(400, f"no se que plazo es {clave!r}")


#: Lo que ofrece la pantalla, en orden. (clave, etiqueta)
PLAZOS = [("mas-tarde", "Más tarde"), ("hoy", "Hoy"), ("manana", "Mañana"), ("2-dias", "En 2 días")]

PERIODOS = {"7d": "7 días", "30d": "30 días", "mes": "Este mes", "todo": "Todo"}


def _cuando_legible(starts_at: str, ahora: datetime) -> str:
    """"2026-09-30T11:00:00" -> "mañana 11:00"; "2026-10-03" -> "el sáb 03/10"."""
    dt = datetime.fromisoformat(starts_at)
    dia = dt.date()
    hora = f" {dt:%H:%M}" if "T" in starts_at else ""
    faltan = (dia - ahora.date()).days
    if faltan == 0:
        return "hoy" + hora
    if faltan == 1:
        return "mañana" + hora
    return f"el {P.fecha_corta(dia)}{hora}"


@dataclass
class Estado:
    db: str
    engine: JobEngine
    ahora: Callable[[], datetime]
    espejo: EspejoCalDAV | None = None
    whatsapp: WhatsApp | None = None
    resultado: Resultado = field(default_factory=Resultado)
    sincronizado_at: datetime | None = None
    #: Ultimo estado conocido de la sesion de WhatsApp (lo avisa WAHA por webhook).
    sesion: str | None = None
    #: Como salio la ultima vez que se buscaron mensajes perdidos.
    recuperacion: dict | None = None
    #: Avisos en el escritorio (None: no se avisa, como en los tests).
    avisos: object | None = None

    def conectar(self):
        return connect(self.db)

    def sincronizar(self) -> None:
        conn = self.conectar()
        try:
            ahora = self.ahora()
            self.resultado = sincronizar(conn, self.engine, ahora, self.espejo)
            self.sincronizado_at = ahora
            if self.avisos is not None:
                self._avisar_fechas(conn)
        finally:
            conn.close()

    def _avisar_fechas(self, conn) -> None:
        """Una fecha que aparecio o se movio en la agenda, avisada en el momento."""
        for job_id, cambio in self.resultado.agenda:
            if cambio not in ("creado", "movido"):
                continue
            fila = conn.execute("SELECT * FROM calendar_events WHERE job_id = ? AND status = 'activo'",
                                (job_id,)).fetchone()
            if fila is None:
                continue
            cuando = _cuando_legible(fila["starts_at"], self.ahora())
            titulo = fila["title"].removeprefix("Sin confirmar · ")
            if fila["tentative"]:
                self.avisos.avisar(f"Posible flete {cuando}", f"{titulo}\nSe habló de esa fecha: confirmala con el cliente.")
            else:
                self.avisos.avisar(f"Flete confirmado {cuando}", titulo)

    def consultar_sesion(self) -> dict | None:
        """Pregunta a WAHA como esta la sesion. None si WAHA no responde."""
        if self.whatsapp is None:
            return None
        try:
            info = self.whatsapp.cliente.sesion()
        except ErrorWaha:
            self.sesion = "SIN_WAHA"
            return None
        self.sesion = info["status"] if info else "SIN_SESION"
        return info


log = logging.getLogger("estiba")


class Decision(BaseModel):
    texto: str | None = None


class AccionPedida(BaseModel):
    tipo: Literal["realizado", "perdido"]
    nota: str | None = None


def create_app(
    db: str | Path,
    engine: JobEngine | None = None,
    ahora: Callable[[], datetime] = datetime.now,
    clave: str | None = None,
    espejo: EspejoCalDAV | None = None,
    whatsapp: WhatsApp | None = None,
    repaso: timedelta | None = None,
    avisos=None,
) -> FastAPI:
    """`repaso`: cada cuanto buscar en WAHA mensajes que no llegaron por
    webhook. La primera vez es al arrancar. None no busca nunca solo: los
    tests no quieren un hilo hablandole a WAHA por su cuenta."""
    if espejo is None and (config := ConfigCalDAV.desde_entorno()):
        espejo = EspejoCalDAV(config)
    if whatsapp is None and (config_waha := ConfigWaha.desde_entorno()):
        whatsapp = WhatsApp(WahaClient(config_waha))
    estado = Estado(str(db), engine or JobEngine(), ahora, espejo, whatsapp)
    estado.avisos = avisos

    @asynccontextmanager
    async def ciclo(_app: FastAPI):
        estado.sincronizar()
        estado.consultar_sesion()
        # Ponerse al dia va aparte: con muchos chats y audios tarda, y la
        # app tiene que abrir ya. Lo recuperado aparece en cuanto llega.
        tareas = []
        if estado.whatsapp and repaso:
            tareas.append(asyncio.create_task(_repasar()))
        if estado.avisos is not None:
            tareas.append(asyncio.create_task(_recordar()))
        yield
        for tarea in tareas:
            tarea.cancel()
            with suppress(asyncio.CancelledError):
                await tarea

    async def _recordar() -> None:
        while True:
            await run_in_threadpool(_recordatorios)
            await asyncio.sleep(300)

    async def _repasar() -> None:
        while True:
            await run_in_threadpool(_ponerse_al_dia)
            await asyncio.sleep(repaso.total_seconds())

    app = FastAPI(
        lifespan=ciclo,
        title="Estiba",
        description="Conversaciones de WhatsApp convertidas en fletes. Nada sale sin aprobacion.",
        version="0.7.0",
    )
    app.state.estado = estado
    #: IPs donde se escucha solo para WAHA (las completa `estiba serve`).
    app.state.puertas_webhook = set()

    @app.middleware("http")
    async def _solo_webhooks(request: Request, call_next):
        """La puerta para Docker atiende webhooks y nada mas: el dashboard no se ve desde ahi."""
        local = (request.scope.get("server") or ("", 0))[0]
        if local in app.state.puertas_webhook and not request.url.path.startswith("/webhooks/"):
            return JSONResponse({"detail": "aca solo se reciben webhooks"}, status_code=404)
        return await call_next(request)
    app.mount("/static", StaticFiles(directory=AQUI / "static"), name="static")

    def _version_estatica() -> str:
        """Huella de los archivos estaticos, para que el navegador no use
        una version vieja.

        Sin esto el navegador se queda con el CSS que ya tenia: se cambia
        una regla, se recarga la pagina y no pasa nada. Es la clase de bug
        que hace perder una tarde buscando en el lugar equivocado.
        """
        estaticos = (AQUI / "static").glob("*.[cj]s*")
        return str(int(max((f.stat().st_mtime for f in estaticos), default=0)))
    templates = Jinja2Templates(directory=AQUI / "templates")
    templates.env.filters["pesos"] = lambda v: P.pesos(Decimal(v))

    # ------------------------------------------------------------------
    # Clave: obligatoria si el dashboard se abre a la red
    # ------------------------------------------------------------------

    token = hashlib.sha256(f"estiba:{clave}".encode()).hexdigest() if clave else None

    if token:
        @app.middleware("http")
        async def _exigir_clave(request: Request, call_next):
            ruta = request.url.path
            # El .ics y el webhook llevan su propio secreto en la URL: ni el
            # celular ni WAHA tienen la cookie.
            if ruta.startswith(("/static", "/calendario/", "/webhooks/")) or ruta == "/entrar":
                return await call_next(request)
            if hmac.compare_digest(request.cookies.get("estiba", ""), token):
                return await call_next(request)
            if ruta.startswith("/api") or ruta in ("/docs", "/openapi.json"):
                return JSONResponse({"detail": "falta la clave"}, status_code=401)
            return RedirectResponse("/entrar", status_code=303)

    @app.exception_handler(HTTPException)
    async def _error_legible(request: Request, exc: HTTPException):
        """Un error tiene que decir algo, no mostrar el JSON de adentro.

        La API sigue devolviendo JSON; las pantallas, una pagina.
        """
        if request.url.path.startswith("/api") or request.headers.get("HX-Request"):
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        titulos = {
            404: "Eso ya no está",
            409: "No se puede hacer eso ahora",
            401: "Falta la clave",
        }
        explicaciones = {
            404: "Puede que la conversación haya pasado a privada, a spam, o que "
                 "el flete se haya unido con otro.",
        }
        return templates.TemplateResponse(
            request, "error.html",
            _contexto(
                request,
                titulo_error=titulos.get(exc.status_code, "Algo salió mal"),
                detalle=explicaciones.get(exc.status_code, str(exc.detail)),
                titulo="Error",
            ),
            status_code=exc.status_code,
        )

    @app.get("/entrar", response_class=HTMLResponse, include_in_schema=False)
    def entrar_form(request: Request, error: bool = False):
        return templates.TemplateResponse(
            request, "entrar.html", {"error": error, "v": _version_estatica()})

    @app.post("/entrar", include_in_schema=False)
    def entrar(clave_ingresada: str = Form(alias="clave")):
        if not token or not hmac.compare_digest(
            hashlib.sha256(f"estiba:{clave_ingresada}".encode()).hexdigest(), token
        ):
            return RedirectResponse("/entrar?error=1", status_code=303)
        r = RedirectResponse("/", status_code=303)
        r.set_cookie("estiba", token, max_age=90 * 86400, httponly=True, samesite="lax")
        return r

    # ------------------------------------------------------------------
    # Lectura
    # ------------------------------------------------------------------

    def _nombres() -> dict[str, str]:
        conn = estado.conectar()
        try:
            return CRMRepository(conn).names()
        finally:
            conn.close()

    def _fletes() -> dict[str, P.Flete]:
        ahora, nombres = estado.ahora(), _nombres()
        conn = estado.conectar()
        try:
            numeros = {
                f["id"]: f["numero"] for f in conn.execute(
                    "SELECT id, numero FROM jobs WHERE numero IS NOT NULL")
            }
        finally:
            conn.close()
        return {
            job_id: P.flete(job_id, ev, conv, ahora, nombres.get(conv.chat_id),
                            numeros.get(job_id))
            for job_id, (ev, conv) in estado.resultado.fletes.items()
        }

    def _bandeja() -> list[tuple[P.Borrador, P.Flete]]:
        conn = estado.conectar()
        try:
            pendientes = DraftRepository(conn).pending()
        finally:
            conn.close()
        fletes = _fletes()
        items = [(P.borrador(b), fletes[b["job_id"]]) for b in pendientes if b["job_id"] in fletes]
        # Dentro de cada grupo, lo mas reciente primero: el que escribio ayer
        # es un lead vivo, el que no contesta hace un mes esta frio. Se ordena
        # en dos pasadas porque el orden de Python es estable.
        items.sort(key=lambda x: x[1].ultimo_at, reverse=True)
        items.sort(key=lambda x: _URGENCIA.index(x[0].tipo))
        return items

    def _sin_salir() -> list[tuple[P.Borrador, P.Flete]]:
        """Aprobados que WhatsApp no mando: arriba de todo, hasta que salgan."""
        conn = estado.conectar()
        try:
            filas = DraftRepository(conn).unsent()
        finally:
            conn.close()
        fletes = _fletes()
        return [(P.borrador(b), fletes[b["job_id"]]) for b in filas if b["job_id"] in fletes]

    def _detalle(job_id: str) -> P.DetalleFlete:
        par = estado.resultado.fletes.get(job_id)
        if par is None:
            raise HTTPException(404, f"no hay un flete {job_id}")
        ev, conv = par
        conn = estado.conectar()
        try:
            borradores = DraftRepository(conn).for_job(job_id)
            nombre = CRMRepository(conn).name(conv.chat_id)
        finally:
            conn.close()
        otros = [f for f in _fletes().values() if f.chat_id == conv.chat_id and f.job_id != job_id]
        return P.detalle(job_id, ev, conv, estado.ahora(), borradores, nombre, otros)

    #: Los filtros de la bandeja. (clave, etiqueta, que deja pasar)
    #: La pantalla viva es solo de clientes y leads. Lo que ya se clasifico
    #: como ajeno al negocio, y lo que se dio por perdido, vive en sus
    #: propias pestañas: esta, pero no molesta.
    #: Cada pestaña se llama como la etiqueta que va a ver adentro. "Cotizar"
    #: y "Cotizado" estaban a un caracter de distancia y querian decir lo
    #: contrario; "Confirmados" mostraba fletes cuyo chip decia "Agendado".
    FILTROS = [
        ("todos", "Todos"), ("hoy", "Hoy"), ("responder", "Responder"),
        ("faltan", "Faltan datos"), ("cotizar", "Para cotizar"),
        ("seguimiento", "Seguimiento"), ("confirmados", "Para hacer"),
    ]
    #: Las de afuera del trabajo del dia.
    #: Leads y clientes NO se separan: van juntos en el hilo principal, que
    #: es de lo que se trata la app. Separarlos es logica de CRM y va despues.
    FILTROS_APARTE = [
        ("sin-clasificar", "Sin clasificar"), ("perdidos", "Perdidos"),
        ("privados", "Privados"), ("spam", "Spam"),
    ]

    def _pasa(filtro: str, b: P.Borrador | None, f: P.Flete, dudosa: bool, hoy: str,
              clase: str | None = None) -> bool:
        perdido = f.estado == JobState.CANCELLED.value
        if filtro == "sin-clasificar":
            return dudosa
        if filtro == "perdidos":
            return perdido
        # En las pestañas de trabajo no entra lo que todavia no se sabe que
        # es, ni lo que ya se dio por perdido.
        if dudosa or perdido:
            return False
        if filtro == "hoy":
            return f.ultimo_at[:10] == hoy
        if filtro == "confirmados":
            return f.estado in (JobState.CONFIRMED.value, JobState.SCHEDULED.value)
        tipos = {"responder": "respuesta", "faltan": "preguntas",
                 "cotizar": "cotizacion", "seguimiento": "seguimiento"}
        if filtro in tipos:
            return b is not None and b.tipo == tipos[filtro]
        return True

    def _apartadas(clases: tuple[str, ...]) -> list[dict]:
        """Las que Estiba dejo afuera del embudo: personales y publicidad.

        No pasan por el JobEngine (no son fletes), asi que se arman con lo
        minimo: quien, que dijo ultimo, cuando y por que quedo afuera. Estan
        para poder revisarlas y corregir, no para trabajarlas.
        """
        ahora = estado.ahora()
        conn = estado.conectar()
        try:
            guardadas = ClaseRepository(conn).todas()
            store = ConversationStore(conn)
            filas = []
            for chat_id, fila in guardadas.items():
                if fila["clase"] not in clases:
                    continue
                conv = store.get(chat_id)
                mensajes = [m for m in conv.messages if m.sent_at <= ahora]
                if not mensajes:
                    continue
                ultimo = mensajes[-1]
                filas.append({
                    "chat_id": chat_id,
                    "quien": telefono(chat_id),
                    "ultimo": P.hace(ultimo.sent_at, ahora),
                    "ultimo_at": ultimo.sent_at.isoformat(),
                    "texto": conv.text_of(ultimo),
                    "clase": fila["clase"],
                    "porque": fila["porque"],
                    "lo_dijiste_vos": fila["quien"] == "humano",
                })
        finally:
            conn.close()
        return sorted(filas, key=lambda x: x["ultimo_at"], reverse=True)

    def _conversaciones(filtro: str = "todos") -> dict:
        """Una sola lista, por orden de llegada. Los filtros solo acotan.

        La cronologia manda: clasificar no puede desordenar el dia. Lo que
        llego recien va arriba, se llame como se llame.
        """
        ahora = estado.ahora()
        borradores = {f.job_id: b for b, f in _bandeja()}
        conn = estado.conectar()
        try:
            dormidos = SnoozeRepository(conn).vigentes(ahora)
        finally:
            conn.close()

        dudosas = {
            chat_id for chat_id, cl in estado.resultado.clases.items() if cl.pide_decision
        }
        clases_por_chat = {
            chat_id: cl.clase.value for chat_id, cl in estado.resultado.clases.items()
        }
        # Un cliente puede tener varios fletes: uno agendado y una consulta
        # nueva. Se muestra una fila por conversacion, pero la del flete que
        # el filtro esta pidiendo; si no, un agendado se pierde detras de una
        # consulta mas reciente y te quedas sin ver trabajo que ya vendiste.
        hoy = ahora.date().isoformat()

        def elegir(clave: str) -> list[tuple[P.Borrador | None, P.Flete, bool]]:
            por_chat: dict[str, P.Flete] = {}
            for f in _fletes().values():
                if f.chat_id in dormidos:
                    continue
                if not _pasa(clave, borradores.get(f.job_id), f, f.chat_id in dudosas, hoy,
                             clases_por_chat.get(f.chat_id)):
                    continue
                if f.chat_id not in por_chat or f.ultimo_at > por_chat[f.chat_id].ultimo_at:
                    por_chat[f.chat_id] = f
            return [
                (borradores.get(f.job_id), f, f.chat_id in dudosas,
                 clases_por_chat.get(f.chat_id))
                for f in sorted(por_chat.values(), key=lambda x: x.ultimo_at, reverse=True)
            ]

        # Una sola lista, del ultimo que escribio al mas viejo. Como WhatsApp.
        #
        # Estaba agrupada por etapa de la venta, "en orden de cercania a la
        # plata". Sonaba bien y rompia lo unico que el fletero tiene metido
        # en el cuerpo: que arriba esta lo que acaba de llegar. Un cliente
        # que escribio recien aparecia tres titulos mas abajo que uno de
        # hace cuatro dias, porque estaba en otra etapa.
        #
        # La etapa no se pierde: viaja en la fila, en el chip de estado y en
        # el color del borde. Y el que quiera ver una etapa sola tiene su
        # pestaña: para eso estan "Para cotizar" y "Para hacer".
        filas = elegir(filtro)
        pospuestas, vistos = [], set()
        for f in sorted(_fletes().values(), key=lambda x: x.ultimo_at, reverse=True):
            if f.chat_id in dormidos and f.chat_id not in vistos:
                vistos.add(f.chat_id)
                pospuestas.append((borradores.get(f.job_id), f, dormidos[f.chat_id],
                                   clases_por_chat.get(f.chat_id)))

        cuentas = {clave: len(elegir(clave)) for clave, _ in FILTROS + FILTROS_APARTE}
        # Personales y spam no llegan a ser fletes: se cuentan aparte.
        privados = _apartadas(("privado",))
        spam = _apartadas(("spam",))
        cuentas["privados"] = len(privados)
        cuentas["spam"] = len(spam)
        apartadas = privados if filtro == "privados" else spam if filtro == "spam" else []
        return {
            "sin_salir": _sin_salir() if filtro in ("todos", "hoy") else [],
            "filas": filas,
            "apartadas": apartadas,
            "filtros_aparte": FILTROS_APARTE,
            "es_spam": filtro == "spam",
            "pospuestas": sorted(pospuestas, key=lambda x: x[2]),
            "filtros": FILTROS,
            "filtro": filtro,
            "cuentas": cuentas,
        }

    def _secciones(fletes: list[P.Flete]) -> list[tuple[str, str, list[P.Flete]]]:
        activos = (JobState.CONFIRMED.value, JobState.SCHEDULED.value)
        proximos = sorted(
            (f for f in fletes if f.estado in activos and not f.pasado),
            key=lambda f: f.fecha_iso or "9999",
        )
        en_curso = sorted(
            (f for f in fletes if f.estado in (JobState.DRAFT.value, JobState.QUOTED.value,
                                               JobState.WAITING_CUSTOMER.value)),
            key=lambda f: f.ultimo_at, reverse=True,
        )
        cerrados = sorted(
            (f for f in fletes if f not in proximos and f not in en_curso),
            key=lambda f: f.fecha_iso or f.ultimo_at, reverse=True,
        )
        return [
            ("proximos", "Próximos", proximos),
            ("en-curso", "En conversación", en_curso),
            ("cerrados", "Pasados y perdidos", cerrados),
        ]

    def _agenda(request: Request, mes: str | None = None) -> P.Agenda:
        conn = estado.conectar()
        try:
            cal = LocalCalendar(conn)
            eventos = cal.eventos(incluir_cancelados=False)
            url = str(request.base_url).rstrip("/") + f"/calendario/{cal.token()}.ics"
        finally:
            conn.close()
        # "2026-10" en la URL: un mes escrito de cualquier otra forma se
        # ignora y se muestra el de hoy, que es lo que se queria ver.
        mirando = None
        if mes and re.fullmatch(r"\d{4}-\d{2}", mes):
            try:
                mirando = datetime.strptime(mes, "%Y-%m").date()
            except ValueError:
                mirando = None
            # Diez años para cada lado alcanzan: mas alla no hay fletes, y en
            # diciembre del 9999 el "mes siguiente" no existe y la pagina caia.
            if mirando is not None and abs(mirando.year - estado.ahora().year) > 10:
                mirando = None
        return P.agenda(eventos, _fletes(), estado.ahora(), url, mirando)

    def _embudo(periodo: str) -> tuple[embudo.Embudo, P.Numeros]:
        ahora = estado.ahora()
        desde = {
            "7d": ahora - timedelta(days=7),
            "30d": ahora - timedelta(days=30),
            "mes": ahora.replace(day=1, hour=0, minute=0, second=0, microsecond=0),
            "todo": None,
        }[periodo]
        e = embudo.calcular(estado.resultado.leads, hasta=ahora, desde=desde)
        return e, P.numeros(e)

    def _clientes() -> list[P.Cliente]:
        return P.clientes(estado.resultado.leads, _fletes(), _nombres(), estado.ahora())

    def _contexto(request: Request, **extra) -> dict:
        conn = estado.conectar()
        try:
            pendientes = len(DraftRepository(conn).pending())
        finally:
            conn.close()
        return {
            "pendientes": pendientes,
            "v": _version_estatica(),
            "whatsapp": P.sesion_whatsapp(estado.sesion) if estado.whatsapp else None,
            "envio_directo": estado.whatsapp is not None,
            "sincronizado": P.hace(estado.sincronizado_at, estado.ahora()) if estado.sincronizado_at else None,
            "espejo_error": estado.resultado.espejo_error,
            "ruta": request.url.path,
            **extra,
        }

    def _periodo(periodo: str) -> str:
        if periodo not in PERIODOS:
            raise HTTPException(422, f"periodo invalido: {periodo}")
        return periodo

    # ------------------------------------------------------------------
    # Paginas
    # ------------------------------------------------------------------

    def _por_confirmar(dias: int = 7) -> list[dict]:
        """Fechas que alguien nombro y el cliente no cerro, de hoy a `dias`.

        Estaban en la agenda con "Sin confirmar", pero para verlas habia que
        ir a buscarlas: una fecha dicha en un audio se pasaba sin que nadie
        le escribiera al cliente.
        """
        ahora = estado.ahora()
        conn = estado.conectar()
        try:
            eventos = LocalCalendar(conn).eventos(incluir_cancelados=False)
        finally:
            conn.close()
        fletes = _fletes()
        salida = []
        for e, _ in eventos:
            dia = e.inicio.date() if isinstance(e.inicio, datetime) else e.inicio
            if not e.tentativo or not (ahora.date() <= dia <= ahora.date() + timedelta(days=dias)):
                continue
            f = fletes.get(e.job_id)
            if f is None or f.cerrado:
                continue
            faltan = (dia - ahora.date()).days
            salida.append({
                "job_id": e.job_id, "quien": f.quien, "ruta": f.ruta if (f.origen or f.destino) else "",
                "cuando": ("hoy" if faltan == 0 else "mañana" if faltan == 1 else P.fecha_corta(dia))
                          + (f" {e.inicio:%H:%M}" if isinstance(e.inicio, datetime) else ""),
                "urgente": faltan <= 1,
            })
        return salida

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def pagina_bandeja(request: Request, f: str = "todos"):
        validos = [c for c, _ in FILTROS + FILTROS_APARTE]
        filtro = f if f in validos else "todos"
        return templates.TemplateResponse(
            request, "bandeja.html",
            _contexto(request, **_conversaciones(filtro), por_confirmar=_por_confirmar(), titulo="Hoy"),
        )

    @app.get("/agenda", response_class=HTMLResponse, include_in_schema=False)
    def pagina_agenda(request: Request, mes: str | None = None):
        return templates.TemplateResponse(
            request, "agenda.html", _contexto(request, a=_agenda(request, mes), titulo="Agenda")
        )

    @app.get("/fletes", response_class=HTMLResponse, include_in_schema=False)
    def pagina_fletes(request: Request):
        return templates.TemplateResponse(
            request, "fletes.html",
            _contexto(request, secciones=_secciones(list(_fletes().values())), titulo="Fletes"),
        )

    @app.get("/conversaciones/{chat_id}", response_class=HTMLResponse, include_in_schema=False)
    def pagina_apartada(request: Request, chat_id: str):
        """Una charla privada o de spam, para mirarla y reclasificarla."""
        conn = estado.conectar()
        try:
            fila = ClaseRepository(conn).de(chat_id)
            conv = ConversationStore(conn).get(chat_id)
            nombre = CRMRepository(conn).name(chat_id)
        finally:
            conn.close()
        if not conv.messages:
            raise HTTPException(404, f"no hay una conversación con {chat_id}")
        # Si ya volvio al circuito (o nunca salio), su lugar es el flete.
        propios = sorted(j for j in estado.resultado.fletes if j.split(":job")[0] == chat_id)
        if propios:
            return RedirectResponse(f"/fletes/{propios[-1]}", status_code=303)
        a = {"chat_id": chat_id, "telefono": telefono(chat_id), "nombre": nombre,
             "clase": fila["clase"] if fila else "privado", "porque": fila["porque"] if fila else None,
             "lo_dijiste_vos": bool(fila and fila["quien"] == "humano")}
        return templates.TemplateResponse(
            request, "apartada.html",
            _contexto(request, a=a, mensajes=P.mensajes_de(conv, estado.ahora()),
                      volver="spam" if a["clase"] == "spam" else "privados", titulo=a["nombre"] or a["telefono"]),
        )

    @app.get("/fletes/{job_id}", response_class=HTMLResponse, include_in_schema=False)
    def pagina_flete(request: Request, job_id: str, f: str = "todos"):
        # Un enlace viejo a una charla que quedo afuera del circuito: se la
        # muestra como lo que es, en vez de un "no encontrado".
        if job_id not in estado.resultado.fletes and (chat := job_id.split(":job")[0]) in estado.resultado.fuera:
            return RedirectResponse(f"/conversaciones/{chat}", status_code=303)
        d = _detalle(job_id)
        conn = estado.conectar()
        try:
            dormida = SnoozeRepository(conn).vigentes(estado.ahora()).get(d.flete.chat_id)
        finally:
            conn.close()
        filtro = f if any(c == f for c, _ in FILTROS) else "todos"
        anterior, siguiente = _vecinos(job_id, filtro)
        clase = estado.resultado.clases.get(d.flete.chat_id)
        sugerido = _precio_aprendido(job_id)
        _, conv_det = estado.resultado.fletes[job_id]
        pistas = episodios.sospechas(conv_det, estado.engine, estado.ahora())
        conn3 = estado.conectar()
        try:
            cortado = bool(
                conv_det.messages
                and conv_det.messages[0].id in CorteRepository(conn3).de(d.flete.chat_id)
            )
        finally:
            conn3.close()
        conn2 = estado.conectar()
        try:
            guardada = ClaseRepository(conn2).de(d.flete.chat_id)
        finally:
            conn2.close()
        ev_flete, _ = estado.resultado.fletes[job_id]
        conn4 = estado.conectar()
        try:
            cierres = [a for a in sin_anuladas(ActionRepository(conn4).for_job(job_id)) if a.tipo == REALIZADO]
        finally:
            conn4.close()
        cobrado = cierres[-1].monto if ev_flete.job.state is JobState.DONE and cierres else None
        cot = P.cotizacion(ev_flete.presupuesto,
                           ev_flete.job.quoted_price.value if ev_flete.job.quoted_price else None, cobrado,
                           job=ev_flete.job, confirmado=ev_flete.precio_confirmado,
                           ajustable=ev_flete.job.state not in (JobState.DONE, JobState.CANCELLED))
        return templates.TemplateResponse(
            request, "flete.html",
            _contexto(
                request, d=d, cot=cot, anterior=anterior, siguiente=siguiente, filtro=filtro,
                plazos=PLAZOS, dudosa=bool(clase and clase.pide_decision),
                motivos=MOTIVOS_PERDIDA,
                clase=guardada["clase"] if guardada else None,
                sugerido=sugerido,
                pistas=pistas,
                cortado_a_mano=cortado,
                mensajes_recientes=list(reversed(d.mensajes[-12:])),
                clase_porque=(guardada["porque"] if guardada else None),
                pospuesta_hasta=P.cuando_vuelve(dormida, estado.ahora()) if dormida else None,
                titulo=d.flete.quien,
            ),
        )

    @app.get("/numeros", response_class=HTMLResponse, include_in_schema=False)
    def pagina_numeros(request: Request, periodo: str = "30d"):
        _, n = _embudo(_periodo(periodo))
        return templates.TemplateResponse(
            request, "numeros.html",
            _contexto(request, n=n, periodo=periodo, periodos=PERIODOS, titulo="Números"),
        )

    # ------------------------------------------------------------------
    # Objetivos: lo que cuesta un flete y lo que hace falta para la meta
    # ------------------------------------------------------------------

    def _costos() -> F.Costos:
        conn = estado.conectar()
        try:
            return F.Costos.desde_dict(SettingsRepository(conn).json("costos"))
        finally:
            conn.close()

    def _fletes_hechos() -> list[F.FleteHecho]:
        """Los realizados, con lo cobrado si se anoto o lo cotizado si no.

        El dia es el del flete si el chat lo dice, y si no el del ultimo
        mensaje: marcar realizado una semana despues no lo mueve de mes.
        """
        conn = estado.conectar()
        try:
            acciones = ActionRepository(conn)
            hechos = []
            for job_id, (ev, conv) in estado.resultado.fletes.items():
                if ev.job.state is not JobState.DONE:
                    continue
                cierres = [a for a in sin_anuladas(acciones.for_job(job_id)) if a.tipo == REALIZADO]
                cobrado = cierres[-1].monto if cierres and cierres[-1].monto else None
                if cobrado is None and ev.job.quoted_price:
                    cobrado = ev.job.quoted_price.value
                dia = ev.job.when.value if ev.job.when else conv.messages[-1].sent_at.date()
                hechos.append(F.FleteHecho(job_id, dia, cobrado))
            return hechos
        finally:
            conn.close()

    def _gastos() -> tuple[list, list[F.GastoPublicidad]]:
        conn = estado.conectar()
        try:
            filas = GastoRepository(conn).todos()
        finally:
            conn.close()
        return filas, [
            F.GastoPublicidad(date.fromisoformat(g["period_start"]), date.fromisoformat(g["period_end"]),
                              Decimal(g["spend"]), g["resultados"])
            for g in filas
        ]

    @app.get("/numeros/objetivos", response_class=HTMLResponse, include_in_schema=False)
    def pagina_objetivos(request: Request, periodo: str = "30d"):
        periodo = _periodo(periodo)
        ahora = estado.ahora()
        hoy = ahora.date()
        desde = {
            "7d": hoy - timedelta(days=6),
            "30d": hoy - timedelta(days=29),
            "mes": hoy.replace(day=1),
            "todo": None,
        }[periodo]
        c = _costos()
        hechos = _fletes_hechos()
        filas, gastos = _gastos()
        e, _ = _embudo(periodo)
        cierre = (e.pasos[-1].cantidad, e.pasos[0].cantidad) if e.pasos else None
        o = P.objetivos(
            c,
            F.balance(c, hechos, gastos, desde, hoy),
            F.balance(c, hechos, gastos, hoy.replace(day=1), hoy),
            filas, ahora, cierre,
        )
        o.pronostico = P.pronostico(pronostico.pronosticar(c, _historia(hechos, gastos, hoy)))
        base = _base()
        o.negocio = _negocio() if _negocio() != "Tu negocio de fletes" else ""
        o.base = base.get("texto", "")
        o.base_nota = (f"{base['error']}: quedó la anterior." if base.get("error")
                       else f"En el mapa: {base['como']}" if base.get("como") else None)
        return templates.TemplateResponse(
            request, "objetivos.html",
            _contexto(request, o=o, periodo=periodo, periodos=PERIODOS, titulo="Objetivos"),
        )

    def _historia(hechos: list[F.FleteHecho], gastos: list[F.GastoPublicidad], hoy: date) -> pronostico.Historia:
        """Los ultimos 30 dias, o desde que arranco la publicidad si fue despues.

        Contar dias sin campaña como dias de campaña bajaria el gasto por
        dia y el pronostico saldria prolijamente equivocado.
        """
        desde = hoy - timedelta(days=29)
        if gastos:
            desde = max(desde, min(g.desde for g in gastos))
        de_publi = set()
        consultas = hechos_publi = hechos_otros = 0
        for lead in estado.resultado.leads:
            if not (desde <= lead.creado.date() <= hoy):
                continue
            par = estado.resultado.fletes.get(lead.job_id)
            primero = par[1].text_of(par[1].messages[0]) if par and par[1].messages else ""
            if _DESDE_PUBLI.search(plano(primero or "")):
                de_publi.add(lead.job_id)
                consultas += 1
        for f in hechos:
            if not (desde <= f.dia <= hoy):
                continue
            if f.job_id in de_publi:
                hechos_publi += 1
            else:
                hechos_otros += 1
        return pronostico.Historia(
            dias=(hoy - desde).days + 1,
            consultas_publicidad=consultas,
            hechos_publicidad=hechos_publi,
            hechos_sin_publicidad=hechos_otros,
            gasto=sum((g.dentro(desde, hoy) for g in gastos), Decimal(0)),
            # Solo si todos los gastos de la ventana dicen cuantas trajeron:
            # mezclar las de Meta con las que reconoce Estiba no da nada.
            consultas_meta=round(sum(g.resultados * g.parte(desde, hoy) for g in en_ventana))
            if (en_ventana := [g for g in gastos if g.parte(desde, hoy) > 0])
            and all(g.resultados for g in en_ventana) else None,
            cobrado=[f.cobrado for f in hechos if f.cobrado is not None and desde <= f.dia <= hoy],
        )

    # ------------------------------------------------------------------
    # Tablero e informe: lo mismo, en pantalla y en papel
    # ------------------------------------------------------------------

    def _de_publicidad() -> set[str]:
        """Los fletes que arrancaron con el texto del boton de un anuncio."""
        salida = set()
        for job_id, (_, conv) in estado.resultado.fletes.items():
            primero = conv.text_of(conv.messages[0]) if conv.messages else ""
            if _DESDE_PUBLI.search(plano(primero or "")):
                salida.add(job_id)
        return salida

    def _rango(periodo: str, desde: str | None, hasta: str | None) -> tuple[date | None, date, str]:
        """El periodo del filtro. "fechas" usa desde/hasta; si no se entienden, 30 dias."""
        hoy = estado.ahora().date()
        if periodo == "fechas":
            try:
                d, h = date.fromisoformat(desde or ""), date.fromisoformat(hasta or "")
                if d <= h:
                    return d, min(h, hoy), f"del {d:%d/%m} al {min(h, hoy):%d/%m}"
            except ValueError:
                pass
            periodo = "30d"
        return {
            "7d": (hoy - timedelta(days=6), hoy, "últimos 7 días"),
            "30d": (hoy - timedelta(days=29), hoy, "últimos 30 días"),
            "mes": (hoy.replace(day=1), hoy, "este mes"),
            "todo": (None, hoy, "desde el principio"),
        }.get(periodo, (hoy - timedelta(days=29), hoy, "últimos 30 días"))

    def _negocio() -> str:
        conn = estado.conectar()
        try:
            return (SettingsRepository(conn).json("negocio") or {}).get("nombre") or "Tu negocio de fletes"
        finally:
            conn.close()

    def _tablero(periodo: str, desde_txt: str | None, hasta_txt: str | None, origen: str,
                 tema: graficos.Tema, elasticidad: float = 1.0, saturacion: float = 0.8,
                 tope_dia: float | None = None) -> dict:
        desde, hasta, etiqueta = _rango(periodo, desde_txt, hasta_txt)
        c = _costos()
        todos = _fletes_hechos()
        filas, gastos = _gastos()
        publi = _de_publicidad()
        # El origen filtra los fletes y el embudo. La publicidad solo se
        # cobra a los que vinieron por ella: los que vuelven no la usaron.
        elegir = {"publicidad": lambda j: j in publi, "vuelven": lambda j: j not in publi}.get(origen, lambda j: True)
        hechos = [f for f in todos if elegir(f.job_id)]
        gastos_filtro = [] if origen == "vuelven" else gastos
        leads = [l for l in estado.resultado.leads if elegir(l.job_id)]
        e = embudo.calcular(leads, hasta=datetime.combine(hasta, datetime.max.time()),
                            desde=datetime.combine(desde, datetime.min.time()) if desde else None)
        nombres = _nombres()
        fletes = []
        for f in hechos:
            if desde is not None and f.dia < desde or f.dia > hasta:
                continue
            ev, _ = estado.resultado.fletes[f.job_id]
            j, pres = ev.job, ev.presupuesto
            det = pres.detalle if pres else {}
            fletes.append(informe.FleteDelInforme(
                dia=f.dia,
                cliente=nombres.get(j.conversation_id) or telefono(j.conversation_id),
                ruta=f"{j.origin.value} → {j.destination.value}" if j.origin and j.destination else "",
                km=int(det["km"]) if det.get("km") is not None else None,
                cobrado=f.cobrado,
                sugerido=pres.total if pres else None,
                minimo=det.get("minimo"),
            ))
        h = _historia(todos, gastos, estado.ahora().date())
        p = pronostico.pronosticar(c, h)
        ctx = informe.datos(negocio=_negocio(), periodo=etiqueta, desde=desde, hoy=hasta, ahora=estado.ahora(),
                            c=c, hechos=hechos, gastos=gastos_filtro, embudo=e, pronostico=p,
                            fletes=fletes, tema=tema, origen=origen, historia=h,
                            elasticidad=elasticidad, saturacion=saturacion, tope_dia=tope_dia)
        ctx["pronostico_crudo"] = p
        return ctx

    ORIGENES = {"todos": "Todos", "publicidad": "Por publicidad", "vuelven": "Sin publicidad"}
    PERIODOS_TABLERO = {**PERIODOS, "fechas": "Fechas"}

    @app.get("/numeros/tablero", response_class=HTMLResponse, include_in_schema=False)
    def pagina_tablero(request: Request, periodo: str = "30d", desde: str | None = None,
                       hasta: str | None = None, origen: str = "todos", elasticidad: float = 1.0,
                       saturacion: float = 0.8, tope: str = ""):
        origen = origen if origen in ORIGENES else "todos"
        periodo = periodo if periodo in PERIODOS_TABLERO else "30d"
        elasticidad = _acotar(elasticidad, 0.0, 3.0, 1.0)
        saturacion = _acotar(saturacion, 0.3, 1.0, 0.8)
        tope_dia = float(t) if (t := _numero(tope)) else None
        t = _tablero(periodo, desde, hasta, origen, graficos.PANTALLA, elasticidad, saturacion, tope_dia)
        p = t.pop("pronostico_crudo")
        c = _costos()
        _, gastos = _gastos()
        decisiones = _decisiones(c, _fletes_hechos(), gastos)
        sim = {
            "precio": int(p.ticket.valor.quantize(Decimal(1))),
            "gasto_dia": int(p.gasto_dia.quantize(Decimal(1))) or 4000,
            "costo_consulta": int(p.costo_consulta.valor),
            "cierre": f"{float(p.cierre.valor):.1f}".replace(".", ",").removesuffix(",0"),
            "solos": f"{float(p.sin_publicidad_mes):.1f}".replace(".", ",").removesuffix(",0"),
            "meta": int(c.ganancia_objetivo),
            "capacidad": int(c.fletes_por_mes_max),
        }
        consulta = str(request.url.query)
        return templates.TemplateResponse(
            request, "tablero.html",
            _contexto(request, t=t, periodo=periodo, periodos=PERIODOS_TABLERO, origen=origen, origenes=ORIGENES,
                      desde=desde or "", hasta=hasta or "", sim=sim, d=decisiones,
                      elasticidad=elasticidad, saturacion=saturacion, tope=tope or "",
                      resultado=_simulacion(c, **{k: (_numero(str(v)) or Decimal(0)) for k, v in sim.items()},
                                  elasticidad=elasticidad, saturacion=saturacion, precio_base=sim["precio"]),
                      pdf=f"/numeros/informe.pdf{'?' + consulta if consulta else ''}",
                      titulo="Tablero"),
        )

    def _simulacion(c: F.Costos, *, precio, gasto_dia, costo_consulta, cierre, solos,
                    meta=None, capacidad=None, elasticidad: float = 1.0, saturacion: float = 0.8,
                    precio_base=None) -> dict:
        """El mes con estos numeros.

        `cierre` y `solos` son los de hoy, a `precio_base`. Si se prueba otro
        precio, cierran mas o menos segun la sensibilidad al precio: la misma
        cuenta que la optimizacion. Sin eso, la calculadora decia que a
        $88.000 salian 14 fletes y $718.000, cuando lo esperable es 5 y
        $47.000: una decision tomada con ese numero sale mal.
        """
        from dataclasses import replace as _cambiar

        from estiba.finanzas import decision as D

        if meta:
            c = _cambiar(c, ganancia_objetivo=Decimal(str(meta)))
        if capacidad:
            c = _cambiar(c, fletes_por_mes_max=Decimal(str(capacidad)))
        base = float(precio_base or precio) or 1.0
        m = D.Modelo(precio=base, gasto_dia=float(gasto_dia) or 1.0,
                     costo_consulta=float(costo_consulta) or 1.0, cierre=float(cierre) / 100,
                     solos_mes=float(solos), elasticidad=elasticidad, saturacion=saturacion)
        factor = m.factor_precio(float(precio))
        cierre_ef = Decimal(str(round(m.cierre_a(float(precio)) * 100, 2)))
        solos_ef = Decimal(str(round(float(solos) * factor, 2)))
        s = simular(c, precio=Decimal(str(precio)), gasto_dia=Decimal(str(gasto_dia)),
                    costo_consulta=Decimal(str(costo_consulta)), cierre=cierre_ef, solos_mes=solos_ef)
        signo = lambda v: f"−{pesos(-v)}" if v < 0 else pesos(v)
        partes = [("Camioneta", s.camioneta), ("Publicidad", s.publicidad), ("Fijos", s.fijos)]
        base = max(s.ingresos, s.costos) or Decimal(1)
        veredicto = []
        if s.sobran:
            veredicto.append((f"La publicidad traería {int(s.pedidos)} fletes y entran {int(c.fletes_por_mes_max)}: "
                              f"sobran {s.sobran}. Estás pagando consultas que no vas a poder atender; "
                              "conviene subir el precio antes que la publicidad.", "aviso"))
        if s.minimo is not None and Decimal(str(precio)) < s.minimo:
            veredicto.append((f"A {s.fletes} fletes por mes, para no perder necesitás cobrar {pesos(F.redondear(s.minimo))} "
                              "en promedio: con este precio cada flete pierde.", "mal"))
        if s.falta <= 0:
            veredicto.append((f"Llegás a la meta y te sobran {pesos(-s.falta)}.", "ok"))
        elif s.ganancia > 0:
            veredicto.append((f"Ganás {pesos(s.ganancia)}; para la meta faltan {pesos(s.falta)}.", "aviso"))
        # Rangos: que pasa si se mueve una sola cosa, con el mismo modelo.
        if abs(factor - 1) > 0.01:
            coma = lambda v: f"{float(v):.1f}".replace(".", ",")
            veredicto.insert(0, (
                f"A {pesos(Decimal(str(precio)))} cierran {'menos' if factor < 1 else 'más'}: "
                f"{coma(cierre_ef)}% en vez de {coma(cierre)}% (sensibilidad al precio {coma(elasticidad)}, "
                "ajustable en «Mejor combinación»).", "neutro"))

        def fila(r, marca):
            return {"precio": pesos(Decimal(str(round(r.precio, -2)))), "gasto": pesos(Decimal(str(round(r.gasto_dia, -2)))),
                    "fletes": f"{r.fletes:.0f}", "ganancia": signo(Decimal(str(round(r.ganancia)))),
                    "tono": "ok" if r.ganancia >= float(c.ganancia_objetivo) else ("aviso" if r.ganancia >= 0 else "mal"),
                    "actual": marca}

        rango_precio = [fila(D.evaluar(c, m, float(precio) * f, float(gasto_dia)), f == 1)
                        for f in (0.7, 0.85, 1, 1.15, 1.3, 1.5, 1.75, 2)]
        rango_gasto = [fila(D.evaluar(c, m, float(precio), float(gasto_dia) * f), f == 1)
                       for f in (0, 0.5, 1, 1.5, 2, 3, 4, 6)]
        eq = D.equilibrio(c, float(precio), float(gasto_dia))
        return {
            "rango_precio": rango_precio, "rango_gasto": rango_gasto,
            "equilibrio": f"{eq:.0f}" if eq else None,
            "cierre_efectivo": f"{cierre_ef:.1f}".replace(".", ","),
            "valores": {"precio": precio, "gasto_dia": gasto_dia, "costo_consulta": costo_consulta,
                        "cierre": cierre, "solos": solos, "meta": int(c.ganancia_objetivo),
                        "capacidad": int(c.fletes_por_mes_max)},
            "fletes": s.fletes, "consultas": s.consultas,
            "ingresos": pesos(s.ingresos), "ganancia": signo(s.ganancia), "mal": s.ganancia < 0,
            "margen": f"{s.margen:.0%}" if s.margen is not None and s.margen >= 0 else (f"−{-s.margen:.0%}" if s.margen else "—"),
            "minimo": pesos(F.redondear(s.minimo)) if s.minimo is not None else "—",
            "meta": pesos(c.ganancia_objetivo),
            "avance": max(0.0, min(100.0, float(s.ganancia / c.ganancia_objetivo * 100))) if c.ganancia_objetivo else 0,
            "barras": [{"nombre": n, "monto": pesos(m.quantize(Decimal(1))), "ancho": float(m / base * 100),
                        "color": P.COLOR_PARTE.get(n, "pf")} for n, m in partes],
            "ingreso_ancho": float(s.ingresos / base * 100),
            "veredicto": veredicto,
        }

    @app.get("/numeros/tablero/simular", response_class=HTMLResponse, include_in_schema=False)
    def simular_form(request: Request, precio: str = "0", gasto_dia: str = "0", costo_consulta: str = "0",
                     cierre: str = "0", solos: str = "0", meta: str = "", capacidad: str = "",
                     elasticidad: float = 1.0, saturacion: float = 0.8, precio_base: str = ""):
        """La calculadora, recalculada del lado del servidor: la misma cuenta que el resto."""
        valores = {k: (_numero(v) or Decimal(0)) for k, v in
                   {"precio": precio, "gasto_dia": gasto_dia, "costo_consulta": costo_consulta,
                    "cierre": cierre, "solos": solos}.items()}
        return templates.TemplateResponse(
            request, "_simulacion.html",
            {"r": _simulacion(_costos(), **valores, meta=_numero(meta), capacidad=_numero(capacidad),
                              precio_base=_numero(precio_base),
                              elasticidad=_acotar(elasticidad, 0.0, 3.0, 1.0),
                              saturacion=_acotar(saturacion, 0.3, 1.0, 0.8))})

    def _acotar(v: float, lo: float, hi: float, defecto: float) -> float:
        try:
            v = float(v)
        except (TypeError, ValueError):
            return defecto
        return defecto if v != v else max(lo, min(hi, v))

    # -- escenarios y decisiones -----------------------------------------

    _CAMPOS_ESCENARIO = ("precio", "gasto_dia", "costo_consulta", "cierre", "solos", "meta", "capacidad",
                         "precio_base", "elasticidad", "saturacion")

    @app.post("/numeros/tablero/escenario", include_in_schema=False)
    async def guardar_escenario(request: Request):
        form = await request.form()
        valores = {k: str(_numero(str(form.get(k, ""))) or 0) for k in _CAMPOS_ESCENARIO}
        nombre = str(form.get("nombre", "")).strip()[:60] or f"Escenario del {estado.ahora():%d/%m %H:%M}"
        conn = estado.conectar()
        try:
            DecisionRepository(conn).guardar(nombre, valores, estado.ahora())
        finally:
            conn.close()
        return RedirectResponse("/numeros/tablero#escenarios", status_code=303)

    @app.post("/numeros/tablero/escenario/{decision_id}/aplicar", include_in_schema=False)
    def aplicar_escenario(decision_id: int):
        """Decidir: los valores del escenario pasan a la configuracion.

        La meta y la capacidad mueven el valor de la hora, y con eso el
        precio sugerido de cada flete. El cierre y el costo por conversacion
        pasan a ser los supuestos de la publicidad. El precio y la
        publicidad por dia quedan como objetivo, para medir contra lo real.
        """
        conn = estado.conectar()
        try:
            repo = DecisionRepository(conn)
            try:
                fila = repo.aplicar(decision_id, estado.ahora())
            except KeyError as e:
                raise HTTPException(404, str(e)) from e
            v = json.loads(fila["valores"])
            ajustes = SettingsRepository(conn)
            actual = F.Costos.desde_dict(ajustes.json("costos")).a_dict()
            for campo, clave in (("meta", "ganancia_objetivo"), ("capacidad", "fletes_por_mes_max"),
                                 ("cierre", "cierre"), ("costo_consulta", "costo_por_consulta")):
                if Decimal(v.get(campo) or 0) > 0:
                    actual[clave] = v[campo]
            ajustes.guardar_json("costos", actual)
        finally:
            conn.close()
        estado.sincronizar()
        return RedirectResponse("/numeros/tablero#decision", status_code=303)

    @app.post("/numeros/tablero/escenario/{decision_id}/borrar", include_in_schema=False)
    def borrar_escenario(decision_id: int):
        conn = estado.conectar()
        try:
            if not DecisionRepository(conn).borrar(decision_id):
                raise HTTPException(409, "solo se borra un escenario guardado, no una decisión")
        finally:
            conn.close()
        return RedirectResponse("/numeros/tablero#escenarios", status_code=303)

    def _decisiones(c: F.Costos, hechos: list[F.FleteHecho], gastos: list[F.GastoPublicidad]) -> dict:
        """Los escenarios guardados, comparados, y como viene la decision vigente."""
        conn = estado.conectar()
        try:
            filas = DecisionRepository(conn).todas()
        finally:
            conn.close()
        escenarios, vigente = [], None
        for f in filas:
            v = {k: Decimal(x) for k, x in json.loads(f["valores"]).items()}
            r = _simulacion(c, precio=v["precio"], gasto_dia=v["gasto_dia"], costo_consulta=v["costo_consulta"],
                            cierre=v["cierre"], solos=v["solos"], meta=v.get("meta"), capacidad=v.get("capacidad"),
                            precio_base=v.get("precio_base") or None,
                            elasticidad=float(v.get("elasticidad") or 1), saturacion=float(v.get("saturacion") or 0.8))
            item = {"id": f["id"], "nombre": f["nombre"], "estado": f["estado"],
                    "cuando": datetime.fromisoformat(f["aplicada"] or f["creada"]), "v": v, "r": r}
            escenarios.append(item)
            if f["estado"] == "aplicada":
                vigente = item
        seguimiento = None
        if vigente:
            desde = vigente["cuando"].date()
            hoy = estado.ahora().date()
            dias = (hoy - desde).days + 1
            desde_entonces = [x for x in hechos if x.dia >= desde]
            cobrados = [x.cobrado for x in desde_entonces if x.cobrado is not None]
            gasto = sum((g.dentro(desde, hoy) for g in gastos), Decimal(0))
            v = vigente["v"]
            meta_fletes = Decimal(vigente["r"]["fletes"]) * dias / 30
            seguimiento = [
                ("Precio promedio", pesos(v["precio"]),
                 pesos(sum(cobrados, Decimal(0)) / len(cobrados)) if cobrados else "sin fletes todavía",
                 bool(cobrados) and sum(cobrados, Decimal(0)) / len(cobrados) >= v["precio"] * Decimal("0.95")),
                ("Publicidad por día", pesos(v["gasto_dia"]), pesos(gasto / dias) if gasto else "sin gasto cargado",
                 bool(gasto) and abs(gasto / dias - v["gasto_dia"]) <= v["gasto_dia"] * Decimal("0.2")),
                ("Fletes", f"{meta_fletes:.0f} a esta altura", f"{len(desde_entonces)} hechos",
                 len(desde_entonces) >= meta_fletes * Decimal("0.9")),
            ]
        return {"escenarios": [e for e in escenarios if e["estado"] != "reemplazada"],
                "historial": [e for e in escenarios if e["estado"] == "reemplazada"],
                "vigente": vigente, "seguimiento": seguimiento}

    @app.get("/numeros/informe.pdf", include_in_schema=False)
    def informe_pdf(periodo: str = "30d", desde: str | None = None, hasta: str | None = None, origen: str = "todos",
                    elasticidad: float = 1.0, saturacion: float = 0.8, tope: str = ""):
        origen = origen if origen in ORIGENES else "todos"
        tope_dia = float(x) if (x := _numero(tope)) else None
        t = _tablero(periodo if periodo in PERIODOS_TABLERO else "30d", desde, hasta, origen, graficos.PAPEL,
                     _acotar(elasticidad, 0.0, 3.0, 1.0), _acotar(saturacion, 0.3, 1.0, 0.8), tope_dia)
        t.pop("pronostico_crudo")
        try:
            contenido = informe.pdf(t)
        except ImportError as e:
            raise HTTPException(501, "Falta WeasyPrint para armar el PDF: uv sync --extra informe") from e
        nombre = f"informe-{estado.ahora():%Y-%m-%d}.pdf"
        return Response(contenido, media_type="application/pdf",
                        headers={"Content-Disposition": f'attachment; filename="{nombre}"'})

    def _numero(texto: str) -> Decimal | None:
        """"8.700", "$ 8700", "17", "0,5": como se escribe un numero aca.

        No pasa por `_monto`: un 20 en "aceite por km" son veinte pesos, no
        veinte mil.
        """
        crudo = re.sub(r"[^\d.,]", "", texto or "")
        # Un punto con uno o dos digitos despues es un decimal ("10.3", lo que
        # escribe un teclado en ingles); con tres es de miles ("8.700").
        if "," not in crudo and re.fullmatch(r"\d+\.\d{1,2}", crudo):
            return Decimal(crudo)
        limpio = crudo.replace(".", "").replace(",", ".")
        if not limpio or limpio.count(".") > 1:
            return None
        return Decimal(limpio)

    def _base() -> dict:
        conn = estado.conectar()
        try:
            return SettingsRepository(conn).json("base") or BASE_DEFAULT
        finally:
            conn.close()

    @app.post("/numeros/objetivos", include_in_schema=False)
    async def guardar_costos(request: Request):
        form = await request.form()
        actual = _costos().a_dict()
        for clave in actual:
            if clave in form and (valor := _numero(str(form[clave]))) is not None:
                actual[clave] = str(valor)
        conn = estado.conectar()
        try:
            ajustes = SettingsRepository(conn)
            ajustes.guardar_json("costos", actual)
            if "negocio" in form:
                ajustes.guardar_json("negocio", {"nombre": str(form["negocio"]).strip()[:80]})
            texto = str(form.get("base", "")).strip()
            base = ajustes.json("base") or BASE_DEFAULT
            if texto and texto != base.get("texto"):
                # Se ubica con el mismo mapa que los fletes. Si no la
                # encuentra, queda la anterior y se avisa: una base mal
                # puesta corre todos los precios.
                u = estado.engine.pricing.ubicar(texto, "") if hasattr(estado.engine.pricing, "ubicar") else None
                if u is None:
                    ajustes.guardar_json("base", {**base, "error": f"No encontré '{texto}'"})
                else:
                    ajustes.guardar_json("base", {"texto": texto, "lat": u.lat, "lon": u.lon, "como": u.como})
            elif "error" in base:
                ajustes.guardar_json("base", {k: v for k, v in base.items() if k != "error"})
        finally:
            conn.close()
        # Los precios sugeridos dependen de todo esto: se recalculan ya.
        estado.sincronizar()
        return RedirectResponse("/numeros/objetivos#costos", status_code=303)

    @app.post("/numeros/objetivos/gasto", include_in_schema=False)
    def cargar_gasto(desde: str = Form(...), hasta: str = Form(...), monto: str = Form(...),
                     resultados: str = Form("")):
        try:
            d, h = date.fromisoformat(desde), date.fromisoformat(hasta)
        except ValueError as e:
            raise HTTPException(422, "las fechas no se entienden") from e
        valor = _numero(monto)
        if h < d:
            raise HTTPException(422, "la fecha de fin es anterior a la de inicio")
        if not valor:
            raise HTTPException(422, "falta cuánto se gastó")
        conn = estado.conectar()
        try:
            conversaciones = int(r) if (r := _numero(resultados)) else None
            GastoRepository(conn).cargar(d, h, valor, resultados=conversaciones)
        finally:
            conn.close()
        return RedirectResponse("/numeros/objetivos#publicidad", status_code=303)

    @app.post("/numeros/objetivos/gasto/{gasto_id}/borrar", include_in_schema=False)
    def borrar_gasto(gasto_id: str):
        conn = estado.conectar()
        try:
            if not GastoRepository(conn).borrar(gasto_id):
                raise HTTPException(404, "ese gasto ya no está")
        finally:
            conn.close()
        return RedirectResponse("/numeros/objetivos#publicidad", status_code=303)

    # ------------------------------------------------------------------
    # Panel: analitica y exportacion, fuera de la mensajeria
    # ------------------------------------------------------------------
    #
    # La mensajeria es el trabajo del dia; el panel es donde uno se sienta a
    # mirar numeros. Son dos modos distintos y no comparten cascara: el panel
    # no muestra bandeja ni contadores, para no empujar a trabajar mientras
    # se analiza.

    #: La cascara del panel. "API" vivia en la barra de trabajo, al lado de
    #: Hoy y Agenda, y no existia en el celular: es documentacion, se mira
    #: sentado, y ese es exactamente el lugar del panel.
    SECCIONES_PANEL = [("/panel", "Cómo viene"), ("/docs", "API")]

    def _contexto_panel(request: Request, **extra) -> dict:
        return {
            "v": _version_estatica(),
            "ruta": request.url.path,
            "secciones": SECCIONES_PANEL,
            **extra,
        }

    @app.get("/panel", response_class=HTMLResponse, include_in_schema=False)
    def pagina_panel(request: Request):
        conn = estado.conectar()
        try:
            repo = PanelRepository(conn)
            datos = {
                "p": repo.propuestas(),
                "precios": repo.estimado_vs_real(),
                "correcciones": repo.correcciones_de_clase(),
                "tablas": repo.tablas(),
            }
        finally:
            conn.close()
        return templates.TemplateResponse(
            request, "panel.html", _contexto_panel(request, titulo="Cómo viene", **datos)
        )

    @app.get("/panel/exportar.json", include_in_schema=False)
    def exportar_json():
        """Todo, con las relaciones intactas: alcanza para levantar otra
        instalacion desde cero. Los datos son del negocio."""
        conn = estado.conectar()
        try:
            repo = PanelRepository(conn)
            todo = {}
            for nombre in repo.tablas():
                columnas, filas = repo.tabla(nombre)
                todo[nombre] = [dict(zip(columnas, f)) for f in filas]
        finally:
            conn.close()
        cuando = estado.ahora().date().isoformat()
        return Response(
            json.dumps({"estiba": "1", "exportado": cuando, "tablas": todo},
                       ensure_ascii=False, indent=2),
            media_type="application/json; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="estiba-{cuando}.json"'},
        )

    @app.get("/panel/exportar/{nombre}.csv", include_in_schema=False)
    def exportar_csv(nombre: str):
        conn = estado.conectar()
        try:
            columnas, filas = PanelRepository(conn).tabla(nombre)
        except KeyError as e:
            raise HTTPException(404, f"no hay una tabla {nombre}") from e
        finally:
            conn.close()
        salida = io.StringIO()
        escritor = csv.writer(salida)
        escritor.writerow(columnas)
        escritor.writerows(filas)
        return Response(
            salida.getvalue(),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{nombre}.csv"'},
        )

    @app.get("/clientes", response_class=HTMLResponse, include_in_schema=False)
    def pagina_clientes(request: Request):
        return templates.TemplateResponse(
            request, "clientes.html", _contexto(request, clientes=_clientes(), titulo="Clientes")
        )

    # ------------------------------------------------------------------
    # Acciones
    # ------------------------------------------------------------------

    def _decidir(draft_id: int, aprobado: bool, texto: str | None) -> P.Borrador:
        conn = estado.conectar()
        try:
            fila = DraftRepository(conn).decide(draft_id, aprobado, texto, estado.ahora())
        except KeyError as e:
            raise HTTPException(404, str(e)) from e
        except ValueError as e:
            raise HTTPException(409, str(e)) from e
        finally:
            conn.close()
        return P.borrador(fila)

    def _enviar(draft_id: int) -> P.Borrador:
        conn = estado.conectar()
        try:
            estado.whatsapp.enviar(conn, draft_id, estado.ahora())
            fila = DraftRepository(conn).get(draft_id)
        finally:
            conn.close()
        estado.sincronizar()
        return P.borrador(fila)

    def _tarjeta(request: Request, b: P.Borrador) -> Response:
        if request.headers.get("HX-Request"):
            return templates.TemplateResponse(
                request, "_borrador.html",
                {"b": b, "f": _fletes().get(b.job_id), "suelto": True, "envio_directo": estado.whatsapp is not None},
            )
        return RedirectResponse(request.headers.get("referer", "/"), status_code=303)

    #: Debajo de esto nadie esta hablando de pesos sueltos: esta diciendo
    #: miles. No existe un flete de $35 ni de $500.
    EN_MILES = Decimal(1000)

    def _monto(texto: str | None) -> Decimal | None:
        """Un precio escrito como se escribe: "45.000", "45000", "45", "45 mil".

        El fletero dice "treinta y cinco" y escribe "35". Antes eso se
        guardaba como treinta y cinco pesos: hay un flete real anotado en
        $35 que ensucia el precio que Estiba aprende a sugerir. Ahora un
        numero suelto por debajo de mil se lee como miles, que es la unica
        lectura posible de un precio de flete.

        Con separador o con cuatro cifras se respeta tal cual: "45.000" y
        "45000" son cuarenta y cinco mil, no cuarenta y cinco millones.
        """
        limpio = re.sub(r"[^\d]", "", (texto or ""))
        if not limpio:
            return None
        valor = Decimal(limpio)
        return valor * EN_MILES if valor < EN_MILES else valor

    def _accion(job_id: str, tipo: str, nota: str | None, monto: Decimal | None = None) -> None:
        if job_id not in estado.resultado.fletes:
            raise HTTPException(404, f"no hay un flete {job_id}")
        ev, _ = estado.resultado.fletes[job_id]
        permitido = {
            REALIZADO: P.puede_realizarse(ev),
            PERDIDO: ev.job.state not in (JobState.DONE, JobState.CANCELLED),
            COTIZADO: True,
            # Decidir precio y fecha tiene sentido mientras el flete no se hizo.
            AJUSTADO: ev.job.state is not JobState.DONE,
            # Anular es lo contrario: solo tiene sentido si esta cerrado.
            ANULADO: ev.job.state in (JobState.DONE, JobState.CANCELLED),
        }[tipo]
        if not permitido:
            if tipo == ANULADO:
                raise HTTPException(409, "este flete no está cerrado: no hay nada que reabrir")
            raise HTTPException(409, f"un flete {ev.job.state.value} no se puede marcar {tipo}")
        conn = estado.conectar()
        try:
            ActionRepository(conn).add(
                job_id, tipo, estado.ahora(), "humano", (nota or "").strip() or None, monto
            )
        finally:
            conn.close()
        estado.sincronizar()

    def _no_es_cliente(job_id: str, nota: str | None, despedida: str | None = None) -> None:
        """Saca la charla del circuito, contestandole algo antes si hace falta.

        Se manda primero: si WhatsApp falla, no se excluye nada y el error se
        ve. Asi nunca queda uno "despedido" al que no le llego la despedida.
        """
        if job_id not in estado.resultado.fletes:
            raise HTTPException(404, f"no hay un flete {job_id}")
        _, conv = estado.resultado.fletes[job_id]
        despedida = (despedida or "").strip()
        if despedida:
            if estado.whatsapp is None:
                raise HTTPException(409, "WhatsApp no esta conectado: no se puede contestar")
            try:
                estado.whatsapp.cliente.enviar_texto(conv.chat_id, despedida)
            except ErrorWaha as e:
                raise HTTPException(502, f"no salio el mensaje, no se saco a nadie: {e}") from e
        conn = estado.conectar()
        try:
            # Sacar algo del circuito es decir que no es negocio: es la
            # misma decision que marcarlo spam, y tiene que vivir en un solo
            # lugar. Asi queda contado en su pestaña y se puede deshacer.
            ClaseRepository(conn).corregir(conv.chat_id, "spam", estado.ahora())
        finally:
            conn.close()
        estado.sincronizar()

    def _precio_aprendido(job_id: str):
        """Que cobrar, segun lo que este fletero cobro antes.

        La tarifa de fabrica supone una ciudad y unos precios que pueden no
        ser los de este negocio. Esto no supone nada: son sus numeros.
        """
        par = estado.resultado.fletes.get(job_id)
        if par is None:
            return None
        ev, _ = par
        conn = estado.conectar()
        try:
            filas = PanelRepository(conn).cotizaciones_pasadas()
        finally:
            conn.close()
        historial = []
        for fila in filas:
            otro = estado.resultado.fletes.get(fila["job_id"])
            if otro is None or fila["job_id"] == job_id:
                continue
            historial.append((aprendido.categoria(otro[0].job), Decimal(fila["monto"])))
        return aprendido.sugerir(ev.job, historial)

    def _vecinos(job_id: str, filtro: str) -> tuple[str | None, str | None]:
        """La anterior y la siguiente, en el orden del filtro que estas usando.

        Resolver una y pasar a la otra sin volver a la lista, respetando lo
        que estabas mirando: si filtraste "Cotizar", seguis entre esas.
        """
        orden = [fila[1].job_id for fila in _conversaciones(filtro)["filas"]]
        if job_id not in orden:
            # Ya no pasa el filtro (recien lo resolviste): sigue el primero.
            return None, orden[0] if orden else None
        i = orden.index(job_id)
        return (orden[i - 1] if i else None, orden[i + 1] if i + 1 < len(orden) else None)

    def _volver(request: Request, a: str) -> Response:
        if request.headers.get("HX-Request"):
            return Response(headers={"HX-Refresh": "true"})
        return RedirectResponse(request.headers.get("referer", a), status_code=303)

    def _aprobar(draft_id: int, texto: str | None) -> P.Borrador:
        b = _decidir(draft_id, True, texto)
        return _enviar(draft_id) if estado.whatsapp is not None else b

    @app.post("/borradores/{draft_id}/aprobar", include_in_schema=False)
    def aprobar_form(request: Request, draft_id: int, texto: str = Form(""),
                     monto: str = Form("")):
        """Aprobar una cotizacion deja registrado el precio que salio.

        No se deduce del texto despues: lo que se mando es lo que se cotizo,
        y con eso se puede comparar contra lo que se termino cobrando.
        """
        b = _aprobar(draft_id, texto)
        if b.tipo == "cotizacion" and (valor := _monto(monto)):
            _accion(b.job_id, COTIZADO, None, valor)
        return _tarjeta(request, b)

    @app.post("/borradores/{draft_id}/reenviar", include_in_schema=False)
    def reenviar_form(request: Request, draft_id: int):
        if estado.whatsapp is None:
            raise HTTPException(409, "WhatsApp no esta conectado")
        try:
            return _tarjeta(request, _enviar(draft_id))
        except ValueError as e:
            raise HTTPException(409, str(e)) from e

    @app.post("/borradores/{draft_id}/descartar", include_in_schema=False)
    def descartar_form(request: Request, draft_id: int):
        return _tarjeta(request, _decidir(draft_id, False, None))

    @app.post("/fletes/{job_id}/realizado", include_in_schema=False)
    def realizado_form(request: Request, job_id: str, cobrado: str = Form("")):
        """Cerrarlo y anotar lo que se cobro, que no siempre es lo cotizado."""
        _accion(job_id, REALIZADO, None, _monto(cobrado))
        return _volver(request, f"/fletes/{job_id}")

    @app.post("/fletes/{job_id}/ajustar", include_in_schema=False)
    def ajustar_form(request: Request, job_id: str, precio: str = Form(""), fecha: str = Form(""),
                     hora: str = Form("")):
        """Confirmar el precio y, si se quiere, la fecha y hora tentativas.

        Lo decide el fletero mirando la cotizacion: le gana a lo que Estiba
        dedujo del chat, entra en el mensaje de cotizacion y en la agenda
        (como "sin confirmar" hasta que el cliente acepte).
        """
        valor = _monto(precio)
        if fecha:
            try:
                date.fromisoformat(fecha)
            except ValueError as e:
                raise HTTPException(422, "la fecha no se entiende") from e
        if hora and not re.fullmatch(r"\d{1,2}:\d{2}", hora):
            raise HTTPException(422, "la hora no se entiende")
        if valor is None and not fecha and not hora:
            raise HTTPException(422, "no hay nada para confirmar")
        _accion(job_id, AJUSTADO, json.dumps({"fecha": fecha or None, "hora": hora or None}), valor)
        return _volver(request, f"/fletes/{job_id}")

    @app.post("/fletes/{job_id}/perdido", include_in_schema=False)
    def perdido_form(request: Request, job_id: str, motivo: str = Form(""), nota: str = Form("")):
        """El motivo se elige de la lista: sirve para los numeros despues."""
        etiquetas = dict(MOTIVOS_PERDIDA)
        texto = etiquetas.get(motivo, "")
        if nota.strip():
            texto = f"{texto}: {nota.strip()}" if texto else nota.strip()
        _accion(job_id, PERDIDO, texto)
        return _volver(request, f"/fletes/{job_id}")

    @app.post("/fletes/{job_id}/reabrir", include_in_schema=False)
    def reabrir_form(request: Request, job_id: str):
        """Deshacer un cierre marcado por error.

        No borra nada: agrega una accion que anula la anterior. El estado se
        vuelve a deducir desde los mensajes, asi que el flete queda donde
        estaba antes de cerrarlo, y el historial conserva las dos cosas.
        """
        _accion(job_id, ANULADO, None)
        return _volver(request, f"/fletes/{job_id}")

    @app.post("/clientes/{chat_id}/clase", include_in_schema=False)
    def clase_form(request: Request, chat_id: str, clase: str = Form(...)):
        """Un toque para resolver una dudosa. Queda guardado que dijo Estiba
        y que dijiste vos: con eso se afinan las reglas mas adelante."""
        if clase not in ("lead", "cliente", "privado", "spam"):
            raise HTTPException(400, f"no se que clase es {clase!r}")
        conn = estado.conectar()
        try:
            ClaseRepository(conn).corregir(chat_id, clase, estado.ahora())
        finally:
            conn.close()
        estado.sincronizar()
        return _volver(request, "/")

    @app.post("/clientes/{chat_id}/borrar", include_in_schema=False)
    def borrar_chat_form(request: Request, chat_id: str):
        """Borra una conversacion de spam, con todo lo suyo. No se deshace."""
        conn = estado.conectar()
        try:
            repo = ClaseRepository(conn)
            fila = repo.de(chat_id)
            if fila is None or fila["clase"] != "spam":
                raise HTTPException(409, "solo se borra lo marcado como spam")
            repo.borrar_chat(chat_id)
        finally:
            conn.close()
        estado.sincronizar()
        return _volver(request, "/?f=spam")

    @app.post("/fletes/{job_id}/otro-flete", include_in_schema=False)
    def otro_flete_form(request: Request, job_id: str, desde: str = Form(...)):
        """Partir la conversacion: de aca en adelante es otro flete.

        Lo que se arregla en persona o por telefono no deja rastro en el
        chat, asi que ninguna regla lo va a separar sola.
        """
        par = estado.resultado.fletes.get(job_id)
        if par is None:
            raise HTTPException(404, f"no hay un flete {job_id}")
        _, conv = par
        if not any(m.id == desde for m in conv.messages):
            raise HTTPException(404, "ese mensaje no es de esta conversación")
        conn = estado.conectar()
        try:
            CorteRepository(conn).cortar(conv.chat_id, desde, estado.ahora())
        finally:
            conn.close()
        estado.sincronizar()
        return _volver(request, "/")

    @app.post("/fletes/{job_id}/unir", include_in_schema=False)
    def unir_flete_form(request: Request, job_id: str):
        """Deshacer un corte: esto era el mismo flete que el anterior.

        Partir mal es tan facil como no partir, y sin vuelta atras nadie se
        anima a usar el boton.
        """
        par = estado.resultado.fletes.get(job_id)
        if par is None:
            raise HTTPException(404, f"no hay un flete {job_id}")
        _, conv = par
        primero = conv.messages[0].id if conv.messages else None
        conn = estado.conectar()
        try:
            repo = CorteRepository(conn)
            if primero is None or primero not in repo.de(conv.chat_id):
                raise HTTPException(409, "este flete no empieza en un corte a mano")
            repo.deshacer(conv.chat_id, primero)
        finally:
            conn.close()
        estado.sincronizar()
        return _volver(request, "/")

    @app.post("/fletes/{job_id}/posponer", include_in_schema=False)
    def posponer_form(request: Request, job_id: str, cuando: str = Form("manana"),
                      fecha: str = Form(""), motivo: str = Form("")):
        """Sacarla de lo urgente sin cerrarla: vuelve sola en la fecha."""
        if job_id not in estado.resultado.fletes:
            raise HTTPException(404, f"no hay un flete {job_id}")
        _, conv = estado.resultado.fletes[job_id]
        ahora = estado.ahora()
        conn = estado.conectar()
        try:
            SnoozeRepository(conn).posponer(
                conv.chat_id, _cuando_vuelve(cuando, ahora, fecha or None), motivo, ahora
            )
        finally:
            conn.close()
        return _volver(request, "/")

    @app.post("/fletes/{job_id}/despertar", include_in_schema=False)
    def despertar_form(request: Request, job_id: str):
        if job_id not in estado.resultado.fletes:
            raise HTTPException(404, f"no hay un flete {job_id}")
        _, conv = estado.resultado.fletes[job_id]
        conn = estado.conectar()
        try:
            SnoozeRepository(conn).despertar(conv.chat_id)
        finally:
            conn.close()
        return _volver(request, "/")

    @app.post("/fletes/{job_id}/no-es-cliente", include_in_schema=False)
    def no_es_cliente_form(request: Request, job_id: str, nota: str = Form(""),
                           despedida: str = Form("")):
        """Charla personal, spam, numero equivocado: se saca para siempre de
        la bandeja, los fletes y Clientes. Los mensajes no se borran."""
        _no_es_cliente(job_id, nota, despedida)
        return _volver(request, "/")

    @app.post("/clientes/{chat_id}/nombre", include_in_schema=False)
    def nombre_form(request: Request, chat_id: str, nombre: str = Form("")):
        conn = estado.conectar()
        try:
            CRMRepository(conn).set_name(chat_id, nombre)
        except KeyError as e:
            raise HTTPException(404, str(e)) from e
        finally:
            conn.close()
        # Guardar el nombre no recarga la pantalla. Con `hx-trigger="change"`,
        # salir del campo devolvia HX-Refresh y se repintaba la conversacion
        # entera: el hilo saltaba al final y se perdia donde estabas leyendo.
        # Lo que se escribio ya esta en pantalla; no hay nada que volver a
        # dibujar. Sin htmx el form navega y el 303 lo trae de vuelta.
        if request.headers.get("HX-Request"):
            return Response(status_code=204)
        return _volver(request, "/clientes")

    @app.post("/sincronizar", include_in_schema=False)
    def sincronizar_form(request: Request):
        estado.sincronizar()
        return _volver(request, "/")

    # ------------------------------------------------------------------
    # WhatsApp
    # ------------------------------------------------------------------

    def _secreto_webhook() -> str:
        conn = estado.conectar()
        try:
            return SettingsRepository(conn).secreto("webhook_waha")
        finally:
            conn.close()

    def _whatsapp_vista() -> P.VistaWhatsApp:
        info = estado.consultar_sesion()
        return P.vista_whatsapp(estado.whatsapp, info, estado.sesion,
                                estado.recuperacion, estado.ahora())

    @app.get("/whatsapp", response_class=HTMLResponse, include_in_schema=False)
    def pagina_whatsapp(request: Request):
        return templates.TemplateResponse(
            request, "whatsapp.html", _contexto(request, w=_whatsapp_vista(), titulo="WhatsApp")
        )

    @app.get("/whatsapp/estado", response_class=HTMLResponse, include_in_schema=False)
    def estado_whatsapp(request: Request):
        return templates.TemplateResponse(request, "_whatsapp_estado.html", {"w": _whatsapp_vista()})

    @app.get("/whatsapp/qr.png", include_in_schema=False)
    def qr_whatsapp():
        if estado.whatsapp is None:
            raise HTTPException(404)
        try:
            return Response(estado.whatsapp.cliente.qr(), media_type="image/png",
                            headers={"Cache-Control": "no-store"})
        except ErrorWaha as e:
            raise HTTPException(404, str(e)) from e

    def _exigir_whatsapp() -> WhatsApp:
        if estado.whatsapp is None:
            raise HTTPException(409, "WhatsApp no esta configurado (ESTIBA_WAHA_URL)")
        return estado.whatsapp

    @app.post("/whatsapp/conectar", include_in_schema=False)
    def conectar_whatsapp(request: Request):
        try:
            _exigir_whatsapp().cliente.conectar(_secreto_webhook())
        except ErrorWaha as e:
            raise HTTPException(502, str(e)) from e
        return _volver(request, "/whatsapp")

    @app.post("/whatsapp/desconectar", include_in_schema=False)
    def desconectar_whatsapp(request: Request):
        try:
            _exigir_whatsapp().cliente.desconectar()
        except ErrorWaha as e:
            raise HTTPException(502, str(e)) from e
        return _volver(request, "/whatsapp")

    @app.post("/whatsapp/importar", include_in_schema=False)
    def importar_whatsapp(request: Request, background: BackgroundTasks, dias: int = Form(30)):
        w = _exigir_whatsapp()
        desde = estado.ahora() - timedelta(days=max(1, min(dias, 365)))
        conn = estado.conectar()
        try:
            w.importar(conn, desde)
        except ErrorWaha as e:
            raise HTTPException(502, str(e)) from e
        finally:
            conn.close()
        background.add_task(_procesar)
        estado.sincronizar()
        return _volver(request, "/")

    def _aperturas_automaticas() -> int:
        """Contestarle solo al que llego por la publicidad, una sola vez.

        Es la unica cosa que sale por WhatsApp sin aprobacion, y las
        condiciones estan todas en `bandeja/automatico.py`. Aca se agrega
        la que no puede vivir alla: que ya haya salido algo antes a esa
        conversacion, que se sabe mirando la tabla de borradores.

        Si WhatsApp no esta conectado no pasa nada: el borrador queda
        pendiente y se aprueba a mano, como cualquier otro.
        """
        if estado.whatsapp is None or not encendido():
            return 0
        salieron = 0
        conn = estado.conectar()
        try:
            repo = DraftRepository(conn)
            candidatos = []
            for fila in repo.pending():
                par = estado.resultado.fletes.get(fila["job_id"])
                if par is None:
                    continue
                ya = conn.execute(
                    "SELECT 1 FROM drafts WHERE chat_id = ? AND sent_at IS NOT NULL",
                    (fila["chat_id"],),
                ).fetchone() is not None
                if sale_sola(par[1], fila["text"], estado.ahora(), ya):
                    candidatos.append((fila["id"], fila["text"]))
        finally:
            conn.close()
        for draft_id, texto in candidatos:
            conn = estado.conectar()
            try:
                # El texto va explicito: aprobar sin texto lo toma como un
                # mensaje vacio y lo rechaza, que es la misma proteccion que
                # impide mandar un borrador en blanco desde la pantalla.
                repo_d = DraftRepository(conn)
                repo_d.decide(draft_id, True, texto, estado.ahora())
                repo_d.marcar_automatico(draft_id)
                estado.whatsapp.enviar(conn, draft_id, estado.ahora())
                salieron += 1
            except (ErrorWaha, ValueError) as e:
                # Que no salga uno no puede frenar el resto ni tirar el
                # webhook: queda pendiente y se manda a mano.
                log.warning("no salio la apertura automatica de %s: %s", draft_id, e)
            finally:
                conn.close()
        return salieron

    #: Cuando avisa la vispera: a la mañana lo que hay que confirmar, a la
    #: tarde lo confirmado (para acomodar el dia siguiente).
    HORA_AVISO_TENTATIVO, HORA_AVISO_CONFIRMADO = 10, 18

    candado_recordatorios = threading.Lock()

    def _recordatorios() -> int:
        """La vispera de cada flete, un aviso en el escritorio. Una sola vez.

        Lo sin confirmar avisa a la mañana, con el sonido fuerte: es el
        momento de escribirle al cliente. Lo confirmado, a la tarde.
        De a uno por vez: dos pasadas juntas leian lo mismo y avisaban doble.
        """
        if estado.avisos is None:
            return 0
        with candado_recordatorios:
            return _recordar_ahora()

    def _recordar_ahora() -> int:
        ahora = estado.ahora()
        manana = ahora.date() + timedelta(days=1)
        conn = estado.conectar()
        try:
            ajustes = SettingsRepository(conn)
            avisados = set((ajustes.json("avisados") or {}).get("claves", []))
            nuevos = 0
            for e, _ in LocalCalendar(conn).eventos(incluir_cancelados=False):
                dia = e.inicio.date() if isinstance(e.inicio, datetime) else e.inicio
                if dia != manana:
                    continue
                hora = HORA_AVISO_TENTATIVO if e.tentativo else HORA_AVISO_CONFIRMADO
                clave = f"{e.uid}:{dia.isoformat()}:{'t' if e.tentativo else 'c'}"
                if ahora.hour < hora or clave in avisados:
                    continue
                titulo = e.titulo.removeprefix("Sin confirmar · ")
                cuando = _cuando_legible(e.inicio.isoformat(), ahora)
                if e.tentativo:
                    estado.avisos.avisar(f"¿Se confirma el flete de {cuando}?",
                                         f"{titulo}\nNo está confirmado: escribile hoy al cliente.", urgente=True)
                else:
                    estado.avisos.avisar(f"Mañana: flete {cuando.removeprefix('mañana').strip()}", titulo)
                avisados.add(clave)
                nuevos += 1
            if nuevos:
                # Solo lo reciente: el conjunto no crece para siempre.
                vigentes = sorted(avisados)[-500:]
                ajustes.guardar_json("avisados", {"claves": vigentes})
            return nuevos
        finally:
            conn.close()

    app.state.recordatorios = _recordatorios

    def _avisar_mensaje(mensaje) -> None:
        """Un mensaje nuevo de alguien que es (o puede ser) cliente.

        Lo privado y el spam no avisan: a eso no se contesta desde Estiba.
        """
        if estado.avisos is None or mensaje is None or not mensaje.from_customer:
            return
        if mensaje.chat_id in estado.resultado.fuera:
            return
        nombre = _nombres().get(mensaje.chat_id) or telefono(mensaje.chat_id)
        texto = mensaje.text or ("🎤 Audio" if mensaje.kind.value == "audio" else "Mandó un archivo")
        conn = estado.conectar()
        try:
            t = conn.execute("SELECT text FROM transcripts WHERE message_id = ? ORDER BY confidence DESC",
                             (mensaje.id,)).fetchone()
        finally:
            conn.close()
        if t:
            texto = f"🎤 {t['text']}"
        estado.avisos.avisar(f"Mensaje de {nombre}", texto[:180])

    def _procesar(mensaje=None) -> None:
        """Despues de recibir: transcribir los audios nuevos, reevaluar y avisar."""
        conn = estado.conectar()
        try:
            estado.whatsapp.transcribir(conn)
        finally:
            conn.close()
        estado.sincronizar()
        if _aperturas_automaticas():
            estado.sincronizar()
        _avisar_mensaje(mensaje)

    def _ponerse_al_dia() -> None:
        """Traer lo que llego con la app cerrada, y procesarlo como si
        hubiera entrado por webhook. Si WAHA no contesta, se anota y se
        vuelve a probar en el proximo repaso: no es motivo para caerse."""
        cuando = estado.ahora()
        conn = estado.conectar()
        try:
            r = estado.whatsapp.ponerse_al_dia(conn, cuando)
        except Exception as e:  # WAHA caido, sesion sin conectar, red
            log.warning("no se pudieron buscar mensajes perdidos: %s", e)
            estado.recuperacion = {"cuando": cuando, "error": str(e) or type(e).__name__}
            return
        finally:
            conn.close()
        if r is None:
            return
        if r.nuevos:
            log.info("recuperados %d mensajes de %d chats", r.nuevos, r.chats)
            _procesar()
            if estado.avisos is not None:
                estado.avisos.avisar(
                    f"{r.nuevos} {'mensaje' if r.nuevos == 1 else 'mensajes'} mientras Estiba estaba cerrada",
                    f"De {r.chats} {'chat' if r.chats == 1 else 'chats'}. Ya están en Hoy.")
        # Se anota al final: "revisado" quiere decir que ya esta en la bandeja.
        estado.recuperacion = {"cuando": cuando, "desde": r.desde, "chats": r.chats, "nuevos": r.nuevos}

    @app.post("/whatsapp/al-dia", include_in_schema=False)
    def al_dia_form(request: Request, background: BackgroundTasks):
        """Buscar ahora, sin esperar al proximo repaso."""
        _exigir_whatsapp()
        background.add_task(_ponerse_al_dia)
        return _volver(request, "/whatsapp")

    @app.post("/webhooks/waha/{secreto}", include_in_schema=False)
    async def webhook_waha(secreto: str, request: Request, background: BackgroundTasks):
        if estado.whatsapp is None or not hmac.compare_digest(secreto, _secreto_webhook()):
            raise HTTPException(404)
        cuerpo = await request.body()
        clave_hmac = estado.whatsapp.cliente.config.hmac_key
        if clave_hmac and not firma_valida(cuerpo, request.headers.get("X-Webhook-Hmac"), clave_hmac):
            raise HTTPException(401, "firma invalida")
        evento = await request.json()
        if evento.get("event") == "session.status":
            estado.sesion = (evento.get("payload") or {}).get("status", estado.sesion)
            return {"ok": True}
        conn = estado.conectar()
        try:
            nuevo = estado.whatsapp.recibir(conn, evento)
        finally:
            conn.close()
        if nuevo is not None:
            background.add_task(_procesar, nuevo)
        return {"ok": True, "nuevo": nuevo is not None}

    # ------------------------------------------------------------------
    # Calendario para suscribirse desde el celular
    # ------------------------------------------------------------------

    @app.get("/audios/{message_id}", include_in_schema=False)
    def audio(message_id: str):
        """El audio original, para escucharlo en vez de leer la transcripcion.

        Transcribir se equivoca: cuando algo no cierra, hay que poder ir a
        la fuente. Solo se sirve lo que esta guardado en la carpeta de
        audios, resuelto contra la ruta real para que un id raro no pueda
        sacar un archivo de otro lado.
        """
        conn = estado.conectar()
        try:
            fila = conn.execute(
                "SELECT media_path FROM messages WHERE id = ? AND kind = 'audio'",
                (message_id,),
            ).fetchone()
        finally:
            conn.close()
        if fila is None or not fila["media_path"]:
            raise HTTPException(404, "ese mensaje no tiene audio")
        ruta = Path(fila["media_path"]).resolve()
        if not ruta.is_relative_to(MEDIA_DIR.resolve()) or not ruta.is_file():
            raise HTTPException(404, "el audio no está guardado")
        return FileResponse(ruta, media_type="audio/ogg")

    @app.get("/calendario/{secreto}.ics", include_in_schema=False)
    def ics(secreto: str):
        conn = estado.conectar()
        try:
            cal = LocalCalendar(conn)
            if not hmac.compare_digest(secreto, cal.token()):
                raise HTTPException(404)
            cuerpo = calendario(cal.eventos(), estado.ahora())
        finally:
            conn.close()
        return Response(cuerpo, media_type="text/calendar; charset=utf-8")

    # ------------------------------------------------------------------
    # API JSON
    # ------------------------------------------------------------------

    @app.get("/api/bandeja", tags=["bandeja"], summary="Borradores pendientes de aprobacion")
    def api_bandeja():
        return [{"borrador": P.a_json(b), "flete": P.a_json(f)} for b, f in _bandeja()]

    @app.post("/api/borradores/{draft_id}/aprobar", tags=["bandeja"],
              summary="Aprobar un borrador, con el texto final")
    def api_aprobar(draft_id: int, decision: Decision):
        return P.a_json(_aprobar(draft_id, decision.texto))

    @app.post("/api/borradores/{draft_id}/descartar", tags=["bandeja"], summary="Descartar un borrador")
    def api_descartar(draft_id: int):
        return P.a_json(_decidir(draft_id, False, None))

    @app.get("/api/fletes", tags=["fletes"], summary="Todos los fletes, por seccion")
    def api_fletes():
        return {clave_: [P.a_json(f) for f in fs] for clave_, _, fs in _secciones(list(_fletes().values()))}

    @app.get("/api/fletes/{job_id}", tags=["fletes"],
             summary="Un flete con cada dato, su evidencia, historia y conversacion")
    def api_flete(job_id: str):
        d = _detalle(job_id)
        datos = P.a_json(d)
        datos["flete"] = P.a_json(d.flete)
        return datos

    @app.post("/api/fletes/{job_id}/acciones", tags=["fletes"],
              summary="Marcar un flete como realizado o darlo por perdido")
    def api_accion(job_id: str, pedido: AccionPedida):
        _accion(job_id, pedido.tipo, pedido.nota)
        return P.a_json(_detalle(job_id).flete)

    @app.get("/api/agenda", tags=["agenda"], summary="Fletes agendados, por dia")
    def api_agenda(request: Request):
        return P.a_json(_agenda(request))

    @app.get("/api/embudo", tags=["numeros"], summary="Embudo de ventas del periodo")
    def api_embudo(periodo: str = "30d"):
        _, n = _embudo(_periodo(periodo))
        return P.a_json(n)

    @app.get("/api/clientes", tags=["numeros"], summary="Clientes con sus fletes")
    def api_clientes():
        return [P.a_json(c) for c in _clientes()]

    @app.get("/api/whatsapp", tags=["sistema"], summary="Estado de la conexion con WhatsApp")
    def api_whatsapp():
        return P.a_json(_whatsapp_vista())

    @app.post("/api/borradores/{draft_id}/reenviar", tags=["bandeja"],
              summary="Reintentar el envio de un borrador aprobado que fallo")
    def api_reenviar(draft_id: int):
        if estado.whatsapp is None:
            raise HTTPException(409, "WhatsApp no esta conectado")
        try:
            return P.a_json(_enviar(draft_id))
        except ValueError as e:
            raise HTTPException(409, str(e)) from e

    @app.post("/api/sincronizar", tags=["sistema"], summary="Reevaluar todas las conversaciones")
    def api_sincronizar():
        estado.sincronizar()
        r = estado.resultado
        return {"fletes": len(r.fletes), "agenda": r.agenda, "espejo_error": r.espejo_error,
                "at": estado.sincronizado_at}

    return app
