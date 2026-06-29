#!/usr/bin/env bash
# Start the offline Voicebox stack (backend stub + web UI) and keep running.
# Your saved data (voices, stories, TTS history, transcriptions) is loaded from
# data/offline-stub/state.json — nothing is reseeded as long as that file exists.
#
#   ./scripts/start-offline.sh
#
# Open http://localhost:5173  (Ctrl+C stops both servers).
set -e
cd "$(dirname "$0")/.."
export PATH="$HOME/.bun/bin:$PATH"

STUB_PY="data/offline-stub/.venv-stt/bin/python"   # venv has faster-whisper for STT
[ -x "$STUB_PY" ] || STUB_PY="python3"             # fallback (STT falls back to canned)

echo "[start] launching offline backend stub (TTS via say, STT via whisper)…"
"$STUB_PY" scripts/offline-stub-server.py &
STUB_PID=$!

echo "[start] waiting for backend on :17493…"
for _ in $(seq 1 30); do
  curl -s -o /dev/null http://127.0.0.1:17493/health && break || sleep 1
done

echo "[start] launching web UI (vite) on :5173…"
( cd web && bun run dev ) &
WEB_PID=$!

cleanup() { echo; echo "[start] stopping…"; kill "$STUB_PID" "$WEB_PID" 2>/dev/null || true; }
trap cleanup INT TERM

echo "[start] ready → http://localhost:5173   (backend http://127.0.0.1:17493)"
wait
