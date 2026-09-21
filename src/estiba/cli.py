"""Interfaz de linea de comandos.

En la Fase 1 hace tres cosas: cargar fixtures, listar conversaciones y
mostrar una reconstruida desde la base.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from estiba.adapters.fixtures import FIXTURES_DIR, FixtureMessageSource
from estiba.conversation.store import ConversationStore
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
    print(f"{len(pendientes)} audios esperando al AudioPipeline (Fase 2):")
    for m in pendientes:
        existe = "" if Path(m.media_path or "").exists() else "  [FALTA EL ARCHIVO]"
        print(f"  {m.id}  {m.media_path}{existe}")
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

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
