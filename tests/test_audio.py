"""Fase 2: AudioPipeline, WER y el adaptador de Whisper sin cargar el modelo."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

import estiba.cli
from estiba.adapters.whisper import FasterWhisperTranscriber, confianza
from estiba.audio.pipeline import AudioPipeline
from estiba.audio.wer import comparar, normalizar, wer_total
from estiba.cli import main
from estiba.core.models import Transcript
from estiba.core.ports import Transcriber

from conftest import mensaje


class TranscriberFalso:
    """Doble de prueba del puerto: devuelve lo que le digan por archivo."""

    def __init__(
        self, textos: dict[str, str], rompe_en: set[str] = frozenset(), nombre: str = "falso"
    ) -> None:
        self.textos = textos
        self.nombre = nombre
        self.rompe_en = rompe_en
        self.llamadas: list[str] = []

    def transcribe(self, audio_path: str, message_id: str) -> Transcript:
        self.llamadas.append(message_id)
        if message_id in self.rompe_en:
            raise RuntimeError("audio corrupto")
        return Transcript(
            message_id=message_id,
            text=self.textos[message_id],
            confidence=0.8,
            model=self.nombre,
            duration_s=3.0,
        )


@pytest.fixture
def audio(tmp_path):
    """Crea un archivo de audio (vacio: el transcriptor falso no lo lee)."""

    def crear(nombre: str) -> str:
        ruta = tmp_path / nombre
        ruta.write_bytes(b"OggS")
        return str(ruta)

    return crear


class TestPuerto:
    def test_whisper_cumple_el_puerto_sin_cargar_el_modelo(self):
        assert isinstance(FasterWhisperTranscriber(), Transcriber)

    def test_el_doble_de_prueba_tambien(self):
        assert isinstance(TranscriberFalso({}), Transcriber)

    def test_el_nombre_del_modelo_queda_registrado(self):
        assert FasterWhisperTranscriber(model_size="medium").nombre == "faster-whisper/medium"


class _Seg:
    start, end, avg_logprob, text = 0.0, 2.0, -0.1, " somos dos "


class _Info:
    duration = 2.0


class _ModeloFalso:
    def __init__(self, error: str | None) -> None:
        self.error = error

    def transcribe(self, *a, **k):
        if self.error:
            raise RuntimeError(self.error)
        return iter([_Seg()]), _Info()


class TestSinCuda:
    def test_si_faltan_las_librerias_de_cuda_sigue_en_cpu(self, monkeypatch):
        w = FasterWhisperTranscriber()
        cargas = []

        def cargar():
            cargas.append(w.device)
            return _ModeloFalso("Library libcublas.so.12 is not found or cannot be loaded"
                                if w.device != "cpu" else None)

        monkeypatch.setattr(w, "_cargar", cargar)
        t = w.transcribe("a.ogg", "m1")
        assert t.text == "somos dos" and cargas == ["auto", "cpu"] and w.device == "cpu"

    def test_otro_error_no_se_tapa(self, monkeypatch):
        w = FasterWhisperTranscriber()
        monkeypatch.setattr(w, "_cargar", lambda: _ModeloFalso("archivo roto"))
        with pytest.raises(RuntimeError, match="archivo roto"):
            w.transcribe("a.ogg", "m1")
        assert w.device == "auto"


class TestPipeline:
    def test_transcribe_y_el_texto_aparece_en_el_hilo(self, store, audio):
        store.ingest([
            mensaje(id="c:000", chat_id="c", minuto=0, texto="hola"),
            mensaje(id="c:001", chat_id="c", minuto=1, audio=audio("a.ogg")),
        ])
        reporte = AudioPipeline(store, TranscriberFalso({"c:001": "somos dos"})).run()

        assert [t.message_id for t in reporte.transcriptos] == ["c:001"]
        assert store.pending_audio() == []
        assert "cliente: somos dos" in store.get("c").as_plain_text()

    def test_sin_archivo_no_se_inventa_nada_y_queda_pendiente(self, store):
        store.ingest([mensaje(id="c:000", chat_id="c", audio="no/existe.ogg")])
        falso = TranscriberFalso({"c:000": "esto no deberia aparecer"})
        reporte = AudioPipeline(store, falso).run()

        assert [m.id for m in reporte.sin_archivo] == ["c:000"]
        assert falso.llamadas == []
        assert [m.id for m in store.pending_audio()] == ["c:000"]

    def test_un_audio_roto_no_frena_a_los_demas(self, store, audio):
        store.ingest([
            mensaje(id="c:000", chat_id="c", minuto=0, audio=audio("roto.ogg")),
            mensaje(id="c:001", chat_id="c", minuto=1, audio=audio("bueno.ogg")),
        ])
        falso = TranscriberFalso({"c:001": "quince cajas"}, rompe_en={"c:000"})
        reporte = AudioPipeline(store, falso).run()

        assert [m.id for m, _ in reporte.fallidos] == ["c:000"]
        assert "audio corrupto" in reporte.fallidos[0][1]
        assert [t.message_id for t in reporte.transcriptos] == ["c:001"]
        assert [m.id for m in store.pending_audio()] == ["c:000"]

    def test_correr_dos_veces_no_retranscribe(self, store, audio):
        store.ingest([mensaje(id="c:000", chat_id="c", audio=audio("a.ogg"))])
        falso = TranscriberFalso({"c:000": "hola"})
        AudioPipeline(store, falso).run()
        AudioPipeline(store, falso).run()
        assert falso.llamadas == ["c:000"]

    def test_comparar_retranscribe_con_otro_modelo_y_conserva_ambas(self, store, audio):
        store.ingest([mensaje(id="c:000", chat_id="c", audio=audio("a.ogg"))])
        AudioPipeline(store, TranscriberFalso({"c:000": "quince cajas"}, nombre="chico")).run()

        grande = TranscriberFalso({"c:000": "15 cajas"}, nombre="grande")
        assert AudioPipeline(store, grande).run().transcriptos == []
        reporte = AudioPipeline(store, grande).run(modelo="grande")
        assert [t.model for t in reporte.transcriptos] == ["grande"]

        modelos = {t.model for t in store.transcripciones.all_for_message("c:000")}
        assert modelos == {"chico", "grande"}
        assert AudioPipeline(store, grande).run(modelo="grande").transcriptos == []

    def test_respeta_el_limite(self, store, audio):
        store.ingest([
            mensaje(id=f"c:00{i}", chat_id="c", minuto=i, audio=audio(f"{i}.ogg"))
            for i in range(3)
        ])
        falso = TranscriberFalso({f"c:00{i}": "x" for i in range(3)})
        reporte = AudioPipeline(store, falso).run(limite=2)
        assert len(reporte.transcriptos) == 2
        assert len(store.pending_audio()) == 1


class TestConfianza:
    @dataclass
    class Seg:
        start: float
        end: float
        avg_logprob: float

    def test_sin_segmentos_es_cero(self):
        assert confianza([]) == 0.0

    def test_logprob_cero_es_certeza(self):
        assert confianza([self.Seg(0, 2, 0.0)]) == pytest.approx(1.0)

    def test_pesa_por_duracion(self):
        # Un segmento largo y seguro pesa mas que uno corto y dudoso.
        c = confianza([self.Seg(0, 9, 0.0), self.Seg(9, 10, -10.0)])
        assert 0.89 < c < 0.91


class TestWer:
    def test_identico_es_cero(self):
        assert comparar("son quince cajas", "son quince cajas").wer == 0.0

    def test_ignora_tildes_mayusculas_y_puntuacion(self):
        assert normalizar("Mañana, ¿a las 9?") == ["manana", "a", "las", "9"]
        assert comparar("mañana te confirmo", "Manana te confirmo.").wer == 0.0

    def test_cuenta_sustitucion_borrado_e_insercion(self):
        c = comparar("vivo en un tercero", "vivo en el tercero piso")
        assert (c.errores, c.palabras) == (2, 4)

    def test_el_total_pesa_por_palabras(self):
        corto = comparar("hola", "chau")                   # 1/1
        largo = comparar("a b c d e f g h i", "a b c d e f g h i")  # 0/9
        assert wer_total([corto, largo]) == pytest.approx(0.1)


class TestCli:
    @pytest.fixture
    def conversacion(self, tmp_path, audio):
        ruta = audio("cajas.ogg")
        conv = tmp_path / "conv"
        conv.mkdir()
        (conv / "a.json").write_text(
            '{"chat_id": "549", "mensajes": ['
            '{"de": "cliente", "hora": "2026-09-15T10:00:00", '
            f'"audio": "{ruta}", '
            '"transcripcion_esperada": "son como quince cajas"}]}',
            encoding="utf-8",
        )
        return conv

    def test_transcribe_y_mide_el_error(self, tmp_path, conversacion, monkeypatch, capsys):
        db = str(tmp_path / "t.db")
        falso = TranscriberFalso({"549:000": "son como 15 cajas"})
        monkeypatch.setattr(estiba.cli, "crear_transcriber", lambda args: falso)

        assert main(["--db", db, "ingest", "--fixtures", str(conversacion)]) == 0
        capsys.readouterr()

        assert main(["--db", db, "transcribe"]) == 0
        assert "1 transcriptos, 0 sin archivo, 0 fallidos" in capsys.readouterr().out

        assert main(["--db", db, "show", "549"]) == 0
        assert "cliente: son como 15 cajas" in capsys.readouterr().out

        assert main(["--db", db, "wer", "--fixtures", str(conversacion)]) == 0
        salida = capsys.readouterr().out
        assert "WER 25.0%" in salida  # "quince" contra "15"
        assert "WER total: 25.0%" in salida

    def test_transcribe_sin_pendientes_no_carga_el_modelo(self, tmp_path, monkeypatch, capsys):
        def no_deberia_llamarse(args):
            raise AssertionError("cargo el modelo sin necesidad")

        monkeypatch.setattr(estiba.cli, "crear_transcriber", no_deberia_llamarse)
        assert main(["--db", str(tmp_path / "v.db"), "transcribe"]) == 0
        assert "No hay audios pendientes" in capsys.readouterr().out

    def test_un_fallo_se_reporta_con_codigo_de_error(self, tmp_path, conversacion, monkeypatch, capsys):
        db = str(tmp_path / "f.db")
        falso = TranscriberFalso({}, rompe_en={"549:000"})
        monkeypatch.setattr(estiba.cli, "crear_transcriber", lambda args: falso)
        main(["--db", db, "ingest", "--fixtures", str(conversacion)])
        assert main(["--db", db, "transcribe"]) == 1
        assert "FALLO: audio corrupto" in capsys.readouterr().err
