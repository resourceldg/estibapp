"""Importar el gasto de Meta por campania y dia sin duplicar ni contar dos veces.

Fija las reglas de la migracion 031: reimportar actualiza en el lugar, lo
manual que la importacion cubre entero queda como historia, lo que cubre a
medias sigue valiendo y tapa los dias importados, y la suma de los dias se
concilia contra el total que da Meta.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

import pytest

from estiba.adapters.meta import ExportInvalido, MetaExportProvider, desde_dict, leer_export
from estiba.cli import main
from estiba.finanzas.publicidad_meta import gastado_desde, importar
from estiba.storage.repositories import GastoRepository

D = Decimal
A, B = "120251932369960431", "120252130863200431"


def _export(filas, totales=(), actualizado="2026-10-07T12:00:00-03:00"):
    return desde_dict({
        "cuenta": "2656599231362025", "moneda": "ARS", "zona_horaria": "America/Argentina/Buenos_Aires",
        "actualizado": actualizado, "metrica": "onsite_conversion.messaging_conversation_started_7d",
        "filas": [{"campania_id": c, "campania": n, "dia": d, "gasto": g, "conversaciones": k}
                  for c, n, d, g, k in filas],
        "totales": [{"campania_id": c, "gasto": g, "conversaciones": k} for c, g, k in totales],
    })


TRES_DIAS = [(A, "A", "2026-09-16", "2100.74", 4), (A, "A", "2026-09-17", "3526.56", 1),
             (A, "A", "2026-09-18", "4060.78", 5)]


class TestLeerElExport:
    def test_sin_resultados_meta_dice_no_disponible_y_eso_es_cero(self):
        e = _export([(A, "A", "2026-10-05", "0", "Not available")])
        assert e.filas[0].conversaciones == 0

    def test_los_dias_que_todavia_no_pasaron_no_se_importan(self):
        e = _export([(A, "A", "2026-10-07", "0", None), (A, "A", "2026-10-08", "0", None)])
        assert [f.dia for f in e.filas] == [date(2026, 10, 7)]

    def test_sin_moneda_ni_zona_horaria_no_se_adivina(self):
        with pytest.raises(ExportInvalido):
            desde_dict({"cuenta": "1", "actualizado": "2026-10-07T12:00:00", "filas": [{}]})

    def test_un_dia_repetido_en_el_export_es_un_error(self):
        with pytest.raises(ExportInvalido):
            _export([(A, "A", "2026-09-16", "1", 0), (A, "A", "2026-09-16", "2", 0)])

    def test_un_gasto_que_no_es_monto_no_entra(self):
        with pytest.raises(ExportInvalido):
            _export([(A, "A", "2026-09-16", "mucho", 0)])

    def test_el_adaptador_cumple_el_puerto_con_una_campania_por_dia(self):
        p = MetaExportProvider(_export(TRES_DIAS))
        dias = list(p.spend(date(2026, 9, 17), date(2026, 9, 30)))
        assert [(c.external_id, c.period_start, c.spend) for c in dias] == [
            (A, date(2026, 9, 17), D("3526.56")), (A, date(2026, 9, 18), D("4060.78"))]


class TestImportar:
    def test_guarda_cuenta_moneda_zona_horaria_y_cuando_se_leyo(self, conn):
        importar(conn, _export(TRES_DIAS))
        f = conn.execute("SELECT * FROM campaigns WHERE period_start = '2026-09-16'").fetchone()
        assert (f["origen"], f["cuenta"], f["external_id"], f["moneda"], f["zona_horaria"]) == (
            "importado", "2656599231362025", A, "ARS", "America/Argentina/Buenos_Aires")
        assert f["actualizado"].startswith("2026-10-07T12:00")
        assert (f["spend"], f["resultados"], f["impuestos"], f["incluye_impuestos"]) == ("2100.74", 4, None, 0)

    def test_reimportar_no_duplica_y_actualiza_lo_que_cambio(self, conn):
        importar(conn, _export(TRES_DIAS))
        otra = [*TRES_DIAS[:2], (A, "A", "2026-09-18", "4100.00", 6)]
        r = importar(conn, _export(otra, actualizado="2026-10-08T09:00:00-03:00"))
        assert (r.nuevas, r.actualizadas, r.iguales) == (0, 1, 2)
        assert conn.execute("SELECT COUNT(*) FROM campaigns").fetchone()[0] == 3
        f = conn.execute("SELECT spend, resultados, actualizado FROM campaigns WHERE period_start = '2026-09-18'"
                         ).fetchone()
        assert (f["spend"], f["resultados"]) == ("4100.00", 6)
        assert f["actualizado"].startswith("2026-10-08")

    def test_los_impuestos_no_se_inventan(self, conn):
        importar(conn, _export(TRES_DIAS))
        assert {r["impuestos"] for r in conn.execute("SELECT impuestos FROM campaigns")} == {None}


class TestLoManual:
    def test_la_carga_manual_cubierta_entera_queda_como_historia_y_no_cuenta_dos_veces(self, conn):
        GastoRepository(conn).cargar(date(2026, 9, 16), date(2026, 9, 18), D("9600"), campania=A, resultados=10)
        r = importar(conn, _export(TRES_DIAS))
        assert len(r.manuales_reemplazadas) == 1
        vigentes = GastoRepository(conn).todos()
        assert {g["origen"] for g in vigentes} == {"importado"}
        assert sum(D(g["spend"]) for g in vigentes) == D("9688.08")
        manual = conn.execute("SELECT vigente, reemplazado_por FROM campaigns WHERE origen = 'manual'").fetchone()
        assert manual["vigente"] == 0 and "importacion de Meta" in manual["reemplazado_por"]

    def test_la_diferencia_entre_lo_manual_y_lo_importado_se_muestra(self, conn):
        GastoRepository(conn).cargar(date(2026, 9, 16), date(2026, 9, 18), D("9600"), campania=A, resultados=10)
        r = importar(conn, _export(TRES_DIAS))
        gasto = next(d for d in r.conciliacion if d.que.endswith("gasto"))
        assert (gasto.esperado, gasto.obtenido, gasto.cierra) == (D("9600"), D("9688.08"), False)
        convs = next(d for d in r.conciliacion if d.que.endswith("conversaciones"))
        assert convs.cierra and not convs.es_monto
        # Lo manual distinto se informa, pero no hace fallar una importacion que cierra con Meta.
        assert r.todo_cierra

    def test_si_la_cubre_a_medias_la_manual_sigue_y_tapa_los_dias_importados(self, conn):
        GastoRepository(conn).cargar(date(2026, 9, 17), date(2026, 9, 20), D("20000"), campania=A)
        r = importar(conn, _export(TRES_DIAS))
        assert r.manuales_reemplazadas == [] and r.dias_tapados == 2
        vigentes = GastoRepository(conn).todos()
        assert sorted((g["origen"], g["period_start"]) for g in vigentes) == [
            ("importado", "2026-09-16"), ("manual", "2026-09-17")]

    def test_una_carga_manual_sin_campania_vale_para_todas(self, conn):
        GastoRepository(conn).cargar(date(2026, 9, 16), date(2026, 9, 16), D("2100.74"))
        filas = [TRES_DIAS[0], (B, "B", "2026-09-16", "0", None)]
        r = importar(conn, _export(filas))
        assert len(r.manuales_reemplazadas) == 1 and r.todo_cierra

    def test_lo_manual_de_otra_campania_no_se_toca(self, conn):
        GastoRepository(conn).cargar(date(2026, 9, 16), date(2026, 9, 18), D("5000"), campania="999")
        r = importar(conn, _export(TRES_DIAS))
        assert r.manuales_reemplazadas == [] and r.dias_tapados == 0
        assert len(GastoRepository(conn).todos()) == 4


class TestConciliar:
    def test_la_suma_de_los_dias_se_compara_con_el_total_de_meta(self, conn):
        r = importar(conn, _export(TRES_DIAS, totales=[(A, "9688.08", 10)]))
        assert r.todo_cierra and len(r.conciliacion) == 2

    def test_si_falta_un_dia_no_cierra(self, conn):
        r = importar(conn, _export(TRES_DIAS[:2], totales=[(A, "9688.08", 10)]))
        assert not r.todo_cierra


class TestTopeDeLaPrueba:
    def test_lo_gastado_desde_el_inicio_cuenta_solo_lo_vigente(self, conn):
        GastoRepository(conn).cargar(date(2026, 9, 16), date(2026, 9, 18), D("9600"), campania=A)
        importar(conn, _export(TRES_DIAS))
        assert gastado_desde(conn, date(2026, 9, 17)) == D("7587.34")

    def test_una_carga_de_varios_dias_se_prorratea(self, conn):
        GastoRepository(conn).cargar(date(2026, 10, 6), date(2026, 10, 9), D("4000"))
        assert gastado_desde(conn, date(2026, 10, 8)) == D("2000")


class TestComando:
    def test_importa_concilia_y_dice_cuanto_queda_del_tope(self, tmp_path, capsys):
        archivo = tmp_path / "meta.json"
        archivo.write_text(json.dumps({
            "cuenta": "2656599231362025", "moneda": "ARS", "zona_horaria": "America/Argentina/Buenos_Aires",
            "actualizado": "2026-10-07T12:00:00-03:00",
            "filas": [{"campania_id": A, "campania": "A", "dia": "2026-10-07", "gasto": "1500.50",
                       "conversaciones": 1}],
            "totales": [{"campania_id": A, "gasto": "1500.50", "conversaciones": 1}],
        }), encoding="utf-8")
        codigo = main(["--db", str(tmp_path / "e.db"), "publicidad", str(archivo),
                       "--desde", "2026-10-07", "--tope", "70000"])
        salida = capsys.readouterr().out
        assert codigo == 0
        assert "ARS, America/Argentina/Buenos_Aires" in salida
        assert "queda del tope de $70.000,00: $68.499,50" in salida
        assert "se estiman" in salida

    def test_un_export_roto_no_toca_la_base(self, tmp_path, capsys):
        archivo = tmp_path / "meta.json"
        archivo.write_text("{no es json", encoding="utf-8")
        assert main(["--db", str(tmp_path / "e.db"), "publicidad", str(archivo)]) == 1
        assert "No se importo nada" in capsys.readouterr().err
        with pytest.raises(ExportInvalido):
            leer_export(archivo)


class TestPantalla:
    def test_lo_importado_se_ve_un_renglon_por_campania_sin_boton_de_borrar(self, tmp_path):
        from datetime import datetime

        from fastapi.testclient import TestClient

        from estiba.storage.db import connect
        from estiba.web.app import create_app

        ruta = tmp_path / "publi.db"
        conn = connect(ruta)
        importar(conn, _export([*TRES_DIAS, (B, "B", "2026-09-16", "0", None)]))
        conn.close()
        with TestClient(create_app(ruta, ahora=lambda: datetime(2026, 9, 29, 18))) as c:
            html = c.get("/numeros/objetivos").text
        assert html.count("importado de Meta (ARS)") == 2
        assert "A · 16/09 al 18/09/2026 · 10 conversaciones" in html
        assert "/borrar" not in html
