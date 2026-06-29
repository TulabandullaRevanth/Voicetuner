#!/usr/bin/env bash
# Start VoiceTuner locally: Postgres (persistent) + backend (Docker) + web (Vite).
# Data lives in the Docker volume `voicetuner_pgdata` (survives container removal)
# and in ./data (audio files). Safe to run repeatedly.
#
#   ./scripts/run-local.sh
#   Open http://localhost:5173   (Ctrl+C stops the web server; backend keeps running)
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="$HOME/.bun/bin:$HOME/homebrew/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

PG_PW="Uma@0177"          # postgres superuser password
PG_VOLUME="voicetuner_pgdata"

command -v docker >/dev/null || { echo "docker not found"; exit 1; }
docker info >/dev/null 2>&1 || { echo "Docker Desktop is not running — start it and retry."; exit 1; }
command -v bun >/dev/null || { echo "bun not found (expected at ~/.bun/bin)"; exit 1; }

# ── 1. PostgreSQL (persistent volume) ────────────────────────────────────────
if [ -n "$(docker ps -aq -f name='^voicetuner-pg$')" ]; then
  docker start voicetuner-pg >/dev/null
else
  echo "[run] creating Postgres on persistent volume ${PG_VOLUME}…"
  docker volume create "$PG_VOLUME" >/dev/null
  docker run -d --name voicetuner-pg \
    -e POSTGRES_USER=postgres -e POSTGRES_PASSWORD="$PG_PW" \
    -p 5433:5432 -v "${PG_VOLUME}:/var/lib/postgresql/data" postgres:16 >/dev/null
fi
echo "[run] waiting for Postgres…"
until docker exec voicetuner-pg pg_isready -U postgres >/dev/null 2>&1; do sleep 1; done
sleep 1

# Create role/db + restore from backup ONLY if the database doesn't exist yet
# (i.e. a brand-new empty volume). Existing data is left untouched.
if [ "$(docker exec voicetuner-pg psql -U postgres -tAc "SELECT 1 FROM pg_database WHERE datname='voicetuner'" 2>/dev/null)" != "1" ]; then
  echo "[run] fresh database — initialising + restoring backup…"
  docker exec voicetuner-pg psql -U postgres -c "CREATE ROLE voicetuner LOGIN PASSWORD 'voicetuner_dev';" 2>/dev/null || true
  docker exec voicetuner-pg psql -U postgres -c "CREATE DATABASE voicetuner OWNER voicetuner;" 2>/dev/null || true
  if [ -f backups/voicetuner.dump ]; then
    docker exec -i voicetuner-pg pg_restore -U postgres -d voicetuner --no-owner --role=voicetuner < backups/voicetuner.dump || true
    echo "[run] restored data from backups/voicetuner.dump"
  fi
fi

# ── 2. Backend (Docker, connects to host Postgres on 5433) ───────────────────
export DATABASE_URL="postgresql://voicetuner:voicetuner_dev@host.docker.internal:5433/voicetuner"
echo "[run] starting backend…"
docker compose -f docker-compose.dev.yml up -d
echo "[run] waiting for backend health…"
until curl -sf http://localhost:17493/health >/dev/null 2>&1; do sleep 2; done
echo "[run] backend ready  → http://localhost:17493"

# ── 3. Web UI (Vite, foreground) ─────────────────────────────────────────────
echo "[run] starting web UI → http://localhost:5173   (Ctrl+C to stop)"
cd web && exec bun run dev
