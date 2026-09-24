"""Fase 5: bandeja de aprobacion, dashboard y API."""

from __future__ import annotations

import re
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from estiba.adapters.fixtures import FixtureMessageSource
from estiba.bandeja.redactor import proponer
from estiba.bandeja.servicio import sincronizar
from estiba.cli import main
from estiba.conversation.store import ConversationStore
from estiba.core.models import Conversation, Message, MessageKind
from estiba.jobs.engine import JobEngine
from estiba.storage.db import connect
from estiba.storage.repositories import DraftRepository
from estiba.web.app import create_app

JUEVES_18 = datetime(2026, 9, 18, 12)
LUNES_21 = datetime(2026, 9, 21, 12)


@pytest.fixture
def db(tmp_path):
    ruta = tmp_path / "web.db"
    conn = connect(ruta)
    ConversationStore(conn).ingest(list(FixtureMessageSource().poll()))
    conn.close()
    return ruta


def _chat(*lineas: tuple[str, str]) -> Conversation:
    inicio = datetime(2026, 9, 15, 10)
    return Conversation("x", [
        Message(f"x:{i:03d}", "x", quien == "c", inicio + timedelta(minutes=i), MessageKind.TEXT, text=t)
        for i, (quien, t) in enumerate(lineas)
    ])


def _propuesta(conv: Conversation, ahora: datetime = datetime(2026, 9, 15, 11)):
    return proponer(JobEngine().evaluar(conv, ahora), conv, ahora)


# --------------------------------------------------------------------------
# Redactor
# --------------------------------------------------------------------------


class TestRedactor:
    def test_faltan_datos_pregunta_todo_junto(self):
        p = _propuesta(_chat(("c", "hola, haces fletes?")))
        assert p.kind == "preguntas"
        assert p.text.startswith("¡Buenas! Para pasarte el precio necesito un par de datos:\n• ")

    def test_la_razon_dice_que_falta_exactamente(self):
        p = _propuesta(_chat(("c", "hola, haces fletes?")))
        assert p.reason == "Falta origen, destino, fecha y qué lleva"

    def test_con_origen_y_destino_la_razon_dice_lo_que_queda(self):
        p = _propuesta(_chat(("c", "de Almagro a Boedo, che")))
        assert p.reason == "Falta fecha y qué lleva"

    def test_con_todo_para_cotizar_propone_el_precio(self):
        p = _propuesta(_chat(("c", "de Almagro a Boedo una heladera, hay ascensor")))
        assert p.kind == "cotizacion"
        assert p.text == (
            "Te sale $38.000, de Almagro a Boedo, con carga y descarga de heladera. "
            "¿Para cuándo lo necesitás?"
        )

    def test_una_pregunta_que_las_reglas_no_entienden_queda_para_vos(self):
        p = _propuesta(_chat(("c", "de Almagro a Boedo una heladera, hay ascensor, el viernes"),
                             ("v", "te sale 40 mil"), ("c", "incluye subirla?")))
        assert p.kind == "respuesta" and p.text == ""

    def test_si_el_ultimo_mensaje_es_nuestro_no_se_insiste(self):
        p = _propuesta(_chat(("c", "de Almagro a Boedo una heladera"), ("v", "te sale 40 mil")))
        assert p is None

    def test_confirmacion_resume_el_flete_y_pregunta_lo_que_falta(self):
        conn = connect(":memory:")
        ConversationStore(conn).ingest(list(FixtureMessageSource().poll()))
        conv = ConversationStore(conn).get("5491177778888")
        p = proponer(JobEngine().evaluar(conv, JUEVES_18), conv, JUEVES_18)
        assert p.kind == "confirmacion"
        assert p.text == (
            "¡Listo! Quedamos el jueves 24/09. Retiro en Almagro y llevamos a Barracas: "
            "cajas y placard. Total $70.000.\n¿A qué hora te queda bien?"
        )

    def test_no_confirma_un_flete_que_ya_paso(self):
        conn = connect(":memory:")
        ConversationStore(conn).ingest(list(FixtureMessageSource().poll()))
        conv = ConversationStore(conn).get("5491133334444")
        assert proponer(JobEngine().evaluar(conv, LUNES_21), conv, LUNES_21) is None


# --------------------------------------------------------------------------
# Bandeja en la base
# --------------------------------------------------------------------------


class TestBandeja:
    def test_sincronizar_dos_veces_no_duplica(self, db):
        conn = connect(db)
        sincronizar(conn, JobEngine(), JUEVES_18)
        sincronizar(conn, JobEngine(), JUEVES_18)
        assert len(DraftRepository(conn).pending()) == 4
        assert conn.execute("SELECT COUNT(*) FROM drafts").fetchone()[0] == 4

    def test_lo_que_ya_no_hace_falta_queda_reemplazado(self, db):
        conn = connect(db)
        sincronizar(conn, JobEngine(), JUEVES_18)
        sincronizar(conn, JobEngine(), LUNES_21)  # el seguimiento de Flores ya no va: se perdio
        estados = dict(conn.execute("SELECT chat_id, status FROM drafts").fetchall())
        assert estados["5491155556666"] == "reemplazado"

    def test_un_mensaje_nuevo_reemplaza_el_borrador_viejo(self, db):
        conn = connect(db)
        sincronizar(conn, JobEngine(), JUEVES_18)
        store = ConversationStore(conn)
        store.ingest([Message("5491177778888:900", "5491177778888", True, datetime(2026, 9, 18, 12, 30),
                              MessageKind.TEXT, text="a las 10 de la mañana te parece?")])
        sincronizar(conn, JobEngine(), datetime(2026, 9, 18, 13))
        filas = conn.execute(
            "SELECT status, basis_message_id FROM drafts WHERE chat_id = '5491177778888' ORDER BY id"
        ).fetchall()
        assert [f["status"] for f in filas] == ["reemplazado", "pendiente"]
        assert filas[1]["basis_message_id"] == "5491177778888:900"

    def test_lo_aprobado_no_vuelve_a_aparecer(self, db):
        conn = connect(db)
        sincronizar(conn, JobEngine(), JUEVES_18)
        repo = DraftRepository(conn)
        primero = repo.pending()[0]
        repo.decide(primero["id"], True, "texto editado", JUEVES_18)
        sincronizar(conn, JobEngine(), JUEVES_18)
        assert primero["id"] not in [b["id"] for b in repo.pending()]
        assert repo.get(primero["id"])["final_text"] == "texto editado"

    def test_no_se_aprueba_vacio_ni_dos_veces(self, db):
        conn = connect(db)
        sincronizar(conn, JobEngine(), JUEVES_18)
        repo = DraftRepository(conn)
        draft_id = repo.pending()[0]["id"]
        with pytest.raises(ValueError, match="vacio"):
            repo.decide(draft_id, True, "   ", JUEVES_18)
        repo.decide(draft_id, False, None, JUEVES_18)
        with pytest.raises(ValueError, match="descartado"):
            repo.decide(draft_id, True, "hola", JUEVES_18)


# --------------------------------------------------------------------------
# Dashboard y API
# --------------------------------------------------------------------------


@pytest.fixture
def cliente(db):
    with TestClient(create_app(db, ahora=lambda: JUEVES_18)) as c:
        yield c


class TestPaginas:
    def test_bandeja_muestra_una_fila_por_conversacion(self, cliente):
        """La bandeja elige a quien atender; el compositor vive adentro."""
        html = cliente.get("/").text
        filas = re.findall(r'class="conv(?: pide-atencion)?"', html)
        assert len(filas) == 4
        assert "Escribí tu respuesta" not in html
        # Lo mas urgente primero: las confirmaciones antes que el seguimiento.
        assert html.index("Confirmar flete") < html.index("Hacer seguimiento")

    def test_la_bandeja_va_por_orden_de_llegada(self, cliente):
        """Clasificar no puede desordenar el dia: lo ultimo que entro, arriba."""
        html = cliente.get("/").text
        # El orden en pantalla, leido por los links a cada conversacion.
        vistos = re.findall(r'class="conv[^"]*" href="/fletes/([^"]+)"', html)
        fletes = {f["job_id"]: f for f in cliente.get("/api/fletes").json()["en-curso"]}
        horas = [fletes[j]["ultimo_at"] for j in vistos if j in fletes]
        assert horas == sorted(horas, reverse=True)

    def test_los_filtros_acotan_sin_reordenar(self, cliente):
        todos = cliente.get("/?f=todos").text
        assert 'href="/?f=responder"' in todos      # los filtros estan siempre
        confirmado = "5491177778888:job"            # un flete agendado
        seguimiento = "5491155556666:job"           # el que espera respuesta
        assert confirmado in todos and seguimiento in todos

        solo = cliente.get("/?f=confirmados").text
        assert confirmado in solo and seguimiento not in solo

    def test_un_agendado_no_se_pierde_detras_de_una_consulta_nueva(self, cliente):
        """Un cliente con un flete agendado y una consulta nueva.

        La fila es una por conversacion, pero la del flete que el filtro
        pide: si no, el trabajo ya vendido desaparece de Confirmados.
        """
        chat = "5491122223333"          # tiene :job agendado y :job2 nuevo
        todos = cliente.get("/?f=todos").text
        assert f"{chat}:job2" in todos          # lo ultimo que hablaron
        confirmados = cliente.get("/?f=confirmados").text
        assert f"{chat}:job" in confirmados and f"{chat}:job2" not in confirmados

    def test_un_filtro_inventado_no_rompe(self, cliente):
        assert cliente.get("/?f=cualquiera").status_code == 200

    def test_fletes_por_seccion(self, cliente):
        html = cliente.get("/fletes").text
        assert html.index("Próximos") < html.index("Villa Crespo → Caballito") < html.index("En conversación")
        assert html.index("En conversación") < html.index("Flores → Morón")

    def test_detalle_liga_cada_dato_a_su_mensaje(self, cliente):
        html = cliente.get("/fletes/5491177778888:job").text
        assert 'href="#m-5491177778888-005"' in html      # destino: "es a Barracas"
        assert 'id="m-5491177778888-005"' in html
        assert "“es a Barracas”" in html
        assert "Audio sin transcribir" in html

    def test_flete_inexistente(self, cliente):
        assert cliente.get("/fletes/no-existe").status_code == 404

    def test_aprobar_desde_la_tarjeta(self, cliente):
        primero = cliente.get("/api/bandeja").json()[0]["borrador"]
        r = cliente.post(f"/borradores/{primero['id']}/aprobar", data={"texto": "¡Listo! Nos vemos el jueves."},
                         headers={"HX-Request": "true"})
        assert r.status_code == 200
        assert "Aprobado" in r.text
        chat = primero["chat_id"]
        assert f"https://wa.me/{chat}?text=%C2%A1Listo%21%20Nos%20vemos%20el%20jueves." in r.text
        assert len(cliente.get("/api/bandeja").json()) == 3

    def test_sin_javascript_el_formulario_igual_anda(self, cliente):
        draft_id = cliente.get("/api/bandeja").json()[0]["borrador"]["id"]
        r = cliente.post(f"/borradores/{draft_id}/descartar", follow_redirects=False)
        assert r.status_code == 303

    def test_aprobar_dos_veces_es_un_conflicto(self, cliente):
        draft_id = cliente.get("/api/bandeja").json()[0]["borrador"]["id"]
        cliente.post(f"/api/borradores/{draft_id}/aprobar", json={"texto": "dale"})
        assert cliente.post(f"/api/borradores/{draft_id}/aprobar", json={"texto": "dale"}).status_code == 409


class TestApi:
    def test_fletes_y_detalle(self, cliente):
        secciones = cliente.get("/api/fletes").json()
        assert [f["ruta"] for f in secciones["proximos"]] == ["Villa Crespo → Caballito", "Almagro → Barracas"]
        d = cliente.get("/api/fletes/5491177778888:job").json()
        destino = next(x for x in d["datos"] if x["campo"] == "destination")
        assert destino["valor"] == "Barracas" and destino["message_id"] == "5491177778888:005"
        assert d["flete"]["estado"] == "agendado"  # confirmado + fecha: se agenda solo

    def test_sincronizar(self, cliente):
        assert cliente.post("/api/sincronizar").json()["fletes"] == 5  # 4 conversaciones, una con 2 fletes

    def test_documentacion(self, cliente):
        assert cliente.get("/docs").status_code == 200


class TestClave:
    @pytest.fixture
    def cerrado(self, db):
        with TestClient(create_app(db, ahora=lambda: JUEVES_18, clave="secreta")) as c:
            yield c

    def test_sin_clave_no_se_ve_nada(self, cerrado):
        assert cerrado.get("/", follow_redirects=False).headers["location"] == "/entrar"
        assert cerrado.get("/api/fletes").status_code == 401
        assert cerrado.get("/static/estiba.css").status_code == 200

    def test_con_la_clave_equivocada(self, cerrado):
        r = cerrado.post("/entrar", data={"clave": "otra"}, follow_redirects=False)
        assert r.headers["location"] == "/entrar?error=1"

    def test_con_la_clave_correcta(self, cerrado):
        cerrado.post("/entrar", data={"clave": "secreta"})
        assert cerrado.get("/api/fletes").status_code == 200


class TestServe:
    def test_no_abre_a_la_red_sin_clave(self, db, capsys, monkeypatch):
        monkeypatch.delenv("ESTIBA_CLAVE", raising=False)
        assert main(["--db", str(db), "serve", "--host", "0.0.0.0"]) == 2
        assert "clave" in capsys.readouterr().err


class TestNavegacion:
    def test_whatsapp_se_alcanza_desde_la_barra_de_abajo(self, cliente):
        """Desde el celular era inalcanzable: arriba solo se ve un punto."""
        html = cliente.get("/").text
        tabbar = html.split('class="tabbar"')[1].split("</nav>")[0]
        assert 'href="/whatsapp"' in tabbar

    def test_una_conexion_caida_se_ve_en_la_barra(self, cliente):
        """Sin WAHA configurado el punto avisa igual, desde cualquier pantalla."""
        tabbar = cliente.get("/agenda").text.split('class="tabbar"')[1].split("</nav>")[0]
        assert "punto-tab" in tabbar


class TestOrden:
    def _bandeja(self, cliente):
        return cliente.get("/api/bandeja").json()

    def test_dentro_del_grupo_lo_mas_nuevo_primero(self, cliente):
        """Al reves tapaba los leads vivos con hilos de hace semanas."""
        items = self._bandeja(cliente)
        por_tipo = {}
        for x in items:
            por_tipo.setdefault(x["borrador"]["tipo"], []).append(x["flete"]["ultimo_at"])
        for tipo, fechas in por_tipo.items():
            assert fechas == sorted(fechas, reverse=True), f"{tipo} no va de nuevo a viejo"

    def test_lo_frio_no_tapa_el_trabajo_de_hoy(self, cliente):
        """Sin noticias hace mas de una semana: baja al fondo, plegado."""
        html = cliente.get("/").text
        if "Sin noticias hace más de una semana" in html:
            assert html.index("Sin noticias hace más de una semana") > html.index("seccion-titulo")


class TestConversacion:
    """La unidad es la conversacion, no la ficha de flete."""

    def test_el_chat_va_antes_que_lo_que_dedujo(self, cliente):
        html = cliente.get("/fletes/5491177778888:job").text
        assert html.index('class="panel chat"') < html.index("Lo que entendí del chat")

    def test_lo_deducido_queda_plegado(self, cliente):
        html = cliente.get("/fletes/5491177778888:job").text
        assert "panel plegado" in html

    def test_ofrece_pasar_a_la_siguiente(self, cliente):
        html = cliente.get("/fletes/5491177778888:job").text
        assert "Siguiente" in html

    def test_se_puede_contestar_antes_de_sacar_del_circuito(self, cliente):
        html = cliente.get("/fletes/5491177778888:job").text
        assert 'name="despedida"' in html

    def test_el_resumen_no_arranca_con_separador(self, cliente):
        html = cliente.get("/fletes/5491155556666:job").text
        assert 'class="entendido">\n  ·' not in html and '>· ' not in html

    def test_lo_que_dijo_el_cliente_se_escapa(self, db):
        """El origen sale de un WhatsApp: no puede inyectar HTML."""
        conn = connect(db)
        ConversationStore(conn).ingest([Message(
            "x:1", "x", True, datetime(2026, 9, 18, 10), MessageKind.TEXT,
            text="de <script>alert(1)</script> a Boedo una heladera",
        )])
        conn.close()
        with TestClient(create_app(db, ahora=lambda: JUEVES_18)) as c:
            html = c.get("/fletes/x:job").text
            assert "<script>alert(1)</script>" not in html


class TestPosponer:
    """Posponer no cierra ni archiva: saca de lo urgente y vuelve sola."""

    def _primero(self, cliente):
        return cliente.get("/api/bandeja").json()[0]["borrador"]["job_id"]

    def test_pospuesta_sale_de_lo_urgente(self, cliente):
        job_id = self._primero(cliente)
        antes = len(cliente.get("/api/bandeja").json())
        cliente.post(f"/fletes/{job_id}/posponer", data={"cuando": "manana"})
        html = cliente.get("/").text
        assert "Pospuestas" in html
        # Sigue existiendo: no se cerro ni se archivo.
        assert len(cliente.get("/api/bandeja").json()) == antes

    def test_vuelve_sola_cuando_se_cumple(self, db):
        """Sin que nadie haga nada: pasa la fecha y reaparece."""
        with TestClient(create_app(db, ahora=lambda: JUEVES_18)) as c:
            job_id = c.get("/api/bandeja").json()[0]["borrador"]["job_id"]
            c.post(f"/fletes/{job_id}/posponer", data={"cuando": "manana"})
            assert "Pospuestas" in c.get("/").text
        # Dos dias despues ya desperto.
        despues = JUEVES_18 + timedelta(days=2)
        with TestClient(create_app(db, ahora=lambda: despues)) as c:
            assert "Pospuestas" not in c.get("/").text

    def test_se_puede_traer_de_vuelta_antes(self, cliente):
        job_id = self._primero(cliente)
        cliente.post(f"/fletes/{job_id}/posponer", data={"cuando": "2-dias"})
        assert "Pospuestas" in cliente.get("/").text
        cliente.post(f"/fletes/{job_id}/despertar")
        assert "Pospuestas" not in cliente.get("/").text

    def test_una_fecha_que_no_se_entiende_no_pasa(self, cliente):
        job_id = self._primero(cliente)
        r = cliente.post(f"/fletes/{job_id}/posponer",
                         data={"cuando": "fecha", "fecha": "cuando pueda"})
        assert r.status_code == 400
