"""Fase 5: bandeja de aprobacion, dashboard y API."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path

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
    def test_al_primero_que_escribe_se_lo_engancha_antes_de_pedirle(self):
        """Un cuestionario de entrada espanta: el que escribio "hola"
        todavia no decidio nada. Una sola pregunta abierta y listo."""
        p = _propuesta(_chat(("c", "hola, haces fletes?")))
        assert p.kind == "preguntas"
        assert p.text.count("?") <= 1        # invita, no interroga
        assert "Contame" in p.text and "foto" in p.text

    def test_una_vez_arrancada_la_charla_vuelve_a_dos_preguntas(self):
        p = _propuesta(_chat(("c", "hola, haces fletes?"),
                             ("v", "¡Hola! ¿De dónde a dónde?"),
                             ("c", "y cuanto me saldria?")))
        assert p.text == "¡Buenas! ¿Desde qué barrio sería? ¿Hasta dónde lo llevamos?"
        assert "•" not in p.text and "\n" not in p.text

    def test_nunca_mas_de_dos_preguntas(self):
        """Tres ya es un formulario: el cliente contesta una sola."""
        p = _propuesta(_chat(("c", "hola")))
        assert p.text.count("?") <= 2

    def test_primero_reconoce_lo_que_entendio(self):
        """Repreguntar lo que ya dijo es la forma mas rapida de perderlo."""
        p = _propuesta(_chat(("c", "tengo que llevar una heladera de Almagro a Boedo")))
        assert p.text.startswith("Perfecto, heladera de Almagro a Boedo.")
        assert "barrio" not in p.text          # no vuelve a preguntar el origen

    def test_las_medidas_se_preguntan_solo_si_el_tamano_importa(self):
        """Por una heladera importan; por unas cajas, no.

        Van ultimas: primero lo que hace falta para cotizar. Con el tope de
        dos preguntas, muchas veces quedan para el mensaje siguiente.
        """
        def pendientes(texto):
            conv = _chat(("c", texto))
            return JobEngine().evaluar(conv, datetime(2026, 9, 15, 11)).preguntas

        assert "¿Sabés más o menos las medidas de la heladera?" in pendientes(
            "llevo una heladera de Almagro a Boedo")
        assert not [p for p in pendientes("llevo unas cajas de Almagro a Boedo")
                    if "medidas" in p]

    def test_con_datos_para_cotizar_no_pregunta_de_mas(self):
        """Tu regla: si alcanza para cotizar, se cotiza."""
        p = _propuesta(_chat(("c", "llevo una heladera de Almagro a Boedo, hay ascensor")))
        assert p.kind == "cotizacion" and "medidas" not in p.text

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


class TestBotonera:
    """Adentro de la conversacion manda la accion, no la navegacion global."""

    CONFIRMAR = "5491177778888:job"     # tiene un borrador de confirmacion
    AGENDADO = "5491133334444:job"      # agendado y ya paso: se puede realizar

    def test_reemplaza_la_navegacion_de_abajo(self, cliente):
        html = cliente.get(f"/fletes/{self.CONFIRMAR}").text
        assert "barra-accion" in html
        assert 'class="tabbar"' not in html     # dos barras se comen la pantalla

    def test_la_accion_principal_es_la_que_pide_el_estado(self, cliente):
        html = cliente.get(f"/fletes/{self.CONFIRMAR}").text
        assert "Confirmar flete" in html

    def test_no_repite_el_boton_de_aprobar(self, cliente):
        """Si la accion vive abajo, arriba queda duplicada."""
        html = cliente.get(f"/fletes/{self.CONFIRMAR}").text
        assert html.count("data-aprobar") == 1

    def test_sin_nada_que_hacer_lo_dice(self, cliente):
        html = cliente.get("/fletes/5491155556666:job").text
        assert "Marcar realizado" in html or "Nada que hacer" in html or "Hacer seguimiento" in html

    def test_la_navegacion_respeta_el_filtro(self, cliente):
        """Si filtraste Cotizar, Siguiente sigue entre esas."""
        html = cliente.get(f"/fletes/{self.AGENDADO}?f=confirmados").text
        assert "?f=confirmados" in html

    def test_la_bandeja_sigue_teniendo_su_navegacion(self, cliente):
        assert 'class="tabbar"' in cliente.get("/").text


class TestPestanas:
    """La pantalla viva es de clientes y leads; el resto, aparte."""

    def test_lo_dudoso_no_se_mezcla_con_los_clientes(self, cliente):
        todos = cliente.get("/?f=todos").text
        assert 'href="/?f=sin-clasificar"' in todos and 'href="/?f=privados"' in todos

    def test_un_perdido_no_ocupa_el_trabajo_del_dia(self, cliente):
        """Existe, pero en su pestaña."""
        assert cliente.get("/?f=perdidos").status_code == 200
        assert cliente.get("/?f=spam").status_code == 200

    def test_las_apartadas_dicen_por_que_quedaron_afuera(self, db):
        from estiba.storage.repositories import ClaseRepository

        # Una correccion humana: lo inferido lo recalcula cada sincronizacion.
        conn = connect(db)
        ClaseRepository(conn).corregir("5491122223333", "privado", JUEVES_18)
        conn.close()
        with TestClient(create_app(db, ahora=lambda: JUEVES_18)) as c:
            html = c.get("/?f=privados").text
            assert "Lo dijiste vos" in html
            # Y deja de ocupar lugar entre los clientes.
            assert "5491122223333:job" not in c.get("/?f=todos").text


class TestClasesSumaCero:
    """Una conversacion es lead, cliente o privado: nunca dos a la vez."""

    def _clase(self, conn, chat_id):
        from estiba.storage.repositories import ClaseRepository
        return ClaseRepository(conn).de(chat_id)["clase"]

    def test_cambiar_una_apaga_la_otra(self, cliente, db):
        chat = "5491122223333"
        cliente.post(f"/clientes/{chat}/clase", data={"clase": "privado"})
        conn = connect(db)
        assert self._clase(conn, chat) == "privado"
        cliente.post(f"/clientes/{chat}/clase", data={"clase": "cliente"})
        assert self._clase(conn, chat) == "cliente"
        conn.close()

    def test_el_que_ya_hizo_un_flete_deja_de_ser_lead(self, db):
        """Lo deduce del historial: no hay que marcarlo a mano."""
        from estiba.storage.repositories import ClaseRepository

        conn = connect(db)
        sincronizar(conn, JobEngine(), JUEVES_18)
        # 5491133334444 tiene un flete agendado en los fixtures.
        assert ClaseRepository(conn).de("5491133334444")["clase"] == "cliente"
        conn.close()

    def test_una_clase_inventada_no_pasa(self, cliente):
        r = cliente.post("/clientes/5491122223333/clase", data={"clase": "amigo"})
        assert r.status_code == 400

    def test_solo_se_borra_lo_marcado_spam(self, cliente, db):
        """Lo unico de Estiba que borra de verdad, y lo pide una persona."""
        from estiba.storage.repositories import ClaseRepository

        chat = "5491122223333"
        assert cliente.post(f"/clientes/{chat}/borrar").status_code == 409

        conn = connect(db)
        ClaseRepository(conn).corregir(chat, "spam", JUEVES_18)
        conn.close()
        cliente.post(f"/clientes/{chat}/borrar")
        conn = connect(db)
        quedan = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE chat_id = ?", (chat,)).fetchone()[0]
        conn.close()
        assert quedan == 0


class TestAudioOriginal:
    """Transcribir se equivoca: hay que poder ir a la fuente."""

    def test_se_puede_escuchar_el_audio(self, db, tmp_path):
        ruta = tmp_path / "audios" / "a.ogg"
        ruta.parent.mkdir()
        ruta.write_bytes(b"OggS prueba")
        conn = connect(db)
        ConversationStore(conn).ingest([Message(
            "a:1", "a", True, datetime(2026, 9, 18, 10), MessageKind.AUDIO,
            media_path=str(ruta))])
        conn.close()
        import estiba.web.app as app_mod
        antes = app_mod.MEDIA_DIR
        app_mod.MEDIA_DIR = ruta.parent
        try:
            with TestClient(create_app(db, ahora=lambda: JUEVES_18)) as c:
                r = c.get("/audios/a:1")
                assert r.status_code == 200 and r.content == b"OggS prueba"
                assert '<audio' in c.get("/fletes/a:job").text
        finally:
            app_mod.MEDIA_DIR = antes

    def test_no_sirve_archivos_de_otro_lado(self, db, tmp_path):
        """Un id raro no puede sacar un archivo fuera de la carpeta."""
        afuera = tmp_path / "secreto.txt"
        afuera.write_text("no")
        conn = connect(db)
        ConversationStore(conn).ingest([Message(
            "b:1", "b", True, datetime(2026, 9, 18, 10), MessageKind.AUDIO,
            media_path=str(afuera))])
        conn.close()
        with TestClient(create_app(db, ahora=lambda: JUEVES_18)) as c:
            assert c.get("/audios/b:1").status_code == 404

    def test_la_categoria_se_puede_cambiar_desde_arriba(self, cliente, db):
        from estiba.storage.repositories import ClaseRepository

        conn = connect(db)
        ClaseRepository(conn).corregir("5491122223333", "lead", JUEVES_18)
        conn.close()
        html = cliente.get("/fletes/5491122223333:job2").text
        # La etiqueta y los cuatro botones, antes del chat.
        assert html.index("clase-puesta") < html.index('class="panel chat"')


class TestChatCompacto:
    def test_el_chat_ocupa_poco_y_se_puede_agrandar(self, cliente):
        html = cliente.get("/fletes/5491177778888:job").text
        assert "data-agrandar" in html and "data-al-final" in html

    def test_el_hilo_no_atrapa_el_scroll_de_la_pagina(self):
        """`overscroll-behavior: contain` dejaba los botones de abajo
        inalcanzables: al llegar al final del hilo la pagina no seguia."""
        css = (Path(__file__).parents[1] / "src/estiba/web/static/estiba.css").read_text()
        bloque = css[css.index(".mensajes {"):css.index("}", css.index(".mensajes {"))]
        assert "overscroll-behavior" not in bloque


class TestCacheDelNavegador:
    def test_el_css_y_el_js_van_versionados(self, cliente):
        """Sin esto el navegador se queda con el CSS viejo: se cambia una
        regla, se recarga y no pasa nada. Cuesta una tarde de buscar mal."""
        html = cliente.get("/").text
        assert re.search(r'estiba\.css\?v=\d+', html)
        assert re.search(r'estiba\.js\?v=\d+', html)

    def test_la_version_cambia_si_cambia_un_archivo(self, cliente, tmp_path):
        import os
        from estiba.web.app import AQUI

        antes = re.search(r'estiba\.css\?v=(\d+)', cliente.get("/").text).group(1)
        css = AQUI / "static" / "estiba.css"
        stat = css.stat()
        try:
            os.utime(css, (stat.st_atime, stat.st_mtime + 60))
            despues = re.search(r'estiba\.css\?v=(\d+)', cliente.get("/").text).group(1)
            assert despues != antes
        finally:
            os.utime(css, (stat.st_atime, stat.st_mtime))


class TestPlata:
    """Cotizar, cerrar y perder: un solo circuito con la plata adentro."""

    def _acciones(self, conn, job_id):
        from estiba.storage.repositories import ActionRepository
        return ActionRepository(conn).for_job(job_id)

    def test_aprobar_una_cotizacion_deja_registrado_el_precio(self, cliente, db):
        from decimal import Decimal

        bandeja = cliente.get("/api/bandeja").json()
        cot = next(x for x in bandeja if x["borrador"]["tipo"] == "cotizacion")
        cliente.post(f"/borradores/{cot['borrador']['id']}/aprobar",
                     data={"texto": "Te sale $52.000", "monto": "52.000"})
        conn = connect(db)
        montos = [a.monto for a in self._acciones(conn, cot["borrador"]["job_id"])
                  if a.tipo == "cotizado"]
        conn.close()
        assert montos == [Decimal("52000")]

    def test_cerrar_el_flete_anota_lo_cobrado(self, cliente, db):
        from decimal import Decimal

        job = "5491133334444:job"      # agendado y ya pasó
        cliente.post(f"/fletes/{job}/realizado", data={"cobrado": "48.500"})
        conn = connect(db)
        hechos = [a for a in self._acciones(conn, job) if a.tipo == "realizado"]
        conn.close()
        assert hechos and hechos[0].monto == Decimal("48500")

    def test_el_motivo_de_perdida_sale_de_la_lista(self, cliente, db):
        job = "5491155556666:job"
        cliente.post(f"/fletes/{job}/perdido", data={"motivo": "precio"})
        conn = connect(db)
        perdidos = [a for a in self._acciones(conn, job) if a.tipo == "perdido"]
        conn.close()
        assert perdidos and perdidos[0].nota == "Por precio"

    def test_un_precio_escrito_como_sea_se_entiende(self, cliente, db):
        from decimal import Decimal

        job = "5491133334444:job"
        cliente.post(f"/fletes/{job}/realizado", data={"cobrado": "$ 48.500 "})
        conn = connect(db)
        hechos = [a for a in self._acciones(conn, job) if a.tipo == "realizado"]
        conn.close()
        assert hechos[0].monto == Decimal("48500")


class TestMemoria:
    """Fase A: lo que Estiba estimo y cada decision humana, sin pisar nada."""

    def test_lo_que_estima_queda_guardado(self, db):
        from estiba.storage.repositories import EstimacionRepository

        conn = connect(db)
        sincronizar(conn, JobEngine(), JUEVES_18)
        est = EstimacionRepository(conn).ultima("5491122223333:job2")
        conn.close()
        assert est is not None and est["valor"] == "52000"

    def test_sincronizar_dos_veces_no_inventa_una_opinion_nueva(self, db):
        from estiba.storage.repositories import EstimacionRepository

        conn = connect(db)
        sincronizar(conn, JobEngine(), JUEVES_18)
        sincronizar(conn, JobEngine(), JUEVES_18)
        todas = EstimacionRepository(conn).todas()
        conn.close()
        por_job = [e for e in todas if e["job_id"] == "5491122223333:job2"]
        assert len(por_job) == 1

    def test_guarda_con_que_la_calculo(self, db):
        import json

        from estiba.storage.repositories import EstimacionRepository

        conn = connect(db)
        sincronizar(conn, JobEngine(), JUEVES_18)
        est = EstimacionRepository(conn).ultima("5491122223333:job2")
        conn.close()
        supuestos = json.loads(est["supuestos"])
        assert supuestos["desglose"]      # el detalle de como llego a ese numero

    def test_cada_correccion_queda_no_solo_la_ultima(self, db):
        from estiba.storage.repositories import ClaseRepository

        conn = connect(db)
        repo = ClaseRepository(conn)
        repo.corregir("x", "lead", JUEVES_18)
        repo.corregir("x", "privado", JUEVES_18)
        repo.corregir("x", "cliente", JUEVES_18)
        historia = list(conn.execute(
            "SELECT clase FROM clase_historia WHERE chat_id = 'x' ORDER BY id"))
        conn.close()
        assert [h["clase"] for h in historia] == ["lead", "privado", "cliente"]

    def test_traer_de_vuelta_no_borra_que_la_pospusiste(self, db):
        from estiba.storage.repositories import SnoozeRepository

        conn = connect(db)
        repo = SnoozeRepository(conn)
        repo.posponer("y", LUNES_21, "dijo que seguían después", JUEVES_18)
        repo.despertar("y", JUEVES_18)
        historia = list(conn.execute(
            "SELECT accion FROM snooze_historia WHERE chat_id = 'y' ORDER BY id"))
        conn.close()
        assert [h["accion"] for h in historia] == ["pospuso", "desperto"]


class TestPanel:
    """El analisis vive fuera de la mensajeria."""

    def test_el_panel_no_arrastra_la_mensajeria(self, cliente):
        html = cliente.get("/panel").text
        assert 'class="tabbar"' not in html       # sin barra de trabajo
        assert "Volver al trabajo" in html

    def test_se_entra_desde_la_mensajeria(self, cliente):
        assert 'href="/panel"' in cliente.get("/").text

    def test_mide_si_estiba_ahorra_trabajo(self, cliente):
        """La pregunta que importa: si lo que escribe se usa o se tira."""
        primero = cliente.get("/api/bandeja").json()[0]["borrador"]
        cliente.post(f"/borradores/{primero['id']}/aprobar",
                     data={"texto": primero["texto"]})       # sin tocarla
        html = cliente.get("/panel").text
        assert "salieron sin tocarlas" in html and "100%" in html

    def test_exporta_todo_en_json(self, cliente):
        r = cliente.get("/panel/exportar.json")
        assert r.status_code == 200
        datos = r.json()
        assert "messages" in datos["tablas"] and "jobs" in datos["tablas"]
        assert "attachment" in r.headers["content-disposition"]

    def test_exporta_una_tabla_en_csv(self, cliente):
        r = cliente.get("/panel/exportar/messages.csv")
        assert r.status_code == 200 and "chat_id" in r.text.splitlines()[0]

    def test_no_exporta_cualquier_cosa(self, cliente):
        """El nombre se valida contra el esquema real."""
        assert cliente.get("/panel/exportar/sqlite_master.csv").status_code == 404
        assert cliente.get("/panel/exportar/no_existe.csv").status_code == 404


class TestCotizarAMano:
    """La tarifa puede no estar definida y el flete igual esta listo."""

    def _sin_tarifa(self):
        """Un motor que no sabe cobrar: le falta la zona, como en Mar del Plata."""
        return JobEngine()

    def test_con_los_datos_se_puede_cotizar_aunque_no_haya_precio(self):
        conv = _chat(("c", "necesito llevar una heladera desde b.goñi 836 "
                           "hasta Colón 10955, hay ascensor"))
        p = proponer(self._sin_tarifa().evaluar(conv, datetime(2026, 9, 15, 11)),
                     conv, datetime(2026, 9, 15, 11))
        assert p.kind == "cotizacion"
        assert "b.goñi 836" in p.text and "Colón 10955" in p.text

    def test_deja_el_hueco_del_precio_para_completar(self):
        from estiba.bandeja.redactor import SIN_PRECIO

        conv = _chat(("c", "necesito llevar una heladera desde b.goñi 836 "
                           "hasta Colón 10955, hay ascensor"))
        p = proponer(self._sin_tarifa().evaluar(conv, datetime(2026, 9, 15, 11)),
                     conv, datetime(2026, 9, 15, 11))
        assert SIN_PRECIO in p.text

    def test_el_campo_de_precio_esta_aunque_no_haya_sugerencia(self, cliente, db):
        conn = connect(db)
        ConversationStore(conn).ingest([Message(
            "m:1", "m", True, datetime(2026, 9, 18, 10), MessageKind.TEXT,
            text="necesito llevar una heladera desde b.goñi 836 hasta Colón 10955, hay ascensor")])
        conn.close()
        with TestClient(create_app(db, ahora=lambda: JUEVES_18)) as c:
            html = c.get("/fletes/m:job").text
            assert "data-monto" in html and "Poné el precio" in html
