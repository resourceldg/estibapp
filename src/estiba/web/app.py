"""Dashboard y API.

Una sola app: las paginas HTML (htmx, pensadas para el celular) y la API
JSON en /api, documentada en /docs. Las dos leen lo mismo: la ultima
sincronizacion, que corre al arrancar y cada vez que se toca "actualizar".

Nada de aca le escribe al cliente. Aprobar un borrador lo marca como
aprobado y abre WhatsApp con el texto listo; el envio automatico llega con
WAHA en la Fase 7.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from estiba.bandeja.servicio import sincronizar
from estiba.core.models import Conversation, JobState
from estiba.jobs.engine import Evaluacion, JobEngine
from estiba.storage.db import connect
from estiba.storage.repositories import DraftRepository
from estiba.web import presentacion as P

AQUI = Path(__file__).parent

#: En la bandeja, lo mas urgente primero.
_URGENCIA = ["respuesta", "confirmacion", "cotizacion", "preguntas", "seguimiento"]


@dataclass
class Estado:
    db: str
    engine: JobEngine
    ahora: Callable[[], datetime]
    evaluaciones: dict[str, tuple[Evaluacion, Conversation]] = field(default_factory=dict)
    sincronizado_at: datetime | None = None

    def conectar(self):
        return connect(self.db)

    def sincronizar(self) -> None:
        conn = self.conectar()
        try:
            ahora = self.ahora()
            self.evaluaciones = sincronizar(conn, self.engine, ahora)
            self.sincronizado_at = ahora
        finally:
            conn.close()


class Decision(BaseModel):
    texto: str | None = None


def create_app(
    db: str | Path,
    engine: JobEngine | None = None,
    ahora: Callable[[], datetime] = datetime.now,
    clave: str | None = None,
) -> FastAPI:
    estado = Estado(str(db), engine or JobEngine(), ahora)

    @asynccontextmanager
    async def ciclo(_app: FastAPI):
        estado.sincronizar()
        yield

    app = FastAPI(
        lifespan=ciclo,
        title="Estiba",
        description="Conversaciones de WhatsApp convertidas en fletes. Nada sale sin aprobacion.",
        version="0.5.0",
    )
    app.state.estado = estado
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
            if ruta.startswith("/static") or ruta == "/entrar":
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

    def _fletes() -> list[P.Flete]:
        ahora = estado.ahora()
        return [P.flete(ev, conv, ahora) for ev, conv in estado.evaluaciones.values()]

    def _bandeja() -> list[tuple[P.Borrador, P.Flete]]:
        conn = estado.conectar()
        try:
            pendientes = DraftRepository(conn).pending()
        finally:
            conn.close()
        fletes = {f.chat_id: f for f in _fletes()}
        items = [(P.borrador(b), fletes[b["chat_id"]]) for b in pendientes if b["chat_id"] in fletes]
        return sorted(items, key=lambda x: (_URGENCIA.index(x[0].tipo), x[1].ultimo_at))

    def _detalle(chat_id: str) -> P.DetalleFlete:
        par = estado.evaluaciones.get(chat_id)
        if par is None:
            raise HTTPException(404, f"no hay un flete para {chat_id}")
        ev, conv = par
        conn = estado.conectar()
        try:
            borradores = DraftRepository(conn).for_job(ev.job.id)
        finally:
            conn.close()
        return P.detalle(ev, conv, estado.ahora(), borradores)

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
            key=lambda f: f.ultimo_at, reverse=True,
        )
        return [
            ("proximos", "Próximos", proximos),
            ("en-curso", "En conversación", en_curso),
            ("cerrados", "Pasados y perdidos", cerrados),
        ]

    def _contexto(request: Request, **extra) -> dict:
        conn = estado.conectar()
        try:
            pendientes = len(DraftRepository(conn).pending())
        finally:
            conn.close()
        return {
            "pendientes": pendientes,
            "sincronizado": P.hace(estado.sincronizado_at, estado.ahora()) if estado.sincronizado_at else None,
            "ruta": request.url.path,
            **extra,
        }

    # ------------------------------------------------------------------
    # Paginas
    # ------------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def pagina_bandeja(request: Request):
        return templates.TemplateResponse(
            request, "bandeja.html", _contexto(request, items=_bandeja(), titulo="Bandeja")
        )

    @app.get("/fletes", response_class=HTMLResponse, include_in_schema=False)
    def pagina_fletes(request: Request):
        return templates.TemplateResponse(
            request, "fletes.html", _contexto(request, secciones=_secciones(_fletes()), titulo="Fletes")
        )

    @app.get("/fletes/{chat_id}", response_class=HTMLResponse, include_in_schema=False)
    def pagina_flete(request: Request, chat_id: str):
        d = _detalle(chat_id)
        return templates.TemplateResponse(
            request, "flete.html", _contexto(request, d=d, titulo=d.flete.ruta)
        )

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

    def _tarjeta(request: Request, b: P.Borrador) -> Response:
        if request.headers.get("HX-Request"):
            fletes = {f.chat_id: f for f in _fletes()}
            return templates.TemplateResponse(
                request, "_borrador.html", {"b": b, "f": fletes.get(b.chat_id), "suelto": True}
            )
        return RedirectResponse(request.headers.get("referer", "/"), status_code=303)

    @app.post("/borradores/{draft_id}/aprobar", include_in_schema=False)
    def aprobar_form(request: Request, draft_id: int, texto: str = Form("")):
        return _tarjeta(request, _decidir(draft_id, True, texto))

    @app.post("/borradores/{draft_id}/descartar", include_in_schema=False)
    def descartar_form(request: Request, draft_id: int):
        return _tarjeta(request, _decidir(draft_id, False, None))

    @app.post("/sincronizar", include_in_schema=False)
    def sincronizar_form(request: Request):
        estado.sincronizar()
        if request.headers.get("HX-Request"):
            return Response(headers={"HX-Refresh": "true"})
        return RedirectResponse(request.headers.get("referer", "/"), status_code=303)

    # ------------------------------------------------------------------
    # API JSON
    # ------------------------------------------------------------------

    @app.get("/api/bandeja", tags=["bandeja"], summary="Borradores pendientes de aprobacion")
    def api_bandeja():
        return [{"borrador": P.a_json(b), "flete": P.a_json(f)} for b, f in _bandeja()]

    @app.post("/api/borradores/{draft_id}/aprobar", tags=["bandeja"],
              summary="Aprobar un borrador, con el texto final")
    def api_aprobar(draft_id: int, decision: Decision):
        return P.a_json(_decidir(draft_id, True, decision.texto))

    @app.post("/api/borradores/{draft_id}/descartar", tags=["bandeja"], summary="Descartar un borrador")
    def api_descartar(draft_id: int):
        return P.a_json(_decidir(draft_id, False, None))

    @app.get("/api/fletes", tags=["fletes"], summary="Todos los fletes, por seccion")
    def api_fletes():
        return {clave_: [P.a_json(f) for f in fs] for clave_, _, fs in _secciones(_fletes())}

    @app.get("/api/fletes/{chat_id}", tags=["fletes"],
             summary="Un flete con cada dato, su evidencia, historia y conversacion")
    def api_flete(chat_id: str):
        d = _detalle(chat_id)
        datos = P.a_json(d)
        datos["flete"] = P.a_json(d.flete)
        return datos

    @app.post("/api/sincronizar", tags=["sistema"], summary="Reevaluar todas las conversaciones")
    def api_sincronizar():
        estado.sincronizar()
        return {"fletes": len(estado.evaluaciones), "at": estado.sincronizado_at}

    return app
