"""Fase 3: intencion y extraccion de datos por reglas.

Los tests de mas abajo corren sobre las conversaciones de fixtures/, que son
los casos dificiles de verdad. Si una regla nueva rompe alguno, no es un test
viejo: es un cliente real al que se le entenderia mal.
"""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal

import pytest

from estiba.adapters.fixtures import FIXTURES_DIR, FixtureMessageSource
from estiba.cli import main
from estiba.core.models import Conversation, Message, MessageKind, Transcript
from estiba.extraction.extractor import SlotExtractor
from estiba.extraction.fechas import fechas, horarios
from estiba.extraction.intencion import Intencion, intencion
from estiba.extraction.reglas import ayudantes, items, lugares, pisos, precio


def _conversacion(nombre: str, con_audios: bool = True) -> Conversation:
    archivo = FIXTURES_DIR / nombre
    mensajes = list(FixtureMessageSource.read_file(archivo))
    transcripciones = {}
    if con_audios:
        transcripciones = {
            mid: Transcript(mid, texto, 0.9, "esperada", 1.0)
            for mid, texto in FixtureMessageSource.expected_transcripts(archivo).items()
        }
    return Conversation(mensajes[0].chat_id, mensajes, transcripciones)


def _extraer(nombre: str, con_audios: bool = True):
    return SlotExtractor().extract(_conversacion(nombre, con_audios))


def _valores(ex) -> dict[str, object]:
    return {campo: c.slot.value for campo, c in ex.ganadores.items()}


MARTES = datetime(2026, 9, 15, 10)
JUEVES = datetime(2026, 9, 17, 10)


# --------------------------------------------------------------------------
# Las tres conversaciones de fixtures
# --------------------------------------------------------------------------


class TestDatosFueraDeOrden:
    def test_junta_datos_que_llegan_salteados(self):
        v = _valores(_extraer("01-datos-fuera-de-orden.json"))
        assert v["origin"] == "Villa Crespo"
        assert v["destination"] == "Caballito"
        assert v["items"] == ["lavarropas", "heladera"]
        assert v["when"] == date(2026, 9, 19)  # "el sabado", dicho un martes
        assert v["daypart"] == "manana"
        assert v["floors_no_lift"] == 0
        assert v["helpers"] == 2
        assert v["quoted_price"] == Decimal(45000)

    def test_la_hora_propuesta_por_el_fletero_cuenta_porque_el_cliente_acepto(self):
        ex = _extraer("01-datos-fuera-de-orden.json")
        hora = ex.ganadores["hour"]
        assert hora.slot.value == time(9, 0)
        assert hora.slot.evidence.quote == "9 de la manana"

    def test_somos_dos_queda_como_duda(self):
        ex = _extraer("01-datos-fuera-de-orden.json")
        assert ex.job.missing == []
        assert any("somos 2" in d for d in ex.dudas)


class TestMananaTeConfirmo:
    def test_no_inventa_una_fecha(self):
        ex = _extraer("02-manana-te-confirmo.json")
        assert "when" not in ex.ganadores
        assert ex.job.missing == ["when"]

    def test_detecta_que_el_cliente_pospone(self):
        ex = _extraer("02-manana-te-confirmo.json")
        ultima = ex.intenciones[-1]
        assert ultima.intencion is Intencion.POSPONE
        assert ultima.cita == "manana te confirmo"
        assert not any(i.intencion is Intencion.ACEPTA for i in ex.intenciones)

    def test_igual_junta_lo_que_si_dijo(self):
        v = _valores(_extraer("02-manana-te-confirmo.json"))
        assert (v["origin"], v["destination"]) == ("Flores", "Morón")
        assert v["quoted_price"] == Decimal(60000)


class TestCambioDeDestino:
    def test_gana_el_ultimo_destino_y_el_anterior_queda_registrado(self):
        ex = _extraer("03-cambio-de-destino.json")
        assert ex.ganadores["destination"].slot.value == "Barracas"
        destinos = [c.slot.value for c in ex.candidatos if c.campo == "destination"]
        assert destinos == ["San Telmo", "Barracas"]
        assert any(i.intencion is Intencion.CORRIGE for i in ex.intenciones)

    def test_el_jueves_ambiguo_se_resuelve_con_la_fecha_que_el_cliente_acepta(self):
        ex = _extraer("03-cambio-de-destino.json")
        cuando = ex.ganadores["when"]
        assert cuando.slot.value == date(2026, 9, 24)
        assert cuando.slot.evidence.quote == "jueves 24"
        assert cuando.duda is None
        # El "jueves que viene" original quedo registrado con su duda.
        ambiguo = [c for c in ex.candidatos if c.campo == "when" and c.duda]
        assert len(ambiguo) == 1 and "dicho un jueves" in ambiguo[0].duda

    def test_con_el_audio_transcripto_aparecen_las_cajas_y_el_piso(self):
        v = _valores(_extraer("03-cambio-de-destino.json"))
        assert v["items"] == ["15 cajas", "placard"]
        assert v["floors_no_lift"] == 3
        assert v["quoted_price"] == Decimal(70000)

    def test_sin_el_audio_no_se_inventa_lo_que_dice(self):
        ex = _extraer("03-cambio-de-destino.json", con_audios=False)
        v = _valores(ex)
        assert v["items"] == ["cajas", "placard"]
        # El fletero dijo "tercero sin ascensor", pero el dato es del cliente.
        assert "floors_no_lift" not in v
        assert ex.sin_texto == ["5491177778888:002"]


class TestEvidencia:
    @pytest.mark.parametrize("archivo", sorted(p.name for p in FIXTURES_DIR.glob("*.json")))
    def test_cada_cita_esta_en_el_mensaje_que_dice(self, archivo):
        """Regla 2: ningun dato existe sin evidencia verificable."""
        conv = _conversacion(archivo)
        textos = {m.id: conv.text_of(m) for m in conv.messages}
        ex = SlotExtractor().extract(conv)
        for c in ex.candidatos:
            texto = textos[c.slot.evidence.message_id]
            for pedazo in c.slot.evidence.quote.split(" ... "):
                assert pedazo in texto, f"{c.campo}: {pedazo!r} no esta en {texto!r}"


# --------------------------------------------------------------------------
# Reglas sueltas
# --------------------------------------------------------------------------


class TestFechas:
    def test_a_la_manana_es_franja_y_no_manana(self):
        assert fechas("el sabado a la manana", MARTES)[0].valor == date(2026, 9, 19)
        assert fechas("a la mañana", MARTES) == []
        assert [h.valor for h in horarios("a la mañana")] == ["manana"]

    def test_manana_si_es_fecha_cuando_no_pospone(self):
        assert fechas("mañana a la tarde", MARTES)[0].valor == date(2026, 9, 16)
        assert fechas("pasado mañana", MARTES)[0].valor == date(2026, 9, 17)

    @pytest.mark.parametrize("texto", ["mañana te confirmo", "manana te aviso", "mañana le digo"])
    def test_manana_te_confirmo_no_es_fecha(self, texto):
        assert fechas(texto, MARTES) == []

    def test_se_resuelve_contra_la_hora_del_mensaje_no_contra_hoy(self):
        assert fechas("el viernes", MARTES)[0].valor == date(2026, 9, 18)
        assert fechas("el viernes", JUEVES)[0].valor == date(2026, 9, 18)

    def test_el_mismo_dia_de_la_semana_es_ambiguo(self):
        h = fechas("el jueves que viene", JUEVES)[0]
        assert h.valor == date(2026, 9, 24)
        assert h.confianza <= 0.5 and h.duda

    def test_dia_que_no_coincide_con_el_numero(self):
        h = fechas("el jueves 25", MARTES)[0]
        assert h.valor == date(2026, 9, 25)
        assert "viernes" in h.duda

    def test_dia_del_mes_explicito_y_el_que_ya_paso(self):
        assert fechas("el 3/10", MARTES)[0].valor == date(2026, 10, 3)
        assert fechas("el 5 de octubre", MARTES)[0].valor == date(2026, 10, 5)
        assert fechas("puede ser el 10?", MARTES)[0].valor == date(2026, 10, 10)

    def test_horas(self):
        assert horarios("9 de la manana")[0].valor == time(9, 0)
        assert horarios("a las 3 de la tarde")[0].valor == time(15, 0)
        assert horarios("tipo 14:30hs")[0].valor == time(14, 30)
        dudosa = horarios("a las 3")[0]
        assert dudosa.valor == time(3, 0) and dudosa.duda


class TestReglas:
    def test_lugares_en_minuscula_y_sin_tildes(self):
        origen, destino = lugares("de flores a moron")
        assert (origen.valor, destino.valor) == ("Flores", "Morón")

    def test_lugar_desconocido_con_mayuscula_va_con_duda(self):
        origen, destino = lugares("de Gerli a Sarandi")
        assert (origen.valor, destino.valor) == ("Gerli", "Sarandi")
        assert origen.duda and origen.confianza < 0.7

    def test_a_que_hora_no_es_un_destino(self):
        assert lugares("a que hora pasarias?") == []

    def test_items_con_cantidades(self):
        [h] = items("llevo 2 sillones, la tele y como quince cajas")
        assert [str(i) for i in h.valor] == ["2 sillones", "televisor", "15 cajas"]

    def test_sin_ascensor_sin_piso_no_inventa_el_piso(self):
        [h] = pisos("no hay ascensor")
        assert h.valor is None and "piso" in h.duda

    def test_pisos(self):
        assert pisos("es un 4to piso sin ascensor")[0].valor == 4
        assert pisos("vivo en un tercero sin ascensor")[0].valor == 3
        assert pisos("hay ascensor")[0].valor == 0
        assert pisos("vivo en un cuarto piso") == []  # no dijo nada del ascensor

    def test_ayudantes(self):
        assert ayudantes("con 2 ayudantes")[0].valor == 2
        assert ayudantes("sin ayudante")[0].valor == 0
        assert ayudantes("somos 3")[0].duda

    def test_precio(self):
        assert precio("te sale 45 mil")[0].valor == 45000
        assert precio("serian $52.500")[0].valor == 52500
        assert precio("salen 2 viajes") == []


class TestIntencion:
    @pytest.mark.parametrize(
        ("texto", "esperada"),
        [
            ("hola buenas, haces fletes?", Intencion.CONSULTA),
            ("dale, cerramos", Intencion.ACEPTA),
            ("si, ese", Intencion.ACEPTA),
            ("dale, mañana te confirmo", Intencion.POSPONE),
            ("pera, cambio de planes", Intencion.CORRIGE),
            ("ya consegui, gracias", Intencion.CANCELA),
            ("el sabado a la manana si se puede", None),
        ],
    )
    def test_clasifica(self, texto, esperada):
        hallada = intencion(texto)
        assert (hallada[0] if hallada else None) is esperada


class TestPropuestasDelFletero:
    def _conv(self, respuesta: str) -> Conversation:
        return Conversation(
            "c",
            [
                Message("c:0", "c", True, MARTES, MessageKind.TEXT, text="de Flores a Moron"),
                Message("c:1", "c", False, MARTES.replace(minute=5), MessageKind.TEXT,
                        text="Te viene bien el viernes a las 10?"),
                Message("c:2", "c", True, MARTES.replace(minute=6), MessageKind.TEXT, text=respuesta),
            ],
        )

    def test_si_el_cliente_acepta_cuenta(self):
        ex = SlotExtractor().extract(self._conv("dale, perfecto"))
        assert ex.job.when.value == date(2026, 9, 18)
        assert ex.job.hour.value == time(10, 0)
        assert ex.job.when.evidence.message_id == "c:1"

    def test_si_el_cliente_no_acepta_no_cuenta(self):
        ex = SlotExtractor().extract(self._conv("uh no, tengo que ver"))
        assert ex.job.when is None and ex.job.hour is None


class TestCli:
    def test_extract_muestra_datos_faltantes_y_audios(self, tmp_path, capsys):
        db = str(tmp_path / "x.db")
        main(["--db", db, "ingest"])
        capsys.readouterr()
        assert main(["--db", db, "extract"]) == 0
        salida = capsys.readouterr().out
        assert "destino           Barracas" in salida
        assert "FALTA: fecha" in salida
        assert "AUDIOS SIN TRANSCRIBIR: 5491177778888:002" in salida
        assert 'pospone   5491155556666:005  "manana te confirmo"' in salida

    def test_extract_de_un_chat_inexistente(self, tmp_path, capsys):
        db = str(tmp_path / "x.db")
        main(["--db", db, "ingest"])
        assert main(["--db", db, "extract", "no-existe"]) == 1


class TestDireccionesDeVerdad:
    """Como escribe la gente fuera de Buenos Aires.

    La lista de barrios es de CABA y el conurbano; este fletero trabaja en
    Mar del Plata, donde se dice la esquina o la direccion. En 58
    conversaciones reales no se entendio un solo origen hasta que se
    aceptaron direcciones sueltas, en minuscula.
    """

    def _lugares(self, texto):
        from estiba.extraction.reglas import lugares
        h = lugares(texto)
        return (h[0].valor, h[1].valor) if h else None

    def test_una_esquina_en_minuscula(self):
        assert self._lugares("De Alberti y santa fe hasta rufino inda y vertiz") == (
            "Alberti y santa fe", "rufino inda y vertiz")

    def test_una_direccion_con_numero(self):
        assert self._lugares("un flete desde b.goñi 836 hasta Colón 10955") == (
            "b.goñi 836", "Colón 10955")

    def test_un_horario_no_es_un_viaje(self):
        """"de lunes a viernes" tiene la misma forma que "de Alberti a Colon"."""
        assert self._lugares("Nuestro horario es de Lunes a viernes") is None
        assert self._lugares("Estamos en Talcahuano 449 de 9 a 17h") is None

    def test_de_nuevo_no_es_un_lugar(self):
        assert self._lugares("voy de nuevo a casa de mi mamá") is None

    def test_no_cruza_de_una_oracion_a_la_otra(self):
        assert self._lugares("Hola. De Alberti a Colon. Gracias") == ("Alberti", "Colon")

    def test_la_direccion_queda_marcada_como_dudosa(self):
        """Escrita a mano: hay que mirarla antes de cotizar sobre eso."""
        from estiba.extraction.reglas import lugares
        h = lugares("desde chile y alvarado hasta mario bravo y de la maza")
        assert h[0].confianza < 0.7 and h[0].duda


class TestComoSeDiceUnPrecio:
    """Salen de mensajes reales del fletero.

    La regla anterior solo agarraba "sale 45 mil": de 58 conversaciones
    reconocia un precio en una sola. El fletero escribe "esta 35 mil el
    flete", "son 40 mil", "desde 35mil".
    """

    def _precio(self, texto):
        from estiba.extraction.reglas import precio
        h = precio(texto)
        return h[0].valor if h else None

    def test_como_lo_escribe_de_verdad(self):
        assert self._precio("Genial, sin ayudante esta 35 mil el flete") == 35000
        assert self._precio("Perfecto son 40 mil el flete") == 40000
        assert self._precio("Desde 35mil") == 35000
        assert self._precio("el total es 170.000") == 170000

    def test_un_numero_pelado_no_es_un_precio(self):
        """Al aceptar "es" y "son" se colaban años: $2.017, $2.026.

        Un precio se escribe de una de tres formas: "$45.000", "45.000" o
        "45 mil". "es 2026" no es ninguna.
        """
        assert self._precio("es 2026") is None
        assert self._precio("fue en 2017") is None
        assert self._precio("son 4708 pesos") is None

    def test_lo_que_sale_el_ayudante_no_es_el_flete(self):
        assert self._precio("15 mil sale el ayudante") is None
        assert self._precio("el ayudante son 15 mil") is None
        # Pero si el mensaje habla del flete, ese es el precio.
        assert self._precio("sin ayudante el flete esta 35 mil") == 35000

    def test_no_confunde_cantidades_con_plata(self):
        assert self._precio("son 2 cajas") is None
        assert self._precio("es un segundo piso") is None
        assert self._precio("quedan 3 sillas") is None
