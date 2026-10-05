"""Casos reales de la auditoria de interpretacion (audit/AUDIT_REPORT.md).

Cada test es una frase que escribio un cliente o el fletero, anonimizada, y
lo que Estiba deberia entender. Los que hoy fallan estan marcados `xfail`
estricto: documentan el error sin cambiar produccion, y cuando se corrija la
regla el test pasa a fallar por "xpass" para que se le saque la marca.
"""

from datetime import datetime

import pytest

from estiba.extraction import fechas, reglas
from estiba.extraction.intencion import Intencion, intencion

AHORA = datetime(2026, 10, 1, 10, 0)


def _precio(texto):
    h = reglas.precio(texto)
    return h[0].valor if h else None


def _lugares(texto):
    return {h.campo: h.valor for h in reglas.lugares(texto)}


def _items(texto):
    h = reglas.items(texto)
    return [str(i) for i in h[0].valor] if h else []


# --------------------------------------------------------------------------
# Lo que ya funciona (para no romperlo al corregir lo demas)
# --------------------------------------------------------------------------


class TestQueFunciona:
    def test_precio_con_mil(self):
        assert _precio("Hola buenas tardes te sale 50 mil") == 50000

    def test_precio_con_signo(self):
        assert _precio("el flete te queda en $50.000.") == 50000

    def test_de_a_con_mayusculas_o_minusculas(self):
        assert _lugares("Mesa y sillas desde ostende a San Clemente") == {
            "origin": "ostende", "destination": "San Clemente"}

    def test_no_inventa_cantidad_de_sillas(self):
        assert _items("Mesa y sillas desde ostende a San Clemente") == ["mesa", "sillas"]

    def test_direccion_con_al(self):
        assert _lugares("Desde Bermejo al 2500, hasta Juramento al 2500.")["destination"] == "Juramento 2500"


# --------------------------------------------------------------------------
# P0/P1: el precio que dice el fletero
# --------------------------------------------------------------------------


class TestPrecio:
    @pytest.mark.parametrize("texto,valor", [
        ("te sale 40", 40000),
        ("hola te sale 80 si es planta baja", 80000),
        ("Si es en planta baja todo te sale 30", 30000),
    ])
    def test_numero_pelado_despues_de_sale(self, texto, valor):
        assert _precio(texto) == valor

    @pytest.mark.parametrize("texto,valor", [("35 le sale", 35000), ("25 mil sale. Cuando lo llevamos?", 25000)])
    def test_numero_antes_del_verbo(self, texto, valor):
        assert _precio(texto) == valor

    def test_el_ayudante_no_es_el_precio(self):
        assert _precio("15 mil sale el ayudante") is None

    @pytest.mark.parametrize("texto", ["salen 2 viajes", "son las 19 hs", "son 6 cuadras", "es 2026",
                                       "necesito una seña de 10 mil", "llego en 20 minutos", "Mañana puedo 19hs"])
    def test_lo_que_no_es_un_precio(self, texto):
        assert _precio(texto) is None

    def test_sin_ayudante_si_es_el_flete(self):
        assert _precio("Genial, sin ayudante esta 35 mil el flete") == 35000

    def test_varias_opciones_toma_la_base(self):
        texto = ("Traslado puerta a puerta: $190.000. Si necesitás que yo ayude con la carga/descarga, "
                 "el valor sería $220.000.")
        assert _precio(texto) == 190000


# --------------------------------------------------------------------------
# P1: intencion
# --------------------------------------------------------------------------


class TestIntencion:
    def test_te_confirmo_con_hora_es_aceptar(self):
        assert intencion("Hola si te confirmo hoy a las 19 hs")[0] is Intencion.ACEPTA

    @pytest.mark.parametrize("texto", [
        "Ok..te aviso en la semana",
        "Dale gracias. Es para fin de mes. Te contacto luego",
        "Genial!! A penas tenga la fecha de ingreso te escribo si??",
    ])
    def test_ok_pero_te_aviso_es_posponer(self, texto):
        assert intencion(texto)[0] is Intencion.POSPONE

    def test_si_condicional_no_es_aceptar(self):
        resultado = intencion("Si están enganchados con el food truck, todo bien jefe")
        assert resultado is None or resultado[0] is not Intencion.ACEPTA

    def test_te_escribo_de_nuevo_es_una_consulta(self):
        assert intencion("hola! te escribo de nuevo, ahora necesito llevar un lavarropas")[0] is Intencion.CONSULTA

    def test_manana_te_confirmo_sigue_siendo_posponer(self):
        assert intencion("mañana te confirmo")[0] is Intencion.POSPONE

    def test_dale_gracias_solo_es_cortesia(self):
        assert intencion("Dale gracias") is None

    def test_estoy_averiguando_te_confirmo_mas_tarde_es_posponer(self):
        assert intencion("Estoy averiguando precios te confirmo más tarde cuando hago la compra")[0] \
            is Intencion.POSPONE


# --------------------------------------------------------------------------
# P1/P2: lugares y direcciones
# --------------------------------------------------------------------------


class TestLugares:
    @pytest.mark.xfail(strict=True, reason="P1: 'la/las' al principio de una calle se toma como pronombre")
    @pytest.mark.parametrize("texto,origen,destino", [
        ("De la rioja y Alberti a luis varese 1979", "la rioja y Alberti", "luis varese 1979"),
        ("necesito un flete de Frávega a las dalias", "Frávega", "las dalias"),
    ])
    def test_calles_con_articulo(self, texto, origen, destino):
        assert _lugares(texto) == {"origin": origen, "destination": destino}

    @pytest.mark.xfail(strict=True, reason="P2: 'Ortiz de Zárate y Edison' queda cortado en 'Ortiz'")
    def test_calle_con_de_en_el_nombre(self):
        assert _lugares("Necesito traer dos freezer desde Miramar a Ortiz de Zárate y Edison zona puerto")[
            "destination"].startswith("Ortiz de Zárate")

    @pytest.mark.xfail(strict=True, reason="P2: direccion con altura sin 'al' en un mensaje suelto")
    @pytest.mark.parametrize("texto", ["Hipólito Yrigoyen 3751", "Araoz 2619", "Y vamos a Catamarca 1185"])
    def test_direccion_suelta_con_altura(self, texto):
        assert reglas.lugares(texto)


# --------------------------------------------------------------------------
# P1/P2: cosas, cantidades, pisos y fechas
# --------------------------------------------------------------------------


class TestCosasPisosFechas:
    @pytest.mark.xfail(strict=True, reason="P2: 'alacenas de cocina' agrega una cocina que no existe")
    def test_cocina_como_adjetivo(self):
        assert "cocina" not in _items("Son alacenas de cocina")

    @pytest.mark.xfail(strict=True, reason="P2: errores de tipeo frecuentes (frezer, friser)")
    @pytest.mark.parametrize("texto", ["Necesito traer dos frezer", "Tengo q llevar un friser"])
    def test_freezer_mal_escrito(self, texto):
        assert any("freezer" in i for i in _items(texto))

    @pytest.mark.xfail(strict=True, reason="P2: 'es planta baja' y 'escalera 1 piso' sin decir 'ascensor'")
    @pytest.mark.parametrize("texto", ["Es planta baja", "De dónde van tengo escalera 1 piso y el otro ascensor"])
    def test_pisos_sin_ascensor_explicito(self, texto):
        assert reglas.pisos(texto)

    @pytest.mark.xfail(strict=True, reason="P2: una calle con nombre de fecha se lee como fecha")
    @pytest.mark.parametrize("texto", ["Desde Hipólito Yrigoyen y Quintana hasta Catamarca y 11 de setiembre",
                                       "está afuera el carro, Castelli y 14 de julio"])
    def test_calle_fecha_no_es_fecha(self, texto):
        assert not [h for h in fechas.fechas(texto, AHORA) if h.campo == "when"]

    @pytest.mark.xfail(strict=True, reason="P2: 'tipo 9 / 9:30' es una hora, no el 9 de septiembre")
    def test_hora_con_barra_no_es_fecha(self):
        assert not [h for h in fechas.fechas("el Lunes por casualidad tipo 9 / 9 : 30 podremos?", AHORA)
                    if h.campo == "when" and h.valor.month == 9 and h.valor.day == 9]

    @pytest.mark.xfail(strict=True, reason="P2: '6 cajas' y '10 cajas' se pisan en vez de sumarse")
    def test_cantidades_del_mismo_objeto_en_un_mensaje(self):
        assert "16 cajas" in _items("6 cajas de plástico grandes y unas 10 cajas de carton")


class TestClasificacion:
    @pytest.mark.xfail(strict=True, reason="P1: el nombre 'tipo usuario' gana sobre el boton del anuncio y una consulta real")
    def test_lead_del_anuncio_con_nombre_raro_no_es_spam(self):
        from estiba.conversation.clasificacion import Clase, clasificar
        from estiba.core.models import Conversation, Message, MessageKind
        from estiba.extraction.extractor import SlotExtractor

        t = datetime(2026, 9, 22, 19, 50)
        conv = Conversation("549223", [
            Message("a", "549223", True, t, MessageKind.TEXT, text="¡Hola! Quiero más información"),
            Message("b", "549223", True, t.replace(minute=54), MessageKind.TEXT,
                    text="Tengo que llevar un sommier hasta San Lorenzo, cuánto me sale?"),
        ])
        cl = clasificar(conv, SlotExtractor().extract(conv), "ideaspublicom518")
        assert cl.clase is not Clase.SPAM
