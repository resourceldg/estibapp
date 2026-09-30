"""Lo que esta en curso: probabilidad por etapa con la historia resuelta, y el precio mas firme."""

from datetime import date, datetime, timedelta
from decimal import Decimal

from estiba.core.models import Stage
from estiba.crm.leads import LeadInfo, Paso
from estiba.economia import en_curso
from estiba.economia.evolucion import Hito, hitos_de_publicidad, ordenar, semanas
from estiba.economia.mes import FleteDelMes
from estiba.web.economia_vista import partir, vista_fuentes

HOY = date(2026, 9, 30)


def _lead(job, etapa, alcanzo=None, cotizado=None):
    t = datetime(2026, 9, 1)
    pasos = [Paso(Stage.NEW, t, "")]
    if (alcanzo or etapa) not in (Stage.NEW,) and (alcanzo or etapa) is not Stage.LOST:
        pasos.append(Paso(alcanzo or etapa, t, ""))
    if etapa is Stage.LOST:
        pasos.append(Paso(Stage.LOST, t, ""))
    return LeadInfo(job, job, t, etapa, tuple(pasos), None, cotizado, None, None)


class TestProbabilidades:
    def test_las_abiertas_no_entran_en_el_denominador(self):
        leads = [_lead("a", Stage.DONE), _lead("b", Stage.LOST, alcanzo=Stage.QUOTED),
                 _lead("c", Stage.QUOTED)]
        p = en_curso.probabilidades(leads)[Stage.QUOTED]
        assert (p.n, p.valor) == (2, 0.5)

    def test_sin_historia_no_inventa(self):
        p = en_curso.probabilidades([_lead("c", Stage.QUOTED)])[Stage.QUOTED]
        assert p.valor is None and not p.fuerte


class TestPosibles:
    def test_el_precio_mas_firme_y_los_vencidos_primero(self):
        probs = en_curso.probabilidades([_lead("a", Stage.DONE)])
        abiertos = [
            (_lead("cot", Stage.QUOTED, cotizado=Decimal(45000)), Decimal(50000), None, True),
            (_lead("ruta", Stage.QUALIFIED), Decimal(41000), HOY, False),
            (_lead("nada", Stage.NEW), None, HOY - timedelta(days=4)),
        ]
        ps = en_curso.posibles(abiertos, probs, Decimal(40000), HOY)
        assert ps[0].job_id == "nada" and ps[0].vencido and ps[0].fuente == en_curso.BASE
        por = {x.job_id: x for x in ps}
        assert por["cot"].monto == 45000 and por["cot"].fuente == en_curso.COTIZADO
        assert por["ruta"].monto == 41000 and por["ruta"].fuente == en_curso.PARCIAL


class TestEvolucion:
    def test_semanas_desde_la_primera_actividad(self):
        f = FleteDelMes(job_id="x", dia=date(2026, 9, 19), cobrado=Decimal(20000), km=Decimal(10),
                        km_vacios=None, horas=Decimal(2), directos=Decimal(0), recurrente=False, medido=False)
        ss = semanas([f], {date(2026, 9, 16): Decimal(1000)}, Decimal(100), Decimal(30000), HOY)
        assert ss[0].inicio == date(2026, 9, 14) and sum(s.fletes for s in ss) == 1
        assert ss[0].publicidad == 1000 and ss[0].margen == 19000
        # antes del 16 no habia negocio: sin fijos esos dias
        assert ss[0].fijos == Decimal(30000) / 30 * 5

    def test_hitos_de_publicidad_y_orden(self):
        pub = {date(2026, 9, 16): Decimal(1), date(2026, 9, 17): Decimal(1), date(2026, 9, 25): Decimal(1)}
        hs = hitos_de_publicidad(pub)
        assert [h.texto for h in ordenar(hs, None, HOY)] == [
            "Arranca la publicidad", "Se corta la publicidad", "Vuelve la publicidad"]
        assert ordenar([Hito(HOY + timedelta(days=1), "futuro", "x")], None, HOY) == []


class TestPresentacion:
    def test_partir_la_frase(self):
        assert partir("Uno: dos tres.") == ("Uno.", "Dos tres.")
        assert partir("Sin corte") == ("Sin corte", "")

    def test_fuentes_dicen_cuanto_creerle(self):
        f = {x["dato"]: x["nivel"] for x in vista_fuentes(5, 5, 0, 0, 3, date(2026, 9, 25), HOY, "recién",
                                                          Decimal(88000))}
        assert f["Lo cobrado"] == "firme" and f["Km y horas de cada flete"] == "supuesto"
        assert f["Publicidad"] == "desactualizado"
