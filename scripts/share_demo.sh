#!/usr/bin/env bash
# Share a LIVE demo from your own machine through a temporary public link (Cloudflare quick tunnel: free, no account, no card).
#   sh scripts/share_demo.sh            starts the app if it is not running, then prints a https://....trycloudflare.com link
# Use it for a screen recording or a judging window. The link works only while this command is running on your machine, and it is different every time,
# so do NOT paste it into the README or commit it. Ctrl+C closes the link (the app keeps running; stop it with `docker compose down`).
# Anyone with the link can use the chat endpoint, which spends your Gemini/Groq quota: the per-visitor limits (ASK_PER_IP_PER_HOUR, ASK_PER_DAY) still apply.
set -euo pipefail
cd "$(dirname "$0")/.."
PORT="${PORT:-7860}"

command -v docker >/dev/null || { echo "Docker is not installed or not running."; exit 1; }
if ! command -v cloudflared >/dev/null; then
  cat <<'MSG'
cloudflared is not installed. Install it once (no account needed):
  Debian/Ubuntu/WSL:  curl -L --output cloudflared.deb https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb && sudo dpkg -i cloudflared.deb
  macOS:              brew install cloudflared
  Windows:            winget install --id Cloudflare.cloudflared
then run this script again.
MSG
  exit 1
fi

if ! curl -sf "http://localhost:${PORT}/healthz" >/dev/null 2>&1; then
  echo "Starting Lineage (docker compose up --build -d) ..."
  docker compose up --build -d
  printf "Waiting for it to be healthy"
  for _ in $(seq 1 120); do curl -sf "http://localhost:${PORT}/healthz" >/dev/null 2>&1 && break; printf "."; sleep 2; done
  echo
fi
curl -sf "http://localhost:${PORT}/healthz" >/dev/null || { echo "Lineage did not become healthy on port ${PORT}; check: docker compose logs"; exit 1; }

echo "Lineage is up on http://localhost:${PORT}. Opening a temporary public link (look for the https://...trycloudflare.com line below) ..."
exec cloudflared tunnel --url "http://localhost:${PORT}"
