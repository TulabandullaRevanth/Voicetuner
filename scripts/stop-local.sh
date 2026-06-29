#!/usr/bin/env bash
# Stop VoiceTuner WITHOUT losing data.
# Containers are stopped (not removed); the Postgres volume `voicetuner_pgdata`
# and ./data are preserved. Re-run ./scripts/run-local.sh to start again.
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="$HOME/.bun/bin:$HOME/homebrew/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

echo "[stop] stopping web (vite)…"
pkill -f "node .*vite" 2>/dev/null || true
lsof -ti:5173 2>/dev/null | xargs -r kill 2>/dev/null || true

echo "[stop] stopping backend container…"
docker compose -f docker-compose.dev.yml stop 2>/dev/null || true

echo "[stop] stopping Postgres (data kept in volume voicetuner_pgdata)…"
docker stop voicetuner-pg 2>/dev/null || true

echo "[stop] done. Data preserved. Start again with: ./scripts/run-local.sh"
echo "       (Do NOT run 'docker rm voicetuner-pg' or 'docker volume rm voicetuner_pgdata' — that deletes the database.)"
