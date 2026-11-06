"""De donde viene cada consulta y que paso con ella (migracion 032).

Reglas: el codigo de un link le gana al mensaje precargado; entre marcadores
gana el mas especifico; lo que no se reconoce queda sin atribuir; lo manual
no se pisa y deja historia; la pauta se reimporta sin duplicar; el costo por
flete sale de la pauta del anuncio con impuestos, y lo organico cuesta cero.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

import pytest

from estiba.adapters.meta import ExportInvalido
from estiba.core.models import Stage
from estiba.extraction.texto import plano
from estiba.origen import atribucion as A
from estiba.origen import pauta as PA
from estiba.origen import piezas as P
from estiba.origen.resultado import SIN_ATRIBUIR, por_pieza

D = Decimal
AT = datetime(2026, 11, 8, 10)
HOGAR_AD = "120252742958560431"


@pytest.fixture
def web(tmp_path):
    from fastapi.testclient import TestClient

    from estiba.adapters.fixtures import FixtureMessageSource
    from estiba.conversation.store import ConversationStore
    from estiba.storage.db import connect
    from estiba.web.app import create_app

    ruta = tmp_path / "web.db"
    c = connect(ruta)
    ConversationStore(c).ingest(list(FixtureMessageSource().poll()))
    c.close()
    with TestClient(create_app(ruta, ahora=lambda: AT)) as cliente:
        yield cliente


@dataclass
class Op:
    job_id: str
    creado: datetime
    alcanzo: Stage
    hecha: bool = False
    cobrado: Decimal | None = None


class TestReconocerLaPieza:
    @pytest.mark.parametrize("texto, clave", [
        ("¡Hola! Vi el anuncio de mudanzas y muebles, quiero cotizar", "nov-hogar"),
        ("¡Hola! Compré algo en Marketplace y necesito traerlo", "nov-marketplace"),
        ("¡Hola! Terminé de cursar y quiero llevar mis cosas a mi ciudad", "nov-cursada"),
        ("¡Hola! Estoy preparando un alquiler para la temporada, quiero cotizar", "nov-temporada"),
        ("¡Hola! Te encontré en Google, quiero cotizar", "google-ficha"),
        ("¡Hola! Quiero cotizar un flete", "b1"),
        ("¡Hola! Quiero más información", "a-anteriores"),
    ])
    def test_cada_mensaje_precargado_dice_su_pieza(self, conn, texto, clave):
        pieza, metodo, _ = P.reconocer(plano(texto), P.todas(conn))
        assert pieza.clave == clave and metodo == "mensaje_precargado"

    def test_un_mensaje_escrito_a_mano_queda_sin_atribuir(self, conn):
        assert P.reconocer(plano("hola cuanto sale un flete a batan"), P.todas(conn)) is None

    def test_el_mas_especifico_le_gana_al_generico(self, conn):
        P.agregar(conn, "generico", "Genérico", "otro", "organico", "otro", marcador=r"\bflete\b")
        pieza, _, _ = P.reconocer(plano("¡Hola! Quiero cotizar un flete"), P.todas(conn))
        assert pieza.clave == "b1"

    def test_el_codigo_de_un_link_le_gana_al_mensaje(self, conn):
        P.agregar(conn, "grupo-alquileres", "Grupo Alquileres MdP", "posteo_grupo", "organico", "organico",
                  codigo="GR7")
        pieza, metodo, _ = P.reconocer(plano("Hola! vi el anuncio de mudanzas y muebles GR7"), P.todas(conn))
        assert (pieza.clave, metodo) == ("grupo-alquileres", "codigo")

    def test_un_marcador_roto_falla_al_cargarlo_y_no_al_leer_mensajes(self, conn):
        with pytest.raises(Exception):
            P.agregar(conn, "rota", "Rota", "otro", "organico", "otro", marcador="(sin cerrar")

    def test_tipo_y_medio_se_validan(self, conn):
        with pytest.raises(ValueError):
            P.agregar(conn, "x", "X", "tele", "pago", "meta")
        with pytest.raises(ValueError):
            P.agregar(conn, "x", "X", "otro", "trueque", "meta")


class TestAtribuir:
    def test_sincronizar_guarda_con_evidencia_y_salta_lo_desconocido(self, conn):
        n = A.sincronizar(conn, {"j1": (plano("¡Hola! Te encontré en Google, quiero cotizar"), AT),
                                 "j2": (plano("hola"), AT)})
        v = A.vigentes(conn)
        assert n == 1 and set(v) == {"j1"}
        assert v["j1"].metodo == "mensaje_precargado" and "google" in v["j1"].evidencia

    def test_sincronizar_dos_veces_no_duplica(self, conn):
        primeros = {"j1": (plano("¡Hola! Te encontré en Google, quiero cotizar"), AT)}
        A.sincronizar(conn, primeros)
        assert A.sincronizar(conn, primeros) == 0

    def test_lo_manual_no_se_pisa_y_deja_historia(self, conn):
        A.sincronizar(conn, {"j1": (plano("¡Hola! Te encontré en Google, quiero cotizar"), AT)})
        A.asignar(conn, "j1", "nov-hogar", evidencia="me dijo que vio el anuncio", ahora=AT)
        A.sincronizar(conn, {"j1": (plano("¡Hola! Te encontré en Google, quiero cotizar"), AT)})
        assert A.vigentes(conn)["j1"].pieza_id == P.por_clave(conn, "nov-hogar").id
        assert conn.execute("SELECT COUNT(*) FROM atribuciones WHERE job_id = 'j1'").fetchone()[0] == 2

    def test_asignar_a_una_pieza_que_no_existe_falla(self, conn):
        with pytest.raises(ValueError):
            A.asignar(conn, "j1", "no-existe")


def _pauta(filas, actualizado="2026-11-13T10:00:00-03:00"):
    return PA.desde_dict({"cuenta": "2656599231362025", "actualizado": actualizado,
                          "filas": [{"anuncio_id": a, "anuncio": "x", "dia": d, "gasto": g,
                                     "impresiones": 100, "conversaciones": c} for a, d, g, c in filas]})


class TestPauta:
    def test_reimportar_actualiza_en_el_lugar(self, conn):
        PA.importar(conn, _pauta([(HOGAR_AD, "2026-11-07", "1000", 1)]))
        PA.importar(conn, _pauta([(HOGAR_AD, "2026-11-07", "2900.50", 2)]))
        assert PA.por_anuncio(conn) == {HOGAR_AD: (D("2900.50"), 2)}

    def test_filtra_por_periodo(self, conn):
        PA.importar(conn, _pauta([(HOGAR_AD, "2026-11-07", "1000", 1), (HOGAR_AD, "2026-11-20", "500", 1)]))
        assert PA.por_anuncio(conn, date(2026, 11, 1), date(2026, 11, 13)) == {HOGAR_AD: (D(1000), 1)}

    def test_un_dia_que_no_paso_no_se_importa(self, conn):
        assert PA.importar(conn, _pauta([(HOGAR_AD, "2026-11-20", "1000", 1)])) == 0

    def test_una_fila_repetida_es_un_error(self):
        with pytest.raises(ExportInvalido):
            _pauta([(HOGAR_AD, "2026-11-07", "1", 0), (HOGAR_AD, "2026-11-07", "2", 0)])


class TestResultado:
    def _base(self, conn):
        A.sincronizar(conn, {
            "j1": (plano("¡Hola! Vi el anuncio de mudanzas y muebles, quiero cotizar"), AT),
            "j2": (plano("¡Hola! Vi el anuncio de mudanzas y muebles, quiero cotizar"), AT),
            "j3": (plano("¡Hola! Te encontré en Google, quiero cotizar"), AT),
        })
        ops = [Op("j1", AT, Stage.DONE, True, D(40000)), Op("j2", AT, Stage.QUOTED),
               Op("j3", AT, Stage.DONE, True, D(35000)), Op("j4", AT, Stage.NEW)]
        return ops, P.todas(conn), A.vigentes(conn)

    def test_cruza_pauta_consultas_y_fletes(self, conn):
        ops, piezas, atrib = self._base(conn)
        filas = {f.nombre: f for f in por_pieza(ops, piezas, atrib, {HOGAR_AD: (D(8000), 3)}, D(25))}
        hogar = filas["Nov Hogar"]
        assert (hogar.consultas, hogar.cotizados, hogar.realizados) == (2, 2, 1)
        assert hogar.pagado == D(10000) and hogar.costo_por_flete == D(10000)
        assert hogar.facturado == D(40000) and hogar.retorno == D(4)
        assert hogar.conversaciones_meta == 3

    def test_lo_organico_cuesta_cero(self, conn):
        ops, piezas, atrib = self._base(conn)
        google = next(f for f in por_pieza(ops, piezas, atrib, {}, D(25)) if f.nombre == "Ficha de Google")
        assert google.pagado == 0 and google.costo_por_flete is None and google.realizados == 1

    def test_lo_que_no_se_sabe_queda_sin_atribuir_al_final(self, conn):
        ops, piezas, atrib = self._base(conn)
        filas = por_pieza(ops, piezas, atrib, {}, D(25))
        assert filas[-1].nombre == SIN_ATRIBUIR and filas[-1].consultas == 1 and filas[-1].pagado == 0

    def test_una_pieza_con_gasto_y_sin_consultas_igual_aparece(self, conn):
        filas = por_pieza([], P.todas(conn), {}, {HOGAR_AD: (D(5000), 0)}, D(0))
        assert [f.nombre for f in filas] == ["Nov Hogar"] and filas[0].costo_por_consulta is None

    def test_el_periodo_filtra_las_consultas(self, conn):
        ops, piezas, atrib = self._base(conn)
        assert por_pieza(ops, piezas, atrib, {}, D(25), desde=date(2026, 11, 9)) == []


class TestPantallaYComandos:
    def test_la_pestana_origen_responde(self, web):
        html = web.get("/numeros/origen").text
        assert "¿Qué trae fletes, y a qué costo?" in html and 'href="/numeros/origen"' in html

    def test_cli_agrega_una_pieza_de_grupo_y_la_lista(self, tmp_path, capsys):
        from estiba.cli import main
        db = str(tmp_path / "e.db")
        assert main(["--db", db, "pieza", "agregar", "--clave", "gr-alquileres", "--nombre",
                     "Grupo Alquileres MdP", "--tipo", "posteo_grupo", "--codigo", "GR1"]) == 0
        assert main(["--db", db, "pieza", "listar"]) == 0
        assert "gr-alquileres" in capsys.readouterr().out

    def test_cli_importa_la_pauta(self, tmp_path):
        import json
        from estiba.cli import main
        archivo = tmp_path / "p.json"
        archivo.write_text(json.dumps({"cuenta": "1", "actualizado": "2026-11-13T10:00:00-03:00", "filas": [
            {"anuncio_id": HOGAR_AD, "anuncio": "Hogar", "dia": "2026-11-07", "gasto": "100", "conversaciones": 1}]}))
        assert main(["--db", str(tmp_path / "e.db"), "pauta", str(archivo)]) == 0

    def test_cli_atribuir_a_una_pieza_inexistente_falla(self, tmp_path):
        from estiba.cli import main
        assert main(["--db", str(tmp_path / "e.db"), "atribuir", "j1", "no-existe"]) == 1


class TestCosasVacias:
    def test_una_lista_de_cosas_vacia_no_rompe_el_borrador(self):
        """Con datos reales, un flete con las cosas borradas tumbaba el arranque."""
        from estiba.bandeja.redactor import _cosas
        assert _cosas([]) == "" and _cosas(["heladera"]) == "heladera"
        assert _cosas(["cama", "mesa", "sillas"]) == "cama, mesa y sillas"
