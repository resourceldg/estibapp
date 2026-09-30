"""Fase 5: ¿tenemos suficientes datos para confiar en esta conclusion puntual?"""

from __future__ import annotations

from datetime import date, timedelta

from estiba.economia.suficiencia import (
    Cliente,
    Estado,
    Obs,
    cambia_la_decision,
    concentracion,
    elasticidad,
    eventos_raros,
    lo_que_mas_falta,
    media,
    mostrar,
    n_efectivo,
    peso,
    proporcion,
    recurrencia,
)

HOY = date(2026, 9, 30)


def _obs(valores, dias_atras=1, clientes=True, calidad="medido", segmento=None, paso=3):
    return [Obs(v, HOY - timedelta(days=dias_atras + i * paso), f"c{i}" if clientes else "unico", calidad, segmento)
            for i, v in enumerate(valores)]


PAREJOS = [40000, 41000, 39000, 40500, 39500, 40200, 39800, 40100, 39900, 40300, 40000, 39700]
DISPERSOS = [15000, 80000, 22000, 65000, 30000, 90000, 18000, 55000, 25000, 70000, 12000, 95000]


class TestDatosInsuficientes:
    def test_sin_datos_es_exploratorio_y_dice_que_falta(self):
        s = media("ticket", [], HOY, falta="Cobrar y anotar fletes")
        assert s.estado is Estado.EXPLORATORIO and s.valor is None
        assert s.falta == "Cobrar y anotar fletes"

    def test_con_dos_casos_no_hay_recomendacion_fuerte(self):
        s = media("ticket", _obs([40000, 35000]), HOY)
        assert not s.fuerte


class TestPorMetrica:
    def test_la_misma_cantidad_con_poca_dispersion_alcanza(self):
        assert media("ticket", _obs(PAREJOS), HOY).estado is Estado.UTILIZABLE

    def test_la_misma_cantidad_con_mucha_dispersion_no(self):
        s = media("ticket", _obs(DISPERSOS), HOY)
        assert s.estado.value < Estado.UTILIZABLE.value
        assert "intervalo" in " ".join(s.razones)

    def test_el_bootstrap_es_determinista(self):
        a = media("ticket", _obs(DISPERSOS), HOY)
        b = media("ticket", _obs(DISPERSOS), HOY)
        assert (a.lo, a.hi) == (b.lo, b.hi)
        assert a.lo < a.valor < a.hi


class TestWilson:
    def test_tres_de_veintinueve_es_exploratorio(self):
        s = proporcion("conversión", 3, 29)
        assert s.estado is Estado.EXPLORATORIO
        assert s.lo < 0.10 < s.hi

    def test_muchos_casos_y_tiempo_lo_vuelven_robusto(self):
        assert proporcion("conversión", 200, 1000, dias_observados=120).estado is Estado.ROBUSTO

    def test_sin_tiempo_de_observacion_no_llega_a_robusto(self):
        assert proporcion("conversión", 200, 1000).estado is Estado.UTILIZABLE


class TestCohortes:
    def test_una_recurrencia_joven_no_se_mide(self):
        clientes = [Cliente(f"c{i}", HOY - timedelta(days=10), 1) for i in range(20)]
        s = recurrencia(clientes, HOY)
        assert s.estado is Estado.EXPLORATORIO and s.n == 0
        assert "muy nuevos para saber si vuelve" in " ".join(s.razones)

    def test_solo_cuentan_los_maduros(self):
        viejos = [Cliente(f"v{i}", HOY - timedelta(days=90), 2 if i < 3 else 1) for i in range(10)]
        nuevos = [Cliente(f"n{i}", HOY - timedelta(days=5), 1) for i in range(10)]
        s = recurrencia(viejos + nuevos, HOY)
        assert s.n == 10 and s.valor == 0.3


class TestSupuestosYSegmentos:
    def test_la_elasticidad_sin_variacion_es_un_supuesto(self):
        s = elasticidad([40000] * 30)
        assert s.estado is Estado.EXPLORATORIO and "supuesto" in s.razones[0]

    def test_un_segmento_sin_datos(self):
        obs = _obs(PAREJOS, segmento="urbano")
        s = media("margen", obs, HOY, segmento="Miramar")
        assert s.estado is Estado.EXPLORATORIO
        assert "Miramar" in s.razones[0]
        assert media("margen", obs, HOY, segmento="urbano").estado is Estado.UTILIZABLE

    def test_las_roturas_necesitan_exposicion_no_fletes(self):
        poco = eventos_raros("roturas", 0, 3_000, 2)
        mucho = eventos_raros("roturas", 4, 150_000, 30)
        assert poco.estado is Estado.EXPLORATORIO and mucho.estado is Estado.UTILIZABLE
        assert "muy poca exposición" in " ".join(poco.razones)
        assert "hasta 1 cada" in " ".join(eventos_raros("roturas", 0, 20_000, 6).razones)


class TestPesos:
    def test_diez_fletes_de_un_cliente_no_son_diez_clientes(self):
        mismos = _obs(PAREJOS, clientes=False)
        distintos = _obs(PAREJOS)
        ws = [peso(o, HOY) for o in mismos]
        assert n_efectivo(mismos, ws) == 1.0
        assert concentracion(mismos, ws) == 1.0
        assert media("ticket", mismos, HOY).estado is Estado.EXPLORATORIO
        assert media("ticket", distintos, HOY).estado is Estado.UTILIZABLE

    def test_las_estimaciones_pesan_menos_que_las_mediciones(self):
        est = _obs(PAREJOS, calidad="estimado")
        assert media("horas", est, HOY).estado.value < media("horas", _obs(PAREJOS), HOY).estado.value

    def test_un_cambio_de_condiciones_baja_la_confianza(self):
        antes = media("ticket", _obs(PAREJOS), HOY)
        despues = media("ticket", _obs(PAREJOS), HOY, cambio=HOY - timedelta(days=1))
        assert despues.n_efectivo < antes.n_efectivo
        assert despues.estado.value <= antes.estado.value

    def test_los_datos_viejos_valen_menos_y_no_pasan_de_emergente(self):
        viejos = media("ticket", _obs(PAREJOS * 3, dias_atras=200, paso=1), HOY)
        nuevos = media("ticket", _obs(PAREJOS * 3, paso=1), HOY)
        assert viejos.estado.value <= Estado.EMERGENTE.value < nuevos.estado.value + 1
        assert "más nuevo tiene" in " ".join(viejos.razones)
        assert peso(Obs(1, HOY - timedelta(days=120)), HOY) == 0.5


class TestDecidirYMostrar:
    def test_si_el_umbral_cae_en_el_intervalo_no_se_decide(self):
        s = media("margen por hora", _obs(DISPERSOS), HOY)
        assert cambia_la_decision(s, s.valor)
        assert not cambia_la_decision(media("ticket", _obs(PAREJOS), HOY), 20000)

    def test_la_precision_visual_sigue_a_la_de_los_datos(self):
        exp = media("ticket", _obs([40000, 20000, 60000]), HOY)
        assert exp.estado is Estado.EXPLORATORIO
        assert mostrar(exp.valor, exp).startswith("entre $")
        usable = media("ticket", _obs(PAREJOS), HOY)
        assert mostrar(usable.valor, usable) == "$40.000"
        emergente = media("ticket", _obs(PAREJOS[:4]), HOY)
        if emergente.estado is Estado.EMERGENTE:
            assert mostrar(emergente.valor, emergente).startswith("~$")

    def test_lo_que_mas_falta_es_lo_mas_debil_que_mas_importa(self):
        fuerte = media("ticket", _obs(PAREJOS), HOY)
        debil = proporcion("conversión", 3, 29)
        tambien_debil = elasticidad([40000])
        assert lo_que_mas_falta([(fuerte, 1), (debil, 3), (tambien_debil, 1)]).metrica == "conversión"
