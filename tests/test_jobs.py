"""Fase 4: JobEngine, datos faltantes y PricingEngine."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from estiba.adapters.fixtures import FIXTURES_DIR, FixtureMessageSource
from estiba.cli import main
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
    Transcript,
)
from estiba.jobs.engine import JobEngine, Politica
from estiba.jobs.pricing import PricingEngine, Tarifa
from estiba.storage.db import connect
from estiba.storage.repositories import JobRepository

JUEVES_18 = datetime(2026, 9, 18, 12)
LUNES_21 = datetime(2026, 9, 21, 12)


def _fixture(nombre: str, con_audios: bool = True) -> Conversation:
    archivo = FIXTURES_DIR / nombre
    mensajes = list(FixtureMessageSource.read_file(archivo))
    transcripciones = {}
    if con_audios:
        transcripciones = {
            mid: Transcript(mid, texto, 0.9, "esperada", 1.0)
            for mid, texto in FixtureMessageSource.expected_transcripts(archivo).items()
        }
    return Conversation(mensajes[0].chat_id, mensajes, transcripciones)


def _chat(*lineas: tuple[str, str]) -> Conversation:
    """Conversacion a mano: ("c", texto) del cliente, ("v", texto) del fletero."""
    inicio = datetime(2026, 9, 15, 10)
    mensajes = [
        Message(f"x:{i:03d}", "x", quien == "c", inicio + timedelta(minutes=i),
                MessageKind.TEXT, text=texto)
        for i, (quien, texto) in enumerate(lineas)
    ]
    return Conversation("x", mensajes)


def _estados(ev) -> list[JobState]:
    return [e.hacia for e in ev.eventos]


def _slot(valor, confianza: float = 0.9) -> Slot:
    return Slot(valor, confianza, Evidence("m", "cita", ExtractionMethod.REGEX))


# --------------------------------------------------------------------------
# Las tres conversaciones de fixtures
# --------------------------------------------------------------------------


class TestFixtures:
    def test_flujo_feliz_cotiza_y_confirma_con_su_causa(self):
        ev = JobEngine().evaluar(_fixture("01-datos-fuera-de-orden.json"), LUNES_21)
        assert ev.job.state is JobState.CONFIRMED
        assert _estados(ev) == [JobState.QUOTED, JobState.CONFIRMED]
        assert ev.eventos[0].message_id == "5491133334444:007"  # "Te sale 45 mil"
        assert ev.eventos[1].message_id == "5491133334444:008"  # "dale, cerramos"
        # El flete era el sabado 19: al lunes 21 ya paso.
        assert ev.alertas == ["la fecha (19/09) ya pasó: ¿se hizo el flete?"]
        assert JobEngine().evaluar(_fixture("01-datos-fuera-de-orden.json"), JUEVES_18).alertas == []

    def test_manana_te_confirmo_espera_y_despues_se_pierde(self):
        conv = _fixture("02-manana-te-confirmo.json")

        antes = JobEngine().evaluar(conv, JUEVES_18)
        assert antes.job.state is JobState.WAITING_CUSTOMER
        assert any("seguimiento" in a for a in antes.alertas)

        despues = JobEngine().evaluar(conv, LUNES_21)
        assert despues.job.state is JobState.CANCELLED
        cancelacion = despues.eventos[-1]
        assert "sin respuesta" in cancelacion.causa
        assert cancelacion.at == datetime(2026, 9, 19, 19, 33)  # 4 dias despues del ultimo mensaje

    def test_la_aceptacion_con_fecha_dudosa_no_confirma_hasta_que_se_aclara(self):
        ev = JobEngine().evaluar(_fixture("03-cambio-de-destino.json"), LUNES_21)
        assert ev.job.state is JobState.CONFIRMED
        # "dale, lo hacemos el jueves que viene" (007) no alcanza: la fecha es ambigua.
        # Confirma "si, ese" (009), despues de que el fletero propuso el jueves 24.
        assert ev.eventos[-1].message_id == "5491177778888:009"
        assert ev.job.when.value == date(2026, 9, 24)
        assert ev.alertas == []

    def test_reprocesar_da_la_misma_historia(self):
        conv = _fixture("03-cambio-de-destino.json")
        assert JobEngine().evaluar(conv, LUNES_21).eventos == JobEngine().evaluar(conv, LUNES_21).eventos

    def test_lo_que_pasa_despues_de_ahora_no_cuenta(self):
        ev = JobEngine().evaluar(_fixture("01-datos-fuera-de-orden.json"), datetime(2026, 9, 15, 10, 36))
        assert ev.job.state is JobState.QUOTED


# --------------------------------------------------------------------------
# Reglas del motor
# --------------------------------------------------------------------------


class TestTransiciones:
    def test_aceptar_sin_cotizacion_no_confirma(self):
        ev = JobEngine().evaluar(_chat(("c", "de Flores a Moron una heladera el viernes"), ("c", "dale")), LUNES_21)
        assert ev.job.state is JobState.DRAFT

    def test_aceptar_con_datos_faltantes_queda_como_alerta(self):
        ev = JobEngine().evaluar(
            _chat(("c", "de Flores a Moron una heladera"), ("v", "te sale 50 mil"), ("c", "dale")),
            datetime(2026, 9, 15, 11),
        )
        assert ev.job.state is JobState.QUOTED
        assert any("aceptó, pero falta fecha" in a for a in ev.alertas)
        assert "¿Para qué día lo necesitás?" in ev.preguntas

    def test_el_cliente_cancela(self):
        ev = JobEngine().evaluar(
            _chat(("c", "de Flores a Moron una heladera"), ("v", "te sale 50 mil"), ("c", "ya consegui, gracias")),
            LUNES_21,
        )
        assert ev.job.state is JobState.CANCELLED
        assert ev.eventos[-1].causa == "el cliente canceló"

    def test_nueva_cotizacion_despues_de_posponer_vuelve_a_presupuestado(self):
        ev = JobEngine().evaluar(
            _chat(
                ("c", "de Flores a Moron una heladera"), ("v", "te sale 50 mil"),
                ("c", "tengo que ver"), ("v", "te lo dejo en 45 mil, sale 45 mil"),
            ),
            datetime(2026, 9, 15, 11),
        )
        assert _estados(ev) == [JobState.QUOTED, JobState.WAITING_CUSTOMER, JobState.QUOTED]
        assert ev.job.quoted_price.value == Decimal(45000)

    def test_corregir_despues_de_cotizar_avisa_que_hay_que_revisar_el_precio(self):
        ev = JobEngine().evaluar(
            _chat(("c", "de Flores a Moron una heladera"), ("v", "te sale 50 mil"),
                  ("c", "pera, no es mas a Moron, es a Merlo")),
            datetime(2026, 9, 15, 11),
        )
        assert ev.job.destination.value == "Merlo"
        assert any("revisar el precio" in a for a in ev.alertas)

    def test_si_el_cliente_escribio_ultimo_no_se_cancela_por_silencio(self):
        """El que no respondio es el fletero: eso no es un cliente perdido."""
        ev = JobEngine().evaluar(
            _chat(("c", "de Flores a Moron una heladera"), ("v", "te sale 50 mil"), ("c", "incluye subirla?")),
            LUNES_21,
        )
        assert ev.job.state is JobState.QUOTED
        assert any("espera respuesta" in a for a in ev.alertas)

    def test_la_politica_de_silencio_es_configurable(self):
        conv = _fixture("02-manana-te-confirmo.json")
        paciente = JobEngine(politica=Politica(abandono=timedelta(days=30)))
        assert paciente.evaluar(conv, LUNES_21).job.state is JobState.WAITING_CUSTOMER


# --------------------------------------------------------------------------
# Precio
# --------------------------------------------------------------------------


def _job(**slots) -> FreightJob:
    base = {"origin": "Almagro", "destination": "Caballito", "items": ["heladera"], "floors_no_lift": 0}
    base.update(slots)
    return FreightJob(
        "j", "c",
        **{k: v if isinstance(v, Slot) else _slot(v) for k, v in base.items() if v is not None},
    )


class TestPrecio:
    def test_desglose(self):
        p = PricingEngine().estimate(_job(items=["heladera", "15 cajas"], floors_no_lift=3))
        assert dict(p.desglose) == {
            "base": Decimal(30000), "heladera": Decimal(8000),
            "15 cajas": Decimal(10500), "3 pisos por escalera": Decimal(21000),
        }
        assert p.total == Decimal(70000) and p.completo

    def test_sin_lugares_o_cosas_no_hay_precio(self):
        assert PricingEngine().estimate(FreightJob("j", "c", origin=_slot("Almagro"))) is None

    def test_lo_que_no_sabe_no_lo_esconde(self):
        p = PricingEngine().estimate(_job(items=["cajas", "heladera"], floors_no_lift=None))
        assert not p.completo
        assert any("cajas" in s for s in p.sin_contar)
        assert any("pisos" in s for s in p.sin_contar)

    def test_lugar_desconocido(self):
        p = PricingEngine().estimate(_job(destination="Gerli"))
        assert any("zona" in s for s in p.sin_contar)

    def test_conurbano_y_juego_de_comedor(self):
        p = PricingEngine().estimate(_job(destination="Morón", items=["juego de comedor", "mesa", "6 sillas"]))
        assert p.total == Decimal(57000)  # la mesa y las sillas son parte del juego

    def test_ayudantes_dudosos_no_se_cobran(self):
        p = PricingEngine().estimate(_job(helpers=_slot(2, 0.7)))
        assert "2 ayudantes" not in dict(p.desglose)
        assert any("ayudantes" in s for s in p.sin_contar)
        seguro = PricingEngine().estimate(_job(helpers=_slot(2, 0.9)))
        assert dict(seguro.desglose)["2 ayudantes"] == Decimal(20000)

    def test_tarifa_desde_toml(self, tmp_path):
        toml = tmp_path / "tarifa.toml"
        toml.write_text("base = 40000\npiso_sin_ascensor = 5000\n", encoding="utf-8")
        t = Tarifa.desde_toml(toml)
        assert t.base == Decimal(40000) and t.grande == Tarifa().grande
        assert Tarifa.desde_toml(tmp_path / "no-existe.toml") == Tarifa()

    def test_tarifa_con_clave_mal_escrita_falla(self, tmp_path):
        toml = tmp_path / "tarifa.toml"
        toml.write_text("bsae = 40000\n", encoding="utf-8")
        with pytest.raises(ValueError, match="bsae"):
            Tarifa.desde_toml(toml)

    @pytest.mark.parametrize(
        ("archivo", "cotizado"),
        [("01-datos-fuera-de-orden.json", 45000), ("02-manana-te-confirmo.json", 60000),
         ("03-cambio-de-destino.json", 70000)],
    )
    def test_la_tarifa_por_defecto_anda_cerca_de_lo_que_cotizaste(self, archivo, cotizado):
        ev = JobEngine().evaluar(_fixture(archivo), LUNES_21)
        assert abs(ev.presupuesto.total - cotizado) <= Decimal("0.1") * cotizado


# --------------------------------------------------------------------------
# Preguntas
# --------------------------------------------------------------------------


class TestPreguntas:
    def test_pregunta_lo_que_falta_para_cotizar(self):
        ev = JobEngine().evaluar(_chat(("c", "hola, haces fletes?")), datetime(2026, 9, 15, 10, 5))
        assert ev.preguntas[:3] == [
            "¿Desde qué barrio sería?", "¿Hasta dónde lo llevamos?", "¿Qué cosas hay que llevar?"
        ]

    def test_pregunta_cantidades_y_piso_si_dijo_sin_ascensor(self):
        ev = JobEngine().evaluar(
            _chat(("c", "de Almagro a Boedo unas cajas, no hay ascensor")), datetime(2026, 9, 15, 10, 5)
        )
        assert "¿Cuántas cajas son, más o menos?" in ev.preguntas
        assert "¿En qué piso es?" in ev.preguntas

    def test_una_fecha_con_duda_se_pregunta_con_la_fecha_concreta(self):
        ev = JobEngine().evaluar(
            _chat(("c", "de Almagro a Boedo una heladera, el martes que viene")), datetime(2026, 9, 15, 10, 5)
        )
        assert "¿Sería el martes 22/09?" in ev.preguntas

    def test_un_flete_cerrado_no_tiene_preguntas(self):
        assert JobEngine().evaluar(_fixture("02-manana-te-confirmo.json"), LUNES_21).preguntas == []


# --------------------------------------------------------------------------
# Persistencia y CLI
# --------------------------------------------------------------------------


class TestPersistencia:
    def test_guarda_estado_slots_con_evidencia_e_historia(self, tmp_path):
        conn = connect(tmp_path / "j.db")
        conv = _fixture("03-cambio-de-destino.json", con_audios=False)
        ConversationStore(conn).ingest(conv.messages)
        ev = JobEngine().evaluar(conv, LUNES_21)
        repo = JobRepository(conn)
        repo.save(ev.job, ev.eventos)
        repo.save(ev.job, ev.eventos)  # guardar dos veces no duplica

        assert repo.state(ev.job.id) is JobState.CONFIRMED
        slots = repo.slots(ev.job.id)
        assert slots["destination"]["value_json"] == '"Barracas"'
        assert slots["destination"]["message_id"] == "5491177778888:005"
        assert slots["when"]["value_json"] == '"2026-09-24"'
        assert slots["quoted_price"]["value_json"] == '"70000"'
        assert [e["to_state"] for e in repo.events(ev.job.id)] == ["presupuestado", "confirmado"]


class TestCli:
    def test_jobs_muestra_estado_precio_y_preguntas(self, tmp_path, capsys):
        db = str(tmp_path / "c.db")
        # Las conversaciones de prueba son de Buenos Aires: con la tarifa por
        # barrios, que sin un tarifa.toml ya no es la que se usa.
        tarifa = tmp_path / "tarifa.toml"
        tarifa.write_text("")
        main(["--db", db, "ingest"])
        capsys.readouterr()
        assert main(["--db", db, "jobs", "--ahora", "2026-09-18T12:00", "--tarifa", str(tarifa)]) == 0
        salida = capsys.readouterr().out
        assert "5491155556666  ESPERANDO_CLIENTE" in salida
        assert "cotizado $60.000 | sugerido $57.000 o mas" in salida
        assert "¿Para qué día lo necesitás?" in salida
        assert "hacer seguimiento" in salida

        main(["--db", db, "jobs", "5491155556666", "--ahora", "2026-09-21T12:00", "--tarifa", str(tarifa)])
        assert "CANCELADO" in capsys.readouterr().out
        assert JobRepository(connect(db)).state("5491155556666:job") is JobState.CANCELLED
