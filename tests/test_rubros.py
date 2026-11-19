"""Rubro de cada trabajo y nombre declarado (18-nov-2026): casos tomados de charlas reales, anonimizados."""
from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

from estiba.core.models import Conversation, Message, MessageKind
from estiba.rubros import nombre_declarado, rubro

T0 = datetime(2026, 11, 18, 10)


def _conv(*lineas):
    return Conversation("r", [Message(f"r:{i}", "r", q == "c", T0 + timedelta(minutes=i), MessageKind.TEXT, text=t) for i, (q, t) in enumerate(lineas)])


def _job(origen=None, destino=None):
    s = lambda v: SimpleNamespace(value=v) if v else None
    return SimpleNamespace(origin=s(origen), destination=s(destino))


class TestRubro:
    def test_flete_claro(self):
        r = rubro(_conv(("c", "necesito llevar una heladera y un lavarropas"), ("v", "te sale 40 mil")), _job("Centro", "Constitución"))
        assert r["valor"] == "flete" and not r["revisar"]

    def test_carpinteria(self):
        r = rubro(_conv(("c", "quería saber si me podés hacer una mesa de cedro a medida"), ("v", "sí, de qué medida")))
        assert r["valor"] == "carpinteria" and not r["revisar"]

    def test_venta(self):
        r = rubro(_conv(("c", "vi que vendés un traje de neoprene, qué talle es?")))
        assert r["valor"] == "venta" and not r["revisar"]

    def test_ventana_no_es_venta(self):
        assert rubro(_conv(("c", "tengo que llevar una ventana y un sillón")), _job("A", "B"))["valor"] == "flete"

    def test_la_mudanza_como_contexto_y_el_trabajo_es_el_vidrio(self):
        # Caso real: «la mudanza la hizo mi hija»; el trabajo es cambiar un vidrio (compra y colocación).
        r = rubro(_conv(("c", "la mudanza la hizo mi hija"), ("c", "pero el vidrio de la ventana hay que cambiarlo, compra y colocación")))
        assert r["valor"] == "otro" and not r["revisar"]

    def test_senales_parejas_de_dos_rubros_queda_a_revisar(self):
        r = rubro(_conv(("c", "¿podés llevar la mesa y lijarla?")))
        assert r["revisar"]

    def test_sin_senales_queda_a_revisar(self):
        r = rubro(_conv(("c", "hola, info")))
        assert r["revisar"] and r["valor"] == "otro"


class TestNombre:
    def test_me_llamo_y_mi_nombre_es(self):
        assert nombre_declarado(_conv(("v", "pasame tu nombre así lo agendo"), ("c", "Dale, me llamo Marta Giménez, muchísimas gracias"))) == "Marta Giménez"
        assert nombre_declarado(_conv(("c", "Hola, buenas noches! Mi nombre es Andrés Torres."))) == "Andrés Torres"

    def test_en_minusculas_de_un_audio(self):
        assert nombre_declarado(_conv(("c", "me llamo celia y quería un flete"))) == "Celia"

    def test_soy_solo_con_nombre_propio(self):
        assert nombre_declarado(_conv(("c", "Soy Luis, el de la heladera"))) == "Luis"
        assert nombre_declarado(_conv(("c", "soy de Mar del Plata"))) is None
        assert nombre_declarado(_conv(("c", "soy nuevo por acá"))) is None

    def test_lo_que_dice_el_fletero_no_cuenta(self):
        assert nombre_declarado(_conv(("v", "Me llamo Lucas y nada, dale"))) is None
