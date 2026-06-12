#!/usr/bin/env bash
# VoiceTuner local dev launcher.
# Uses local PostgreSQL (not Docker). Backend runs in Docker.
#
# Usage:  ./start.sh
# Stop:   Ctrl+C  (shuts down the backend Docker container)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# ── colours ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'
BOLD='\033[1m'; RESET='\033[0m'

info()    { echo -e "${CYAN}[start]${RESET} $*"; }
success() { echo -e "${GREEN}[start]${RESET} $*"; }
warn()    { echo -e "${YELLOW}[start]${RESET} $*"; }
error()   { echo -e "${RED}[start]${RESET} $*" >&2; }

# ── cleanup on Ctrl+C / exit ─────────────────────────────────────────────────
cleanup() {
  echo ""
  info "Stopping backend container..."
  docker compose -f docker-compose.dev.yml down --remove-orphans 2>/dev/null || true
  exit 0
}
trap cleanup INT TERM

# ── preflight: Docker ─────────────────────────────────────────────────────────
if ! command -v docker &>/dev/null; then
  error "Docker not found. Install Docker Desktop from https://docker.com"
  exit 1
fi
if ! docker info &>/dev/null; then
  error "Docker daemon is not running. Please start Docker Desktop and try again."
  exit 1
fi
if ! command -v bun &>/dev/null; then
  error "bun not found. Install from https://bun.sh"
  exit 1
fi

# ── environment setup ────────────────────────────────────────────────────────
if [[ ! -f .env ]]; then
  cp .env.example .env
  warn "Created .env from .env.example — add your SARVAM_API_KEY for Hindi/Telugu."
fi
set -a; source .env 2>/dev/null || true; set +a

# ── resolve PostgreSQL connection ─────────────────────────────────────────────
# Prefer .env overrides; fall back to EDB defaults
PG_HOST="${POSTGRES_HOST:-127.0.0.1}"
PG_PORT="${POSTGRES_PORT:-5432}"
PG_USER="${POSTGRES_USER:-postgres}"
PG_PASS="${POSTGRES_PASSWORD:-}"

# Resolve host.docker.internal → 127.0.0.1 for local psql calls
LOCAL_PG_HOST="$PG_HOST"
if [[ "$LOCAL_PG_HOST" == "host.docker.internal" ]]; then
  LOCAL_PG_HOST="127.0.0.1"
fi

# Find psql binary
PSQL=""
for candidate in \
  /Library/PostgreSQL/16/bin/psql \
  /Library/PostgreSQL/15/bin/psql \
  /opt/homebrew/bin/psql \
  /usr/local/bin/psql \
  psql; do
  if [[ -f "$candidate" ]] || command -v "$candidate" &>/dev/null 2>&1; then
    PSQL="$candidate"
    break
  fi
done

if [[ -z "$PSQL" ]]; then
  error "psql not found. Install PostgreSQL:"
  error "  macOS (EDB):  https://www.enterprisedb.com/downloads/postgres-postgresql-downloads"
  error "  macOS (brew): brew install postgresql@16"
  exit 1
fi

# Check postgres is reachable
if ! PGPASSWORD="$PG_PASS" "$PSQL" -U "$PG_USER" -h "$LOCAL_PG_HOST" -p "$PG_PORT" -c '\q' &>/dev/null 2>&1; then
  error "Cannot connect to PostgreSQL at $LOCAL_PG_HOST:$PG_PORT as $PG_USER."
  error "Start PostgreSQL and ensure the credentials in .env are correct."
  exit 1
fi
info "PostgreSQL is running at $LOCAL_PG_HOST:$PG_PORT."

# ── create voicetuner role + database if missing ──────────────────────────────
PGPASSWORD="$PG_PASS" "$PSQL" -U "$PG_USER" -h "$LOCAL_PG_HOST" -p "$PG_PORT" << SQL
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'voicetuner') THEN
    CREATE ROLE voicetuner WITH LOGIN PASSWORD 'voicetuner_dev';
  END IF;
END \$\$;
SQL

DB_EXISTS=$(PGPASSWORD="$PG_PASS" "$PSQL" -U "$PG_USER" -h "$LOCAL_PG_HOST" -p "$PG_PORT" \
  -tAc "SELECT 1 FROM pg_database WHERE datname='voicetuner'" 2>/dev/null || echo "")
if [[ "$DB_EXISTS" != "1" ]]; then
  PGPASSWORD="$PG_PASS" "$PSQL" -U "$PG_USER" -h "$LOCAL_PG_HOST" -p "$PG_PORT" \
    -c "CREATE DATABASE voicetuner OWNER voicetuner;" &>/dev/null
  info "Created database 'voicetuner'."
fi

# ── allow Docker containers to reach local PostgreSQL ─────────────────────────
# Docker Desktop on macOS routes container traffic through 192.168.65.0/24
# We use adminpack's pg_file_write to append the rule to pg_hba.conf (once).
DOCKER_CIDR="192.168.65.0/24"
PGPASSWORD="$PG_PASS" "$PSQL" -U "$PG_USER" -h "$LOCAL_PG_HOST" -p "$PG_PORT" << SQL 2>/dev/null || true
DO \$\$
DECLARE
  hba_path text;
  hba_content text;
  docker_rule text;
BEGIN
  SELECT setting INTO hba_path FROM pg_settings WHERE name = 'hba_file';
  hba_content := pg_read_file(hba_path);
  docker_rule := 'host  voicetuner  voicetuner  $DOCKER_CIDR  scram-sha-256';
  IF hba_content NOT LIKE '%$DOCKER_CIDR%' THEN
    PERFORM pg_file_write(hba_path, E'\n' || docker_rule || E'\n', true);
    PERFORM pg_reload_conf();
    RAISE NOTICE 'pg_hba.conf updated for Docker access and reloaded.';
  END IF;
END \$\$;
SQL

success "Database 'voicetuner' is ready."

# ── frontend deps ────────────────────────────────────────────────────────────
if [[ ! -d node_modules ]]; then
  info "Installing frontend dependencies (one-time)..."
  bun install
fi

# ── build DATABASE_URL for the backend container ─────────────────────────────
# The container must reach the host's PostgreSQL via host.docker.internal.
DOCKER_PG_HOST="host.docker.internal"
CONTAINER_DB_URL="postgresql://voicetuner:voicetuner_dev@${DOCKER_PG_HOST}:${PG_PORT}/voicetuner"
export DATABASE_URL="$CONTAINER_DB_URL"

# ── docker: backend only ─────────────────────────────────────────────────────
info "Starting backend (first run builds the Docker image — ~3 min)..."
docker compose -f docker-compose.dev.yml up --build -d

info "Waiting for backend to be ready..."
ATTEMPTS=0
until curl -sf http://localhost:17493/health &>/dev/null; do
  ATTEMPTS=$((ATTEMPTS + 1))
  if [[ $ATTEMPTS -ge 60 ]]; then
    error "Backend did not become healthy after 2 minutes."
    error "Check logs: docker compose -f docker-compose.dev.yml logs voicetuner"
    cleanup
  fi
  sleep 2
done

success "Backend is ready."

# ── print connection info ─────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo -e "${GREEN}  VoiceTuner is running${RESET}"
echo ""
echo -e "  Web UI    →  ${CYAN}http://localhost:5173${RESET}"
echo -e "  API       →  ${CYAN}http://localhost:17493${RESET}"
echo -e "  API docs  →  ${CYAN}http://localhost:17493/docs${RESET}"
echo -e "  Database  →  ${CYAN}localhost:${PG_PORT}${RESET}  (voicetuner / voicetuner_dev)"
echo ""
echo -e "  Press ${BOLD}Ctrl+C${RESET} to stop."
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo ""

cd web && bun run dev
