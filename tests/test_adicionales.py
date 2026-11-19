"""Trabajo adicional pedido en medio de otro (19-nov-2026). Caso real anonimizado: flete confirmado dentro de la
ciudad y, en la misma charla, un tramo aparte desde otra localidad, cotizado por separado y sin confirmar."""
from __future__ import annotations

from datetime import datetime, timedelta

from estiba.core.models import Conversation, Message, MessageKind
from estiba.extraction.intencion import Intencion, intencion
from estiba.extraction.reglas import lugares
from estiba.horizon import _con_zona
from estiba.jobs.adicionales import ID_ADICIONAL, con_adicional, separar
from estiba.jobs.engine import CONFIRMADO, Accion, JobEngine, Politica
from estiba.jobs.episodios import Episodio
from estiba.core.models import JobState

T0 = datetime(2026, 11, 18, 20, 16)
CHARLA = [
    ("c", "Hola, buenas noches! Mi nombre es Pedro Ruiz."),
    ("c", "Te escribía para consultarte si es posible combinar para un flete domingo a las 11hs. Necesitamos mover "
          "una heladera con freezer, una cama (colchón + sommier) y un escritorio de pc."),
    ("c", "En mar del plata!"),
    ("v", "Si el flete ese te cue ta 50. Si necesitas ayudante sumarle 15 mas"),
    ("c", "Te confirmo para el flete el domingo!!"),
    ("c", "Y te consulto por si las dudas, que también tendríamos que hacerlo, para traer desde Miramar hasta MdP dos "
          "mesas y dos sillas, cuánto sería? Acá en MdP serían dos paradas."),
    ("v", "¡Buenísimo! Quedamos para el domingo a las 11. Si vos me ayudás con la carga, mantenemos los $50.000. "
          "Pasame las direcciones del flete.\n\nPor lo de Miramar, mandame la dirección de retiro y las medidas de "
          "las mesas. Con esos datos te paso el precio."),
    ("c", "Te voy diciendo por partes!\nYo te doy una mano el domingo entonces.\n\nAcá en Mardel tendríamos que llevar "
          "heladera, cama y escritorio desde Rioja 2100 (e/ Mitre y Salta) hasta Falucho 1500 (/e Olazábal y "
          "Córdoba).\n\nEn Miramar habría que retirar las mesas de calle 21 n900."),
    ("v", "Para cotizar el tramo de Miramar, pasame medidas de las mesas. Con eso te paso un precio aparte del flete "
          "original"),
    ("c", "Dale, dejame consulto bien y te confirmo las medidas!"),
    ("v", "Te confirmo el flete en Mardel por $50.000 como hablamos. Por sumar Miramar (retirar dos mesas y llevar "
          "una a cada casa), son $70.000 más. Total por todo $120.000. Te va así?"),
    ("c", "Dale, tema flete acá en MdP confirmado!\nTema mesas Miramar tengo que confirmarte aún! Pero el precio "
          "está perfecto, si."),
    ("v", "perfecto entonces estamos al habla, el domingo por lo pronto 11 en rioja 2100"),
]
AHORA = T0 + timedelta(hours=3)


def _conv(lineas=CHARLA):
    return Conversation("549000", [
        Message(f"m{i}", "549000", q == "c", T0 + timedelta(minutes=5 * i), MessageKind.TEXT, text=t)
        for i, (q, t) in enumerate(lineas)])


def _textos(conv):
    return " | ".join(conv.text_of(m) for m in conv.messages)


class TestSeparacion:
    def test_el_adicional_va_aparte_con_su_carga_y_su_precio(self):
        s = separar(_conv())
        assert s.adicional is not None
        eng = JobEngine()
        a, b = eng.evaluar(s.principal, AHORA).job, eng.evaluar(s.adicional, AHORA).job
        assert (a.origin.value, a.destination.value) == ("Rioja 2100", "Falucho 1500")
        assert "mesa" not in " ".join(a.items.value) and "heladera" in a.items.value
        assert a.quoted_price.value == 50000 and a.when.value.weekday() == 6 and a.hour.value.hour == 11
        assert b.origin.value == "Miramar" and b.items.value == ["2 mesas", "2 sillas"]
        assert b.quoted_price.value == 70000

    def test_el_total_de_los_dos_no_es_de_ninguno(self):
        s = separar(_conv())
        assert "120.000" not in _textos(s.principal) and "120.000" not in _textos(s.adicional)

    def test_el_adicional_queda_pendiente_y_el_original_aceptado(self):
        s, eng = separar(_conv()), JobEngine()
        b = eng.evaluar(s.adicional, AHORA)
        assert b.job.state is JobState.WAITING_CUSTOMER  # «tengo que confirmarte aún»
        # Decisión de Lucas (19-nov): aceptó y hay fecha → confirmado solo, aunque las direcciones sean a mano.
        # (La charla es del 18-nov, anterior a la regla: acá se prueba con la regla ya vigente.)
        a = JobEngine(politica=Politica(confirma_solo_desde=datetime(2026, 1, 1))).evaluar(s.principal, AHORA)
        assert a.job.state is JobState.CONFIRMED and a.job.quoted_price.value == 50000

    def test_ids_estables_y_releer_da_lo_mismo(self):
        ep = Episodio("549000:job", _conv())
        uno, dos = con_adicional(ep), con_adicional(ep)
        assert [e.job_id for e in uno] == ["549000:job", "549000:job" + ID_ADICIONAL] == [e.job_id for e in dos]
        assert [_textos(e.conversacion) for e in uno] == [_textos(e.conversacion) for e in dos]

    def test_sin_pedido_adicional_no_se_separa(self):
        sin = [x for x in CHARLA if "Miramar" not in x[1]]
        assert len(con_adicional(Episodio("549000:job", _conv(sin)))) == 1

    def test_tambien_sin_otro_origen_no_separa(self):
        charla = CHARLA[:5] + [("c", "También tendríamos que llevar un sillón, ¿cuánto más sería?")]
        assert separar(_conv(charla)).adicional is None

    def test_tambien_antes_de_que_el_fletero_responda_no_separa(self):
        charla = [CHARLA[1], ("c", "también tendríamos que traer desde Miramar hasta MdP dos mesas")]
        assert separar(_conv(charla)).adicional is None


class TestLecturas:
    def test_te_confirmo_para_el_flete_el_domingo_es_aceptar(self):
        assert intencion("Te confirmo para el flete el domingo!!")[0] is Intencion.ACEPTA
        assert intencion("mañana te confirmo")[0] is Intencion.POSPONE

    def test_tengo_que_confirmarte_es_posponer(self):
        assert intencion("Tema mesas tengo que confirmarte aún! Pero el precio está perfecto, si.")[0] is Intencion.POSPONE

    def test_entrecalles_entre_parentesis_no_cortan_el_recorrido(self):
        h = lugares("llevar heladera desde Rioja 2100 (e/ Mitre y Salta) hasta Falucho 1500 (/e Olazábal y Córdoba).")
        assert [(x.campo, x.valor) for x in h] == [("origin", "Rioja 2100"), ("destination", "Falucho 1500")]

    def test_si_el_primer_de_a_no_es_un_lugar_se_sigue_buscando(self):
        h = lugares("Tema foto de la escalera estamos buscando a ver si tenemos. Hay que llevar todo desde Rioja 2100 "
                    "hasta Falucho 1500.")
        assert [x.valor for x in h] == ["Rioja 2100", "Falucho 1500"]

    def test_la_hora_acordada_viaja_a_horizon(self):
        assert _con_zona("2026-11-22", "11 h").startswith("2026-11-22T11:00")
        assert _con_zona("2026-11-22", None).startswith("2026-11-22T12:00")


class TestLoQueDiceElFletero:
    """19-nov-2026: el recorrido y la hora que propone el fletero, cuando el cliente no los dio o los acepta."""

    def _job(self, *lineas):
        return JobEngine().evaluar(_conv(list(lineas)), AHORA).job

    def test_el_recorrido_repetido_al_cotizar_cuenta_si_el_cliente_no_dio_uno(self):
        j = self._job(("c", "Hola, necesito llevar una heladera, un lavarropas y una cama"),
                      ("v", "Desde Talcahuano y Mario Bravo hasta Camet Norte, por la heladera, el lavarropas y la cama: $50.000"))
        assert (j.origin.value, j.destination.value) == ("Talcahuano y Mario Bravo", "Camet Norte")

    def test_si_el_cliente_dio_el_recorrido_manda_el_suyo(self):
        j = self._job(("c", "Necesito llevar un sillón de Rioja 2100 a Falucho 1500"),
                      ("v", "Desde Rioja hasta Falucho te sale $30.000"))
        assert (j.origin.value, j.destination.value) == ("Rioja 2100", "Falucho 1500")

    def test_la_hora_propuesta_sin_signo_cuenta_si_el_cliente_acepta(self):
        j = self._job(("c", "Hola, quiero llevar una heladera el sábado"), ("v", "te sale $40.000"),
                      ("v", "sabado a la mañana me confirmas 11 hs aprox"), ("c", "Si perfecto"))
        assert j.hour.value.hour == 11

    def test_una_hora_que_el_fletero_cuenta_sin_proponer_no_cuenta(self):
        j = self._job(("c", "Hola, quiero llevar una heladera"), ("v", "te sale $40.000"),
                      ("v", "a las 10 tengo otro flete, después te escribo"), ("c", "dale"))
        assert j.hour is None
