"""Lo que Estiba aprende de lo que cobra el fletero.

No hay tarifa de fabrica que sirva para todos los negocios: la que venia
supone barrios, precios y costumbres de otra ciudad. Estas reglas miran lo
que se cotizo antes y proponen a partir de eso.
"""

from __future__ import annotations

from decimal import Decimal

from estiba.core.models import Evidence, ExtractionMethod, FreightJob, Slot
from estiba.jobs.aprendido import MINIMO, categoria, sugerir


def _job(*cosas: str) -> FreightJob:
    job = FreightJob(id="x:job", conversation_id="x")
    if cosas:
        job.items = Slot(list(cosas), 0.9, Evidence("m1", "…", ExtractionMethod.REGEX))
    return job


def _historial(*pares) -> list[tuple[str, Decimal]]:
    return [(c, Decimal(m)) for c, m in pares]


class TestCategoria:
    def test_agrupa_por_tipo_de_carga(self):
        assert categoria(_job("heladera")) == "electrodomestico"
        assert categoria(_job("placard", "cama")) == "mueble"
        assert categoria(_job("mudanza de un departamento")) == "mudanza"
        assert categoria(_job("12 cajas")) == "cajas"

    def test_lo_que_no_reconoce_no_lo_inventa(self):
        assert categoria(_job("una escultura")) == "otro"
        assert categoria(_job()) == "otro"


class TestSugerencia:
    def test_sin_historia_no_sugiere_nada(self):
        assert sugerir(_job("heladera"), []) is None

    def test_con_pocos_casos_tampoco(self):
        """Un promedio de dos numeros no es un precio, es una coincidencia."""
        pocos = _historial(("electrodomestico", 40000), ("electrodomestico", 60000))
        assert len(pocos) < MINIMO
        assert sugerir(_job("heladera"), pocos) is None

    def test_usa_la_mediana_no_el_promedio(self):
        """Un flete regalado o carisimo no puede arrastrar a los demas."""
        h = _historial(("electrodomestico", 40000), ("electrodomestico", 45000),
                       ("electrodomestico", 50000), ("electrodomestico", 500000))
        s = sugerir(_job("heladera"), h)
        assert s.valor == Decimal(47500)      # el promedio daria 158.750

    def test_prefiere_los_trabajos_del_mismo_tipo(self):
        h = _historial(("mueble", 90000), ("mueble", 95000), ("mueble", 100000),
                       ("electrodomestico", 30000), ("electrodomestico", 30000),
                       ("electrodomestico", 30000))
        assert sugerir(_job("placard"), h).valor == Decimal(95000)

    def test_si_no_alcanzan_los_parecidos_mira_todo_y_lo_dice(self):
        h = _historial(("mueble", 50000), ("cajas", 30000), ("mudanza", 100000))
        s = sugerir(_job("heladera"), h)
        assert s.valor == Decimal(50000)
        assert "cualquier tipo" in s.porque

    def test_siempre_dice_de_donde_sale(self):
        """Un numero solo no se puede discutir."""
        h = _historial(*[("mueble", 50000)] * 4)
        assert sugerir(_job("placard"), h).porque

    def test_avisa_cuando_el_precio_varia_mucho(self):
        disperso = _historial(*[("mueble", v) for v in (20000, 40000, 60000, 90000, 200000)])
        parejo = _historial(*[("mueble", v) for v in (50000, 52000, 55000, 58000, 60000)])
        assert not sugerir(_job("placard"), disperso).firme
        assert sugerir(_job("placard"), parejo).firme
