"""Difusion en grupos: la cola del dia, los textos, el codigo y lo publicado.

Reglas: solo grupos vigentes que aceptan publicidad, en sus dias; dias de por
medio entre posteos en el mismo grupo; pocos por dia, primero el que hace mas
que no se publica; el texto cambia respecto del ultimo usado en ese grupo; el
link lleva un codigo que Origen reconoce y que NO se confunde con un anuncio.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from urllib.parse import unquote

import pytest

from estiba.conversation.clasificacion import _DESDE_PUBLI
from estiba.core.models import Stage
from estiba.difusion import grupos as G
from estiba.extraction.texto import plano
from estiba.origen import atribucion as A
from estiba.origen import piezas as P
from estiba.origen.resultado import por_pieza

LUNES = date(2026, 11, 9)


class TestCola:
    def test_solo_grupos_que_aceptan_publicidad_y_en_sus_dias(self, conn):
        G.agregar(conn, "Abierto")
        G.agregar(conn, "Sin publicidad", acepta_publicidad=False)
        G.agregar(conn, "Solo jueves", dias=(3,))
        assert [p.grupo.nombre for p in G.cola(conn, LUNES)] == ["Abierto"]
        assert "Solo jueves" in [p.grupo.nombre for p in G.cola(conn, LUNES + timedelta(days=3))]

    def test_un_grupo_pausado_no_aparece(self, conn):
        g = G.agregar(conn, "Pausado")
        G.pausar(conn, g.id)
        assert G.cola(conn, LUNES) == []

    def test_dias_de_por_medio_entre_posteos(self, conn):
        G.agregar(conn, "Alquileres MdP")
        G.publicado(conn, G.cola(conn, LUNES)[0], LUNES)
        assert G.cola(conn, LUNES + timedelta(days=6)) == []
        assert len(G.cola(conn, LUNES + timedelta(days=7))) == 1

    def test_pocos_por_dia_y_primero_el_mas_olvidado(self, conn):
        for n in "ABCDEF":
            G.agregar(conn, f"Grupo {n}")
        hace_mucho = LUNES - timedelta(days=30)
        G.publicado(conn, G.cola(conn, hace_mucho)[0], hace_mucho)  # Grupo A
        cola = G.cola(conn, LUNES)
        assert len(cola) == G.POR_DIA
        assert [p.grupo.nombre for p in cola][:3] == ["Grupo B", "Grupo C", "Grupo D"]

    def test_el_texto_cambia_respecto_del_ultimo_en_el_grupo(self, conn):
        G.agregar(conn, "Estudiantes UNMdP", tema="estudiantes")
        primero = G.cola(conn, LUNES)[0]
        G.publicado(conn, primero, LUNES)
        segundo = G.cola(conn, LUNES + timedelta(days=7))[0]
        assert primero.plantilla != segundo.plantilla and segundo.plantilla.startswith("estudiantes")

    def test_marcar_publicado_dos_veces_no_duplica(self, conn):
        G.agregar(conn, "X")
        p = G.cola(conn, LUNES)[0]
        assert G.publicado(conn, p, LUNES).id == G.publicado(conn, p, LUNES).id

    def test_tema_invalido_falla(self, conn):
        with pytest.raises(ValueError):
            G.agregar(conn, "Y", tema="politica")


class TestLinkYOrigen:
    def test_el_texto_lleva_la_zona_y_el_link_con_el_codigo(self, conn):
        G.agregar(conn, "Alquileres MdP", zona="Mar del Plata", tema="alquileres")
        p = G.cola(conn, LUNES)[0]
        assert "Mar del Plata" in p.texto and "wa.me/5492235128209" in p.texto
        assert p.codigo in unquote(p.texto)

    def test_el_mensaje_del_link_no_se_confunde_con_un_anuncio_de_meta(self, conn):
        g = G.agregar(conn, "Alquileres MdP")
        mensaje = unquote(G.link(g, "G1-0911").split("text=")[1])
        assert not _DESDE_PUBLI.search(plano(mensaje))

    def test_una_consulta_por_el_link_cuenta_para_el_grupo_en_origen(self, conn):
        g = G.agregar(conn, "Alquileres MdP")
        p = G.cola(conn, LUNES)[0]
        G.publicado(conn, p, LUNES)
        mensaje = unquote(G.link(g, p.codigo).split("text=")[1])
        A.sincronizar(conn, {"j1": (plano(mensaje), datetime(2026, 11, 10, 9))})

        class Op:
            job_id, creado, alcanzo, hecha, cobrado = "j1", datetime(2026, 11, 10, 9), Stage.DONE, True, Decimal(45000)

        fila = next(f for f in por_pieza([Op()], P.todas(conn), A.vigentes(conn), {}, Decimal(25))
                    if f.nombre == "Grupo · Alquileres MdP")
        assert (fila.consultas, fila.realizados, fila.facturado, fila.pagado) == (1, 1, Decimal(45000), 0)

    def test_varios_posteos_del_mismo_grupo_son_una_sola_fila(self, conn):
        G.agregar(conn, "Alquileres MdP")
        for semana in range(3):
            dia = LUNES + timedelta(days=7 * semana)
            G.publicado(conn, G.cola(conn, dia)[0], dia)
        primeros = {f"j{i}": (plano(f"te escribo por el grupo alquileres mdp (g1-{(LUNES + timedelta(days=7 * i)):%d%m})"),
                              datetime(2026, 11, 20)) for i in range(3)}
        A.sincronizar(conn, primeros)

        class Op:
            def __init__(self, j):
                self.job_id, self.creado, self.alcanzo, self.hecha, self.cobrado = j, datetime(2026, 11, 20), Stage.NEW, False, None

        filas = [f for f in por_pieza([Op(j) for j in primeros], P.todas(conn), A.vigentes(conn), {}, Decimal(0))
                 if f.nombre.startswith("Grupo")]
        assert len(filas) == 1 and filas[0].consultas == 3


class TestPedidosVistos:
    def test_anotar_y_resolver(self, conn):
        g = G.agregar(conn, "Compraventa MdP", tema="compraventa")
        pid = G.anotar_pedido(conn, "Busco flete para un sillón a Camet", datetime(2026, 11, 9, 10), g.id, "Ana")
        assert [p.grupo for p in G.pedidos(conn)] == ["Compraventa MdP"]
        G.resolver_pedido(conn, pid, "contactado", datetime(2026, 11, 9, 11))
        assert G.pedidos(conn) == [] and G.pedidos(conn, solo_pendientes=False)[0].estado == "contactado"

    def test_un_pedido_vacio_no_se_anota(self, conn):
        with pytest.raises(ValueError):
            G.anotar_pedido(conn, "   ", datetime(2026, 11, 9))


class TestPantalla:
    def test_cargar_un_grupo_publicar_y_anotar_un_pedido(self, tmp_path):
        from fastapi.testclient import TestClient

        from estiba.storage.db import connect
        from estiba.web.app import create_app

        ruta = tmp_path / "web.db"
        connect(ruta).close()
        with TestClient(create_app(ruta, ahora=lambda: datetime(2026, 11, 9, 10))) as web:
            assert "Todavía no cargaste grupos" in web.get("/grupos").text
            web.post("/grupos", data={"nombre": "Alquileres MdP", "tema": "alquileres", "zona": "Mar del Plata",
                                      "acepta_publicidad": "1"})
            html = web.get("/grupos").text
            assert "Hoy tocan 1 grupo" in html and "G1-0911" in html
            web.post("/grupos/publicado", data={"grupo_id": "1"})
            assert "Hoy no toca ningún grupo" in web.get("/grupos").text
            web.post("/grupos/pedidos", data={"texto": "Busco flete heladera", "grupo_id": "1"})
            assert "Busco flete heladera" in web.get("/grupos").text
