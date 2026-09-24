"""Fase 10: que es cada conversacion, sin preguntar lo que se puede inferir.

Los casos de publicidad son mensajes reales que le llegaron al fletero: la
promo de un plan de celular y una cadena de regalos. Los dos entraban al
embudo como consultas de flete.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from estiba.conversation.clasificacion import Clase, clasificar
from estiba.core.models import Conversation, Message, MessageKind
from estiba.extraction.extractor import SlotExtractor


def _chat(*lineas: tuple[str, str]) -> Conversation:
    inicio = datetime(2026, 9, 20, 10)
    return Conversation("x", [
        Message(f"x:{i:03d}", "x", quien == "c", inicio + timedelta(minutes=i),
                MessageKind.TEXT, text=t)
        for i, (quien, t) in enumerate(lineas)
    ])


def _clase(*lineas: tuple[str, str]):
    conv = _chat(*lineas)
    return clasificar(conv, SlotExtractor().extract(conv))


class TestEsNegocio:
    def test_pedido_claro_no_pregunta_nada(self):
        r = _clase(("c", "hola, necesito llevar una heladera de Almagro a Boedo"))
        assert r.clase is Clase.LEAD and not r.pide_decision

    def test_alcanza_con_nombrar_el_oficio(self):
        r = _clase(("c", "hacés mudanzas los sábados?"))
        assert r.clase is Clase.LEAD

    def test_un_gracias_mi_amor_no_saca_a_un_cliente_del_embudo(self):
        """El afecto no descalifica: hay clientes que tutean y agradecen así."""
        r = _clase(("c", "necesito un flete para el viernes"), ("v", "dale"),
                   ("c", "gracias mi amor!"))
        assert r.clase is Clase.LEAD


class TestPublicidad:
    def test_la_promo_de_un_plan_no_es_una_consulta(self):
        """Real: el extractor leia "el precio del plan" como consulta de precio."""
        r = _clase(("c", "🔥La promo mas HOT🔥 Plan 3GB + 10GB de regalo + 1 MES GRATIS. "
                         "Aprovechá el HOT SALE. El precio del plan incluye 70% de descuento"))
        assert r.clase is Clase.SPAM

    def test_una_fecha_de_promo_no_convierte_en_flete(self):
        """Real: "válido solo por hoy" dejaba una fecha y entraba como lead."""
        r = _clase(("c", "REGALITO de primavera para vos, VÁLIDO SOLO POR HOY, "
                         "60GB de regalo si activás tu plan con descuento"))
        assert r.clase is Clase.SPAM

    def test_un_link_solo_no_es_publicidad(self):
        """Real: un link de MercadoLibre marcaba spam una charla de 12 idas y vueltas.

        Es lo que manda un cliente para mostrar lo que hay que llevar.
        """
        r = _clase(("c", "https://www.mercadolibre.com.ar/up/MLAU316?matt_tool=894&ua=3t-clic"),
                   ("v", "lo veo"), ("c", "eso"), ("v", "ok"), ("c", "gracias"))
        assert r.clase is not Clase.SPAM


class TestPersonal:
    def test_charla_de_familia_queda_afuera(self):
        r = _clase(("c", "hola ma, cómo andás? mañana paso por lo de la abuela"),
                   ("v", "dale hijo"), ("c", "un beso, te quiero"))
        assert r.clase is Clase.PRIVADO


class TestCuandoPregunta:
    def test_un_hola_suelto_no_es_una_duda(self):
        """Todavia no hablaron: no hay decision que tomar."""
        r = _clase(("c", "Hola"))
        assert r.clase is Clase.DUDOSA and not r.pide_decision

    def test_escribio_bastante_y_no_se_entiende_si_pregunta(self):
        r = _clase(("c", "buenas, te escribo por lo que hablamos el otro día con Marcelo, "
                         "quería ver si seguís con eso o ya lo dejaste"))
        assert r.clase is Clase.DUDOSA and r.pide_decision

    def test_siempre_dice_por_que(self):
        """La pantalla tiene que poder explicar la decision."""
        for lineas in (("c", "necesito un flete"),), (("c", "Hola"),):
            assert _clase(*lineas).porque


# --------------------------------------------------------------------------
# Identidad: lo que se decide una vez no se olvida
# --------------------------------------------------------------------------


class TestIdentidad:
    def _db(self, tmp_path, *mensajes):
        from estiba.conversation.store import ConversationStore
        from estiba.storage.db import connect

        conn = connect(tmp_path / "c.db")
        ConversationStore(conn).ingest(list(mensajes))
        return conn

    def test_lo_que_decidis_vos_le_gana_a_estiba_para_siempre(self, tmp_path):
        from estiba.bandeja.servicio import sincronizar
        from estiba.jobs.engine import JobEngine
        from estiba.storage.repositories import ClaseRepository

        ahora = datetime(2026, 9, 20, 12)
        conn = self._db(tmp_path, Message("p:1", "p", True, datetime(2026, 9, 20, 10),
                                          MessageKind.TEXT, text="hola ma, un beso"),
                        Message("p:2", "p", False, datetime(2026, 9, 20, 11),
                                MessageKind.TEXT, text="chau"))
        sincronizar(conn, JobEngine(), ahora)
        repo = ClaseRepository(conn)
        assert repo.de("p")["clase"] == "privado"

        # El fletero dice que no, que es un cliente.
        repo.corregir("p", "lead", ahora)
        sincronizar(conn, JobEngine(), ahora)
        fila = repo.de("p")
        assert fila["clase"] == "lead" and fila["quien"] == "humano"
        assert fila["clase_previa"] == "privado"   # queda para mejorar las reglas

    def test_las_correcciones_quedan_juntas(self, tmp_path):
        from estiba.storage.repositories import ClaseRepository

        ahora = datetime(2026, 9, 20, 12)
        conn = self._db(tmp_path, Message("q:1", "q", True, datetime(2026, 9, 20, 10),
                                          MessageKind.TEXT, text="hola"))
        repo = ClaseRepository(conn)
        repo.guardar_inferida("q", "spam", "parece publicidad", ahora)
        repo.corregir("q", "lead", ahora)
        correcciones = repo.correcciones()
        assert len(correcciones) == 1
        assert (correcciones[0]["clase_previa"], correcciones[0]["clase"]) == ("spam", "lead")

    def test_lo_personal_no_entra_al_embudo(self, tmp_path):
        from estiba.bandeja.servicio import sincronizar
        from estiba.jobs.engine import JobEngine

        conn = self._db(tmp_path, Message("z:1", "z", True, datetime(2026, 9, 20, 10),
                                          MessageKind.TEXT, text="feliz cumple abuela, besos"),
                        Message("z:2", "z", False, datetime(2026, 9, 20, 11),
                                MessageKind.TEXT, text="gracias"))
        r = sincronizar(conn, JobEngine(), datetime(2026, 9, 20, 12))
        assert r.fuera == {"z": "privado"}
        assert not r.fletes and not r.leads

    def test_el_cli_muestra_como_viene_clasificando(self, tmp_path, capsys):
        from estiba.cli import main
        from estiba.storage.repositories import ClaseRepository

        conn = self._db(tmp_path, Message("w:1", "w", True, datetime(2026, 9, 20, 10),
                                          MessageKind.TEXT, text="hola"))
        repo = ClaseRepository(conn)
        repo.guardar_inferida("w", "spam", "parece publicidad", datetime(2026, 9, 20, 12))
        repo.corregir("w", "lead", datetime(2026, 9, 20, 13))
        conn.close()
        assert main(["--db", str(tmp_path / "c.db"), "clases"]) == 0
        salida = capsys.readouterr().out
        assert "creyó spam" in salida.replace("creyo", "creyó") or "spam" in salida


# --------------------------------------------------------------------------
# Lo que alguien reenvio no es lo que este cliente esta pidiendo
# --------------------------------------------------------------------------


class TestConversacionPegada:
    def test_la_hora_de_un_chat_reenviado_no_es_la_fecha_del_flete(self):
        """Real: "[15/8, 12:24 p. m.] María Agustina:" quedaba como 15/08/2027."""
        from estiba.extraction.extractor import SlotExtractor

        conv = _chat(("c", "[15/8, 12:24 p. m.] María Agustina ☀️: Contar con "
                           "intérpretes online para cubrir la demanda"))
        ex = SlotExtractor().extract(conv)
        assert "when" not in ex.ganadores

    def test_lo_que_escribio_antes_de_pegar_si_cuenta(self):
        from estiba.extraction.extractor import SlotExtractor

        conv = _chat(("c", "necesito un flete de Almagro a Boedo\n"
                           "[15/8, 12:24 p. m.] Ana: mirá esto que me pasaron"))
        ex = SlotExtractor().extract(conv)
        assert ex.ganadores["origin"].slot.value == "Almagro"
        assert "when" not in ex.ganadores

    def test_se_corta_hasta_el_final_aunque_se_pierda_algo(self):
        """Decision tomada a proposito, con su costo.

        Una conversacion pegada sigue muchas lineas sin volver a poner el
        corchete: de ahi salian "de 10 a 12:30 hs" como hora del flete. Si
        el cliente escribe algo despues de pegar, se pierde; Estiba se lo
        pregunta de nuevo. Inventar una fecha es peor que preguntar dos veces.
        """
        from estiba.extraction.extractor import SlotExtractor

        conv = _chat(("c", "[15/8, 12:24 p. m.] Ana: mirá\n"
                           "Agustina cubre de 10 a 12:30 hs de lunes a viernes\n"
                           "necesito un flete de Almagro a Boedo"))
        ex = SlotExtractor().extract(conv)
        assert not ex.ganadores


class TestFechaConAnio:
    def test_el_anio_dicho_manda(self):
        """Real: "21/08/26" quedaba en 2027 por correrse al futuro."""
        from datetime import datetime as dt

        from estiba.extraction.fechas import fechas

        h = fechas("el 21/08/26 lo necesito", dt(2026, 9, 24, 10))
        assert h[0].valor == date(2026, 8, 21)

    def test_sin_anio_se_sigue_buscando_la_proxima(self):
        from datetime import datetime as dt

        from estiba.extraction.fechas import fechas

        h = fechas("el 30/9 a las 10", dt(2026, 9, 24, 10))
        assert h[0].valor == date(2026, 9, 30)


class TestSenalesNuevas:
    """El nombre del contacto y como le habla el fletero."""

    def _con_nombre(self, nombre, *lineas):
        conv = _chat(*lineas)
        return clasificar(conv, SlotExtractor().extract(conv), nombre)

    def test_un_usuario_no_es_una_persona(self):
        r = self._con_nombre("ideaspublicom518", ("c", "hacemos fletes y mudanzas, consultanos"))
        assert r.clase is Clase.SPAM

    def test_pero_un_nombre_con_numero_si_lo_es(self):
        """Real: "Natalia Parece 462" quedaba como spam, y el spam se borra.

        El espacio es lo que separa un usuario de una persona.
        """
        r = self._con_nombre("Natalia Parece 462", ("c", "hola, necesito un flete"))
        assert r.clase is Clase.LEAD

    def test_si_dijo_de_donde_a_donde_es_un_pedido_igual(self):
        """Aunque se llame "Tienda X": los datos concretos mandan."""
        r = self._con_nombre("Tienda Sol", ("c", "llevar una heladera de Almagro a Boedo"))
        assert r.clase is Clase.LEAD

    def test_el_que_ofrece_su_servicio_no_esta_pidiendo_uno(self):
        r = self._con_nombre("GoNzA", ("c", "soy entrenador personal, te cuento cómo "
                                            "podés participar de mi canal de WhatsApp"))
        assert r.clase is Clase.SPAM

    def test_como_le_hablas_vos_tambien_cuenta(self):
        """A un cliente no se le escribe "te quiero"."""
        r = self._con_nombre("Sabi", ("c", "llegaste bien?"), ("v", "si, todo bien, te quiero"))
        assert r.clase is Clase.PRIVADO

    def test_un_abrazo_no_alcanza_para_sacar_a_un_cliente(self):
        """Entre hombres se cierra asi un mensaje de trabajo.

        Marcar a un cliente como privado lo saca del embudo sin avisar, que
        es el error mas caro: mejor quedarse corto.
        """
        r = self._con_nombre("Marcelo", ("c", "cuanto me sale?"), ("v", "te paso, un abrazo"))
        assert r.clase is not Clase.PRIVADO
