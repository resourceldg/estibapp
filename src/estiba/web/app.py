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

import hashlib
import hmac
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
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
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from estiba.adapters.waha import MEDIA_DIR, ConfigWaha, ErrorWaha, WahaClient, firma_valida
from estiba.agenda.caldav import ConfigCalDAV, EspejoCalDAV
from estiba.agenda.evento import calendario
from estiba.agenda.local import LocalCalendar
from estiba.bandeja.servicio import Resultado, sincronizar
from estiba.conversation.store import ConversationStore
from estiba.core.models import JobState
from estiba.crm import embudo
from estiba.formato import telefono
from estiba.jobs.engine import PERDIDO, REALIZADO, JobEngine
from estiba.storage.db import connect
from estiba.storage.repositories import (
    ActionRepository,
    CRMRepository,
    DraftRepository,
    ClaseRepository,
    ExclusionRepository,
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

    def conectar(self):
        return connect(self.db)

    def sincronizar(self) -> None:
        conn = self.conectar()
        try:
            ahora = self.ahora()
            self.resultado = sincronizar(conn, self.engine, ahora, self.espejo)
            self.sincronizado_at = ahora
        finally:
            conn.close()

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
) -> FastAPI:
    if espejo is None and (config := ConfigCalDAV.desde_entorno()):
        espejo = EspejoCalDAV(config)
    if whatsapp is None and (config_waha := ConfigWaha.desde_entorno()):
        whatsapp = WhatsApp(WahaClient(config_waha))
    estado = Estado(str(db), engine or JobEngine(), ahora, espejo, whatsapp)

    @asynccontextmanager
    async def ciclo(_app: FastAPI):
        estado.sincronizar()
        estado.consultar_sesion()
        yield

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
    templates = Jinja2Templates(directory=AQUI / "templates")

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

    @app.get("/entrar", response_class=HTMLResponse, include_in_schema=False)
    def entrar_form(request: Request, error: bool = False):
        return templates.TemplateResponse(request, "entrar.html", {"error": error})

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
        return {
            job_id: P.flete(job_id, ev, conv, ahora, nombres.get(conv.chat_id))
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
    FILTROS = [
        ("todos", "Todos"), ("hoy", "Hoy"), ("responder", "Responder"),
        ("faltan", "Faltan datos"), ("cotizar", "Cotizar"),
        ("seguimiento", "Seguimiento"), ("confirmados", "Confirmados"),
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

    def _agenda(request: Request) -> P.Agenda:
        conn = estado.conectar()
        try:
            cal = LocalCalendar(conn)
            eventos = cal.eventos(incluir_cancelados=False)
            url = str(request.base_url).rstrip("/") + f"/calendario/{cal.token()}.ics"
        finally:
            conn.close()
        return P.agenda(eventos, _fletes(), estado.ahora(), url)

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

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def pagina_bandeja(request: Request, f: str = "todos"):
        validos = [c for c, _ in FILTROS + FILTROS_APARTE]
        filtro = f if f in validos else "todos"
        return templates.TemplateResponse(
            request, "bandeja.html",
            _contexto(request, **_conversaciones(filtro), titulo="Hoy"),
        )

    @app.get("/agenda", response_class=HTMLResponse, include_in_schema=False)
    def pagina_agenda(request: Request):
        return templates.TemplateResponse(
            request, "agenda.html", _contexto(request, a=_agenda(request), titulo="Agenda")
        )

    @app.get("/fletes", response_class=HTMLResponse, include_in_schema=False)
    def pagina_fletes(request: Request):
        return templates.TemplateResponse(
            request, "fletes.html",
            _contexto(request, secciones=_secciones(list(_fletes().values())), titulo="Fletes"),
        )

    @app.get("/fletes/{job_id}", response_class=HTMLResponse, include_in_schema=False)
    def pagina_flete(request: Request, job_id: str, f: str = "todos"):
        d = _detalle(job_id)
        conn = estado.conectar()
        try:
            dormida = SnoozeRepository(conn).vigentes(estado.ahora()).get(d.flete.chat_id)
        finally:
            conn.close()
        filtro = f if any(c == f for c, _ in FILTROS) else "todos"
        anterior, siguiente = _vecinos(job_id, filtro)
        clase = estado.resultado.clases.get(d.flete.chat_id)
        conn2 = estado.conectar()
        try:
            guardada = ClaseRepository(conn2).de(d.flete.chat_id)
        finally:
            conn2.close()
        return templates.TemplateResponse(
            request, "flete.html",
            _contexto(
                request, d=d, anterior=anterior, siguiente=siguiente, filtro=filtro,
                plazos=PLAZOS, dudosa=bool(clase and clase.pide_decision),
                clase=guardada["clase"] if guardada else None,
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

    def _accion(job_id: str, tipo: str, nota: str | None) -> None:
        if job_id not in estado.resultado.fletes:
            raise HTTPException(404, f"no hay un flete {job_id}")
        ev, _ = estado.resultado.fletes[job_id]
        permitido = {
            REALIZADO: ev.job.state is JobState.SCHEDULED,
            PERDIDO: ev.job.state not in (JobState.DONE, JobState.CANCELLED),
        }[tipo]
        if not permitido:
            raise HTTPException(409, f"un flete {ev.job.state.value} no se puede marcar {tipo}")
        conn = estado.conectar()
        try:
            ActionRepository(conn).add(job_id, tipo, estado.ahora(), "humano", (nota or "").strip() or None)
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
            ExclusionRepository(conn).excluir(conv.chat_id, nota, estado.ahora())
        finally:
            conn.close()
        estado.sincronizar()

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
    def aprobar_form(request: Request, draft_id: int, texto: str = Form("")):
        return _tarjeta(request, _aprobar(draft_id, texto))

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
    def realizado_form(request: Request, job_id: str):
        _accion(job_id, REALIZADO, None)
        return _volver(request, f"/fletes/{job_id}")

    @app.post("/fletes/{job_id}/perdido", include_in_schema=False)
    def perdido_form(request: Request, job_id: str, nota: str = Form("")):
        _accion(job_id, PERDIDO, nota)
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
        return P.vista_whatsapp(estado.whatsapp, info, estado.sesion)

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

    def _procesar() -> None:
        """Despues de recibir: transcribir los audios nuevos y reevaluar."""
        conn = estado.conectar()
        try:
            estado.whatsapp.transcribir(conn)
        finally:
            conn.close()
        estado.sincronizar()

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
            background.add_task(_procesar)
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
