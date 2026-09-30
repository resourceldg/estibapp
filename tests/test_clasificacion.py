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


def _clase_con_nombre(*lineas: tuple[str, str], nombre: str):
    conv = _chat(*lineas)
    return clasificar(conv, SlotExtractor().extract(conv), nombre)


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


class TestDesdeLaPubli:
    """El mensaje que Meta pre-carga al tocar un anuncio."""

    def _clase_de(self, texto, nombre=None):
        conv = _chat(("c", texto))
        return clasificar(conv, SlotExtractor().extract(conv), nombre)

    def test_quiero_mas_informacion_es_un_lead(self):
        """15 conversaciones reales arrancan asi y quedaban sin clasificar."""
        assert self._clase_de("¡Hola! Quiero más información").clase is Clase.LEAD

    def test_tambien_si_dice_que_vio_la_publicacion(self):
        r = self._clase_de("Buenas tardes, vi la publicación y quería consultar")
        assert r.clase is Clase.LEAD

    def test_no_pide_decidir_nada(self):
        """Dijo poquito, pero no hay nada que dudar."""
        assert not self._clase_de("¡Hola! Quiero más información").pide_decision


class TestElOtroTrabajo:
    """Lucas tambien da clases, y esos mensajes entraban como leads.

    Todos los textos de aca son mensajes reales de su WhatsApp. Seis
    conversaciones de la escuela estaban en la bandeja al lado de las
    mudanzas: una pregunta parecia una consulta de flete, sin mirar de que
    se preguntaba.
    """

    def test_la_escuela_no_es_un_flete(self):
        r = _clase(("c", "Hola! Te consulto hoy podrías juntar a 1er con 2do? "
                         "Porque faltan profesores"),
                   ("v", "Dale, lo vemos"))
        assert r.clase is Clase.PRIVADO

    def test_un_alumno_tampoco(self):
        r = _clase(("c", "Profe, tengo para pasarte el texto que generé para el viernes"),
                   ("v", "Joya, mandalo"))
        assert r.clase is Clase.PRIVADO

    def test_pero_un_profesor_que_se_muda_sigue_siendo_cliente(self):
        """La escuela le gana a lo inferido, no a un pedido de frente."""
        r = _clase(("c", "Hola, soy profe del cens 462 y necesito un flete "
                         "para mudarme el sábado"))
        assert r.clase is Clase.LEAD

    def test_como_lo_tenes_guardado_alcanza(self):
        """El que anoto "JULIA hermana" ya clasifico la relacion."""
        r = _clase_con_nombre(("c", "hola, todo bien?"), nombre="JULIA hermana")
        assert r.clase is Clase.PRIVADO

    def test_el_que_te_agrega_a_un_grupo_no_te_compra_un_flete(self):
        r = _clase(("c", "te agrego al grupo?"), ("v", "dale"))
        assert r.clase is Clase.PRIVADO


class TestNiClienteNiSpam:
    """Los que estan del lado de los costos, no de las ventas."""

    def test_el_mecanico_no_es_un_cliente(self):
        """"camioneta" es del oficio, y el taller entraba como lead.

        Lo que distingue no es la palabra sino quien la dice: es el fletero
        el que cuenta que se le rompio algo.
        """
        r = _clase(("v", "hola, tengo una c10 que se le rompió la caja de dirección, "
                         "¿la puedo llevar mañana?"),
                   ("c", "Hola, cómo va? ¿Qué es lo que le pasa?"))
        assert r.clase is Clase.PRIVADO

    def test_el_nombre_del_contacto_ya_lo_dice(self):
        r = _clase_con_nombre(("c", "seguro el martes"), nombre="Tito Mecanico")
        assert r.clase is Clase.PRIVADO

    def test_arreglar_solo_no_alcanza(self):
        """Un fletero dice "se rompio" de cualquier cosa.

        Con eso, una venta personal quedaba explicada como "le llevas el
        vehiculo": una razon falsa, que es peor que ninguna.
        """
        r = _clase(("v", "te la puedo arreglar si querés"),
                   ("c", "Te la puedo comprar en cuotas"))
        assert r.clase is not Clase.PRIVADO


class TestContestadores:
    """Bots de empresa y la bienvenida de la competencia."""

    def test_el_mensaje_de_otro_fletero_no_es_un_lead(self):
        """Mensaje real: nombra el oficio, asi que entraba como cliente."""
        r = _clase(("c", "Bienvenido/a a Fletes y Auxilios La Costa! "
                         "Brindamos soluciones rápidas"))
        assert r.clase is Clase.SPAM

    def test_un_bot_de_cobranzas_tampoco(self):
        r = _clase(("c", "¡Hola! Gracias por comunicarse con el Departamento de "
                         "cobranzas. Nuestro horario de atención es de lunes a viernes"),
                   ("c", "¿Podés brindarme tu número de DNI?"))
        assert r.clase is Clase.SPAM


class TestUnaPreguntaNoEsUnaConsulta:
    def test_un_hilo_largo_sin_una_palabra_del_oficio_es_dudosa(self):
        """Un amigo que pregunta "¿todo bien?" no es un lead.

        No pasa a privado ni a spam: se dice que no se sabe, que es lo unico
        que pide una decision. Descartarlo en silencio seria peor.
        """
        r = _clase(("c", "Lukiiiii como andassss brooo"), ("v", "todo bien"),
                   ("c", "Mañana 9am seriaaa ?"), ("v", "dale"),
                   ("c", "Siiiii dale manooo te esperooo"), ("v", "ahí voy"),
                   ("c", "mi vecino el rasta dice que se suma"),
                   ("c", "¿tenés el número de Lorena?"))
        assert r.clase is Clase.DUDOSA
        assert r.pide_decision

    def test_pero_en_un_primer_contacto_la_pregunta_se_supone_del_oficio(self):
        r = _clase(("c", "hola, cuánto me saldría?"))
        assert r.clase is Clase.LEAD


# --------------------------------------------------------------------------
# Lo que ya preguntaste vos tambien es contexto
# --------------------------------------------------------------------------


class TestNoRepreguntar:
    """Real: el fletero contesto a mano a las 20:41 con su plantilla de cinco
    preguntas, WhatsApp tardo un dia en sincronizar ese mensaje, y Estiba
    mando "¿desde que barrio seria?" a las 00:43. El cliente vio lo mismo
    dos veces."""

    PLANTILLA = (
        "🚚 ¡Hola! Gracias por consultar por el flete. Para poder pasarte un precio "
        "completame estos datos:\n1 Retiro: ¿desde dónde hay que buscarlo?\n"
        "2 Entrega: ¿hasta dónde lo llevamos?\n3 Carga: ¿qué cosas hay que transportar?\n"
        "4 Ayuda: ¿necesitás ayuda para cargar o descargar?\n"
        "5 Acceso: ¿hay escalera, ascensor o alguna dificultad?"
    )

    def _decision(self, *lineas):
        from estiba.bandeja.accion import decidir
        from estiba.jobs.engine import JobEngine

        conv = _chat(*lineas)
        ahora = datetime(2026, 9, 20, 12)
        return decidir(JobEngine().evaluar(conv, ahora), conv, ahora)

    def test_reconoce_lo_que_pediste_a_mano(self):
        from estiba.bandeja.ya_preguntado import campos_ya_pedidos

        conv = _chat(("c", "quiero más información"), ("v", self.PLANTILLA))
        pedidos = campos_ya_pedidos(conv)
        assert {"origin", "destination", "items", "floors_no_lift"} <= pedidos

    def test_no_repite_lo_que_ya_pediste(self):
        """Tu plantilla no pregunta la fecha: eso si, lo demas no."""
        d = self._decision(("c", "quiero más información"), ("v", self.PLANTILLA),
                           ("c", "dale, gracias"))
        junto = " ".join(d.preguntas).lower()
        assert "día" in junto or "cuando" in junto      # la fecha no la pediste
        assert "barrio" not in junto and "hasta dónde" not in junto

    def test_lo_ya_pedido_no_vuelve_ni_aunque_quede_otra_cosa(self):
        """Tu plantilla no pregunta la hora: queda esa y solo esa."""
        d = self._decision(("c", "quiero un flete el viernes"), ("v", self.PLANTILLA),
                           ("c", "dale, gracias"))
        junto = " ".join(d.preguntas).lower()
        assert "hora" in junto
        for repetida in ("barrio", "hasta dónde", "qué cosas", "ascensor"):
            assert repetida not in junto

    def test_si_ya_pediste_todo_lo_que_falta_no_insiste(self):
        completa = self.PLANTILLA + " ¿Para qué día lo necesitás? ¿A qué hora te queda bien?"
        d = self._decision(("c", "quiero más información"), ("v", completa),
                           ("c", "dale, gracias"))
        assert d.accion.value == "respuesta"
        assert "ya le pediste" in d.razon.lower()

    def test_tampoco_si_vuelve_a_preguntar_el(self):
        d = self._decision(("c", "quiero más información"), ("v", self.PLANTILLA),
                           ("c", "hola? estás?"))
        assert d.accion.value != "preguntas"

    def test_si_todavia_no_pediste_nada_si_pregunta(self):
        d = self._decision(("c", "quiero más información"))
        assert d.accion.value == "preguntas"

    def test_lo_que_falta_y_no_pediste_si_se_pregunta(self):
        """La plantilla no pregunta la fecha: eso si se puede pedir."""
        d = self._decision(("c", "llevo una heladera de Almagro a Boedo, hay ascensor"),
                           ("v", "¿desde dónde hay que buscarlo? ¿hasta dónde lo llevamos?"),
                           ("c", "eso mismo"))
        assert d.accion.value in ("preguntas", "cotizacion")
