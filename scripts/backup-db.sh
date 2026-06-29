#!/usr/bin/env bash
# Back up the VoiceTuner database to ./backups (run this periodically / before risky changes).
# Produces a timestamped dump plus updates backups/voicetuner.dump (used by run-local.sh restore).
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="$HOME/homebrew/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
mkdir -p backups

docker exec voicetuner-pg pg_isready -U postgres >/dev/null 2>&1 || { echo "Postgres (voicetuner-pg) is not running."; exit 1; }

STAMP="$(docker exec voicetuner-pg date -u +%Y%m%d-%H%M%S)"
docker exec voicetuner-pg pg_dump -U postgres -d voicetuner -Fc > "backups/voicetuner-${STAMP}.dump"
cp "backups/voicetuner-${STAMP}.dump" backups/voicetuner.dump
echo "[backup] wrote backups/voicetuner-${STAMP}.dump (and refreshed backups/voicetuner.dump)"
ls -lh backups/voicetuner.dump
