"""Que Estiba se sienta como un solo producto, y no como muchas pantallas.

Estos tests no prueban que algo funcione: prueban que lo que el fletero
aprendio en una pantalla siga valiendo en las demas. Son las reglas del
sistema, escritas de forma que romperlas falle.

Cada una nacio de una contradiccion real que estaba en la app:

- "Marcar realizado" existia tres veces, con tres consecuencias distintas.
- Lo cotizado se mostraba como `placeholder` y se mandaba vacio.
- La accion principal de un lead recien llegado era cerrarlo.
- Lo irreversible pesaba menos que lo recuperable.
- La barra de escritorio y la del celular llevaban a lugares distintos.
- Una pantalla entera no tenia un solo enlace.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from estiba.adapters.fixtures import FixtureMessageSource
from estiba.conversation.store import ConversationStore
from estiba.storage.db import connect
from estiba.web.app import create_app
from estiba.web.presentacion import ESTADOS

JUEVES_18 = datetime(2026, 9, 18, 11, 0)


@pytest.fixture
def db(tmp_path):
    ruta = tmp_path / "coherencia.db"
    conn = connect(ruta)
    ConversationStore(conn).ingest(list(FixtureMessageSource().poll()))
    conn.close()
    return ruta


@pytest.fixture
def cliente(db):
    with TestClient(create_app(db, ahora=lambda: JUEVES_18)) as c:
        yield c


def _todas_las_pantallas(c) -> dict[str, str]:
    """Todo lo que el fletero puede llegar a ver, en un solo lugar."""
    paginas = {
        r: c.get(r).text
        for r in ("/", "/?f=perdidos", "/?f=privados", "/?f=spam", "/?f=sin-clasificar",
                  "/agenda", "/fletes", "/numeros", "/clientes", "/panel", "/whatsapp")
    }
    for job_id in sorted(set(re.findall(r'href="/fletes/([^"?#]+)', paginas["/"]))):
        paginas[f"/fletes/{job_id}"] = c.get(f"/fletes/{job_id}").text
    return paginas


def _botones(html: str) -> list[tuple[str, str]]:
    """(clases, texto) de cada boton de la pagina."""
    salida = []
    for m in re.finditer(r'<button[^>]*class="([^"]*)"[^>]*>(.*?)</button>', html, re.S):
        texto = " ".join(re.sub(r"<[^>]+>", " ", m.group(2)).split())
        if texto:
            salida.append((m.group(1), texto))
    return salida


class TestUnaAccionUnNombre:
    def test_cerrar_un_flete_se_llama_igual_en_todas_partes(self, cliente):
        """Habia tres botones para lo mismo, y no hacian lo mismo.

        Uno anotaba lo cobrado, otro confirmaba pero mandaba el monto vacio
        y el de la agenda no hacia ninguna de las dos. Cerrar un flete es
        irreversible: `ActionRepository` no tiene borrar.
        """
        nombres = set()
        for html in _todas_las_pantallas(cliente).values():
            for m in re.finditer(r'action="/fletes/[^"]+/realizado"[^>]*>(.*?)</form>', html, re.S):
                for clases, texto in _botones(m.group(1)):
                    nombres.add(texto)
        assert nombres in ({"Marcar realizado"}, set()), nombres

    def test_y_siempre_pregunta_cuanto_cobraste(self, cliente):
        """Ese monto es con lo que Estiba aprende a cotizar.

        Dos de las tres formas de cerrar lo tiraban sin avisar.
        """
        for ruta, html in _todas_las_pantallas(cliente).items():
            for m in re.finditer(r'<form[^>]*action="/fletes/[^"]+/realizado".*?</form>', html, re.S):
                assert 'name="cobrado"' in m.group(0), f"cierre sin monto en {ruta}"

    def test_lo_cotizado_viene_cargado_y_no_como_placeholder(self, cliente, db):
        """Un placeholder gris parece un valor puesto y se manda vacio.

        El fletero veia el precio que habia cotizado dentro del campo, tocaba
        el boton, y lo cobrado se perdia sin una sola senal.
        """
        html = "".join(_todas_las_pantallas(cliente).values())
        campos = re.findall(r'<input[^>]*name="cobrado"[^>]*>', html)
        assert campos, "ninguna pantalla deja cerrar un flete"
        for campo in campos:
            assert 'value="' in campo


class TestElPesoSigueALaConsecuencia:
    def test_lo_que_no_se_puede_deshacer_pesa_mas_que_lo_que_si(self, cliente):
        """Borrar los mensajes es lo unico que destruye datos.

        Estaba como un texto gris de 26px, mientras "Dar por perdido" —que
        se recupera con que el cliente vuelva a escribir— iba en rojo.
        """
        pesos = {}
        for html in _todas_las_pantallas(cliente).values():
            for clases, texto in _botones(html):
                pesos.setdefault(texto, set()).add(clases)
        borrar = next((c for t, c in pesos.items() if "Borrar" in t), None)
        if borrar is not None:
            assert any("destructivo" in c for c in borrar)
            perdido = next((c for t, c in pesos.items() if t == "Dar por perdido"), set())
            assert not any("destructivo" in c for c in perdido)

    def test_una_accion_nunca_baja_de_nivel_al_cambiar_de_pantalla(self, cliente):
        """El peso dice "esto es lo principal de esta pantalla", y eso si
        cambia: cerrar el flete es lo que sigue en la agenda y una salida
        lateral en un lead recien llegado.

        Lo que no puede pasar es saltar de nivel: que algo sea la accion
        principal en un lado y un texto gris en otro. Ahi el fletero ya no
        puede predecir que esta mirando.
        """
        NIVEL = {"primario": 3, "secundario": 2, "destructivo": 2, "fantasma": 1, "texto": 0}
        niveles = defaultdict(set)
        for html in _todas_las_pantallas(cliente).values():
            for clases, texto in _botones(html):
                partes = set(clases.split())
                for nombre, n in NIVEL.items():
                    if nombre in partes:
                        niveles[texto].add(n)
        saltos = {t: n for t, n in niveles.items() if n and max(n) - min(n) > 1}
        assert not saltos, saltos

    def test_una_accion_lleva_siempre_el_mismo_icono(self, cliente):
        """El icono se reconoce antes que el texto. Si cambia, la accion
        parece otra aunque se llame igual."""
        iconos = defaultdict(set)
        for html in _todas_las_pantallas(cliente).values():
            for m in re.finditer(r'<button[^>]*>(.*?)</button>', html, re.S):
                texto = " ".join(re.sub(r"<[^>]+>", " ", m.group(1)).split())
                paths = tuple(sorted(re.findall(r'\bd="([^"]{6,})"', m.group(1))))
                if texto:
                    iconos[texto].add(paths)
        confusos = {t: len(i) for t, i in iconos.items() if len(i) > 1}
        assert not confusos, confusos


class TestJerarquia:
    def test_un_flete_nuevo_no_propone_cerrarlo(self, cliente):
        """Cerrar es el final del embudo, no el principio.

        Un lead que acaba de escribir "hola" tenia como accion principal
        "Marcar realizado". Se puede seguir haciendo —hay fletes que se
        arreglan por telefono— pero plegado, no como lo que sigue.
        """
        for job_id, html in (
            (j, cliente.get(f"/fletes/{j}").text)
            for j in sorted(set(re.findall(r'href="/fletes/([^"?#]+)', cliente.get("/").text)))
        ):
            barra = html.split('class="barra-accion"')[-1]
            if "Nuevo" in html.split("quien-estado")[1][:400]:
                assert "/realizado" not in barra, job_id

    def test_una_sola_accion_principal_por_pantalla(self, cliente):
        """Dos botones verdes compitiendo no son una jerarquia."""
        for ruta, html in _todas_las_pantallas(cliente).items():
            principales = [t for c, t in _botones(html) if "primario" in c]
            assert len(principales) <= 1, f"{ruta}: {principales}"


class TestSeLlegaATodo:
    def test_ninguna_pantalla_queda_sin_enlace(self, cliente):
        """/fletes existia con secciones, estados y precios, y sin una sola
        forma de llegar que no fuera escribir la URL."""
        alcanzables = set()
        for html in _todas_las_pantallas(cliente).values():
            alcanzables |= set(re.findall(r'href="(/[a-z]*)["?]', html))
        for ruta in ("/", "/agenda", "/fletes", "/numeros", "/clientes", "/panel", "/whatsapp"):
            assert ruta in alcanzables, f"{ruta} no tiene ningun enlace"

    def test_las_dos_navegaciones_llevan_a_lo_mismo(self, cliente):
        """Quien aprende la app en el celular tiene que encontrar lo mismo
        en la compu. Una decia "API" y la otra "WhatsApp"."""
        html = cliente.get("/").text
        escritorio = re.search(r'<nav class="nav-escritorio".*?</nav>', html, re.S).group(0)
        tabbar = re.search(r'<nav class="tabbar".*?</nav>', html, re.S).group(0)
        assert set(re.findall(r'href="(/[a-z]*)"', escritorio)) == \
               set(re.findall(r'href="(/[a-z]*)"', tabbar))


class TestElColorNoMiente:
    def test_cada_estado_tiene_un_tono_y_no_lo_comparte_con_su_opuesto(self):
        """`neutro` queria decir "todavia no empezo" y "ya se cobro".

        Un flete sin tocar y uno terminado se veian igual.
        """
        from estiba.core.models import JobState

        tonos = {estado: tono for estado, (_, tono) in ESTADOS.items()}
        assert tonos[JobState.DRAFT] != tonos[JobState.DONE]
        assert tonos[JobState.DONE] != tonos[JobState.CANCELLED]

    def test_ningun_estado_se_dibuja_de_dos_formas(self, cliente):
        etiquetas = defaultdict(set)
        for html in _todas_las_pantallas(cliente).values():
            for m in re.finditer(r'class="chip tono-(\w+)">([^<]+)<', html):
                etiquetas[m.group(2).strip()].add(m.group(1))
        confusos = {e: t for e, t in etiquetas.items() if len(t) > 1}
        assert not confusos, confusos


class TestVocabulario:
    def test_ninguna_palabra_significa_dos_cosas(self, cliente):
        """"Cotizar" (hay que hacerlo) y "Cotizado" (ya se hizo) estaban a un
        caracter de distancia, una como pestaña y otra como estado."""
        html = cliente.get("/").text
        pestañas = re.findall(r'class="filtro[^"]*"[^>]*>([^<]+)', html)
        estados = {etiqueta for etiqueta, _ in ESTADOS.values()}
        assert not ({p.strip() for p in pestañas} & estados), \
            "una pestaña se llama igual que un estado y quieren decir cosas distintas"
