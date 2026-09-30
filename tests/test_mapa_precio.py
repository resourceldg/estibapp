"""El mapa propio, el precio de cada flete y el pronostico del mes.

El mapa se arma con lineas con la forma exacta de los archivos de Georef
(verificada contra infra.datos.gob.ar/georef, version 13.0.0): calles de
Mar del Plata con su codigo municipal adelante, como vienen de verdad.
"""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal

import pytest

from estiba.core.models import Evidence, FreightJob, Slot
from estiba.finanzas.costos import Costos
from estiba.finanzas.precio import PrecioPorObjetivos
from estiba.finanzas.pronostico import Historia, pronosticar, wilson
from estiba.mapa.caracteristicas import ciudad_del_telefono
from estiba.mapa.datos import conectar, descargar
from estiba.mapa.nombres import clave
from estiba.mapa.ubicar import ALTURA, CALLE, ESQUINA, Mapa, km_entre, leer

D = Decimal
BASE = (-37.866678, -57.518770)  # Santa Clara del Mar


def _cuadra(nombre, depto, localidad, desde, hasta, puntos, provincia="06"):
    return json.dumps({
        "id": "x", "calle": {
            "id": nombre, "nombre": nombre, "fuente": "INDEC",
            "provincia": {"id": provincia, "nombre": "Buenos Aires"},
            "departamento": {"id": depto, "nombre": "x"},
            "localidad_censal": {"id": "x", "nombre": localidad}, "localidad": {"id": None, "nombre": None},
            "categoria": "CALLE"},
        "altura": {"inicio": {"derecha": desde, "izquierda": desde + 1},
                   "fin": {"derecha": hasta - 1, "izquierda": hasta}},
        "geometria": {"type": "MultiLineString", "coordinates": [puntos]},
    })


def _esquina(a, b, depto, lon, lat, provincia="06"):
    calle = lambda n: {"id": n, "nombre": n, "provincia": {"id": provincia}, "departamento": {"id": depto}}
    return json.dumps({"id": "x", "calle_a": calle(a), "calle_b": calle(b),
                       "geometria": {"type": "Point", "coordinates": [lon, lat]}})


CABECERA = json.dumps({"version": "13.0.0", "fecha_creacion": "2026-04-10"})
CUADRAS = [
    CABECERA,
    # Catamarca de Mar del Plata, con el codigo municipal adelante.
    _cuadra("C 148 - CATAMARCA", "06357", "Mar del Plata", 1100, 1199,
            [[-57.5490, -37.9920], [-57.5480, -37.9935]]),
    # Y la de Madariaga, que se llama solo CATAMARCA: mas lejos de la base.
    _cuadra("AV CATAMARCA", "06420", "General Juan Madariaga", 1100, 1199,
            [[-57.1290, -36.9990], [-57.1280, -37.0000]]),
    _cuadra("C 271 - ALBERTI", "06357", "Mar del Plata", 2300, 2399,
            [[-57.5500, -38.0075], [-57.5505, -38.0085]]),
    _cuadra("C 194 - CHILE", "06357", "Mar del Plata", 1, 99, [[-57.584, -37.995], [-57.583, -37.996]]),
    _cuadra("RIVADAVIA", "06560", "Necochea", 1200, 1299, [[-58.740, -38.555], [-58.741, -38.556]]),
    _cuadra("RIVADAVIA", "06357", "Mar del Plata", 1200, 1299, [[-57.545, -38.000], [-57.546, -38.001]]),
    _cuadra("SEBASTIAN GABOTO", "06518", "Santa Clara del Mar", 400, 499,
            [[-57.5187, -37.8666], [-57.5190, -37.8670]]),
    # De otra provincia: no entra.
    _cuadra("CATAMARCA", "14014", "Cordoba", 1100, 1199, [[-64.1, -31.4], [-64.2, -31.5]], provincia="14"),
]
ESQUINAS = [
    CABECERA,
    _esquina("C 194 - CHILE", "C 279 - ALVARADO", "06357", -57.58357, -37.99555),
    _esquina("C 148 - CATAMARCA", "C 211 - 11 DE SEPTIEMBRE", "06357", -57.5488, -37.9928),
]


@pytest.fixture
def mapa(tmp_path):
    conn = conectar(tmp_path / "mapa.db")
    cargadas = descargar(conn, fuente=lambda archivo: CUADRAS if archivo.startswith("cuadras") else ESQUINAS)
    assert cargadas == {"cuadras": 7, "esquinas": 2}      # la de Cordoba quedo afuera
    return Mapa(conn, BASE)


class TestNombres:
    def test_el_codigo_municipal_no_es_parte_del_nombre(self):
        """"C 148 - CATAMARCA" es la Catamarca que dice cualquiera."""
        assert clave("C 148 - CATAMARCA") == clave("catamarca")
        assert clave("C 54 - P M ARAOZ") == clave("Aráoz")

    def test_como_lo_escribe_la_gente(self):
        assert clave("11 de setiembre") == clave("11 DE SEPTIEMBRE")
        assert clave("Av. Hipólito Irigoyen") == clave("HIPOLITO YRIGOYEN")

    def test_una_calle_numerada_sigue_siendo_su_numero(self):
        assert clave("CALLE 38") == "38" and clave("CALLE 697 BIS") == "697 bis"


class TestLeer:
    def test_piso_y_departamento_no_son_la_altura(self):
        d = leer("Entre Rios 1839 3°G")
        assert (d.calle, d.altura) == ("Entre Rios", 1839)

    def test_esquina(self):
        d = leer("Catamarca y 11 de setiembre")
        assert (d.calle, d.otra) == ("Catamarca", "11 de setiembre")

    def test_con_altura_el_entre_calles_sobra(self):
        d = leer("Alberti 2359 entre santa fe y corrientes")
        assert (d.calle, d.altura, d.otra) == ("Alberti", 2359, None)

    def test_la_ciudad_va_despues_de_una_coma_o_de_en(self):
        ciudades = {"necochea": "Necochea", "alberti": "Alberti"}
        assert leer("Rivadavia 1234, Necochea", ciudades).ciudad == "Necochea"
        assert leer("Rivadavia 1234 en Necochea", ciudades).ciudad == "Necochea"

    def test_una_calle_que_se_llama_como_una_ciudad_no_es_la_ciudad(self):
        """Alberti es una ciudad de la provincia y una calle de Mar del Plata."""
        d = leer("Alberti 2359", {"alberti": "Alberti"})
        assert (d.calle, d.altura, d.ciudad) == ("Alberti", 2359, None)


class TestUbicar:
    def test_calle_y_altura_cae_adentro_de_su_cuadra(self, mapa):
        u = mapa.ubicar("Catamarca 1150")
        assert u.precision == ALTURA and "Mar del Plata" in u.como
        assert -37.9935 < u.lat < -37.9920

    def test_sin_ciudad_gana_la_mas_cercana_a_la_base(self, mapa):
        """Desde Santa Clara, la Catamarca de Mar del Plata y no la de Madariaga."""
        assert "Mar del Plata" in mapa.ubicar("Catamarca 1150").como

    def test_la_ciudad_que_dice_manda(self, mapa):
        assert "Necochea" in mapa.ubicar("Rivadavia 1250, Necochea").como

    def test_la_ciudad_del_telefono_ayuda(self, mapa):
        assert "Necochea" in mapa.ubicar("Rivadavia 1250", "Necochea").como
        assert "Mar del Plata" in mapa.ubicar("Rivadavia 1250", None).como

    def test_esquina(self, mapa):
        u = mapa.ubicar("chile y alvarado")
        assert u.precision == ESQUINA and (u.lat, u.lon) == (-37.99555, -57.58357)

    def test_una_esquina_que_no_existe_cae_en_la_calle(self, mapa):
        assert mapa.ubicar("chile y cualquiera").precision == CALLE

    def test_lo_que_no_esta_no_se_inventa(self, mapa):
        assert mapa.ubicar("calle que no existe 123") is None


class TestTelefono:
    def test_la_caracteristica_dice_la_ciudad(self):
        assert ciudad_del_telefono("5492235550001") == "Mar del Plata"
        assert ciudad_del_telefono("5492291123456") == "Miramar"

    def test_el_11_no_dice_nada(self):
        """Mucha gente de Buenos Aires vive en la costa con su numero de alla."""
        assert ciudad_del_telefono("5491133334444") is None

    def test_un_lid_no_es_un_telefono(self):
        assert ciudad_del_telefono("74891546099834@lid") is None


# --------------------------------------------------------------------------
# El precio de cada flete
# --------------------------------------------------------------------------


def _slot(valor, confianza=1.0):
    return Slot(valor, confianza, Evidence("m1", "cita", "regex"))


def _flete(origen, destino, cosas, pisos=0, ayudantes=None, chat="5492235550001"):
    return FreightJob(
        id=f"{chat}:job", conversation_id=chat,
        origin=_slot(origen), destination=_slot(destino), items=_slot(cosas),
        floors_no_lift=_slot(pisos) if pisos is not None else None,
        helpers=_slot(ayudantes) if ayudantes is not None else None,
    )


@pytest.fixture
def precio(mapa, tmp_path):
    p = PrecioPorObjetivos(tmp_path / "mapa.db")
    p._mapa = mapa
    return p


class TestPrecio:
    def test_cuenta_ida_viaje_y_vuelta_desde_la_base(self, precio):
        p = precio.estimate(_flete("Alberti 2350", "Catamarca 1150", ["heladera"]))
        (concepto, _), *_ = p.desglose
        assert concepto.startswith("Camioneta: ")
        km = int(concepto.split(": ")[1].split(" km")[0])
        # Santa Clara -> Alberti -> Catamarca -> Santa Clara, con las vueltas de las calles.
        recta = (km_entre(BASE, (-38.008, -57.5502)) + km_entre((-38.008, -57.5502), (-37.9927, -57.5485))
                 + km_entre((-37.9927, -57.5485), BASE))
        assert abs(km - recta * 1.3) < 2
        assert p.completo and not p.supuestos

    def test_mas_lejos_sale_mas(self, precio):
        cerca = precio.estimate(_flete("Alberti 2350", "Catamarca 1150", ["heladera"]))
        lejos = precio.estimate(_flete("Alberti 2350", "Rivadavia 1250, Necochea", ["heladera"]))
        assert lejos.total > cerca.total

    def test_pisos_por_escalera_y_ayudantes_suman(self, precio):
        base = precio.estimate(_flete("Alberti 2350", "Catamarca 1150", ["heladera"]))
        pisos = precio.estimate(_flete("Alberti 2350", "Catamarca 1150", ["heladera"], pisos=3))
        ayud = precio.estimate(_flete("Alberti 2350", "Catamarca 1150", ["heladera"], ayudantes=1))
        assert pisos.total > base.total
        assert ayud.total - base.total == D(15000)

    def test_sin_mapa_usa_el_promedio_y_lo_dice(self, precio):
        p = precio.estimate(_flete("un lugar raro", "otro lugar", ["heladera"]))
        assert "Camioneta: 64 km (promedio)" == p.desglose[0][0]
        assert any("usé tu promedio" in s for s in p.supuestos)
        assert p.completo, "suponer la distancia no deja el precio a medias"

    def test_sin_saber_si_hay_ascensor_es_un_piso(self, precio):
        p = precio.estimate(_flete("Alberti 2350", "Catamarca 1150", ["heladera"], pisos=None))
        assert not p.completo

    def test_la_hora_vale_lo_que_pide_la_meta(self):
        """48 fletes de 3 horas: la meta y los fijos se reparten en 144 horas."""
        c = Costos()
        assert c.valor_hora == (c.ganancia_objetivo + c.fijos_mes) / 144

    def test_un_flete_tipico_cuesta_lo_del_plan_a_pleno(self, precio):
        """Coherencia: el precio de un flete de 64 km anda cerca de lo que
        pide la meta trabajando a pleno. Si no, las dos cuentas mienten."""
        from estiba.finanzas.costos import plan_a_capacidad

        p = precio.estimate(_flete("un lugar raro", "otro lugar", ["heladera", "cama"]))
        plan = plan_a_capacidad(Costos())
        assert abs(p.total - plan.precio) / plan.precio < D("0.25")


# --------------------------------------------------------------------------
# El pronostico
# --------------------------------------------------------------------------


SEPTIEMBRE = Historia(dias=14, consultas_publicidad=19, hechos_publicidad=3, hechos_sin_publicidad=2,
                      gasto=D("53608.62"), cobrado=[D(20000), D(35000), D(35000), D(45000), D(40000)],
                      consultas_meta=29)


class TestPronostico:
    def test_wilson_con_pocos_casos_da_un_rango_ancho(self):
        lo, hi = wilson(5, 28)
        assert 0.07 < lo < 0.09 and 0.35 < hi < 0.38

    def test_las_conversaciones_de_meta_mandan(self):
        p = pronosticar(Costos(), SEPTIEMBRE)
        assert p.costo_consulta.medido and p.costo_consulta.valor == D(1849)
        assert "según Meta" in p.cierre.detalle

    def test_tres_escenarios_ordenados(self):
        e = pronosticar(Costos(), SEPTIEMBRE).escenarios
        assert [x.nombre for x in e] == ["Si viene flojo", "Lo más probable", "Si viene bien"]
        assert e[0].ganancia <= e[1].ganancia <= e[2].ganancia

    def test_con_pocos_datos_usa_lo_configurado_y_lo_dice(self):
        h = Historia(dias=7, consultas_publicidad=4, hechos_publicidad=1, hechos_sin_publicidad=0,
                     gasto=D(10000), cobrado=[D(30000)])
        p = pronosticar(Costos(), h)
        assert not p.cierre.medido and not p.ticket.medido and p.pocos_datos

    def test_la_semana_empieza_por_el_precio_si_se_cobra_poco(self):
        pasos = pronosticar(Costos(), SEPTIEMBRE).semana
        assert pasos[0].startswith("Cobrá como tu tarifa")

    def test_si_los_fletes_no_entran_la_meta_sale_de_trabajar_a_pleno(self):
        """82 fletes en un mes donde entran 48 no es un plan."""
        pasos = " ".join(pronosticar(Costos(), SEPTIEMBRE).semana)
        assert "82" not in pasos and "Con 48 fletes por mes como máximo" in pasos

    def test_la_publicidad_se_sube_de_a_poco(self):
        pasos = " ".join(pronosticar(Costos(), SEPTIEMBRE).semana)
        assert "Subí de $4.000 a $5.500" in pasos


class TestElAudioDeAgus:
    """El pedido que llego el 29/09 y Estiba no pudo estimar.

    "De 12 de octubre y tal, a aca de donde me viniste a buscar ... la Garcia
    Lorca, al 1140": una calle con nombre de fecha, una direccion con altura
    y charla en el medio. Se leia como un flete para el 12/10, de "donde me
    viniste" a "buscar", sin saber que llevaba.
    """

    AUDIO = ("Hola, genio, quería saber cuánto me va a salir, estoy pidiendo un adelanto para poder comprar "
             "la máquina. De ahí, de 12 de octubre y tal bueno, a acá de donde me viniste a buscar son 6 "
             "cuadras, o sea, la García Lorca, te digo de García Lorca, al 1140.")

    def test_una_calle_con_nombre_de_fecha_no_es_una_fecha(self):
        from estiba.extraction.fechas import fechas

        assert fechas(self.AUDIO, datetime(2026, 9, 29, 18)) == []
        assert fechas("la avenida 9 de julio al 300", datetime(2026, 9, 29)) == []
        # Y una fecha sigue siendo fecha.
        assert fechas("lo necesito para el 12 de octubre", datetime(2026, 9, 29))[0].valor.day == 12

    def test_los_lugares_de_un_audio(self):
        from estiba.extraction.reglas import lugares

        assert [(h.campo, h.valor) for h in lugares(self.AUDIO)] == [
            ("origin", "12 de octubre y tal"), ("destination", "García Lorca 1140")]

    def test_donde_me_viniste_a_buscar_no_es_un_lugar(self):
        from estiba.extraction.reglas import lugares

        assert lugares("de donde me viniste a buscar") == []

    def test_la_maquina_es_una_carga(self):
        from estiba.extraction.reglas import leer_item

        assert leer_item("maquina").nombre == "maquina"

    def test_una_direccion_suelta_no_pisa_una_clara(self):
        """"Aguirre al 500" despues de "de Villa Crespo a Caballito" precisa, no cambia."""
        from estiba.core.models import Conversation, Message, MessageKind
        from estiba.extraction.extractor import SlotExtractor

        t0 = datetime(2026, 9, 15, 10)
        conv = Conversation("x", [
            Message("x:1", "x", True, t0, MessageKind.TEXT, text="necesito un flete de Villa Crespo a Caballito"),
            Message("x:2", "x", True, t0.replace(minute=5), MessageKind.TEXT, text="Aguirre al 500"),
        ])
        assert SlotExtractor().extract(conv).job.origin.value == "Villa Crespo"

    def test_sin_saber_que_lleva_igual_se_estima(self, precio):
        job = _flete("Alberti 2350", "Catamarca 1150", ["heladera"])
        job.items = None
        p = precio.estimate(job)
        assert p is not None and p.total > 0
        assert any("No dijo qué lleva" in s for s in p.supuestos)

    def test_sin_destino_se_estima_como_piso(self, precio):
        job = _flete("Alberti 2350", "Catamarca 1150", ["heladera"])
        job.destination = None
        p = precio.estimate(job)
        assert p is not None and not p.completo
