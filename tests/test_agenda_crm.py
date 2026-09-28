"""Fase 6: acciones, un cliente con varios fletes, agenda, CRM y embudo."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from estiba.adapters.fixtures import FixtureMessageSource
from estiba.agenda.evento import calendario, evento_de
from estiba.agenda.local import LocalCalendar
from estiba.bandeja.servicio import sincronizar
from estiba.conversation.store import ConversationStore
from estiba.core.models import (
    Conversation,
    Evidence,
    ExtractionMethod,
    FreightJob,
    JobState,
    Message,
    MessageKind,
    Slot,
    Stage,
)
from estiba.crm import embudo
from estiba.jobs.engine import AGENDADO, PERDIDO, REALIZADO, Accion, JobEngine
from estiba.jobs.episodios import episodios
from estiba.storage.db import connect
from estiba.storage.repositories import ActionRepository, CRMRepository
from estiba.web.app import create_app

JUEVES_18 = datetime(2026, 9, 18, 12)
VUELVE = "5491122223333"


def _slot(v) -> Slot:
    return Slot(v, 0.9, Evidence("m", "cita", ExtractionMethod.REGEX))


def _chat(*lineas: tuple[str, str, datetime]) -> Conversation:
    return Conversation("x", [
        Message(f"x:{i:03d}", "x", quien == "c", cuando, MessageKind.TEXT, text=t)
        for i, (quien, t, cuando) in enumerate(lineas)
    ])


@pytest.fixture
def conn():
    c = connect(":memory:")
    ConversationStore(c).ingest(list(FixtureMessageSource().poll()))
    return c


# --------------------------------------------------------------------------
# Acciones en el JobEngine
# --------------------------------------------------------------------------


class TestAcciones:
    def _conv(self, conn):
        return ConversationStore(conn).get("5491177778888")

    def test_agendado_y_realizado(self, conn):
        conv = self._conv(conn)
        acciones = [Accion(datetime(2026, 9, 17, 12), AGENDADO, "calendario"),
                    Accion(datetime(2026, 9, 24, 18), REALIZADO)]
        ev = JobEngine().evaluar(conv, datetime(2026, 9, 25), acciones)
        assert [e.hacia for e in ev.eventos][-2:] == [JobState.SCHEDULED, JobState.DONE]
        assert ev.eventos[-1].causa == "marcado como realizado"

    def test_se_puede_marcar_realizado_sin_haberlo_agendado(self, conn):
        """En la calle se arregla por telefono y el chat nunca se entera.

        Antes hacia falta pasar por "agendado" para poder cerrarlo, y un
        flete hecho de verdad quedaba trabado en "esperando al cliente" sin
        ninguna accion disponible. Si el fletero dice que lo hizo, lo hizo.
        """
        ev = JobEngine().evaluar(self._conv(conn), datetime(2026, 9, 25),
                                 [Accion(datetime(2026, 9, 24, 18), REALIZADO)])
        assert ev.job.state is JobState.DONE

    def test_perdido_con_motivo(self, conn):
        conv = ConversationStore(conn).get("5491155556666")
        ev = JobEngine().evaluar(conv, JUEVES_18, [Accion(datetime(2026, 9, 17), PERDIDO, nota="consiguio mas barato")])
        assert ev.job.state is JobState.CANCELLED
        assert ev.eventos[-1].causa == "dado por perdido: consiguio mas barato"

    def test_una_accion_futura_no_cuenta_todavia(self, conn):
        ev = JobEngine().evaluar(self._conv(conn), JUEVES_18, [Accion(datetime(2026, 9, 30), PERDIDO)])
        assert ev.job.state is JobState.CONFIRMED

    def test_borrador_sin_respuesta_tambien_se_pierde(self):
        inicio = datetime(2026, 9, 1, 10)
        conv = _chat(("c", "hola, haces fletes?", inicio), ("v", "si, de donde a donde?", inicio + timedelta(minutes=5)))
        assert JobEngine().evaluar(conv, inicio + timedelta(days=6)).job.state is JobState.CANCELLED


# --------------------------------------------------------------------------
# Un cliente, varios fletes
# --------------------------------------------------------------------------


class TestEpisodios:
    def test_el_cliente_que_vuelve_son_dos_fletes(self, conn):
        conv = ConversationStore(conn).get(VUELVE)
        eps = episodios(conv, JobEngine(), lambda _: [])
        assert [e.job_id for e in eps] == [f"{VUELVE}:job", f"{VUELVE}:job2"]
        assert [len(e.conversacion.messages) for e in eps] == [6, 3]

    def test_el_segundo_no_hereda_datos_del_primero(self, conn):
        conv = ConversationStore(conn).get(VUELVE)
        segundo = episodios(conv, JobEngine(), lambda _: [])[1]
        job = JobEngine().evaluar(segundo.conversacion, JUEVES_18).job
        assert (job.origin.value, job.destination.value) == ("Belgrano", "Núñez")
        assert job.items.value == ["lavarropas"]
        assert job.floors_no_lift.value == 2
        assert job.when is None  # el sabado de agosto no es de este flete

    def test_el_que_ya_viajo_y_vuelve_abre_un_flete_nuevo(self):
        """En la practica nadie marca un flete como realizado.

        Si solo se mirara el estado "realizado", el cliente que vuelve a los
        quince dias quedaba pegado al flete anterior y heredaba su direccion.
        Un agendado cuya fecha ya paso tambien termino.
        """
        inicio = datetime(2026, 9, 1, 10)
        conv = _chat(
            ("c", "necesito un flete de Almagro a Boedo, una heladera, "
                  "hay ascensor, el 5/9", inicio),
            ("v", "te sale 40 mil", inicio + timedelta(minutes=5)),
            ("c", "dale, cerramos", inicio + timedelta(minutes=10)),
            ("c", "hola de nuevo! ahora necesito mover un placard a Flores",
             inicio + timedelta(days=15)),
        )
        eps = episodios(conv, JobEngine(), lambda _: [])
        assert len(eps) == 2
        segundo = JobEngine().evaluar(eps[1].conversacion, inicio + timedelta(days=16)).job
        assert segundo.origin is None      # no hereda Almagro

    def test_necesito_mover_algo_tambien_es_una_consulta(self):
        from estiba.extraction.intencion import Intencion, intencion

        assert intencion("necesito mover un placard")[0] is Intencion.CONSULTA
        assert intencion("no quiero mover nada") is None

    def test_retomar_un_flete_abierto_no_abre_otro(self):
        inicio = datetime(2026, 9, 1, 10)
        conv = _chat(
            ("c", "necesito llevar una heladera de Almagro a Boedo", inicio),
            ("v", "te sale 40 mil", inicio + timedelta(minutes=5)),
            ("c", "tengo que ver", inicio + timedelta(minutes=10)),
            ("c", "hola! necesito llevar la heladera que te dije", inicio + timedelta(days=2)),
        )
        assert len(episodios(conv, JobEngine(), lambda _: [])) == 1

    def test_despues_de_uno_perdido_una_consulta_nueva_es_otro_flete(self):
        inicio = datetime(2026, 9, 1, 10)
        conv = _chat(
            ("c", "necesito llevar una heladera de Almagro a Boedo", inicio),
            ("v", "te sale 40 mil", inicio + timedelta(minutes=5)),
            ("c", "necesito llevar un sillon de Flores a Once", inicio + timedelta(days=10)),
        )
        assert len(episodios(conv, JobEngine(), lambda _: [])) == 2


# --------------------------------------------------------------------------
# Agenda
# --------------------------------------------------------------------------


def _job(**slots) -> FreightJob:
    base = {"origin": "Almagro", "destination": "Barracas", "when": date(2026, 9, 24), "items": ["placard"]}
    base.update(slots)
    return FreightJob("549:job", "549", **{k: _slot(v) for k, v in base.items() if v is not None})


class TestEvento:
    def test_con_hora(self):
        e = evento_de(_job(hour=time(9), quoted_price=Decimal(70000)))
        assert e.inicio == datetime(2026, 9, 24, 9) and not e.aproximado
        assert e.fin == datetime(2026, 9, 24, 12)
        assert e.titulo == "Flete Almagro → Barracas"
        assert "Precio: $70.000" in e.descripcion and "wa.me/549" in e.descripcion

    def test_solo_franja_es_aproximado(self):
        e = evento_de(_job(daypart="tarde"))
        assert e.inicio == datetime(2026, 9, 24, 14) and e.aproximado
        assert e.titulo.startswith("~")

    def test_sin_hora_es_de_todo_el_dia(self):
        e = evento_de(_job())
        assert e.todo_el_dia and e.fin == date(2026, 9, 25)

    def test_sin_fecha_no_se_agenda(self):
        with pytest.raises(ValueError, match="fecha"):
            evento_de(_job(when=None))

    def test_ics_valido(self):
        ics = calendario([(evento_de(_job(items=["cajas; frágiles, 20"] * 4)), False)], JUEVES_18)
        assert ics.startswith("BEGIN:VCALENDAR\r\n") and ics.endswith("END:VCALENDAR\r\n")
        assert "DTSTART;VALUE=DATE:20260924" in ics
        assert "cajas\\; frágiles\\, 20" in ics.replace("\r\n ", "")
        assert all(len(linea.encode()) <= 75 for linea in ics.split("\r\n"))


class TestAgendaEnSincronizar:
    def test_los_confirmados_se_agendan_solos(self, conn):
        r = sincronizar(conn, JobEngine(), JUEVES_18)
        creados = {j for j, cambio in r.agenda if cambio == "creado"}
        assert creados == {f"{VUELVE}:job", "5491133334444:job", "5491177778888:job"}
        assert r.fletes["5491177778888:job"][0].job.state is JobState.SCHEDULED
        assert sincronizar(conn, JobEngine(), JUEVES_18).agenda == []  # idempotente

    def test_si_cambia_la_hora_el_evento_se_mueve(self, conn):
        sincronizar(conn, JobEngine(), JUEVES_18)
        ConversationStore(conn).ingest([
            Message("5491177778888:100", "5491177778888", False, datetime(2026, 9, 18, 12, 30),
                    MessageKind.TEXT, text="Te queda bien a las 10?"),
            Message("5491177778888:101", "5491177778888", True, datetime(2026, 9, 18, 12, 40),
                    MessageKind.TEXT, text="dale, perfecto"),
        ])
        r = sincronizar(conn, JobEngine(), datetime(2026, 9, 18, 13))
        assert ("5491177778888:job", "movido") in r.agenda
        [(e, _)] = [x for x in LocalCalendar(conn).eventos() if x[0].job_id == "5491177778888:job"]
        assert e.inicio == datetime(2026, 9, 24, 10)

    def test_si_se_pierde_el_evento_se_cancela(self, conn):
        sincronizar(conn, JobEngine(), JUEVES_18)
        ActionRepository(conn).add("5491177778888:job", PERDIDO, datetime(2026, 9, 18, 12, 30))
        r = sincronizar(conn, JobEngine(), datetime(2026, 9, 18, 13))
        assert ("5491177778888:job", "cancelado") in r.agenda
        assert "5491177778888:job" not in {e.job_id for e, _ in LocalCalendar(conn).eventos(incluir_cancelados=False)}

    def test_el_token_del_ics_no_cambia(self, conn):
        cal = LocalCalendar(conn)
        assert cal.token() == cal.token()


class EspejoFalso:
    def __init__(self, falla: bool = False) -> None:
        self.falla = falla
        self.reflejados: list[tuple[str, bool]] = []

    def reflejar(self, evento, cancelado, estampa) -> None:
        if self.falla:
            raise ConnectionError("radicale no responde")
        self.reflejados.append((evento.uid, cancelado))


class TestEspejo:
    def test_copia_solo_lo_que_cambio(self, conn):
        espejo = EspejoFalso()
        sincronizar(conn, JobEngine(), JUEVES_18, espejo)
        assert len(espejo.reflejados) == 3
        sincronizar(conn, JobEngine(), JUEVES_18, espejo)
        assert len(espejo.reflejados) == 3

    def test_si_el_servidor_falla_se_reintenta_despues(self, conn):
        r = sincronizar(conn, JobEngine(), JUEVES_18, EspejoFalso(falla=True))
        assert r.espejo_error == "radicale no responde"
        espejo = EspejoFalso()
        sincronizar(conn, JobEngine(), JUEVES_18, espejo)
        assert len(espejo.reflejados) == 3


# --------------------------------------------------------------------------
# CRM y embudo
# --------------------------------------------------------------------------


class TestCRM:
    def test_historia_de_etapas(self, conn):
        r = sincronizar(conn, JobEngine(), JUEVES_18)
        lead = next(l for l in r.leads if l.job_id == "5491133334444:job")
        assert [p.etapa for p in lead.historia] == [Stage.NEW, Stage.QUALIFIED, Stage.QUOTED, Stage.CONFIRMED]
        assert lead.primera_respuesta == timedelta(minutes=9)

    def test_se_guarda_en_la_base(self, conn):
        sincronizar(conn, JobEngine(), JUEVES_18)
        leads = conn.execute("SELECT id, contact_id, stage FROM leads WHERE contact_id = ?", (VUELVE,)).fetchall()
        assert {tuple(f) for f in leads} == {
            (f"{VUELVE}:job", VUELVE, "confirmado"), (f"{VUELVE}:job2", VUELVE, "calificado")
        }
        assert conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0] == 4

    def test_el_nombre_lo_pone_una_persona_y_sobrevive_a_sincronizar(self, conn):
        sincronizar(conn, JobEngine(), JUEVES_18)
        CRMRepository(conn).set_name(VUELVE, "  Marta  ")
        sincronizar(conn, JobEngine(), JUEVES_18)
        assert CRMRepository(conn).name(VUELVE) == "Marta"


class TestEmbudo:
    def test_sobre_las_conversaciones_de_prueba(self, conn):
        ActionRepository(conn).add("5491155556666:job", PERDIDO, datetime(2026, 9, 17), nota="se arrepintio")
        r = sincronizar(conn, JobEngine(), JUEVES_18)
        e = embudo.calcular(r.leads, hasta=JUEVES_18)
        assert [(p.etiqueta, p.cantidad) for p in e.pasos] == [
            ("Consultas", 5), ("Con datos", 5), ("Presupuestados", 4), ("Confirmados", 3), ("Realizados", 0),
        ]
        assert e.perdidos == 1 and e.motivos == [("Lo diste por perdido", 1)]
        assert e.comprometido == Decimal(48000 + 45000 + 70000)
        assert (e.clientes, e.recurrentes) == (4, 1)

    def test_el_periodo_filtra_por_fecha_de_la_consulta(self, conn):
        r = sincronizar(conn, JobEngine(), JUEVES_18)
        e = embudo.calcular(r.leads, hasta=JUEVES_18, desde=datetime(2026, 9, 1))
        assert e.pasos[0].cantidad == 4  # el flete de agosto queda afuera


# --------------------------------------------------------------------------
# Dashboard
# --------------------------------------------------------------------------


@pytest.fixture
def cliente(tmp_path):
    db = tmp_path / "f6.db"
    c = connect(db)
    ConversationStore(c).ingest(list(FixtureMessageSource().poll()))
    c.close()
    with TestClient(create_app(db, ahora=lambda: JUEVES_18)) as tc:
        yield tc


class TestPantallas:
    def test_agenda(self, cliente):
        html = cliente.get("/agenda").text
        assert "Para cerrar" in html and "Palermo → Belgrano" in html
        assert html.index("Mañana") < html.index("Villa Crespo → Caballito") < html.index("Jueves")
        assert "webcal://" in html

    def test_marcar_realizado_cierra_y_factura(self, cliente):
        r = cliente.post(f"/fletes/{VUELVE}:job/realizado", headers={"HX-Request": "true"})
        assert r.headers["HX-Refresh"] == "true"
        assert cliente.get(f"/api/fletes/{VUELVE}:job").json()["flete"]["estado"] == "realizado"
        assert "Para cerrar" not in cliente.get("/agenda").text
        assert cliente.get("/api/embudo?periodo=todo").json()["kpis"][0]["valor"] == "$48.000"

    def test_se_cierra_un_flete_aunque_no_estuviera_agendado(self, cliente):
        assert cliente.post("/fletes/5491155556666:job/realizado").status_code in (200, 303)
        assert cliente.get("/api/fletes/5491155556666:job").json()["flete"]["estado"] == "realizado"

    def test_lo_ya_cerrado_no_se_cierra_dos_veces(self, cliente):
        cliente.post("/fletes/5491155556666:job/realizado")
        assert cliente.post("/fletes/5491155556666:job/realizado").status_code == 409

    def test_dar_por_perdido(self, cliente):
        cliente.post("/api/fletes/5491155556666:job/acciones", json={"tipo": "perdido", "nota": "muy caro"})
        d = cliente.get("/api/fletes/5491155556666:job").json()
        assert d["flete"]["estado"] == "cancelado"
        assert d["historia"][-1]["causa"] == "dado por perdido: muy caro"

    def test_nombre_del_cliente(self, cliente):
        cliente.post(f"/clientes/{VUELVE}/nombre", data={"nombre": "Marta"})
        assert "Marta" in cliente.get("/clientes").text
        assert cliente.get(f"/api/fletes/{VUELVE}:job2").json()["flete"]["nombre"] == "Marta"

    def test_detalle_muestra_los_otros_fletes_del_cliente(self, cliente):
        html = cliente.get(f"/fletes/{VUELVE}:job2").text
        assert "Otros fletes de este cliente" in html and f"/fletes/{VUELVE}:job" in html

    def test_numeros(self, cliente):
        html = cliente.get("/numeros?periodo=todo").text
        assert "De 5 consultas" in html and "1 volvió a pedir" in html
        assert cliente.get("/numeros?periodo=siempre").status_code == 422

    def test_ics(self, cliente):
        url = cliente.get("/api/agenda").json()["url_ics"]
        ruta = url.split("testserver", 1)[1]
        r = cliente.get(ruta)
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/calendar")
        assert r.text.count("BEGIN:VEVENT") == 3
        assert cliente.get("/calendario/cualquiera.ics").status_code == 404


def test_el_ics_no_pide_la_clave_pero_si_su_secreto(tmp_path):
    db = tmp_path / "c.db"
    c = connect(db)
    ConversationStore(c).ingest(list(FixtureMessageSource().poll()))
    token = LocalCalendar(c).token()
    c.close()
    with TestClient(create_app(db, ahora=lambda: JUEVES_18, clave="x")) as tc:
        assert tc.get(f"/calendario/{token}.ics").status_code == 200
        assert tc.get("/agenda", follow_redirects=False).status_code == 303


class TestOtroFleteAMano:
    """Lo que se arregla en persona no deja rastro en el chat."""

    def test_el_fletero_puede_partir_la_conversacion(self):
        from estiba.jobs.episodios import episodios

        inicio = datetime(2026, 9, 20, 10)
        conv = _chat(
            ("c", "necesito llevar una heladera de Alberti a Colon", inicio),
            ("v", "te sale 35 mil", inicio + timedelta(minutes=5)),
            ("c", "dale", inicio + timedelta(minutes=10)),
            ("c", "hola, como ayer 19 hs", inicio + timedelta(days=2)),
        )
        sin_corte = episodios(conv, JobEngine(), lambda _: [])
        assert len(sin_corte) == 1        # ninguna regla lo separa solo

        ultimo = conv.messages[-1].id
        con_corte = episodios(conv, JobEngine(), lambda _: [], a_mano={ultimo})
        assert len(con_corte) == 2
        assert len(con_corte[1].conversacion.messages) == 1

    def test_el_corte_a_mano_manda_sobre_las_reglas(self):
        """Aunque no haya ninguna señal, si el fletero lo dice, se parte."""
        from estiba.jobs.episodios import episodios

        inicio = datetime(2026, 9, 20, 10)
        conv = _chat(
            ("c", "hola", inicio),
            ("v", "hola", inicio + timedelta(minutes=1)),
            ("c", "gracias", inicio + timedelta(minutes=2)),
        )
        cortado = episodios(conv, JobEngine(), lambda _: [], a_mano={conv.messages[2].id})
        assert len(cortado) == 2


class TestSospechasDeOtroFlete:
    """Señales de un flete que el chat no dice con todas las letras."""

    def _sospechas(self, *lineas):
        from estiba.jobs.episodios import sospechas

        conv = _chat(*lineas)
        return sospechas(conv, JobEngine(), datetime(2026, 9, 26, 12))

    def test_nombrar_un_viaje_anterior(self):
        """Real: "Hola si como ayer 19 hs"."""
        inicio = datetime(2026, 9, 20, 10)
        s = self._sospechas(
            ("c", "necesito un flete de Alberti a Colon", inicio),
            ("c", "hola si como ayer 19 hs", inicio + timedelta(days=2)),
        )
        assert s and "viaje anterior" in s[0].porque

    def test_una_conversacion_normal_no_levanta_sospechas(self):
        inicio = datetime(2026, 9, 20, 10)
        assert not self._sospechas(
            ("c", "necesito un flete de Alberti a Colon", inicio),
            ("v", "te sale 35 mil", inicio + timedelta(minutes=5)),
            ("c", "dale, gracias", inicio + timedelta(minutes=10)),
        )


class TestNumeroDeFlete:
    """Un numero para nombrarlo en voz alta: "el 12"."""

    def test_una_consulta_todavia_no_tiene_numero(self, conn):
        """Cada conversacion crea un flete en borrador: numerarlos a todos
        seria darle numero a cosas que nunca pasaron."""
        from estiba.bandeja.servicio import sincronizar

        sincronizar(conn, JobEngine(), JUEVES_18)
        sin_numero = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE state = 'borrador' AND numero IS NOT NULL"
        ).fetchone()[0]
        assert sin_numero == 0

    def test_una_consulta_que_se_pierde_por_silencio_tampoco(self, conn):
        """Nunca hubo un precio sobre la mesa: no fue un flete.

        Numerando todo lo que salia de borrador quedaban 26 numeros para 5
        fletes reales, y "el flete 26" dejaba de significar algo.
        """
        from estiba.bandeja.servicio import sincronizar

        sincronizar(conn, JobEngine(), datetime(2026, 10, 15, 12))
        filas = conn.execute(
            "SELECT id FROM jobs j WHERE state = 'cancelado' AND numero IS NOT NULL "
            "AND NOT EXISTS (SELECT 1 FROM job_events e WHERE e.job_id = j.id "
            "                AND e.to_state = 'presupuestado')"
        ).fetchall()
        assert not filas

    def test_se_numera_al_dejar_de_ser_una_consulta(self, conn):
        from estiba.bandeja.servicio import sincronizar

        sincronizar(conn, JobEngine(), JUEVES_18)
        numeros = [f["numero"] for f in conn.execute(
            "SELECT numero FROM jobs WHERE numero IS NOT NULL ORDER BY numero")]
        assert numeros and numeros == list(range(1, len(numeros) + 1))

    def test_el_numero_no_cambia_nunca(self, conn):
        """Si se hablo de "el 12" con el cliente, sigue siendo el 12."""
        from estiba.bandeja.servicio import sincronizar

        sincronizar(conn, JobEngine(), JUEVES_18)
        antes = dict(conn.execute("SELECT id, numero FROM jobs WHERE numero IS NOT NULL"))
        sincronizar(conn, JobEngine(), datetime(2026, 9, 21, 12))      # cambian estados, se pierden fletes
        despues = dict(conn.execute("SELECT id, numero FROM jobs WHERE numero IS NOT NULL"))
        for job_id, numero in antes.items():
            assert despues[job_id] == numero

    def test_no_se_repite_un_numero(self, conn):
        from estiba.bandeja.servicio import sincronizar

        sincronizar(conn, JobEngine(), JUEVES_18)
        sincronizar(conn, JobEngine(), datetime(2026, 9, 21, 12))
        filas = [f["numero"] for f in conn.execute(
            "SELECT numero FROM jobs WHERE numero IS NOT NULL")]
        assert len(filas) == len(set(filas))


class TestDespuesDeCerrarUnFlete:
    """Que pasa con los mensajes que llegan despues de cerrarlo."""

    def _eps(self, ultimo):
        from estiba.jobs.engine import Accion, REALIZADO
        from estiba.jobs.episodios import episodios

        inicio = datetime(2026, 9, 20, 10)
        conv = _chat(
            ("c", "necesito un flete de Alberti a Colon", inicio),
            ("v", "te sale 35 mil", inicio + timedelta(minutes=5)),
            ("c", "dale", inicio + timedelta(minutes=10)),
            ("c", ultimo, inicio + timedelta(days=1)),
        )
        hecho = [Accion(inicio + timedelta(minutes=30), REALIZADO)]
        return episodios(conv, JobEngine(), lambda _: hecho)

    def test_lo_que_sigue_hablando_del_mismo_queda_ahi(self):
        """Un gracias o una factura no son otro flete."""
        assert len(self._eps("gracias, llegó todo bien")) == 1
        assert len(self._eps("me pasás la factura?")) == 1

    def test_pedir_otro_flete_si_abre_uno_nuevo(self):
        """Real: "necesito otro flete" quedaba afuera porque el patron pedia
        el sustantivo pegado al verbo, y el cliente que vuelve dice "otro"."""
        assert len(self._eps("necesito otro flete para el viernes")) == 2
        assert len(self._eps("tengo que hacer otro flete")) == 2

    def test_la_negacion_no_abre_nada(self):
        assert len(self._eps("no necesito otro flete, gracias")) == 1


class TestAlarmasYTentativos:
    """El .ics al que se suscribe el celular no tenia ninguna alarma.

    Estaba suscripto y no sonaba nunca, asi que servia para mirar y no para
    enterarse. Y solo entraban los fletes confirmados: una fecha dicha en un
    audio sobre un flete sin cerrar —en la base real hay una, el 30 a las
    11— no estaba en ningun lado.
    """

    ESTAMPA = datetime(2026, 9, 20, 12)

    def test_un_flete_confirmado_suena_antes(self):
        e = evento_de(_job(when=date(2026, 9, 25), hour=time(9)))
        ics = calendario([(e, False)], self.ESTAMPA)
        assert "BEGIN:VALARM" in ics and "TRIGGER:-PT2H" in ics
        assert "STATUS:CONFIRMED" in ics

    def test_uno_de_todo_el_dia_avisa_la_vispera(self):
        """Dos horas antes de un evento sin hora no significa nada."""
        ics = calendario([(evento_de(_job(when=date(2026, 9, 25), hour=None)), False)], self.ESTAMPA)
        assert "TRIGGER:-PT15H" in ics

    def test_lo_que_nadie_confirmo_entra_pero_no_suena(self):
        """Una fecha que alguien nombro y no esta en ningun lado se pasa.
        Pero no se madruga por un quizas."""
        e = evento_de(_job(when=date(2026, 9, 30), hour=time(11)), tentativo=True)
        ics = calendario([(e, False)], self.ESTAMPA)
        assert "STATUS:TENTATIVE" in ics
        assert "BEGIN:VALARM" not in ics

    def test_y_se_ve_que_no_esta_confirmado(self):
        """El calendario del celular no siempre distingue un TENTATIVE de un
        CONFIRMED de un vistazo: el titulo lo dice con palabras."""
        e = evento_de(_job(when=date(2026, 9, 30)), tentativo=True)
        assert e.titulo.startswith("Sin confirmar · ")
        assert "SIN CONFIRMAR" in e.descripcion

    def test_sin_ruta_el_titulo_dice_de_quien_es(self):
        """"Flete ¿? → ¿?" en el calendario del celular no dice nada."""
        e = evento_de(_job(when=date(2026, 9, 30), origin=None, destination=None))
        assert "¿? → ¿?" not in e.titulo
        assert "549" in e.titulo

    def test_un_flete_cancelado_no_suena(self):
        ics = calendario([(evento_de(_job(when=date(2026, 9, 25))), True)], self.ESTAMPA)
        assert "STATUS:CANCELLED" in ics and "BEGIN:VALARM" not in ics


class TestElCalendarioNoSeQuedaConFechasViejas:
    def test_si_el_flete_pierde_la_fecha_el_evento_se_cae(self, conn):
        """Una fecha sale de un audio, se vuelve a transcribir o se corta el
        episodio, y el flete deja de tenerla. El evento quedaba en el celular
        con el titulo viejo y un dia que ya no existia en ningun lado. En la
        base real habia uno asi.
        """
        cal = LocalCalendar(conn, reloj=lambda: datetime(2026, 9, 20, 12))
        cal.programar(_job(when=date(2026, 9, 25)))
        assert [e for e, cancelado in cal.eventos() if not cancelado]

        # El flete se queda sin fecha: nadie vuelve a llamar a `programar`.
        assert cal.cancelar_de("549:job")
        assert not [e for e, cancelado in cal.eventos() if not cancelado]


class TestElMesComoGrilla:
    """El calendario, visto como calendario.

    Una lista dice que viene; la grilla dice donde estan los huecos, que es
    lo que se mira cuando alguien pregunta "¿el jueves podes?".
    """

    HOY = date(2026, 9, 28)

    def _mes(self, eventos=(), cual=None):
        from estiba.web.presentacion import mes_de

        return mes_de(list(eventos), cual or self.HOY, self.HOY)

    def test_las_semanas_empiezan_el_lunes_y_estan_enteras(self):
        """Cada columna tiene que ser siempre el mismo dia de la semana: si
        no, la grilla no se puede leer de un vistazo."""
        m = self._mes()
        for semana in m.semanas:
            assert len(semana) == 7
            assert semana[0].fecha.weekday() == 0
            assert semana[-1].fecha.weekday() == 6

    def test_los_dias_de_los_meses_vecinos_se_marcan(self):
        """Estan para que la grilla quede entera, no para confundir."""
        m = self._mes()
        assert any(c.de_otro_mes for c in m.semanas[0])
        assert all(c.fecha.month == 9 for s in m.semanas for c in s if not c.de_otro_mes)

    def test_cuenta_los_fletes_de_cada_dia_y_separa_los_que_no_cerraron(self):
        e1 = evento_de(_job(when=date(2026, 9, 30), hour=time(9)))
        e2 = evento_de(_job(when=date(2026, 9, 30), hour=time(11)), tentativo=True)
        celda = next(c for s in self._mes([(e1, False), (e2, False)]).semanas
                     for c in s if c.fecha == date(2026, 9, 30))
        assert (celda.confirmados, celda.tentativos, celda.total) == (1, 1, 2)

    def test_un_evento_cancelado_no_se_cuenta(self):
        e = evento_de(_job(when=date(2026, 9, 30)))
        celda = next(c for s in self._mes([(e, True)]).semanas
                     for c in s if c.fecha == date(2026, 9, 30))
        assert celda.total == 0

    def test_un_dia_que_ya_paso_no_promete_un_salto(self):
        """La lista de abajo es de lo que viene: enlazar un dia pasado daba
        un ancla que no existe, se tocaba y no pasaba nada."""
        e = evento_de(_job(when=date(2026, 9, 21)))
        celda = next(c for s in self._mes([(e, False)]).semanas
                     for c in s if c.fecha == date(2026, 9, 21))
        assert celda.total == 1 and celda.pasado and not celda.enlazable

    def test_hoy_se_reconoce_y_se_puede_volver(self):
        m = self._mes()
        assert sum(1 for s in m.semanas for c in s if c.es_hoy) == 1
        assert m.hoy is None, "estando en el mes de hoy no hace falta el botón"
        assert self._mes(cual=date(2026, 12, 1)).hoy == "2026-09"

    def test_se_puede_ir_al_mes_de_al_lado(self):
        m = self._mes()
        assert (m.anterior, m.siguiente) == ("2026-08", "2026-10")

    def test_enero_y_diciembre_no_rompen_el_año(self):
        assert self._mes(cual=date(2026, 1, 15)).anterior == "2025-12"
        assert self._mes(cual=date(2026, 12, 15)).siguiente == "2027-01"
