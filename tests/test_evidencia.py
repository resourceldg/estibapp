"""Fase 5: el catalogo de evidencia y la validacion del negocio."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from estiba.economia.config import Economia
from estiba.economia.evidencia import Datos, catalogo, validar
from estiba.economia.mes import FleteDelMes
from estiba.economia.suficiencia import Estado
from estiba.finanzas.costos import Costos

D = Decimal
HOY = date(2026, 9, 30)
E = Economia(costos=Costos(credito=D(420000), ganancia_objetivo=D(0)), precio_base=D(40000),
             declarados=frozenset(Costos().a_dict()))


def _fletes(n, medido=False, cobrado=40000, clientes=None, dias=2):
    return [FleteDelMes(f"j{i}", HOY - timedelta(days=i * dias), D(cobrado + (i % 3) * 1000), D(40), D(30),
                        D("2.5"), medido=medido) for i in range(n)]


def _datos(fletes, consultas=29, hechos=3, **kw):
    return Datos(fletes, {f.job_id: f"c{i}" for i, f in enumerate(fletes)}, consultas, hechos, 14, **kw)


class TestCatalogo:
    def test_seis_fletes_estimados_son_poca_evidencia(self):
        cat = catalogo(E, _datos(_fletes(6)), HOY)
        assert not cat.horas.fuerte
        assert cat.conversion.estado is Estado.EXPLORATORIO
        assert cat.elasticidad.estado is Estado.EXPLORATORIO
        assert cat.roturas.estado is Estado.EXPLORATORIO

    def test_la_suficiencia_es_distinta_por_metrica(self):
        cat = catalogo(E, _datos(_fletes(30, medido=True)), HOY)
        assert cat.ticket.estado.value >= Estado.UTILIZABLE.value
        assert cat.conversion.estado is Estado.EXPLORATORIO      # 3 de 29 sigue siendo poco
        assert cat.recurrencia.estado is Estado.EXPLORATORIO     # nadie volvio todavia

    def test_mas_datos_suben_la_confianza(self):
        poco = catalogo(E, _datos(_fletes(4, medido=True)), HOY)
        mucho = catalogo(E, _datos(_fletes(30, medido=True)), HOY)
        assert mucho.margen.estado.value > poco.margen.estado.value

    def test_un_cambio_de_precio_reciente_baja_la_confianza_del_ticket(self):
        sin = catalogo(E, _datos(_fletes(30, medido=True)), HOY)
        con = catalogo(E, _datos(_fletes(30, medido=True), cambio_precio=HOY), HOY)
        assert con.ticket.n_efectivo < sin.ticket.n_efectivo


class TestValidacion:
    def test_con_pocos_datos_no_responde_si_o_no(self):
        v = validar(E, catalogo(E, _datos(_fletes(6)), HOY), HOY)
        assert v.conclusion.startswith("Todavía")   # nunca un sí o no seco con pocos datos
        assert v.no_sabemos
        assert v.proximo_dato

    def test_la_pregunta_del_millon_se_mira_por_dimension(self):
        v = validar(E, catalogo(E, _datos(_fletes(30, medido=True)), HOY), HOY)
        dims = {c.dimension for c in v.compatibilidad}
        assert {"margen", "horas", "capacidad", "desgaste", "adquisición", "recurrencia"} <= dims
        horas = next(c for c in v.compatibilidad if c.dimension == "horas")
        assert "no entra sin trabajar más" in horas.frase
        assert v.conclusion.startswith("Todavía hay pocos datos, pero aun en el mejor caso")

    def test_evidencia_solo_si_todo_el_intervalo_queda_de_un_lado(self):
        v = validar(E, catalogo(E, _datos(_fletes(30, medido=True)), HOY), HOY)
        assert any("Cada flete deja algo" in x for x in v.a_favor)
        # 3 de 29 cruza el 10 %: no es evidencia ni a favor ni en contra.
        assert not any("conversaciones de la publicidad" in x for x in v.a_favor + v.en_contra)
        assert any("Conversión" in x for x in v.no_sabemos)
