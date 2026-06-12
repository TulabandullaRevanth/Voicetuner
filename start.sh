#!/usr/bin/env bash
# VoiceTuner local dev launcher.
# Starts postgres + backend in Docker, then runs the Vite frontend locally.
#
# Usage:  ./start.sh
# Stop:   Ctrl+C  (shuts down Docker services automatically)

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
  info "Stopping services..."
  docker compose -f docker-compose.dev.yml down --remove-orphans 2>/dev/null || true
  exit 0
}
trap cleanup INT TERM

# ── preflight checks ─────────────────────────────────────────────────────────
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
  warn "Created .env from .env.example"
  warn "Edit .env to add your SARVAM_API_KEY for Hindi/Telugu TTS."
fi

# Load .env so API keys reach Docker Compose
set -a; source .env 2>/dev/null || true; set +a

# ── frontend deps ────────────────────────────────────────────────────────────
if [[ ! -d node_modules ]]; then
  info "Installing frontend dependencies (one-time)..."
  bun install
fi

# ── docker: postgres + backend ───────────────────────────────────────────────
info "Starting postgres + backend (first run builds the Docker image — ~3 min)..."
docker compose -f docker-compose.dev.yml up --build -d

info "Waiting for backend to be ready..."
ATTEMPTS=0
until curl -sf http://localhost:17493/health &>/dev/null; do
  ATTEMPTS=$((ATTEMPTS + 1))
  if [[ $ATTEMPTS -ge 60 ]]; then
    error "Backend did not become healthy after 2 minutes."
    error "Check logs with: docker compose -f docker-compose.dev.yml logs voicetuner"
    cleanup
  fi
  sleep 2
done

success "Backend is ready."

# ── frontend dev server ───────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo -e "${GREEN}  VoiceTuner is running${RESET}"
echo ""
echo -e "  Web UI    →  ${CYAN}http://localhost:5173${RESET}"
echo -e "  API       →  ${CYAN}http://localhost:17493${RESET}"
echo -e "  API docs  →  ${CYAN}http://localhost:17493/docs${RESET}"
echo -e "  DB        →  ${CYAN}localhost:5432${RESET}  (user: voicetuner / voicetuner_dev)"
echo ""
echo -e "  Press ${BOLD}Ctrl+C${RESET} to stop all services."
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo ""

cd web && bun run dev
