"""Interfaz de linea de comandos.

Carga fixtures, lista conversaciones, muestra una reconstruida desde la base
transcribe los audios pendientes con Whisper local y extrae los datos del
flete de cada conversacion, con la evidencia de donde salio cada uno, y arma
el flete: estado, historia, precio sugerido y que preguntarle al cliente.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

from estiba.adapters.fixtures import FIXTURES_DIR, FixtureMessageSource
from estiba.adapters.whisper import FasterWhisperTranscriber
from estiba.audio.pipeline import AudioPipeline
from estiba.audio.wer import comparar, wer_total
from estiba.conversation.store import ConversationStore
from estiba.extraction.extractor import SlotExtractor
from estiba.extraction.fechas import nombre_dia
from estiba.formato import pesos
from estiba.jobs.engine import JobEngine
from estiba.jobs.preguntas import ETIQUETAS
from estiba.jobs.pricing import TARIFA_DEFAULT, PricingEngine, Tarifa
from estiba.storage.db import DEFAULT_DB, connect
from estiba.storage.repositories import ClaseRepository, JobRepository


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


def cmd_clases(args: argparse.Namespace) -> int:
    """Como viene clasificando Estiba, y donde la corregiste.

    Las correcciones son el material para afinar las reglas: cada una dice
    que creyo Estiba y que resulto ser en realidad.
    """
    conn = connect(args.db)
    try:
        repo = ClaseRepository(conn)
        todas = repo.todas()
        if not todas:
            print("todavia no hay nada clasificado: corre `estiba serve` una vez")
            return 0
        cuenta: dict[tuple[str, str], int] = {}
        for fila in todas.values():
            cuenta[(fila["clase"], fila["quien"])] = cuenta.get((fila["clase"], fila["quien"]), 0) + 1
        print(f"{len(todas)} conversaciones clasificadas")
        for (clase, quien), n in sorted(cuenta.items(), key=lambda x: -x[1]):
            print(f"  {clase:9} {n:4}  ({'lo dijiste vos' if quien == 'humano' else 'lo dedujo Estiba'})")

        correcciones = repo.correcciones()
        print()
        if not correcciones:
            print("sin correcciones todavia: Estiba no se equivoco, o no la corregiste")
            return 0
        print(f"{len(correcciones)} veces que Estiba se equivoco:")
        for fila in correcciones:
            print(f"  {fila['chat_id']:18} creyo {fila['clase_previa']:9} -> era {fila['clase']}")
    finally:
        conn.close()
    return 0


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
        return f"{nombre_dia(v)} {v:%d/%m/%Y}"
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
        linea = f"cotizado {pesos(cotizado.value)}" if cotizado else "sin cotizar"
        if p is not None:
            linea += f" | sugerido {pesos(p.total)}"
            if not p.completo:
                linea += " o mas"
        print(f"  precio: {linea}")
        if p is not None:
            print("    " + " + ".join(f"{c} {pesos(m)}" for c, m in p.desglose))
            for falta in p.sin_contar:
                print(f"    sin contar: {falta}")
        for alerta in ev.alertas:
            print(f"  ALERTA: {alerta}")
        if ev.preguntas:
            print("  para preguntar (borrador, no se envia):")
            for pregunta in ev.preguntas:
                print(f"    {pregunta}")
    return 0


def _ahora(args: argparse.Namespace):
    """Un reloj: el real, o uno fijo si se paso --ahora (demos y pruebas)."""
    if getattr(args, "ahora", None):
        fijo = datetime.fromisoformat(args.ahora)
        return lambda: fijo
    return datetime.now


def cmd_sync(args: argparse.Namespace) -> int:
    from estiba.bandeja.servicio import sincronizar
    from estiba.storage.repositories import DraftRepository

    from estiba.agenda.caldav import ConfigCalDAV, EspejoCalDAV

    conn = connect(args.db)
    engine = JobEngine(pricing=PricingEngine(Tarifa.desde_toml(args.tarifa)))
    config = ConfigCalDAV.desde_entorno()
    r = sincronizar(conn, engine, _ahora(args)(), EspejoCalDAV(config) if config else None)
    pendientes = DraftRepository(conn).pending()
    print(f"{len(r.fletes)} fletes evaluados, {len(pendientes)} borradores esperan aprobacion")
    for job_id, cambio in r.agenda:
        print(f"  agenda: {job_id} {cambio}")
    if r.espejo_error:
        print(f"  no se pudo copiar la agenda al CalDAV: {r.espejo_error}", file=sys.stderr)
        return 1
    return 0


_LOCALES = ("127.0.0.1", "localhost", "::1")


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from estiba.web.app import create_app

    clave = args.clave or os.environ.get("ESTIBA_CLAVE")
    if args.host not in _LOCALES and not clave:
        print(
            f"Abrir el dashboard en {args.host} lo expone a la red con los datos de tus "
            "clientes. Pone una clave con --clave o ESTIBA_CLAVE.",
            file=sys.stderr,
        )
        return 2
    engine = JobEngine(pricing=PricingEngine(Tarifa.desde_toml(args.tarifa)))
    app = create_app(args.db, engine=engine, ahora=_ahora(args), clave=clave)
    sockets = [_escuchar(args.host, args.port)]
    print(f"Estiba en http://{args.host}:{args.port}  (API en /docs)")

    # WAHA corre en Docker y llega al host por la interfaz de Docker, no por
    # 127.0.0.1. Ahi se abre una segunda puerta que solo atiende webhooks.
    ip_docker = os.environ.get("ESTIBA_IP_DOCKER", "172.17.0.1")
    if os.environ.get("ESTIBA_WAHA_URL") and args.host in _LOCALES:
        try:
            sockets.append(_escuchar(ip_docker, args.port))
            app.state.puertas_webhook = {ip_docker}
            print(f"Webhooks de WhatsApp en http://{ip_docker}:{args.port}/webhooks/ (solo desde Docker)")
        except OSError as e:
            print(f"No se pudo escuchar en {ip_docker} para los webhooks ({e}). "
                  "Revisa ESTIBA_IP_DOCKER.", file=sys.stderr)

    servidor = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
    servidor.run(sockets=sockets)
    return 0


def _escuchar(host: str, puerto: int):
    import socket

    familia = socket.AF_INET6 if ":" in host else socket.AF_INET
    s = socket.socket(familia, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((host, puerto))
    s.listen(128)
    s.set_inheritable(True)
    return s


def _whatsapp():
    from estiba.adapters.waha import ConfigWaha, WahaClient
    from estiba.whatsapp import WhatsApp

    config = ConfigWaha.desde_entorno()
    if config is None:
        print("WhatsApp no esta configurado: defini ESTIBA_WAHA_URL (ver .env.example)", file=sys.stderr)
        return None
    return WhatsApp(WahaClient(config))


def cmd_waha(args: argparse.Namespace) -> int:
    from estiba.adapters.waha import ErrorWaha
    from estiba.storage.repositories import SettingsRepository

    w = _whatsapp()
    if w is None:
        return 2
    conn = connect(args.db)
    try:
        if args.accion == "estado":
            info = w.cliente.sesion()
            if info is None:
                print("Sin sesion. Conectala con: estiba waha conectar")
                return 1
            me = (info.get("me") or {}).get("id", "")
            print(f"{info['status']}{'  ' + me if me else ''}")
            return 0 if info["status"] == "WORKING" else 1
        if args.accion == "conectar":
            secreto = SettingsRepository(conn).secreto("webhook_waha")
            info = w.cliente.conectar(secreto)
            print(f"Sesion {info.get('status')}. Escanea el QR desde el dashboard: /whatsapp")
            return 0
        if args.accion == "importar":
            desde = datetime.now() - timedelta(days=args.dias)
            nuevos = w.importar(conn, desde)
            print(f"{nuevos} mensajes nuevos de los ultimos {args.dias} dias")
            reporte = w.transcribir(conn)
            if reporte is not None:
                print(f"{len(reporte.transcriptos)} audios transcriptos")
            return 0
    except ErrorWaha as e:
        print(e, file=sys.stderr)
        return 1
    finally:
        conn.close()
    return 2


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

    y = sub.add_parser("sync", help="evaluar todo y dejar los borradores en la bandeja")
    y.add_argument("--ahora", help="evaluar como si fuera esta fecha (ISO)")
    y.add_argument("--tarifa", default=str(TARIFA_DEFAULT))
    y.set_defaults(func=cmd_sync)

    v = sub.add_parser("serve", help="levantar el dashboard y la API")
    v.add_argument("--host", default="127.0.0.1", help="0.0.0.0 para abrirlo desde el celular (pide clave)")
    v.add_argument("--port", type=int, default=8000)
    v.add_argument("--clave", help="clave para entrar; tambien por ESTIBA_CLAVE")
    v.add_argument("--ahora", help="congelar el reloj en esta fecha (ISO), para demos")
    v.add_argument("--tarifa", default=str(TARIFA_DEFAULT))
    v.set_defaults(func=cmd_serve)

    k = sub.add_parser("waha", help="WhatsApp por WAHA: estado, conectar, importar historial")
    k.add_argument("accion", choices=["estado", "conectar", "importar"])
    k.add_argument("--dias", type=int, default=30, help="para importar: cuantos dias para atras")
    k.set_defaults(func=cmd_waha)

    cl = sub.add_parser("clases", help="como viene clasificando y donde la corregiste")
    cl.set_defaults(func=cmd_clases)

    w = sub.add_parser("wer", help="tasa de error contra la transcripcion esperada")
    w.add_argument("--fixtures", default=str(FIXTURES_DIR))
    w.set_defaults(func=cmd_wer)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


def ejecutar() -> int:
    """El comando `estiba`: lee el .env (el mismo que usa docker compose) y corre."""
    from estiba.entorno import cargar_env

    cargar_env()
    return main()


if __name__ == "__main__":
    raise SystemExit(ejecutar())
