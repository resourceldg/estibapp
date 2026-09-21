"""Interfaz de linea de comandos.

Carga fixtures, lista conversaciones, muestra una reconstruida desde la base
transcribe los audios pendientes con Whisper local y extrae los datos del
flete de cada conversacion, con la evidencia de donde salio cada uno, y arma
el flete: estado, historia, precio sugerido y que preguntarle al cliente.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path

from estiba.adapters.fixtures import FIXTURES_DIR, FixtureMessageSource
from estiba.adapters.whisper import FasterWhisperTranscriber
from estiba.audio.pipeline import AudioPipeline
from estiba.audio.wer import comparar, wer_total
from estiba.conversation.store import ConversationStore
from estiba.extraction.extractor import SlotExtractor
from estiba.extraction.fechas import NOMBRE_DIA
from estiba.jobs.engine import JobEngine
from estiba.jobs.preguntas import ETIQUETAS
from estiba.jobs.pricing import TARIFA_DEFAULT, PricingEngine, Tarifa
from estiba.storage.repositories import JobRepository
from estiba.storage.db import DEFAULT_DB, connect


def cmd_ingest(args: argparse.Namespace) -> int:
    conn = connect(args.db)
    store = ConversationStore(conn)
    fuente = FixtureMessageSource(args.fixtures)
    mensajes = list(fuente.poll())
    nuevos = store.ingest(mensajes)
    print(f"{len(mensajes)} mensajes leidos, {nuevos} nuevos, {len(mensajes) - nuevos} ya estaban")
    for chat_id in store.chat_ids():
        cant = len(store.get(chat_id).messages)
        print(f"  {chat_id}  {cant} mensajes")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    conn = connect(args.db)
    store = ConversationStore(conn)
    ids = store.chat_ids()
    if not ids:
        print("No hay conversaciones. Corre primero: estiba ingest")
        return 1
    for chat_id in ids:
        conv = store.get(chat_id)
        sin_transcribir = sum(
            1 for m in conv.messages if conv.text_of(m) is None
        )
        pendiente = f", {sin_transcribir} sin transcribir" if sin_transcribir else ""
        print(f"{chat_id}  {len(conv.messages)} mensajes{pendiente}")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    conn = connect(args.db)
    store = ConversationStore(conn)
    conv = store.get(args.chat_id)
    if not conv.messages:
        print(f"No hay mensajes para {args.chat_id}", file=sys.stderr)
        return 1
    print(f"--- {conv.chat_id} ({len(conv.messages)} mensajes) ---")
    print(conv.as_plain_text())
    return 0


def cmd_pending_audio(args: argparse.Namespace) -> int:
    conn = connect(args.db)
    store = ConversationStore(conn)
    pendientes = store.pending_audio()
    if not pendientes:
        print("No hay audios pendientes de transcribir")
        return 0
    print(f"{len(pendientes)} audios esperando. Transcribilos con: estiba transcribe")
    for m in pendientes:
        existe = "" if Path(m.media_path or "").exists() else "  [FALTA EL ARCHIVO]"
        print(f"  {m.id}  {m.media_path}{existe}")
    return 0


def crear_transcriber(args: argparse.Namespace) -> FasterWhisperTranscriber:
    return FasterWhisperTranscriber(model_size=args.modelo, device=args.device)


def cmd_transcribe(args: argparse.Namespace) -> int:
    conn = connect(args.db)
    store = ConversationStore(conn)
    if not args.comparar and not store.pending_audio():
        print("No hay audios pendientes de transcribir")
        return 0
    transcriber = crear_transcriber(args)
    modelo = transcriber.nombre if args.comparar else None
    reporte = AudioPipeline(store, transcriber).run(limite=args.limite, modelo=modelo)

    for t in reporte.transcriptos:
        print(f"  {t.message_id}  [{t.confidence:.2f}]  {t.text}")
    for m in reporte.sin_archivo:
        print(f"  {m.id}  FALTA EL ARCHIVO {m.media_path}", file=sys.stderr)
    for m, error in reporte.fallidos:
        print(f"  {m.id}  FALLO: {error}", file=sys.stderr)
    print(
        f"{len(reporte.transcriptos)} transcriptos, "
        f"{len(reporte.sin_archivo)} sin archivo, {len(reporte.fallidos)} fallidos"
    )
    return 1 if reporte.fallidos else 0


def cmd_wer(args: argparse.Namespace) -> int:
    conn = connect(args.db)
    store = ConversationStore(conn)
    comparaciones = []
    for archivo in sorted(Path(args.fixtures).glob("*.json")):
        for message_id, esperada in FixtureMessageSource.expected_transcripts(archivo).items():
            transcripciones = list(store.transcripciones.all_for_message(message_id))
            if not transcripciones:
                print(f"  {message_id}  sin transcribir")
                continue
            for t in transcripciones:
                c = comparar(esperada, t.text)
                comparaciones.append(c)
                print(f"  {message_id}  {t.model}  WER {c.wer:.1%}  ({c.errores}/{c.palabras})")
    if not comparaciones:
        print("No hay audios transcriptos con transcripcion esperada")
        return 1
    print(f"WER total: {wer_total(comparaciones):.1%}")
    return 0


def _valor(v: object) -> str:
    if isinstance(v, list):
        return ", ".join(map(str, v))
    if isinstance(v, date):
        return f"{NOMBRE_DIA[v.weekday()]} {v:%d/%m/%Y}"
    if isinstance(v, time):
        return f"{v:%H:%M}"
    return str(v)


def cmd_extract(args: argparse.Namespace) -> int:
    conn = connect(args.db)
    store = ConversationStore(conn)
    ids = [args.chat_id] if args.chat_id else store.chat_ids()
    if not ids:
        print("No hay conversaciones. Corre primero: estiba ingest")
        return 1
    extractor = SlotExtractor()
    for chat_id in ids:
        conv = store.get(chat_id)
        if not conv.messages:
            print(f"No hay mensajes para {chat_id}", file=sys.stderr)
            return 1
        ex = extractor.extract(conv)
        print(f"--- {chat_id} ---")
        for campo, etiqueta in ETIQUETAS.items():
            c = ex.ganadores.get(campo)
            if c is None:
                continue
            ev = c.slot.evidence
            print(f"  {etiqueta:17} {_valor(c.slot.value):32} [{c.slot.confidence:.2f}]  "
                  f"{ev.message_id}  \"{ev.quote}\"")
        if ex.job.missing:
            faltan = ", ".join(ETIQUETAS[f] for f in ex.job.missing)
            print(f"  FALTA: {faltan}")
        for duda in ex.dudas:
            print(f"  DUDA: {duda}")
        if ex.sin_texto:
            print(f"  AUDIOS SIN TRANSCRIBIR: {', '.join(ex.sin_texto)}")
        for i in ex.intenciones:
            print(f"  {i.intencion.value:9} {i.message_id}  \"{i.cita}\"")
    return 0


def _pesos(monto: Decimal | int) -> str:
    return f"${int(monto):,}".replace(",", ".")


def cmd_jobs(args: argparse.Namespace) -> int:
    conn = connect(args.db)
    store = ConversationStore(conn)
    repo = JobRepository(conn)
    ids = [args.chat_id] if args.chat_id else store.chat_ids()
    if not ids:
        print("No hay conversaciones. Corre primero: estiba ingest")
        return 1
    ahora = datetime.fromisoformat(args.ahora) if args.ahora else datetime.now()
    engine = JobEngine(pricing=PricingEngine(Tarifa.desde_toml(args.tarifa)))

    for chat_id in ids:
        conv = store.get(chat_id)
        if not conv.messages:
            print(f"No hay mensajes para {chat_id}", file=sys.stderr)
            return 1
        ev = engine.evaluar(conv, ahora)
        repo.save(ev.job, ev.eventos)

        print(f"--- {chat_id}  {ev.job.state.value.upper()} ---")
        for e in ev.eventos:
            desde = e.desde.value if e.desde else "-"
            print(f"  {e.at:%d/%m %H:%M}  {desde} -> {e.hacia.value:18} {e.causa}  ({e.message_id})")

        cotizado = ev.job.quoted_price
        p = ev.presupuesto
        linea = f"cotizado {_pesos(cotizado.value)}" if cotizado else "sin cotizar"
        if p is not None:
            linea += f" | sugerido {_pesos(p.total)}"
            if not p.completo:
                linea += " o mas"
        print(f"  precio: {linea}")
        if p is not None:
            print("    " + " + ".join(f"{c} {_pesos(m)}" for c, m in p.desglose))
            for falta in p.sin_contar:
                print(f"    sin contar: {falta}")
        for alerta in ev.alertas:
            print(f"  ALERTA: {alerta}")
        if ev.preguntas:
            print("  para preguntar (borrador, no se envia):")
            for pregunta in ev.preguntas:
                print(f"    {pregunta}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="estiba", description=__doc__)
    p.add_argument("--db", default=str(DEFAULT_DB), help="ruta de la base SQLite")
    sub = p.add_subparsers(dest="comando", required=True)

    i = sub.add_parser("ingest", help="cargar fixtures a la base")
    i.add_argument("--fixtures", default=str(FIXTURES_DIR))
    i.set_defaults(func=cmd_ingest)

    l = sub.add_parser("list", help="listar conversaciones")
    l.set_defaults(func=cmd_list)

    s = sub.add_parser("show", help="mostrar una conversacion reconstruida")
    s.add_argument("chat_id")
    s.set_defaults(func=cmd_show)

    a = sub.add_parser("pending-audio", help="audios sin transcribir")
    a.set_defaults(func=cmd_pending_audio)

    t = sub.add_parser("transcribe", help="transcribir los audios pendientes")
    t.add_argument("--modelo", default="small", help="tamano de Whisper: base, small, medium...")
    t.add_argument("--device", default="auto", help="auto, cpu o cuda")
    t.add_argument("--limite", type=int, help="transcribir como mucho N audios")
    t.add_argument(
        "--comparar",
        action="store_true",
        help="re-transcribir con este modelo los audios que ya tienen otra transcripcion",
    )
    t.set_defaults(func=cmd_transcribe)

    e = sub.add_parser("extract", help="datos del flete con su evidencia")
    e.add_argument("chat_id", nargs="?", help="sin chat_id, todas las conversaciones")
    e.set_defaults(func=cmd_extract)

    j = sub.add_parser("jobs", help="estado de cada flete, precio sugerido y que falta")
    j.add_argument("chat_id", nargs="?", help="sin chat_id, todas las conversaciones")
    j.add_argument("--ahora", help="evaluar como si fuera esta fecha (ISO), para el silencio")
    j.add_argument("--tarifa", default=str(TARIFA_DEFAULT), help="TOML con los montos")
    j.set_defaults(func=cmd_jobs)

    w = sub.add_parser("wer", help="tasa de error contra la transcripcion esperada")
    w.add_argument("--fixtures", default=str(FIXTURES_DIR))
    w.set_defaults(func=cmd_wer)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
