#!/usr/bin/env bash
# VoiceTuner Docker entrypoint.
#
# Pre-downloads the Whisper STT model before the server starts so that
# the first dictation/transcription request is not blocked by a download.
# The model is cached in the whisper-cache volume and survives restarts.
#
# Set VOICETUNER_WHISPER_PREFETCH=skip to disable (e.g. in air-gapped envs
# where models are pre-seeded into the volume manually).

set -euo pipefail

MODEL_SIZE="${VOICETUNER_WHISPER_PREFETCH:-base}"

if [[ "$MODEL_SIZE" != "skip" ]]; then
    echo "[entrypoint] Pre-fetching Whisper '${MODEL_SIZE}' model…"
    python3 - <<'PYEOF'
import os, sys

model_size = os.environ.get("VOICETUNER_WHISPER_PREFETCH", "base")
try:
    import whisper
    # load_model downloads weights to ~/.cache/whisper if not present
    whisper.load_model(model_size)
    print(f"[entrypoint] Whisper '{model_size}' ready.")
except ImportError:
    # mlx-audio whisper — model is downloaded lazily by the backend
    print("[entrypoint] openai-whisper not found; using platform backend (mlx/transformers).")
except Exception as exc:
    print(f"[entrypoint] Warning: could not pre-fetch Whisper model: {exc}", file=sys.stderr)
    print("[entrypoint] Server will attempt model download on first request.", file=sys.stderr)
PYEOF
else
    echo "[entrypoint] VOICETUNER_WHISPER_PREFETCH=skip — skipping model pre-fetch."
fi

exec "$@"
