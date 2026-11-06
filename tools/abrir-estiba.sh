#!/usr/bin/env bash
# Abre Estiba: si no esta corriendo, levanta WhatsApp (WAHA) y el servidor,
# espera a que responda y abre el navegador. Si ya corre, solo abre la pagina.
set -u
cd "$(dirname "$0")/.." || exit 1
URL="http://127.0.0.1:8001"
LOG="$HOME/.local/state/estiba/serve.log"
mkdir -p "$(dirname "$LOG")"

if ! curl -s -o /dev/null --max-time 2 "$URL"; then
  docker compose up -d waha >>"$LOG" 2>&1 || true
  nohup "$HOME/.local/bin/uv" run estiba serve >>"$LOG" 2>&1 &
  for _ in $(seq 1 90); do
    curl -s -o /dev/null --max-time 2 "$URL" && break
    sleep 1
  done
fi
xdg-open "$URL" >/dev/null 2>&1 &
