# ============================================================
# VoiceTuner — Local TTS Server with Web UI (CPU)
# 3-stage build: Frontend → Python deps → Runtime
# ============================================================

# === Stage 1: Build frontend ===
FROM oven/bun:1 AS frontend

WORKDIR /build

# Copy workspace config and frontend source
COPY package.json bun.lock CHANGELOG.md ./
COPY app/ ./app/
COPY web/ ./web/

# Strip workspaces not needed for web build, and fix trailing comma
RUN sed -i '/"tauri"/d; /"landing"/d' package.json && \
    sed -i -z 's/,\n  ]/\n  ]/' package.json
RUN bun install --no-save
# Build frontend (skip tsc — upstream has pre-existing type errors)
RUN cd web && bunx --bun vite build


# === Stage 2: Build Python dependencies ===
FROM python:3.11-slim AS backend-builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir --upgrade pip

COPY backend/requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt
RUN pip install --no-cache-dir --prefix=/install --no-deps chatterbox-tts
RUN pip install --no-cache-dir --prefix=/install --no-deps hume-tada
RUN pip install --no-cache-dir --prefix=/install \
    git+https://github.com/QwenLM/Qwen3-TTS.git


# === Stage 3: Runtime ===
FROM python:3.11-slim

# Create non-root user for security
RUN groupadd -r voicetuner && \
    useradd -r -g voicetuner -m -s /bin/bash voicetuner

WORKDIR /app

# Install only runtime system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy installed Python packages from builder stage
COPY --from=backend-builder /install /usr/local

# Copy backend application code
COPY --chown=voicetuner:voicetuner backend/ /app/backend/

# Copy built frontend from frontend stage
COPY --from=frontend --chown=voicetuner:voicetuner /build/web/dist /app/frontend/

# Create data directories and model cache directories owned by non-root user
RUN mkdir -p /app/data/generations /app/data/profiles /app/data/cache \
    && chown -R voicetuner:voicetuner /app/data \
    && mkdir -p /home/voicetuner/.cache/huggingface /home/voicetuner/.cache/whisper \
    && chown -R voicetuner:voicetuner /home/voicetuner/.cache

# Copy the entrypoint script
COPY --chown=voicetuner:voicetuner scripts/docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod +x /app/docker-entrypoint.sh

# Switch to non-root user
USER voicetuner

# Expose the API port
EXPOSE 17493

# Health check — start_period is extended to allow the Whisper model
# pre-fetch (entrypoint) to complete before the first health probe.
HEALTHCHECK --interval=30s --timeout=10s --retries=3 --start-period=120s \
    CMD curl -f http://localhost:17493/health || exit 1

# Entrypoint pre-fetches the Whisper model; CMD starts the server.
ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "17493"]
