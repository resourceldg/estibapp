"""El adaptador de fixtures y las conversaciones de prueba del repo."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from estiba.adapters.fixtures import FIXTURES_DIR, FixtureMessageSource
from estiba.core.models import MessageKind
from estiba.core.ports import MessageSource

CONVERSACIONES = sorted(Path(FIXTURES_DIR).glob("*.json"))


@pytest.fixture
def dir_fixture(tmp_path):
    def escribir(datos: dict) -> Path:
        d = tmp_path / "conv"
        d.mkdir(exist_ok=True)
        (d / "test.json").write_text(json.dumps(datos), encoding="utf-8")
        return d

    return escribir


class TestAdaptador:
    def test_cumple_el_puerto(self):
        """Si esto falla, WAHA no va a poder reemplazarlo en la Fase 7."""
        assert isinstance(FixtureMessageSource(), MessageSource)

    def test_los_ids_son_estables_entre_corridas(self, dir_fixture):
        d = dir_fixture(
            {
                "chat_id": "549111",
                "mensajes": [
                    {"de": "cliente", "hora": "2026-09-15T10:00:00", "texto": "hola"}
                ],
            }
        )
        primeros = [m.id for m in FixtureMessageSource(d).read_all()]
        segundos = [m.id for m in FixtureMessageSource(d).read_all()]
        assert primeros == segundos == ["549111:000"]

    def test_distingue_quien_habla(self, dir_fixture):
        d = dir_fixture(
            {
                "chat_id": "549111",
                "mensajes": [
                    {"de": "cliente", "hora": "2026-09-15T10:00:00", "texto": "a"},
                    {"de": "vos", "hora": "2026-09-15T10:01:00", "texto": "b"},
                ],
            }
        )
        msgs = list(FixtureMessageSource(d).read_all())
        assert [m.from_customer for m in msgs] == [True, False]

    def test_poll_no_repite_lo_ya_entregado(self, dir_fixture):
        d = dir_fixture(
            {
                "chat_id": "549111",
                "mensajes": [
                    {"de": "cliente", "hora": "2026-09-15T10:00:00", "texto": "hola"}
                ],
            }
        )
        fuente = FixtureMessageSource(d)
        assert len(list(fuente.poll())) == 1
        assert len(list(fuente.poll())) == 0

    def test_mensaje_sin_texto_ni_audio_falla_fuerte(self, dir_fixture):
        d = dir_fixture(
            {
                "chat_id": "549111",
                "mensajes": [{"de": "cliente", "hora": "2026-09-15T10:00:00"}],
            }
        )
        with pytest.raises(ValueError, match="necesita 'texto' o 'audio'"):
            list(FixtureMessageSource(d).read_all())

    def test_rechaza_un_hablante_desconocido(self, dir_fixture):
        d = dir_fixture(
            {
                "chat_id": "549111",
                "mensajes": [
                    {"de": "ellos", "hora": "2026-09-15T10:00:00", "texto": "a"}
                ],
            }
        )
        with pytest.raises(ValueError, match="cliente"):
            list(FixtureMessageSource(d).read_all())

    def test_directorio_inexistente_avisa(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            list(FixtureMessageSource(tmp_path / "nada").read_all())


class TestConversacionesDelRepo:
    def test_hay_conversaciones_cargadas(self):
        assert len(CONVERSACIONES) >= 3

    @pytest.mark.parametrize("archivo", CONVERSACIONES, ids=lambda p: p.stem)
    def test_cada_conversacion_carga_sin_errores(self, archivo):
        msgs = list(FixtureMessageSource.read_file(archivo))
        assert msgs, f"{archivo.name} no tiene mensajes"
        assert len({m.id for m in msgs}) == len(msgs), "ids duplicados"

    @pytest.mark.parametrize("archivo", CONVERSACIONES, ids=lambda p: p.stem)
    def test_cada_conversacion_explica_para_que_sirve(self, archivo):
        datos = json.loads(archivo.read_text(encoding="utf-8"))
        assert datos.get("descripcion"), "sin descripcion: no se sabe que prueba"

    @pytest.mark.parametrize("archivo", CONVERSACIONES, ids=lambda p: p.stem)
    def test_cada_audio_declara_su_transcripcion_esperada(self, archivo):
        """Sin esto no podemos medir la tasa de error de Whisper en la Fase 2."""
        msgs = list(FixtureMessageSource.read_file(archivo))
        esperadas = FixtureMessageSource.expected_transcripts(archivo)
        for m in msgs:
            if m.kind is MessageKind.AUDIO:
                assert m.id in esperadas, f"audio {m.id} sin transcripcion esperada"

    def test_esta_el_caso_manana_te_confirmo(self):
        """El caso que mas nos importa que no se rompa en la Fase 3."""
        textos = [
            (m.text or "").lower()
            for a in CONVERSACIONES
            for m in FixtureMessageSource.read_file(a)
        ]
        assert any("manana te confirmo" in t for t in textos)
