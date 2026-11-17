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
import sqlite3
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

from estiba.adapters.horizon import ConfigHorizon, HorizonClient
from estiba.adapters.waha import MEDIA_DIR, ConfigWaha, ErrorWaha, WahaClient, firma_valida
from estiba import horizon as vinculo
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
from estiba.conversation.clasificacion import anuncio_de as clasif_anuncio
from estiba.extraction.texto import plano
from estiba.finanzas.precio import BASE_DEFAULT
from estiba.economia import cargar as eco
from estiba.economia import evidencia
from estiba.economia import historia
from estiba.economia import optimizar
from estiba.economia import mes as eco_mes
from estiba.economia import registro
from estiba.economia import reparaciones
from estiba.economia.cuentas import Actividad
from estiba.economia.cuentas import cuentas as cuentas_economia
from estiba.economia.config import Camioneta, Credito, Disponibilidad, Fondo, a_json
from estiba.bandeja.avisos import merece_aviso
from estiba.crm import personas, recuperacion
from estiba.difusion import grupos as difusion
from estiba.origen import atribucion as origen_atribucion
from estiba.origen import pauta as origen_pauta
from estiba.origen import piezas as origen_piezas
from estiba.origen import resultado as origen_resultado
from estiba.crm.perdida import perdida_de
from estiba.web import economia_vista as EV
from estiba.web import graficos_eco as G
from estiba.formato import pesos, telefono
from estiba.jobs import aprendido, episodios
from estiba.jobs.engine import (
    AJUSTADO,
    CONFIRMADO,
    COTIZADO,
    MOTIVO,
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
    EconomiaRepository,
    FleteRealRepository,
    ReparacionRepository,
    ContactoEventoRepository,
    RecontactoRepository,
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
    #: Una sincronizacion por vez. Entran desde el webhook (en segundo plano,
    #: una por mensaje), el repaso de WhatsApp, el boton y los formularios:
    #: dos a la vez escribian la misma agenda y los mismos borradores, y la
    #: conexion del mapa no se puede usar desde dos hilos al mismo tiempo.
    candado: threading.RLock = field(default_factory=threading.RLock, repr=False)
    #: Cuando llego el ultimo webhook de WAHA, y desde cuando corre la app.
    ultimo_webhook: datetime | None = None
    arranque: datetime = field(default_factory=datetime.now)

    def conectar(self):
        return connect(self.db)

    def sincronizar(self) -> None:
        with self.candado:
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
    horizon: HorizonClient | None = None,
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
    # Horizon (tablero en la nube) solo con la app de verdad: los tests no
    # tienen que salir a internet por su cuenta.
    if horizon is None and repaso and (config_horizon := ConfigHorizon.desde_entorno()):
        horizon = HorizonClient(config_horizon)
    estado.horizon = {"configurado": horizon is not None}

    @asynccontextmanager
    async def ciclo(_app: FastAPI):
        estado.sincronizar()
        estado.consultar_sesion()
        _asegurar_webhook()
        # Ponerse al dia va aparte: con muchos chats y audios tarda, y la
        # app tiene que abrir ya. Lo recuperado aparece en cuanto llega.
        tareas = []
        if estado.whatsapp and repaso:
            tareas.append(asyncio.create_task(_repasar()))
        if estado.avisos is not None:
            tareas.append(asyncio.create_task(_recordar()))
        if horizon is not None:
            tareas.append(asyncio.create_task(_vincular()))
        yield
        for tarea in tareas:
            tarea.cancel()
            with suppress(asyncio.CancelledError):
                await tarea

    async def _recordar() -> None:
        while True:
            await run_in_threadpool(_recordatorios)
            await asyncio.sleep(300)

    async def _vincular() -> None:
        # Despues del primer repaso, para mandar ya con lo recuperado.
        await asyncio.sleep(20)
        while True:
            await run_in_threadpool(_sincronizar_horizon)
            await asyncio.sleep(600)

    async def _repasar() -> None:
        while True:
            await run_in_threadpool(_asegurar_webhook)
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
    # Una cifra negativa se pinta roja: la clase sale del texto ya formateado.
    templates.env.filters["signo"] = lambda t: "negativo" if str(t).strip().startswith(("−", "-", "~−")) else ""
    templates.env.filters["titular"] = lambda f: EV.partir(f)[0]
    templates.env.filters["detalle"] = lambda f: EV.partir(f)[1]

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
        ("todos", "Todos"), ("decidir", "Para decidir"), ("hoy", "Hoy"), ("responder", "Responder"),
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
        if filtro == "decidir":
            # Minima intervencion: solo lo que necesita criterio. Un pedido que
            # conviene y ya tiene su cotizacion lista no molesta.
            if b is None:
                return False
            ev = estado.resultado.evaluaciones.get(f.job_id)
            return ev is None or ev.capa.value != "automatico" or b.tipo != "cotizacion"
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

    def _agenda(request: Request, mes: str | None = None, dia: str | None = None) -> P.Agenda:
        conn = estado.conectar()
        try:
            cal = LocalCalendar(conn)
            eventos = cal.eventos(incluir_cancelados=False)
            url = str(request.base_url).rstrip("/") + f"/calendario/{cal.token()}.ics"
            comodo = int(eco.economia(conn, estado.ahora().date()).disponibilidad.fletes_dia_comodo) or 2
        finally:
            conn.close()
        seleccion = None
        if dia:
            try:
                seleccion = date.fromisoformat(dia)
            except ValueError:
                seleccion = None
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
        return P.agenda(eventos, _fletes(), estado.ahora(), url, mirando, seleccion, comodo)

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

    def _personas() -> dict[str, "personas.Persona"]:
        """Cada persona con todas sus oportunidades, lo anotado y los recontactos.

        Se arma de nuevo cada vez, como el resto: nada de esto se guarda ya
        calculado. Ver docs/recuperacion.md.
        """
        conn = estado.conectar()
        try:
            acciones = ActionRepository(conn)
            eventos = ContactoEventoRepository(conn).todos()
            recontactos = RecontactoRepository(conn).todos()
            vistos = _fletes()
            nombres = _nombres()
            salida: dict[str, personas.Persona] = {}
            for lead in estado.resultado.leads:
                par = estado.resultado.fletes.get(lead.job_id)
                if par is None:
                    continue
                ev, conv = par
                suyas = acciones.for_job(lead.job_id)
                cierres = [a for a in sin_anuladas(suyas) if a.tipo == REALIZADO]
                primero = conv.text_of(conv.messages[0]) if conv.messages else ""
                f = vistos.get(lead.job_id)
                o = personas.Oportunidad(
                    job_id=lead.job_id, creado=lead.creado, etapa=lead.etapa, alcanzo=lead.alcanzo,
                    historia=lead.historia, ruta=f.ruta if f else None, cosas=f.cosas if f else None,
                    cotizado=lead.cotizado, cobrado=cierres[-1].monto if cierres else None,
                    perdida=perdida_de(ev, suyas),
                    por_anuncio=bool(_DESDE_PUBLI.search(plano(primero or ""))))
                p = salida.get(conv.chat_id)
                if p is None:
                    mensajes = conv.messages
                    suyos = [m for m in mensajes if m.from_customer]
                    p = personas.Persona(
                        chat_id=conv.chat_id, nombre=nombres.get(conv.chat_id), telefono=telefono(conv.chat_id),
                        oportunidades=[],
                        eventos=[personas.Evento(e["tipo"], datetime.fromisoformat(e["at"]), e["valor"], e["nota"])
                                 for e in eventos.get(conv.chat_id, [])],
                        recontactos=[personas.Recontacto(
                            r["id"], r["plantilla"], r["secuencia"], r["paso"], r["texto"], r["estado"],
                            datetime.fromisoformat(r["creado"]),
                            datetime.fromisoformat(r["decidido"]) if r["decidido"] else None, r["job_id"])
                            for r in recontactos.get(conv.chat_id, [])],
                        ultimo_suyo=suyos[-1].sent_at if suyos else None,
                        ultimo_contacto=mensajes[-1].sent_at if mensajes else None,
                        primer_contacto=mensajes[0].sent_at if mensajes else None,
                        mensajes_suyos=len(suyos),
                        fuera=conv.chat_id in estado.resultado.fuera,
                        canal_inferido="meta" if o.por_anuncio else "otro")
                    salida[conv.chat_id] = p
                p.oportunidades.append(o)
            for p in salida.values():
                p.oportunidades.sort(key=lambda o: o.creado)
            return salida
        finally:
            conn.close()

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
    def pagina_bandeja(request: Request, f: str | None = None):
        validos = [c for c, _ in FILTROS + FILTROS_APARTE]
        # En modo minima intervencion, Hoy abre en lo que necesita criterio.
        defecto = "decidir" if _minima_intervencion() else "todos"
        filtro = f if f in validos else defecto
        return templates.TemplateResponse(
            request, "bandeja.html",
            _contexto(request, **_conversaciones(filtro), por_confirmar=_por_confirmar(), titulo="Hoy",
                      linea_mes=_linea_mes()),
        )

    def _minima_intervencion() -> bool:
        conn = estado.conectar()
        try:
            return bool((EconomiaRepository(conn).config("modo") or {}).get("minima"))
        finally:
            conn.close()

    @app.post("/numeros/camioneta/modo", include_in_schema=False)
    def guardar_modo(minima: str = Form("")):
        _guardar_eco("modo", {"minima": minima == "1"})
        return RedirectResponse("/numeros/camioneta#modo", status_code=303)

    def _linea_mes() -> str | None:
        """Una sola linea en Hoy: como va la camioneta este mes. Si algo falla,
        no se muestra: Hoy es para trabajar, no para auditar."""
        try:
            m, _ = _mes_actual()
        except Exception:
            logging.getLogger(__name__).exception("no se pudo calcular el mes")
            return None
        return EV.frase_mes(m)

    @app.get("/agenda", response_class=HTMLResponse, include_in_schema=False)
    def pagina_agenda(request: Request, mes: str | None = None, dia: str | None = None):
        return templates.TemplateResponse(
            request, "agenda.html", _contexto(request, a=_agenda(request, mes, dia), titulo="Agenda")
        )

    @app.get("/agenda/dia/{iso}", response_class=HTMLResponse, include_in_schema=False)
    def agenda_dia(request: Request, iso: str):
        """El panel de un dia, para cambiarlo sin recargar la pagina."""
        try:
            date.fromisoformat(iso)
        except ValueError as e:
            raise HTTPException(422, "la fecha no se entiende") from e
        a = _agenda(request, iso[:7], iso)
        return templates.TemplateResponse(request, "_agenda_dia.html", _contexto(request, a=a))

    @app.post("/fletes/{job_id}/confirmar", include_in_schema=False)
    def confirmar_form(request: Request, job_id: str):
        """El cliente acepto por otro lado: queda confirmado y se agenda."""
        _accion(job_id, CONFIRMADO, "desde la agenda")
        return _volver(request, "/agenda")

    @app.post("/agenda/mover", include_in_schema=False)
    def mover_form(request: Request, job_id: str = Form(...), fecha: str = Form(...), hora: str = Form("")):
        """Cambiar el dia (o la hora) de un flete, arrastrandolo o desde el panel.

        Es un ajuste como el de la cotizacion: le gana a lo que dijo el chat.
        Se conserva lo que ya estaba decidido y no se toca: el precio
        confirmado y, si no se manda hora, la hora que tenia.
        """
        try:
            nueva = date.fromisoformat(fecha)
        except ValueError as e:
            raise HTTPException(422, "la fecha no se entiende") from e
        if hora and not re.fullmatch(r"\d{1,2}:\d{2}", hora):
            raise HTTPException(422, "la hora no se entiende")
        if job_id not in estado.resultado.fletes:
            raise HTTPException(404, f"no hay un flete {job_id}")
        ev, _ = estado.resultado.fletes[job_id]
        if not hora and ev.job.hour:
            hora = f"{ev.job.hour.value:%H:%M}"
        conn = estado.conectar()
        try:
            previos = [a for a in ActionRepository(conn).for_job(job_id) if a.tipo == AJUSTADO]
        finally:
            conn.close()
        monto = previos[-1].monto if previos else None
        _accion(job_id, AJUSTADO, json.dumps({"fecha": nueva.isoformat(), "hora": hora or None}), monto)
        if request.headers.get("HX-Request"):
            return Response(headers={"HX-Redirect": f"/agenda?mes={nueva:%Y-%m}&dia={nueva.isoformat()}"})
        return RedirectResponse(f"/agenda?mes={nueva:%Y-%m}&dia={nueva.isoformat()}", status_code=303)

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
        conn5 = estado.conectar()
        try:
            perdida = perdida_de(ev_flete, ActionRepository(conn5).for_job(job_id))
        finally:
            conn5.close()
        cot = P.cotizacion(ev_flete.presupuesto,
                           ev_flete.job.quoted_price.value if ev_flete.job.quoted_price else None, cobrado,
                           job=ev_flete.job, confirmado=ev_flete.precio_confirmado,
                           ajustable=ev_flete.job.state not in (JobState.DONE, JobState.CANCELLED))
        return templates.TemplateResponse(
            request, "flete.html",
            _contexto(
                request, d=d, cot=cot, anterior=anterior, siguiente=siguiente, filtro=filtro,
                estimado=EV.estimado_cierre(estado.resultado.evaluaciones.get(job_id)),
                canales=registro.CANALES,
                plazos=PLAZOS, dudosa=bool(clase and clase.pide_decision),
                motivos=MOTIVOS_PERDIDA,
                perdida=perdida,
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
                              Decimal(g["spend"]), g["resultados"],
                              Decimal(g["impuestos"]) if g["impuestos"] is not None else None,
                              bool(g["incluye_impuestos"]))
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
        # El pronostico razona en las unidades de Meta (sin impuestos): el
        # impuesto se agrega una sola vez, al pasar a plata pagada.
        tasa = _costos().impuestos_publicidad
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
            gasto=sum((g.neto_dentro(desde, hoy, tasa) for g in gastos), Decimal(0)),
            # Solo si todos los gastos de la ventana dicen cuantas trajeron:
            # mezclar las de Meta con las que reconoce Estiba no da nada.
            consultas_meta=round(sum(g.resultados * g.parte(desde, hoy) for g in en_ventana))
            if (en_ventana := [g for g in gastos if g.parte(desde, hoy) > 0])
            # Un dia importado sin conversaciones dice 0: es un dato, no un faltante.
            and all(g.resultados is not None for g in en_ventana) else None,
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
            gasto = sum((g.neto_dentro(desde, hoy, c.impuestos_publicidad) for g in gastos), Decimal(0))
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
                     resultados: str = Form(""), impuestos: str = Form(""), tipo: str = Form("neto")):
        """`tipo` dice que es el monto: "neto" (lo que muestra Meta) o "total" (la factura)."""
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
            GastoRepository(conn).cargar(d, h, valor, resultados=conversaciones,
                                         impuestos=_numero(impuestos) if impuestos.strip() else None,
                                         incluye_impuestos=tipo == "total")
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
    # Camioneta: lo que el fletero quiere que el negocio haga por el
    # ------------------------------------------------------------------

    def _economia():
        conn = estado.conectar()
        try:
            return eco.economia(conn, estado.ahora().date())
        finally:
            conn.close()

    @app.get("/numeros/camioneta", response_class=HTMLResponse, include_in_schema=False)
    def pagina_camioneta(request: Request):
        hoy = estado.ahora().date()
        conn = estado.conectar()
        try:
            e = eco.economia(conn, hoy)
            historial = EconomiaRepository(conn).historial_precio_base()
        finally:
            conn.close()
        d = e.disponibilidad
        disp = [
            ("dias_semana", "Días por semana que querés trabajar", EV.campo(d.dias_semana), "días"),
            ("fletes_dia_comodo", "Fletes en un día cómodo", EV.campo(d.fletes_dia_comodo), ""),
            ("fletes_dia_pico", "Fletes en un día de mucha demanda (excepción)", EV.campo(d.fletes_dia_pico), ""),
            ("horas_dia", "Horas máximas por día", EV.campo(d.horas_dia), "h"),
            ("tope_horas_mes", "Tope de horas por mes (opcional)", EV.campo(d.tope_horas_mes), "h"),
            ("coordinacion_min", "Coordinar un flete (mensajes, presupuesto)", EV.campo(d.h_coordinacion * 60), "min"),
            ("cobro_min", "Cobrar y cerrar", EV.campo(d.h_cobro * 60), "min"),
            ("valor_hora_libre", "Cuánto vale para vos una hora libre (opcional)", EV.campo(d.valor_hora_libre), "$"),
        ]
        c = e.camioneta
        activo = [
            ("valor_mercado", "Valor de mercado hoy", EV.campo(c.valor_mercado), "$", ""),
            ("valor_reposicion", "Lo que costaría reponerla", EV.campo(c.valor_reposicion), "$", ""),
            ("odometro", "Km del odómetro", EV.campo(c.odometro), "km", ""),
            ("depreciacion_mes", "Depreciación por mes", EV.campo(c.depreciacion_mes), "$",
             "opcional: un supuesto tuyo; vacío = no se descuenta"),
        ]
        conn = estado.conectar()
        try:
            rrepo = ReparacionRepository(conn)
            saldos = {f.clave: reparaciones.saldo(rrepo.movimientos(f.clave)) for f in e.fondos}
            reps = rrepo.todas()
        finally:
            conn.close()
        fondos = [{"clave": f.clave, "nombre": f.nombre, "aporte": EV.campo(f.aporte_mes) if f.aporte_mes else "",
                   "objetivo": EV.campo(f.objetivo), "saldo": EV.plata(saldos[f.clave]),
                   "avance": (min(100, max(0, round(float(saldos[f.clave] / f.objetivo * 100))))
                              if f.objetivo else None)} for f in e.fondos]
        return templates.TemplateResponse(
            request, "camioneta.html",
            _contexto(request, v=EV.vista_camioneta(e, hoy, historial), disp=disp, activo=activo, fondos=fondos,
                      hoy=hoy.isoformat(), titulo="Camioneta", despues=EV.despues_del_credito(e, hoy),
                      reparaciones=EV.vista_reparaciones(reps), minima=_minima_intervencion()),
        )

    @app.post("/numeros/camioneta/reparacion", include_in_schema=False)
    def cargar_reparacion(fecha: str = Form(...), componente: str = Form(...), costo: str = Form(...),
                          km: str = Form(""), dias_parada: str = Form("0"), tipo: str = Form(""),
                          fondo: str = Form("")):
        try:
            dia = date.fromisoformat(fecha)
        except ValueError as e:
            raise HTTPException(422, "la fecha no se entiende") from e
        valor = _monto(costo)
        if not valor or not componente.strip():
            raise HTTPException(422, "falta qué se arregló o cuánto costó")
        comp = componente.strip()[:80]
        rep = reparaciones.Reparacion(
            dia, comp, tipo if tipo in (reparaciones.ORDINARIA, reparaciones.EXTRAORDINARIA)
            else reparaciones.tipo_sugerido(comp), valor, _numero(km) if km.strip() else None,
            int(_numero(dias_parada) or 0), fondo.strip() or None)
        conn = estado.conectar()
        try:
            ReparacionRepository(conn).cargar(rep, estado.ahora())
        finally:
            conn.close()
        return RedirectResponse("/numeros/camioneta#reparaciones", status_code=303)

    @app.post("/numeros/camioneta/fondos/{clave}/mover", include_in_schema=False)
    def mover_fondo(clave: str, monto: str = Form(...), tipo: str = Form("aporte")):
        valor = _monto(monto)
        if not valor:
            raise HTTPException(422, "falta el monto")
        conn = estado.conectar()
        try:
            ReparacionRepository(conn).mover(clave, estado.ahora().date(), valor if tipo == "aporte" else -valor,
                                             estado.ahora(), "aporte" if tipo == "aporte" else "ajuste")
        finally:
            conn.close()
        return RedirectResponse("/numeros/camioneta#fondos", status_code=303)

    def _guardar_eco(clave: str, valor) -> None:
        conn = estado.conectar()
        try:
            EconomiaRepository(conn).guardar(clave, valor, estado.ahora())
        finally:
            conn.close()
        estado.sincronizar()

    def _opcional(form, clave: str) -> str | None:
        texto = str(form.get(clave, "")).strip()
        return str(v) if texto and (v := _numero(texto)) is not None else None

    @app.post("/numeros/camioneta/precio-base", include_in_schema=False)
    def cambiar_precio_base(monto: str = Form(...), desde: str = Form(...), nota: str = Form("")):
        valor = _numero(monto)
        if not valor or valor <= 0:
            raise HTTPException(422, "el precio base tiene que ser un monto")
        try:
            dia = date.fromisoformat(desde)
        except ValueError as e:
            raise HTTPException(422, "la fecha no se entiende") from e
        conn = estado.conectar()
        try:
            EconomiaRepository(conn).cambiar_precio_base(valor, dia, estado.ahora(), nota.strip()[:120] or None)
        finally:
            conn.close()
        estado.sincronizar()
        return RedirectResponse("/numeros/camioneta#precio-base", status_code=303)

    @app.post("/numeros/camioneta/disponibilidad", include_in_schema=False)
    async def guardar_disponibilidad(request: Request):
        form = await request.form()
        actual = a_json(_economia().disponibilidad)
        for clave in ("dias_semana", "fletes_dia_comodo", "fletes_dia_pico", "horas_dia"):
            if (v := _opcional(form, clave)) is not None:
                actual[clave] = v
        for clave in ("tope_horas_mes", "valor_hora_libre"):
            if clave in form:
                actual[clave] = _opcional(form, clave)
        for campo_min, clave in (("coordinacion_min", "h_coordinacion"), ("cobro_min", "h_cobro")):
            if (v := _opcional(form, campo_min)) is not None:
                actual[clave] = str(Decimal(v) / 60)
        if Decimal(actual["fletes_dia_pico"]) < Decimal(actual["fletes_dia_comodo"]):
            actual["fletes_dia_pico"] = actual["fletes_dia_comodo"]
        _guardar_eco("disponibilidad", a_json(Disponibilidad.desde_dict(actual)))
        return RedirectResponse("/numeros/camioneta#disponibilidad", status_code=303)

    @app.post("/numeros/camioneta/activo", include_in_schema=False)
    async def guardar_activo(request: Request):
        form = await request.form()
        camioneta = {k: _opcional(form, k) for k in ("valor_mercado", "valor_reposicion", "odometro",
                                                     "depreciacion_mes")}
        _guardar_eco("camioneta", a_json(Camioneta.desde_dict(camioneta)))
        primera = str(form.get("primera", "")).strip()
        credito = {"cuotas": _opcional(form, "cuotas") or "72",
                   "primera": f"{primera}-01" if re.fullmatch(r"\d{4}-\d{2}", primera) else None,
                   "interes_mes": _opcional(form, "interes_mes")}
        _guardar_eco("credito", a_json(Credito.desde_dict(credito)))
        return RedirectResponse("/numeros/camioneta#activo", status_code=303)

    @app.post("/numeros/camioneta/fondos", include_in_schema=False)
    async def guardar_fondos(request: Request):
        form = await request.form()
        fondos = []
        for f in _economia().fondos:
            aporte = _opcional(form, f"{f.clave}__aporte_mes")
            objetivo = _opcional(form, f"{f.clave}__objetivo")
            fondos.append(Fondo(f.clave, f.nombre, f.tipo, Decimal(aporte) if aporte else Decimal(0),
                                Decimal(objetivo) if objetivo else None))
        _guardar_eco("fondos", a_json(tuple(fondos)))
        return RedirectResponse("/numeros/camioneta#fondos", status_code=303)


    # ------------------------------------------------------------------
    # ¿Como estoy? El mes de la camioneta
    # ------------------------------------------------------------------

    def _fletes_del_mes(e, hoy: date) -> tuple[list, list]:
        """Los hechos del mes y los de los 60 dias anteriores."""
        todos = _fletes_eco(e)
        desde = hoy.replace(day=1)
        return ([f for f in todos if desde <= f.dia <= hoy],
                sorted((f for f in todos if desde - timedelta(days=60) <= f.dia < desde), key=lambda f: f.dia))

    def _fletes_eco(e) -> list:
        """Todos los hechos, con lo mejor que se sepa de cada uno: el cierre
        confirmado, si no la evaluacion, si no tu promedio."""
        conn = estado.conectar()
        try:
            reales = FleteRealRepository(conn).todos()
        finally:
            conn.close()
        uso_prom_km, uso_prom_h = e.costos.km_por_flete, e.costos.horas_flete
        salida = []
        for h in _fletes_hechos():
            r = reales.get(h.job_id)
            ev = estado.resultado.evaluaciones.get(h.job_id)
            est = registro.desde_evaluacion(ev) if ev else None
            km_ = (r.km if r and r.km is not None else None) or (est.km if est else None) or uso_prom_km
            vacios = (r.km_vacios if r and r.km_vacios is not None else None) or (est.km_vacios if est else None)
            horas_ = (r.horas if r and r.horas is not None else None) or (est.horas if est else None) or uso_prom_h
            salida.append(eco_mes.FleteDelMes(
                job_id=h.job_id, dia=h.dia, cobrado=h.cobrado or Decimal(0), km=km_, km_vacios=vacios,
                horas=horas_ + (r.espera_h if r else Decimal(0)),
                directos=r.directos if r else (est.ayudante_pagado if est else Decimal(0)),
                recurrente=(r.canal == "recurrente") if r else bool(ev and ev.cliente.recurrente),
                medido=bool(r and r.medido)))
        return sorted(salida, key=lambda f: f.dia)

    def _reparaciones() -> list:
        conn = estado.conectar()
        try:
            return ReparacionRepository(conn).todas()
        finally:
            conn.close()

    def _evidencia():
        hoy = estado.ahora().date()
        e = _economia()
        fletes = _fletes_eco(e)
        hechos = _fletes_hechos()
        _, gastos = _gastos()
        h = _historia(hechos, gastos, hoy)
        conn = estado.conectar()
        try:
            cambios = EconomiaRepository(conn).historial_precio_base()
        finally:
            conn.close()
        cambio = date.fromisoformat(cambios[0]["desde"]) if len(cambios) > 1 else None
        datos = evidencia.Datos(
            fletes=fletes, cliente_de={f.job_id: f.job_id.split(":job")[0] for f in fletes},
            consultas_publicidad=h.consultas_meta or h.consultas_publicidad,
            hechos_publicidad=h.hechos_publicidad, dias_publicidad=h.dias, cambio_precio=cambio,
            reparaciones_grandes=reparaciones.extraordinarias(_reparaciones()))
        return e, evidencia.catalogo(e, datos, hoy), hoy

    def _base_plan(e, cat, fletes, hoy):
        """El flete tipico y la demanda, desde lo medido; lo que falta, de tu configuracion."""
        _, gastos = _gastos()
        h = _historia(_fletes_hechos(), gastos, hoy)
        dias = max(h.dias, 1)
        tasa = e.costos.impuestos_publicidad
        consultas = h.consultas_meta or h.consultas_publicidad
        pagado = float(sum((g.pagado_dentro(hoy - timedelta(days=dias - 1), hoy, tasa) for g in gastos), Decimal(0)))
        con_vacios = [f for f in fletes if f.km_vacios is not None and f.km]
        prop_vacios = (float(sum(f.km_vacios for f in con_vacios) / sum(f.km for f in con_vacios))
                       if con_vacios else 0.75)
        km = float(cat.km.valor or e.costos.km_por_flete)
        return optimizar.Base(
            ticket=float(cat.ticket.valor or e.precio_base),
            km=km, km_vacios=km * prop_vacios,
            horas=float(cat.horas.valor or e.costos.horas_flete),
            directos=float(sum(f.directos for f in fletes) / len(fletes)) if fletes else 0.0,
            organicos=h.hechos_sin_publicidad * 30 / dias,
            cierre=float(cat.conversion.valor if cat.conversion.valor is not None else e.costos.cierre / 100),
            costo_conversacion=(pagado / consultas if consultas and pagado
                                else float(e.costos.costo_por_consulta * e.costos.con_impuestos)),
            conversaciones_base=consultas * 30 / dias if consultas else 0.0,
            calidad={"margen": cat.margen.estado, "horas": cat.horas.estado, "conversión": cat.conversion.estado,
                     "ticket": cat.ticket.estado, "km": cat.km.estado, "recurrencia": cat.recurrencia.estado,
                     "elasticidad": cat.elasticidad.estado},
        )

    @app.get("/numeros/plan", response_class=HTMLResponse, include_in_schema=False)
    def pagina_plan(request: Request):
        e, cat, hoy = _evidencia()
        m, _ = _mes_actual()
        fletes = _fletes_eco(e)
        b = _base_plan(e, cat, fletes, hoy)
        uso = e.costos.costo_km
        rec = optimizar.lo_mas_simple(
            e, b, hoy, m.prioridades, ya_dejado=float(m.disponible - m.km * uso),
            dias_restantes=m.dias_mes - hoy.day, horas_hechas=float(m.horas))
        proyectados = max(1, round(len(m.fletes) * m.dias_mes / max(1, hoy.day))) if m.fletes else \
            max(1, round(float(e.disponibilidad.fletes_mes())))
        puntos = optimizar.curva(e, b, hoy)
        sim = optimizar.simular_mes(e, b, hoy, [float(f.cobrado) for f in fletes if f.cobrado],
                                    [float(f.km) for f in fletes])
        nombres = _fletes()
        return templates.TemplateResponse(
            request, "plan.html",
            _contexto(request, v=EV.vista_plan(rec, optimizar.escenarios(
                e, b, hoy, proyectados, "Como vengo" if m.fletes else "Tu ritmo cómodo"), puntos,
                                               optimizar.marginal_por_hora(puntos),
                                               optimizar.hasta_donde_conviene(
                                                   puntos, float(e.disponibilidad.valor_hora_libre)
                                                   if e.disponibilidad.valor_hora_libre else None),
                                               optimizar.mejor_por_hora(puntos), sim, m,
                                               e.disponibilidad.valor_hora_libre),
                      encadenar=[{"a": nombres[x.primero].quien if x.primero in nombres else x.primero,
                                  "b": nombres[x.segundo].quien if x.segundo in nombres else x.segundo,
                                  "dia": x.dia.strftime("%d/%m"), "km": round(x.ahorro_km)}
                                 for x in _encadenables(e)],
                      titulo="¿Qué pasa si…?"),
        )

    def _historia_aprendida(e, hoy):
        """Segmentos, canales, jornadas, LTV y oportunidad: cada uno con su suficiencia."""
        fletes = _fletes_eco(e)
        conn = estado.conectar()
        try:
            reales = FleteRealRepository(conn).todos()
        finally:
            conn.close()
        cliente_de = {f.job_id: f.job_id.split(":job")[0] for f in fletes}
        segmento_de, canal_de, cac_de_cliente = {}, {}, {}
        for f in fletes:
            par = estado.resultado.fletes.get(f.job_id)
            job = par[0].job if par else None
            segmento_de[f.job_id] = historia.segmento(
                f.km, job.origin.value if job and job.origin else None,
                job.destination.value if job and job.destination else None,
                job.items.value if job and job.items else None)
            ev = estado.resultado.evaluaciones.get(f.job_id)
            r = reales.get(f.job_id)
            canal_de[f.job_id] = r.canal if r else registro.canal_inferido(
                bool(ev and ev.cliente.recurrente), bool(ev and ev.cliente.por_publicidad))
            if ev is not None and ev.cac.monto:
                cac_de_cliente.setdefault(cliente_de[f.job_id], ev.cac.monto)
        uso = e.costos.costo_km
        anuncio_de = {}
        for f in fletes:
            par = estado.resultado.fletes.get(f.job_id)
            if par and par[1].messages:
                if (cual := clasif_anuncio(plano(par[1].text_of(par[1].messages[0]) or ""))) is not None:
                    anuncio_de[f.job_id] = cual
        grupos_j, conclusion_j = historia.jornadas(fletes, uso, cliente_de, hoy)
        oportunidad = historia.costo_oportunidad_disponible(fletes, hoy)
        return EV.vista_historia(
            historia.por_segmento(fletes, segmento_de, uso, cliente_de, hoy),
            historia.por_canal(fletes, canal_de, uso, cliente_de, hoy),
            grupos_j, conclusion_j, historia.ltv(fletes, cliente_de, cac_de_cliente, uso), oportunidad, _nombres(),
            historia.por_anuncio(fletes, anuncio_de, uso, cliente_de, hoy))

    def _encadenables(e):
        pedidos = []
        for job_id, (ev, _) in estado.resultado.fletes.items():
            if ev.job.state in (JobState.DONE, JobState.CANCELLED) or ev.presupuesto is None or not ev.job.when:
                continue
            d = ev.presupuesto.detalle or {}
            pedidos.append((job_id, ev.job.when.value, d.get("tramos"), d.get("puntos")))
        return historia.encadenables(pedidos, float(e.costos.factor_calles))

    @app.get("/numeros/validacion", response_class=HTMLResponse, include_in_schema=False)
    def pagina_validacion(request: Request):
        e, cat, hoy = _evidencia()
        v = evidencia.validar(e, cat, hoy)
        return templates.TemplateResponse(
            request, "validacion.html",
            _contexto(request, v=v, metricas=EV.vista_catalogo(cat), h=_historia_aprendida(e, hoy),
                      grafico_horas=EV.grafico_horas_meta(v),
                      titulo="¿Vale la pena?"),
        )

    def _prioridades() -> tuple[str, ...]:
        conn = estado.conectar()
        try:
            return eco_mes.prioridades_desde(EconomiaRepository(conn).config("prioridades"))
        finally:
            conn.close()

    def _mes_actual():
        hoy = estado.ahora().date()
        e = _economia()
        del_mes, historia = _fletes_del_mes(e, hoy)
        _, gastos = _gastos()
        publicidad = eco_mes.publicidad_diaria(gastos, e.costos.impuestos_publicidad, hoy.replace(day=1), hoy)
        conn = estado.conectar()
        try:
            reps = ReparacionRepository(conn).todas()
        finally:
            conn.close()
        parada = reparaciones.del_periodo(reps, hoy.replace(day=1), hoy, e.disponibilidad)
        m = eco_mes.calcular(e, hoy, del_mes, publicidad, historia, _prioridades(),
                             reparaciones_caja=parada.costo_caja, dias_parada=parada.dias,
                             fletes_perdidos=parada.fletes_perdidos)
        fraccion = Decimal(hoy.day) / Decimal(m.dias_mes)
        c = cuentas_economia(e, hoy, Actividad(m.ingresos, m.km, m.publicidad, m.directos, m.horas), fraccion)
        return m, c

    def _evolucion(e, cat, hoy: date) -> dict | None:
        """Semana a semana desde la primera actividad, con los hitos que la explican."""
        from estiba.economia import evolucion as evo
        from estiba.economia.estructura import contribucion_por_hora, estructura_mes

        fletes = _fletes_eco(e)
        _, gastos = _gastos()
        inicios = [f.dia for f in fletes] + [g.desde for g in gastos]
        if not inicios:
            return None
        desde = min(inicios)
        publicidad = eco_mes.publicidad_diaria(gastos, e.costos.impuestos_publicidad, desde, hoy)
        fijos = sum((p_.monto for p_ in estructura_mes(e)), Decimal(0)) + (e.credito.interes_mes or Decimal(0))
        ss = evo.semanas(fletes, publicidad, e.costos.costo_km, fijos, hoy, desde)
        hitos = []
        if fletes:
            hitos.append(evo.Hito(fletes[0].dia, "Primer flete", "flete"))
        hitos += evo.hitos_de_publicidad(publicidad)
        conn = estado.conectar()
        try:
            precios = list(reversed(EconomiaRepository(conn).historial_precio_base()))
            reps = ReparacionRepository(conn).todas()
            decisiones = DecisionRepository(conn).todas()
        finally:
            conn.close()
        for i, f in enumerate(precios):
            hitos.append(evo.Hito(date.fromisoformat(f["desde"]),
                                  f"Precio base {EV.plata(Decimal(f['monto']))}" if i else
                                  f"Precio base inicial {EV.plata(Decimal(f['monto']))}", "precio"))
        for r in reps:
            hitos.append(evo.Hito(r.fecha, f"Reparación: {r.componente}", "reparacion"))
        for d in decisiones:
            if d["aplicada"]:
                hitos.append(evo.Hito(datetime.fromisoformat(d["aplicada"]).date(), f"Decisión: {d['nombre']}",
                                      "decision"))
        return EV.vista_evolucion(ss, evo.ordenar(hitos, ss[0].inicio if ss else desde, hoy),
                                  contribucion_por_hora(e, hoy).minima, not cat.margen.fuerte)

    def _en_curso(e, hoy: date) -> dict | None:
        """Las consultas abiertas, cuanto pueden dejar y con que probabilidad."""
        from estiba.economia import en_curso

        leads = estado.resultado.leads
        dias = (hoy - min(l.creado.date() for l in leads)).days if leads else 0
        probs = en_curso.probabilidades(leads, dias)
        abiertos = []
        for lead in leads:
            par = estado.resultado.fletes.get(lead.job_id)
            ev = par[0] if par else None
            p = ev.presupuesto if ev else None
            calculado = p.total if p is not None and p.total else None
            cuando = ev.job.when.value if ev and ev.job.when else None
            abiertos.append((lead, calculado, cuando, bool(p is not None and p.completo)))
        ps = en_curso.posibles(abiertos, probs, e.precio_base, hoy)
        quien = {j: f.quien for j, f in _fletes().items()}
        return EV.vista_en_curso(ps, quien)

    def _fuentes(e, hoy: date) -> list[dict]:
        """De donde sale cada numero de los tableros, y cuanto creerle."""
        from estiba.economia.estructura import estructura_mes

        conn = estado.conectar()
        try:
            acciones, reales = ActionRepository(conn), FleteRealRepository(conn).todos()
            hechos = anotado = cotizado = medidos = dia_chat = 0
            for job_id, (ev, _) in estado.resultado.fletes.items():
                if ev.job.state is not JobState.DONE:
                    continue
                hechos += 1
                cierres = [a for a in sin_anuladas(acciones.for_job(job_id)) if a.tipo == REALIZADO]
                if cierres and cierres[-1].monto:
                    anotado += 1
                elif ev.job.quoted_price:
                    cotizado += 1
                r = reales.get(job_id)
                medidos += bool(r and r.km is not None and r.horas is not None)
                dia_chat += bool(ev.job.when)
        finally:
            conn.close()
        _, gastos = _gastos()
        fijos = sum((p_.monto for p_ in estructura_mes(e)), Decimal(0))
        return EV.vista_fuentes(hechos, anotado, cotizado, medidos, dia_chat,
                                max((g.hasta for g in gastos), default=None), hoy,
                                P.hace(estado.sincronizado_at, estado.ahora()) if estado.sincronizado_at else None,
                                fijos)

    @app.get("/numeros/mes", response_class=HTMLResponse, include_in_schema=False)
    def pagina_mes(request: Request):
        m, c = _mes_actual()
        e, cat, hoy = _evidencia()
        textos = {k: v[0] for k, v in eco_mes.PRIORIDADES.items()}
        return templates.TemplateResponse(
            request, "mes.html",
            _contexto(request, v=EV.vista_mes(m, c, textos, cat), evo=_evolucion(e, cat, hoy),
                      curso=_en_curso(e, hoy), fuentes=_fuentes(e, hoy),
                      opciones=list(textos.items()),
                      elegidas=list(m.prioridades), titulo="¿Cómo estoy?"),
        )

    @app.post("/numeros/mes/prioridades", include_in_schema=False)
    async def guardar_prioridades(request: Request):
        form = await request.form()
        elegidas = [str(form.get(f"p{i}", "")) for i in range(3)]
        _guardar_eco("prioridades", list(eco_mes.prioridades_desde([p for p in elegidas if p])))
        return RedirectResponse("/numeros/mes#prioridades", status_code=303)

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

    # ------------------------------------------------------------------
    # Recuperacion: volver a escribirle a quien no contrato (docs/recuperacion.md)
    # ------------------------------------------------------------------

    def _config_recuperacion() -> tuple[dict, dict]:
        """Plantillas y secuencias: las de fabrica, con lo que se edito encima."""
        conn = estado.conectar()
        try:
            guardado = SettingsRepository(conn).json("recuperacion") or {}
        finally:
            conn.close()
        plantillas = {k: dict(v) for k, v in recuperacion.PLANTILLAS.items()}
        for k, v in (guardado.get("plantillas") or {}).items():
            plantillas.setdefault(k, {}).update(v)
        secuencias = {k: dict(v) for k, v in recuperacion.SECUENCIAS.items()}
        for k, v in (guardado.get("secuencias") or {}).items():
            if k in secuencias:
                secuencias[k].update(v)
        return plantillas, secuencias

    FILTROS_COLA = [("hoy", "Para hacer hoy"), ("programados", "Programados"), ("enviados", "Enviados"),
                    ("recuperados", "Recuperados"), ("todos", "Todos")]

    @app.get("/numeros/recuperacion", response_class=HTMLResponse, include_in_schema=False)
    def tablero_recuperacion(request: Request):
        """Cuanto se pierde, por que, cuanto se recupera y de donde vienen los clientes."""
        _, secuencias = _config_recuperacion()
        gente = list(_personas().values())
        ahora = estado.ahora()
        items = recuperacion.cola(gente, ahora, secuencias=secuencias)
        _, gastos = _gastos()
        tasa = _costos().impuestos_publicidad
        pagado = sum((g.pagado(tasa) for g in gastos), Decimal(0)) or None
        t = recuperacion.tablero(gente, items, ahora, pagado)
        cuenta = lambda v: str(int(v))
        graficos = {
            "embudo": G.barras([(n, float(v), "g-s3" if n == "Contrataron" else "g-s1") for n, v in t.embudo],
                               cuenta, titulo="De presupuesto perdido a cliente recuperado", izq=120),
            "motivos": G.barras([(n[:28], float(v), "g-s2") for n, v in t.resumen.motivos[:8]], cuenta,
                                titulo="Por qué se pierden los fletes", izq=190) if t.resumen.motivos else "",
            "canales": G.barras([(f.nombre[:24], round((f.conversion or 0) * 100), "g-s1") for f in t.canales],
                                lambda v: f"{int(v)}%", titulo="Cuántas consultas terminan en flete, por origen",
                                izq=170) if t.canales else "",
        }
        return templates.TemplateResponse(
            request, "numeros_recuperacion.html",
            _contexto(request, t=t, graficos=graficos, pendientes_cola=sum(
                1 for i in items if i.estado in recuperacion.ACCIONABLES), titulo="Recuperación"))

    @app.get("/numeros/origen", response_class=HTMLResponse, include_in_schema=False)
    def tablero_origen(request: Request, periodo: str = "30d"):
        """De donde viene cada consulta (pago u organico) y que paso con ella."""
        periodo = _periodo(periodo)
        desde, hasta, _ = _rango(periodo, None, None)
        primeros = {}
        for job_id, (_, conv) in estado.resultado.fletes.items():
            if conv.messages:
                primeros[job_id] = (plano(conv.text_of(conv.messages[0]) or ""), conv.messages[0].sent_at)
        oportunidades = [o for p in _personas().values() for o in p.oportunidades]
        conn = estado.conectar()
        try:
            nuevas = origen_atribucion.sincronizar(conn, primeros)
            filas = origen_resultado.por_pieza(
                oportunidades, origen_piezas.todas(conn), origen_atribucion.vigentes(conn),
                origen_pauta.por_anuncio(conn, desde, hasta), _costos().impuestos_publicidad, desde, hasta)
        finally:
            conn.close()
        return templates.TemplateResponse(
            request, "numeros_origen.html",
            _contexto(request, filas=filas, nuevas=nuevas, periodo=periodo, periodos=PERIODOS,
                      tasa=_costos().impuestos_publicidad, titulo="Origen"))

    @app.get("/grupos", response_class=HTMLResponse, include_in_schema=False)
    def pagina_grupos(request: Request):
        """La cola del dia en grupos de Facebook, el catalogo y los pedidos vistos."""
        conn = estado.conectar()
        try:
            hoy = estado.ahora().date()
            ctx = dict(cola=difusion.cola(conn, hoy), grupos=difusion.todos(conn), pedidos=difusion.pedidos(conn),
                       temas=difusion.TEMAS, dias=difusion.DIAS, por_dia=difusion.POR_DIA,
                       espaciado=difusion.ESPACIADO_DIAS)
        finally:
            conn.close()
        return templates.TemplateResponse(request, "grupos.html", _contexto(request, titulo="Grupos", **ctx))

    @app.post("/grupos", include_in_schema=False)
    async def agregar_grupo(request: Request):
        form = await request.form()
        conn = estado.conectar()
        try:
            difusion.agregar(
                conn, str(form.get("nombre", "")), url=str(form.get("url", "")).strip() or None,
                zona=str(form.get("zona", "")).strip() or "Mar del Plata", tema=str(form.get("tema", "general")),
                dias=tuple(int(d) for d in form.getlist("dias") if str(d).isdigit()),
                acepta_publicidad=bool(form.get("acepta_publicidad")),
                reglas=str(form.get("reglas", "")).strip() or None)
        except (ValueError, sqlite3.IntegrityError) as e:
            raise HTTPException(422, f"no se agrego el grupo: {e}") from e
        finally:
            conn.close()
        return RedirectResponse("/grupos", status_code=303)

    @app.post("/grupos/{grupo_id}/vigente", include_in_schema=False)
    async def grupo_vigente(grupo_id: int, request: Request):
        form = await request.form()
        conn = estado.conectar()
        try:
            difusion.pausar(conn, grupo_id, vigente=str(form.get("vigente")) == "1")
        finally:
            conn.close()
        return RedirectResponse("/grupos", status_code=303)

    @app.post("/grupos/publicado", include_in_schema=False)
    async def grupo_publicado(request: Request):
        form = await request.form()
        conn = estado.conectar()
        try:
            hoy = estado.ahora().date()
            posteo = next((p for p in difusion.cola(conn, hoy) if str(p.grupo.id) == str(form.get("grupo_id"))), None)
            if posteo is None:
                raise HTTPException(422, "ese grupo no esta en la cola de hoy")
            difusion.publicado(conn, posteo, hoy)
        finally:
            conn.close()
        return RedirectResponse("/grupos", status_code=303)

    @app.post("/grupos/pedidos", include_in_schema=False)
    async def anotar_pedido(request: Request):
        form = await request.form()
        grupo = str(form.get("grupo_id", ""))
        conn = estado.conectar()
        try:
            difusion.anotar_pedido(conn, str(form.get("texto", "")), estado.ahora(),
                                   int(grupo) if grupo.isdigit() else None,
                                   str(form.get("contacto", "")).strip() or None)
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        finally:
            conn.close()
        return RedirectResponse("/grupos", status_code=303)

    @app.post("/grupos/pedidos/{pedido_id}", include_in_schema=False)
    async def resolver_pedido(pedido_id: int, request: Request):
        form = await request.form()
        conn = estado.conectar()
        try:
            difusion.resolver_pedido(conn, pedido_id, str(form.get("estado", "")), estado.ahora())
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        finally:
            conn.close()
        return RedirectResponse("/grupos", status_code=303)

    @app.get("/recuperacion", response_class=HTMLResponse, include_in_schema=False)
    def pagina_recuperacion(request: Request, ver: str = "hoy", motivo: str = "", previo: str = "",
                            etiqueta: str = ""):
        plantillas, secuencias = _config_recuperacion()
        gente = list(_personas().values())
        ahora = estado.ahora()
        items = recuperacion.cola(gente, ahora, secuencias=secuencias)
        grupos = {"hoy": recuperacion.ACCIONABLES, "programados": (recuperacion.PROGRAMADO,),
                  "enviados": (recuperacion.ENVIADO,), "recuperados": (recuperacion.RECUPERADO,)}
        vista = [i for i in items
                 if (ver == "todos" or i.estado in grupos.get(ver, recuperacion.ACCIONABLES))
                 and (not motivo or i.motivo == motivo)
                 and (not previo or (previo == "si") == bool(i.persona.completados))
                 and (not etiqueta or etiqueta in i.persona.etiquetas)]
        return templates.TemplateResponse(
            request, "recuperacion.html",
            _contexto(request, items=vista, cuenta={k: sum(1 for i in items if i.estado in v)
                                                     for k, v in grupos.items()},
                      ver=ver, filtros=FILTROS_COLA, motivo=motivo, previo=previo, etiqueta=etiqueta,
                      motivos=sorted({i.motivo for i in items}),
                      etiquetas=sorted({e for i in items for e in i.persona.etiquetas}),
                      plantillas=plantillas, secuencias=secuencias, hoy=ahora.date(),
                      en_horario=recuperacion.REGLAS.en_horario(ahora), envio_directo=estado.whatsapp is not None,
                      titulo="Recuperación"))

    @app.get("/recuperacion/plantillas", response_class=HTMLResponse, include_in_schema=False)
    def pagina_plantillas(request: Request):
        plantillas, secuencias = _config_recuperacion()
        return templates.TemplateResponse(
            request, "plantillas.html",
            _contexto(request, plantillas=plantillas, secuencias=secuencias, reglas=recuperacion.REGLAS,
                      variables=["nombre", "origen", "destino", "fecha_consulta", "precio_anterior",
                                 "dias_desde_consulta", "tipo_flete"],
                      titulo="Plantillas y secuencias"))

    @app.post("/recuperacion/plantillas", include_in_schema=False)
    async def guardar_plantillas(request: Request):
        """Textos y dias editados. Lo de fabrica queda debajo: borrar un texto lo vuelve al original."""
        form = await request.form()
        plantillas, secuencias = _config_recuperacion()
        guardar: dict = {"plantillas": {}, "secuencias": {}}
        for clave in plantillas:
            nombre = str(form.get(f"p-{clave}-nombre", "")).strip()
            texto = str(form.get(f"p-{clave}-texto", "")).strip()
            original = recuperacion.PLANTILLAS.get(clave, {})
            cambio = {k: v for k, v in (("nombre", nombre), ("texto", texto)) if v and v != original.get(k)}
            if cambio:
                guardar["plantillas"][clave] = cambio
        nueva = re.sub(r"[^a-z0-9-]+", "-", plano(str(form.get("nueva-clave", ""))).lower()).strip("-")
        if nueva and str(form.get("nueva-texto", "")).strip():
            guardar["plantillas"][nueva] = {"nombre": str(form.get("nueva-nombre", "")).strip() or nueva,
                                            "texto": str(form.get("nueva-texto", "")).strip()}
        for clave, sec in secuencias.items():
            pasos = []
            for i, (dias, plantilla) in enumerate(sec["pasos"]):
                d = str(form.get(f"s-{clave}-{i}-dias", dias)).strip()
                pl = str(form.get(f"s-{clave}-{i}-plantilla", plantilla))
                if not d.isdigit() or not 1 <= int(d) <= 730:
                    raise HTTPException(422, "los días van de 1 a 730")
                if pl not in plantillas and pl not in guardar["plantillas"]:
                    raise HTTPException(422, f"no hay una plantilla {pl}")
                pasos.append([int(d), pl])
            if pasos != recuperacion.SECUENCIAS[clave]["pasos"]:
                guardar["secuencias"][clave] = {"pasos": pasos}
        conn = estado.conectar()
        try:
            SettingsRepository(conn).guardar_json("recuperacion", guardar)
        finally:
            conn.close()
        return RedirectResponse("/recuperacion/plantillas", status_code=303)

    @app.post("/recuperacion/{chat_id}/preparar", include_in_schema=False)
    def preparar_recontacto(request: Request, chat_id: str, plantilla: str = Form("")):
        """Escribe el mensaje con la plantilla del paso (o la elegida). No lo manda."""
        plantillas, secuencias = _config_recuperacion()
        p = _personas().get(chat_id)
        if p is None:
            raise HTTPException(404, f"no hay una persona {chat_id}")
        item = recuperacion.item_de(p, estado.ahora(), secuencias=secuencias)
        if item is None or item.estado in (recuperacion.NO_CONTACTAR, recuperacion.NO_INTERESADO,
                                           recuperacion.RECUPERADO, recuperacion.CADUCADO):
            raise HTTPException(409, "a esta persona no corresponde escribirle ahora")
        clave = plantilla or item.plantilla or "consulta-antigua"
        if clave not in plantillas:
            raise HTTPException(422, f"no hay una plantilla {clave}")
        texto = recuperacion.redactar(plantillas[clave]["texto"],
                                      recuperacion.variables(p, item.oportunidad, estado.ahora().date()))
        conn = estado.conectar()
        try:
            RecontactoRepository(conn).preparar(chat_id, item.oportunidad.job_id if item.oportunidad else None,
                                                clave, item.secuencia, item.paso, texto, estado.ahora())
        finally:
            conn.close()
        return _volver(request, f"/recuperacion#p-{chat_id}")

    def _fijar_perdida(job_id: str | None) -> None:
        """Al escribirle, el pedido viejo queda cerrado de verdad.

        Un perdido por silencio se recalcula desde el ultimo mensaje: el
        nuestro lo "reabria" y la bandeja proponia otro seguimiento encima.
        Se anota "no respondio" (lo que paso), justo antes del mensaje.
        """
        if not job_id or job_id not in estado.resultado.fletes:
            return
        ev, _ = estado.resultado.fletes[job_id]
        conn = estado.conectar()
        try:
            perdida = perdida_de(ev, ActionRepository(conn).for_job(job_id))
            if perdida is not None and not perdida.decidida:
                ActionRepository(conn).add(
                    job_id, PERDIDO, estado.ahora() - timedelta(seconds=1), "estiba",
                    json.dumps({"motivo": perdida.codigo, "etiqueta": perdida.etiqueta,
                                "nota": "fijado al volver a escribirle"}, ensure_ascii=False))
        finally:
            conn.close()

    def _recontacto(recontacto_id: int):
        conn = estado.conectar()
        try:
            r = RecontactoRepository(conn).get(recontacto_id)
        finally:
            conn.close()
        if r is None or r["estado"] != "preparado":
            raise HTTPException(409, "ese mensaje ya no está para mandar")
        p = _personas().get(r["chat_id"])
        # Se vuelve a mirar al momento de mandar: entre preparar y mandar
        # pudo pedir que no le escriban, o contestar, o contratar.
        if p is None or p.no_contactar or p.no_interesado or p.fuera:
            raise HTTPException(409, "esta persona no quiere que le escriban")
        return r, p

    @app.post("/recontactos/{recontacto_id}/enviar", include_in_schema=False)
    def enviar_recontacto(request: Request, recontacto_id: int, texto: str = Form(...)):
        r, p = _recontacto(recontacto_id)
        if estado.whatsapp is None:
            raise HTTPException(409, "WhatsApp no está conectado: copialo y mandalo vos")
        if not recuperacion.REGLAS.en_horario(estado.ahora()):
            raise HTTPException(409, "fuera de horario: se manda entre las 9 y las 20")
        texto = texto.strip()
        if not texto:
            raise HTTPException(422, "el mensaje está vacío")
        _fijar_perdida(r["job_id"])
        conn = estado.conectar()
        try:
            try:
                enviado = estado.whatsapp.cliente.enviar_texto(r["chat_id"], texto)
            except ErrorWaha as e:
                raise HTTPException(502, f"no salió: {e}") from e
            RecontactoRepository(conn).decidir(recontacto_id, "enviado", estado.ahora(), texto,
                                               message_id=str((enviado or {}).get("id") or "") or None)
        finally:
            conn.close()
        estado.sincronizar()
        return _volver(request, "/recuperacion")

    @app.post("/recontactos/{recontacto_id}/copiado", include_in_schema=False)
    def copiado_recontacto(request: Request, recontacto_id: int, texto: str = Form(...)):
        """Lo mandaste vos desde el celular (copiado o abierto en WhatsApp)."""
        r, p = _recontacto(recontacto_id)
        _fijar_perdida(r["job_id"])
        conn = estado.conectar()
        try:
            RecontactoRepository(conn).decidir(recontacto_id, "copiado", estado.ahora(), texto.strip() or None)
        finally:
            conn.close()
        estado.sincronizar()
        return _volver(request, "/recuperacion")

    @app.post("/recontactos/{recontacto_id}/descartar", include_in_schema=False)
    def descartar_recontacto(request: Request, recontacto_id: int):
        conn = estado.conectar()
        try:
            try:
                RecontactoRepository(conn).decidir(recontacto_id, "descartado", estado.ahora())
            except KeyError as e:
                raise HTTPException(409, str(e)) from e
        finally:
            conn.close()
        return _volver(request, "/recuperacion")

    @app.get("/clientes/{chat_id}", response_class=HTMLResponse, include_in_schema=False)
    def pagina_persona(request: Request, chat_id: str):
        """La ficha 360: todo lo que paso con una persona, en orden."""
        p = _personas().get(chat_id)
        if p is None:
            raise HTTPException(404, f"no hay una persona {chat_id}")
        return templates.TemplateResponse(
            request, "persona.html",
            _contexto(request, p=p, rep=personas.reputacion(p), hitos=list(reversed(personas.historial(p))),
                      etiquetas=sorted(set(personas.ETIQUETAS) - set(p.etiquetas)),
                      canales=registro.CANALES, nombre_canal=dict(registro.CANALES),
                      titulo=p.nombre or p.telefono))

    @app.post("/clientes/{chat_id}/evento", include_in_schema=False)
    def evento_persona(request: Request, chat_id: str, tipo: str = Form(...), valor: str = Form(""),
                       nota: str = Form("")):
        """Anotar algo sobre la persona. Se agrega al historial: nada se borra."""
        if tipo not in personas.EVENTOS:
            raise HTTPException(422, f"no se puede anotar {tipo}")
        valor = valor.strip()
        if tipo in ("etiqueta+", "etiqueta-"):
            valor = re.sub(r"[^a-z0-9_]+", "_", plano(valor).lower()).strip("_")
            if not valor:
                raise HTTPException(422, "falta la etiqueta")
        elif tipo == "reputacion" and not (valor.isdigit() and 0 <= int(valor) <= 100):
            raise HTTPException(422, "la reputación va de 0 a 100")
        elif tipo == "programado":
            try:
                date.fromisoformat(valor)
            except ValueError as e:
                raise HTTPException(422, "la fecha no se entiende") from e
        elif tipo == "origen" and valor not in registro.CLAVES_CANAL:
            raise HTTPException(422, f"origen desconocido: {valor}")
        elif tipo == "nota" and not nota.strip():
            raise HTTPException(422, "la nota está vacía")
        if chat_id not in _personas():
            raise HTTPException(404, f"no hay una persona {chat_id}")
        conn = estado.conectar()
        try:
            ContactoEventoRepository(conn).add(chat_id, tipo, estado.ahora(), valor or None, nota)
        finally:
            conn.close()
        return _volver(request, f"/clientes/{chat_id}")

    FILTROS_PERSONA = [("todos", "Todos"), ("lead", "Nunca contrataron"), ("cliente", "Clientes"),
                       ("recurrente", "Recurrentes"), ("recuperado", "Recuperados"), ("no-contactar", "No contactar")]

    @app.get("/clientes", response_class=HTMLResponse, include_in_schema=False)
    def pagina_clientes(request: Request, situacion: str = "todos", etiqueta: str = ""):
        gente = _personas()
        def pasa(p) -> bool:
            if etiqueta and etiqueta not in p.etiquetas:
                return False
            return {"todos": True, "lead": p.situacion == personas.LEAD,
                    "cliente": p.situacion != personas.LEAD, "recurrente": p.situacion == personas.RECURRENTE,
                    "recuperado": p.recuperado, "no-contactar": p.no_contactar or p.no_interesado}.get(situacion, True)
        lista = [c for c in _clientes() if c.chat_id in gente and pasa(gente[c.chat_id])]
        todas = sorted({e for p in gente.values() for e in p.etiquetas})
        return templates.TemplateResponse(
            request, "clientes.html",
            _contexto(request, clientes=lista, gente=gente, reputacion=personas.reputacion,
                      filtros=FILTROS_PERSONA, situacion=situacion, etiqueta=etiqueta, etiquetas=todas,
                      titulo="Clientes"))

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
            # Confirmar a mano: lo cotizado que el cliente acepto por otro lado.
            CONFIRMADO: ev.job.state in (JobState.QUOTED, JobState.WAITING_CUSTOMER),
            # Anotar por que se perdio: solo sobre algo perdido.
            MOTIVO: ev.job.state is JobState.CANCELLED,
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

    def _a_la_lista(request: Request) -> Response:
        """Despues de cerrar un flete (perdido, hecho) se vuelve a la lista de donde se vino.

        Quedarse en la ficha de algo ya cerrado obligaba a volver a mano. La
        ficha recuerda el filtro de la bandeja en `?f=`, igual que su "Hoy".
        """
        from urllib.parse import parse_qs, urlencode, urlsplit

        origen = request.headers.get("HX-Current-URL") or request.headers.get("referer") or ""
        # Desde otra pantalla (la agenda cierra fletes en linea) se queda donde estaba.
        if not urlsplit(origen).path.startswith("/fletes/"):
            return _volver(request, "/")
        filtro = (parse_qs(urlsplit(origen).query).get("f") or [""])[0]
        destino = "/" + (("?" + urlencode({"f": filtro})) if filtro and filtro != "todos" else "")
        if request.headers.get("HX-Request"):
            return Response(headers={"HX-Redirect": destino})
        return RedirectResponse(destino, status_code=303)

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
    def realizado_form(request: Request, job_id: str, cobrado: str = Form(""), visto: str = Form(""),
                       km: str = Form(""), km_vacios: str = Form(""), horas: str = Form(""),
                       espera_min: str = Form(""), ayudante: str = Form(""), otros: str = Form(""),
                       canal: str = Form("")):
        """Cerrarlo y anotar lo que se cobro y como fue de verdad.

        Si el formulario mostraba la estimacion (`visto`) y no se corrigio
        nada, queda como confirmada: "asi fue". Ver economia/registro.py.
        """
        estimado = registro.desde_evaluacion(estado.resultado.evaluaciones.get(job_id))
        cambios = {
            "km": _numero(km) if km.strip() else None,
            "km_vacios": _numero(km_vacios) if km_vacios.strip() else None,
            "horas": _horas(horas) if horas.strip() else None,
            "espera_h": _numero(espera_min) / 60 if espera_min.strip() and _numero(espera_min) is not None else None,
            "ayudante_pagado": _monto(ayudante) if ayudante.strip() else None,
            "otros_gastos": _monto(otros) if otros.strip() else None,
            "canal": canal.strip() or None,
        }
        _accion(job_id, REALIZADO, None, _monto(cobrado))
        conn = estado.conectar()
        try:
            FleteRealRepository(conn).guardar(job_id, registro.cerrar(estimado, visto=visto == "1", cambios=cambios),
                                              estado.ahora())
        finally:
            conn.close()
        return _a_la_lista(request)

    def _horas(texto: str) -> Decimal | None:
        """"2:30", "2,5", "2 h 30": horas como las escribe cualquiera."""
        if m := re.fullmatch(r"\s*(\d+)\s*(?::|h)\s*(\d{1,2})?\s*(?:min)?\s*", texto):
            return Decimal(m.group(1)) + Decimal(m.group(2) or 0) / 60
        return _numero(texto)

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

    def _nota_perdida(motivo: str, nota: str, competencia: str) -> str | None:
        """El motivo de una perdida, con codigo estable. Ver docs/recuperacion.md."""
        etiquetas = dict(MOTIVOS_PERDIDA)
        if motivo and motivo not in etiquetas:
            raise HTTPException(422, f"motivo desconocido: {motivo}")
        datos = {"motivo": motivo or None, "etiqueta": etiquetas.get(motivo),
                 "nota": nota.strip() or None,
                 "competencia": str(_monto(competencia)) if competencia.strip() and _monto(competencia) else None}
        return json.dumps(datos, ensure_ascii=False) if any(datos.values()) else None

    @app.post("/fletes/{job_id}/perdido", include_in_schema=False)
    def perdido_form(request: Request, job_id: str, motivo: str = Form(""), nota: str = Form(""),
                     competencia: str = Form("")):
        """El motivo se elige de la lista: sirve para los numeros y para saber si
        vale la pena volver a escribirle."""
        _accion(job_id, PERDIDO, _nota_perdida(motivo, nota, competencia))
        return _a_la_lista(request)

    @app.post("/fletes/{job_id}/motivo", include_in_schema=False)
    def motivo_form(request: Request, job_id: str, motivo: str = Form(...), nota: str = Form(""),
                    competencia: str = Form("")):
        """Anotar o corregir por que se perdio, sin cambiar el estado. Vale el ultimo."""
        _accion(job_id, MOTIVO, _nota_perdida(motivo, nota, competencia))
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

    def _asegurar_webhook() -> None:
        """Que WAHA apunte a esta Estiba; si no, se corrige y queda en el log."""
        if estado.whatsapp is None:
            return
        try:
            vieja = estado.whatsapp.asegurar_webhook(_secreto_webhook())
        except Exception as e:  # WAHA caido: se vuelve a mirar en el proximo repaso
            log.warning("no se pudo revisar el webhook de WAHA: %s", e)
            return
        if vieja is not None:
            log.warning("el webhook de WAHA apuntaba a %s: corregido a %s", vieja,
                        estado.whatsapp.cliente.config.url_estiba)

    def _avisar_traidos(mensajes) -> None:
        """Lo que trajo el repaso, con la misma regla que el webhook: un aviso
        por chat que lo merece, no un "N mensajes" que cuenta tambien los
        tuyos y los privados."""
        if estado.avisos is None:
            return
        avisar = []
        for m in sorted(mensajes, key=lambda m: m.sent_at):
            if m.from_customer and m.chat_id not in estado.resultado.fuera and m.chat_id not in [x.chat_id for x in avisar]:
                conn = estado.conectar()
                try:
                    anteriores = ConversationStore(conn).get(m.chat_id).messages
                finally:
                    conn.close()
                if merece_aviso(m, anteriores):
                    avisar.append(m)
        if len(avisar) == 1:
            _avisar_mensaje(avisar[0], ya_decidido=True)
        elif avisar:
            nombres = [(_nombres().get(m.chat_id) or telefono(m.chat_id)) for m in avisar]
            estado.avisos.avisar(f"Te escribieron {len(avisar)} clientes", ", ".join(nombres[:5]))

    def _avisar_mensaje(mensaje, ya_decidido: bool = False) -> None:
        """Un mensaje nuevo de alguien que es (o puede ser) cliente.

        Lo privado y el spam no avisan: a eso no se contesta desde Estiba.
        """
        if estado.avisos is None or mensaje is None or not mensaje.from_customer:
            return
        if mensaje.chat_id in estado.resultado.fuera:
            return
        conn = estado.conectar()
        try:
            anteriores = ConversationStore(conn).get(mensaje.chat_id).messages
        finally:
            conn.close()
        # Uno por turno del cliente, y nada si ya estas en esa charla.
        if not ya_decidido and not merece_aviso(mensaje, anteriores):
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

    def _ejecutar_desde_horizon(tipo: str, job_id: str, datos: dict) -> None:
        """Lo mismo que los botones de la pantalla del flete, pedido desde Horizon."""
        if tipo == "confirmar":
            _accion(job_id, CONFIRMADO, "desde Horizon")
        elif tipo == "realizado":
            monto = _monto(str(datos.get("monto") or ""))
            if monto is None:
                raise ValueError("falta cuanto se cobro")
            estimado = registro.desde_evaluacion(estado.resultado.evaluaciones.get(job_id))
            _accion(job_id, REALIZADO, "desde Horizon", monto)
            sin_cambios = {"km": None, "km_vacios": None, "horas": None, "espera_h": None,
                           "ayudante_pagado": None, "otros_gastos": None, "canal": None}
            conn = estado.conectar()
            try:
                FleteRealRepository(conn).guardar(job_id, registro.cerrar(estimado, visto=False, cambios=sin_cambios),
                                                  estado.ahora())
            finally:
                conn.close()
        elif tipo == "perdido":
            _accion(job_id, PERDIDO, _nota_perdida(str(datos.get("motivo") or ""), str(datos.get("nota") or ""), ""))
        elif tipo == "reabrir":
            _accion(job_id, ANULADO, None)
        else:
            raise ValueError(f"cambio desconocido: {tipo}")

    def _sincronizar_horizon() -> dict:
        """Aplica los cambios pedidos desde Horizon y manda los fletes que cambiaron y el resumen del mes."""
        acks: list[dict] = []
        try:
            try:
                comandos = horizon.comandos()
            except Exception as e:
                log.warning("Horizon: no se pudieron traer los cambios pendientes: %s", e)
                comandos = []
            if comandos:
                acks = vinculo.aplicar_comandos(comandos, _ejecutar_desde_horizon)
                log.info("Horizon: %d cambios pedidos desde Horizon (%d aplicados)", len(acks),
                         sum(1 for a in acks if a["ok"]))
            fletes = _fletes()
            conn = estado.conectar()
            try:
                montos = {f["job_id"]: f for f in PanelRepository(conn).estimado_vs_real()}
                creados = {f["id"]: f["created_at"] for f in conn.execute("SELECT id, created_at FROM jobs")}
            finally:
                conn.close()
            try:
                mes = vinculo.mes_para_horizon(_mes_actual()[0])
            except Exception as e:  # sin economia configurada todavia
                log.warning("Horizon: no se pudo armar el resumen del mes: %s", e)
                mes = None
            payload = [vinculo.flete_para_horizon(f, montos.get(j), creados.get(j)) for j, f in fletes.items()]
            conn = estado.conectar()
            try:
                r = vinculo.sincronizar(conn, horizon, payload, mes, estado.ahora(), acks=acks,
                                        catalogos={"motivos_perdida": [list(m) for m in MOTIVOS_PERDIDA]},
                                        forzar={c["flete"] for c in comandos if c.get("flete")})
            finally:
                conn.close()
        except Exception as e:
            log.warning("Horizon: fallo la sincronizacion: %s", e)
            r = {"cuando": estado.ahora().isoformat(), "ok": False, "error": str(e) or type(e).__name__}
        if not r.get("ok"):
            log.warning("Horizon: %s", r.get("error") or f"{r.get('fallidos')} fletes no se guardaron")
        estado.horizon = {"configurado": True, **r}
        return r

    @app.get("/horizon/estado", include_in_schema=False)
    def horizon_estado():
        return estado.horizon

    @app.post("/horizon/sincronizar", include_in_schema=False)
    def horizon_sincronizar():
        if horizon is None:
            raise HTTPException(409, "Horizon no esta configurado: defini ESTIBA_HORIZON_URL y ESTIBA_HORIZON_TOKEN")
        return _sincronizar_horizon()

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
        perdidos = 0
        if r.nuevos:
            log.info("recuperados %d mensajes de %d chats", r.nuevos, r.chats)
            _procesar()
            _avisar_traidos(r.mensajes)
            # Con la app abierta hace rato, lo que trae el repaso es lo que el
            # webhook no trajo. Antes esto no se veia en ningun lado.
            if cuando - estado.arranque > timedelta(minutes=15):
                perdidos = sum(1 for m in r.mensajes if m.sent_at >= estado.arranque)
                if perdidos:
                    log.warning("%d mensajes llegaron solo por el repaso: el webhook no esta entregando", perdidos)
        # Se anota al final: "revisado" quiere decir que ya esta en la bandeja.
        estado.recuperacion = {"cuando": cuando, "desde": r.desde, "chats": r.chats, "nuevos": r.nuevos,
                               "webhook_perdio": perdidos}

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
        estado.ultimo_webhook = estado.ahora()
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
