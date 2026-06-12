# VoiceTuner v1.0 — Principal Architect Review

**Date:** 2026-06-11  
**Current state:** Functional prototype (0.5.0) with 9 TTS engines, ~50 ML dependencies, 30–90 s cold start  
**Target state:** Production v1.0 — cloud-first, 2 s cold start, ~150 MB binary, commercial-grade

---

## Executive Summary

The current codebase carries enormous local-ML weight it no longer needs. Stripping 7 local TTS engines, local Whisper, and local Qwen LLM in favour of Sarvam + Groq APIs cuts the Python dependency tree by 65 %, the PyInstaller binary from ~4 GB to ~150 MB, and cold-start time from 30–90 s to under 2 s. The architecture itself is sound — the service layer, protocol-based backend abstraction, custom migration framework, and MCP integration are all worth keeping. The work is a focused subtraction, not a rewrite.

---

## 1. What to Keep

### Backend
| Module | Keep | Reason |
|--------|------|--------|
| `backend/app.py` | ✅ | Clean FastAPI factory, good middleware |
| `backend/routes/captures.py` | ✅ | Core dictation flow |
| `backend/routes/generations.py` | ✅ | Voice studio flow |
| `backend/routes/profiles.py` | ✅ | Profile CRUD + preset catalog |
| `backend/routes/effects.py` | ✅ | Pedalboard effects |
| `backend/routes/stories.py` | ✅ | Multi-track timeline |
| `backend/routes/speak.py` | ✅ | MCP agent voice output |
| `backend/routes/mcp_bindings.py` | ✅ | Per-agent voice assignment |
| `backend/routes/settings.py` | ✅ | User preferences |
| `backend/routes/channels.py` | ✅ | Audio output routing |
| `backend/routes/events.py` | ✅ | SSE speak events |
| `backend/routes/health.py` | ✅ | Server health check |
| `backend/routes/history.py` | ✅ | Generation history |
| `backend/routes/audio.py` | ✅ | Audio file serving |
| `backend/routes/transcription.py` | ✅ | Standalone STT |
| `backend/adapters/sarvam.py` | ✅ | Primary TTS + STT |
| `backend/adapters/elevenlabs.py` | ✅ | Premium cloning |
| `backend/adapters/groq.py` | ✅ (extend) | STT today; add LLM chat |
| `backend/adapters/speaker_id.py` | ✅ | Resemblyzer speaker ID |
| `backend/adapters/credentials.py` | ✅ | Key loader |
| `backend/services/captures.py` | ✅ | Capture + speaker ID pipeline |
| `backend/services/profiles.py` | ✅ | Profile + embedding management |
| `backend/services/effects.py` | ✅ | Pedalboard effects |
| `backend/services/stories.py` | ✅ | Timeline |
| `backend/services/versions.py` | ✅ | Generation versioning |
| `backend/services/export_import.py` | ✅ | ZIP import/export |
| `backend/services/channels.py` | ✅ | Audio routing |
| `backend/services/speech_router.py` | ✅ (simplify) | Language → engine routing |
| `backend/services/settings.py` | ✅ | Preferences |
| `backend/services/history.py` | ✅ | History queries |
| `backend/database/migrations.py` | ✅ | Idempotent migration framework |
| `backend/database/models.py` | ✅ (clean) | ORM models |
| `backend/database/session.py` | ✅ | PostgreSQL session factory (psycopg2, connection pooling) |
| `backend/mcp_server/` | ✅ | Full MCP integration |
| `backend/languages.py` | ✅ | en/hi/te constants |
| `backend/utils/audio.py` | ✅ | Audio I/O |
| `backend/utils/effects.py` | ✅ | Pedalboard wrappers |
| `backend/utils/chunked_tts.py` | ✅ | Sentence splitter (still needed for long text) |
| `backend/utils/capture_chords.py` | ✅ | Hotkey defaults |

### Frontend
| Component | Keep |
|-----------|------|
| `VoicesTab/` | ✅ |
| `MainEditor/` | ✅ |
| `CapturesTab/` | ✅ |
| `StoriesTab/` | ✅ |
| `EffectsTab/` | ✅ |
| `DictateWindow/` | ✅ |
| `AudioPlayer/` | ✅ |
| `AudioStudio/` | ✅ |
| `ServerTab/` | ✅ (minus GpuPage) |
| `AppFrame/`, `Sidebar`, `ListPane` | ✅ |
| `AccessibilityGate/`, `InputMonitoringGate/` | ✅ |
| `i18n/locales/en + hi + te` | ✅ |
| All Zustand stores | ✅ |

### Tauri Shell
All Rust modules — keep without changes. The desktop integration is solid.

---

## 2. What to Remove

### Backend — delete these files entirely

```
backend/backends/qwen_backend.py           # Local TTS — replaced by Sarvam
backend/backends/qwen_custom_voice_backend.py
backend/backends/chatterbox_backend.py
backend/backends/chatterbox_turbo_backend.py
backend/backends/hume_backend.py           # TADA (HumeAI)
backend/backends/kokoro_backend.py
backend/backends/luxtts_backend.py
backend/backends/mlx_backend.py
backend/backends/pytorch_backend.py
backend/backends/qwen_llm_backend.py       # Local LLM — replaced by Groq

backend/routes/cuda.py                     # GPU management — irrelevant for cloud
backend/routes/models.py                   # Local model download — no local models
backend/routes/llm.py                      # Local LLM endpoint — Groq replaces it
backend/routes/tasks.py                    # Serial GPU queue status — no longer needed

backend/services/cuda.py                   # GPU management
backend/services/task_queue.py             # Serial GPU queue
backend/services/llm.py                    # Local LLM loader
backend/services/transcribe.py             # Local Whisper loader
backend/services/tts.py                    # Local TTS loader (was delegation shim)

backend/utils/hf_offline_patch.py          # HuggingFace patches
backend/utils/hf_progress.py              # HuggingFace download progress
backend/utils/dac_shim.py                 # Descript Audio Codec shim (TADA dep)
backend/utils/platform_detect.py          # MLX vs PyTorch detection — irrelevant
backend/utils/progress.py                 # HF progress manager
backend/utils/tasks.py                    # Task manager for model downloads
backend/utils/cache.py                    # Profile audio cache (keep only _get_cache_dir)

backend/pyi_hooks/                         # PyInstaller hooks for ML libs
backend/pyi_rth_numpy_compat.py
backend/pyi_rth_torch_compiler_disable.py
```

### Backend — remove these routes from existing files
- `routes/settings.py` — remove `GET/POST /settings/llm` (local LLM model size setting)
- `routes/generations.py` — remove `model_size` parameter from generate request
- `routes/profiles.py` — remove `voice_type = "designed"` path

### Frontend — delete or replace
```
app/src/components/ModelsTab/ModelsTab.tsx   # Replace with Provider Settings page
app/src/components/ServerTab/GpuPage.tsx     # No GPU to manage
```

### Frontend — fields to remove from UI
- Engine selector: remove all local engines (qwen, luxtts, chatterbox, kokoro, tada)
- Model size selector (0.6B, 1.7B etc.) — entirely irrelevant for cloud APIs
- "Download model" buttons
- GPU status / VRAM indicator

### Dependencies to remove from `backend/requirements.txt`
These 27 packages can be deleted:

```
torch
transformers
accelerate
huggingface_hub
qwen-tts
linacodec @ git+...
Zipvoice @ git+...
conformer
diffusers
omegaconf
pykakasi
resemble-perth         # Perth watermark detector — not resemblyzer
s3tokenizer
spacy-pkuseg
pyloudnorm             # Keep only if used outside of TADA
kokoro
misaki[en,ja,zh]
en_core_web_sm @ ...   # spaCy model for misaki
unidic-lite
numba                  # Only needed by librosa for local ML paths
```

**Result:** requirements.txt shrinks from 75 lines → ~25 lines.  
**Binary size:** ~4 GB → ~150 MB.  
**Cold-start time:** 30–90 s → < 2 s.

---

## 3. What to Refactor

### 3.1 `backend/adapters/groq.py` — Add LLM chat alongside STT

Currently Groq is STT-only. Extend it with a chat completions client to replace local Qwen for refinement and personality rewriting.

```python
# backend/adapters/groq.py — add below existing STT class

_CHAT_URL = f"{_BASE_URL.replace('/audio', '')}/chat/completions"
_DEFAULT_LLM_MODEL = os.environ.get("GROQ_LLM_MODEL", "llama-3.3-70b-versatile")

class GroqLLMBackend:
    """Groq chat completions — replaces local Qwen3."""

    async def complete(
        self,
        system_prompt: str,
        user_message: str,
        temperature: float = 0.3,
        max_tokens: int = 1024,
    ) -> str:
        key = _require_key()
        payload = {
            "model": _DEFAULT_LLM_MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                _CHAT_URL,
                json=payload,
                headers={"Authorization": f"Bearer {key}"},
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()
```

### 3.2 `backend/services/refinement.py` — Wire to Groq instead of local LLM

Replace the `from . import llm as llm_service` import chain with a direct call to `GroqLLMBackend`.

```python
# Before
from . import llm as llm_service
result = await llm_service.get_llm_model().generate(prompt, ...)

# After
from ..adapters.groq import GroqLLMBackend
_llm = GroqLLMBackend()
result = await _llm.complete(system_prompt, transcript, temperature=0.2)
```

The `collapse_repetitive_artifacts()` function in `refinement.py` is pure Python — keep it as a pre-pass before the LLM call.

### 3.3 `backend/services/personality.py` — Wire to Groq

Same substitution as refinement. The compose (temperature=0.9) and rewrite (temperature=0.3) modes map directly to `GroqLLMBackend.complete()` with different temperatures.

### 3.4 `backend/services/speech_router.py` — Simplify engine resolution

The current router handles 9 TTS engines + 3 STT engines. After removing local models:

```python
# Simplified routing table (replaces the current ~120-line module)

def resolve_tts_engine(requested: str | None, language: str) -> str:
    """Always returns 'sarvam' or 'elevenlabs'."""
    if requested == "elevenlabs":
        return "elevenlabs"       # Premium cloning — explicit opt-in only
    return "sarvam"               # Primary for all languages

def get_stt_backend_for_language(language: str | None) -> STTBackend:
    """Sarvam primary, Groq fallback."""
    if _sarvam_key_present():
        return SarvamSTTBackend()
    if _groq_key_present():
        return GroqSTTBackend()
    raise NoSpeechProviderError("No STT provider configured. Set SARVAM_API_KEY.")
```

### 3.5 `backend/services/generation.py` — Remove local model loading paths

The generation service currently calls `task_queue.enqueue()` to serialize GPU access. Replace with a simple `asyncio.Semaphore(5)` that limits concurrent cloud API calls:

```python
_cloud_semaphore = asyncio.Semaphore(5)  # Sarvam free tier: ~5 req/s

async def run_generation(...):
    async with _cloud_semaphore:
        # generate via Sarvam / ElevenLabs
```

Also remove: `model_size` parameter, `load_engine_model()` call, the 30 s model-load status update.

### 3.6 `backend/backends/__init__.py` — Collapse to 2 engines

After deleting the 7 local engine files, the `__init__.py` factory becomes:

```python
def get_tts_backend_for_engine(engine: str) -> TTSBackend:
    if engine == "elevenlabs":
        from ..adapters.elevenlabs import ElevenLabsTTSBackend
        return ElevenLabsTTSBackend()
    from ..adapters.sarvam import SarvamTTSBackend
    return SarvamTTSBackend()      # default for "sarvam" or anything else
```

### 3.7 `backend/database/models.py` — Remove unused columns

These columns have no function after removing local models:

| Table | Column to remove |
|-------|-----------------|
| `generations` | `model_size` |
| `capture_settings` | `stt_model` (Sarvam auto-selects) |
| `capture_settings` | `llm_model` (Groq auto-selects) |
| `generation_settings` | `max_chunk_chars` (keep with Sarvam's 450-char limit as the ceiling) |

**Add a `provider_config` singleton table** (see §5).

### 3.8 `app/src/components/ModelsTab/` → `ProviderSettings`

Replace the model-download tab with a Provider Settings page:

```
Provider Settings
├── Sarvam API Key     [••••••••] [Test] [Save]   ← required
├── Groq API Key       [••••••••] [Test] [Save]   ← required for refinement
├── ElevenLabs API Key [••••••••] [Test] [Save]   ← optional, premium
└── Provider Status
    ├── Sarvam    ● Connected  (TTS + STT)
    ├── Groq      ● Connected  (STT fallback + LLM)
    └── ElevenLabs ○ Not configured
```

### 3.9 `app/src/components/Generation/EngineModelSelector.tsx` — Simplify

Replace 9-engine dropdown + model size dropdown with a single binary choice:

```
Voice Quality
  ○ Standard  — Sarvam (fast, free)
  ○ Premium   — ElevenLabs (highest quality, requires key)
```

Show Premium option greyed out with "API key required" tooltip if ElevenLabs key not set.

---

## 4. Architecture Improvements

### 4.1 Startup Health Check

On server start, validate all configured API keys before accepting requests:

```python
# backend/app.py — add to lifespan

@asynccontextmanager
async def lifespan(app: FastAPI):
    results = await asyncio.gather(
        check_sarvam_health(),
        check_groq_health(),
        check_elevenlabs_health(),   # only if key present
        return_exceptions=True,
    )
    for r in results:
        if isinstance(r, Exception):
            logger.warning("Provider check failed: %s", r)
    yield
```

Expose results at `GET /health`:
```json
{
  "status": "ok",
  "providers": {
    "sarvam": "ok",
    "groq": "ok",
    "elevenlabs": "not_configured"
  },
  "version": "1.0.0"
}
```

### 4.2 Unified Provider Error Taxonomy

Create `backend/errors.py` with structured exceptions:

```python
class ProviderError(RuntimeError):
    """Base for all cloud provider errors."""
    provider: str
    status_code: int | None = None
    retryable: bool = False

class ProviderRateLimitError(ProviderError):
    retryable = True

class ProviderAuthError(ProviderError):
    retryable = False

class ProviderUnavailableError(ProviderError):
    retryable = True
```

Map these to HTTP responses in a single FastAPI exception handler:

```python
@app.exception_handler(ProviderError)
async def provider_error_handler(request, exc):
    status = 503 if exc.retryable else 502
    return JSONResponse({"error": str(exc), "provider": exc.provider}, status_code=status)
```

### 4.3 Replace Serial GPU Queue with Cloud Concurrency Control

`services/task_queue.py` exists purely to prevent GPU memory contention. With cloud APIs, replace with a `asyncio.Semaphore` per provider:

```python
# backend/services/cloud_limits.py
import asyncio

# Sarvam free tier: ~5 concurrent requests
SARVAM_SEM = asyncio.Semaphore(int(os.getenv("SARVAM_CONCURRENCY", "5")))
# Groq is generous but still rate-limited
GROQ_SEM = asyncio.Semaphore(int(os.getenv("GROQ_CONCURRENCY", "10")))
# ElevenLabs free is very limited
ELEVENLABS_SEM = asyncio.Semaphore(int(os.getenv("ELEVENLABS_CONCURRENCY", "2")))
```

### 4.4 Retry with Exponential Backoff

Add a shared retry decorator for all cloud calls:

```python
# backend/utils/retry.py
import asyncio, random
from functools import wraps

def with_retry(max_attempts=3, base_delay=1.0, retryable_exceptions=(ProviderRateLimitError,)):
    def decorator(fn):
        @wraps(fn)
        async def wrapper(*args, **kwargs):
            for attempt in range(max_attempts):
                try:
                    return await fn(*args, **kwargs)
                except retryable_exceptions as e:
                    if attempt == max_attempts - 1:
                        raise
                    delay = base_delay * (2 ** attempt) + random.uniform(0, 0.5)
                    await asyncio.sleep(delay)
        return wrapper
    return decorator
```

### 4.5 PostgreSQL Connection Pooling

Already implemented in `backend/database/session.py`:

```python
engine = create_engine(
    config.get_database_url(),
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
)
```

`pool_pre_ping=True` recycles stale connections automatically. For high-concurrency deployments increase `pool_size` and add `pool_timeout=30`.

### 4.6 Structured Logging

Replace `print()` and unstructured `logger.info()` with structured JSON logging in production:

```python
# backend/logging_config.py
import logging, json, sys

class JSONFormatter(logging.Formatter):
    def format(self, record):
        return json.dumps({
            "ts": self.formatTime(record),
            "level": record.levelname,
            "module": record.module,
            "msg": record.getMessage(),
            **({"exc": self.formatException(record.exc_info)} if record.exc_info else {}),
        })
```

---

## 5. Database Improvements

### 5.1 Add `provider_config` Singleton Table

Store API key status and per-provider settings in the DB (not just `.env`):

```sql
CREATE TABLE provider_config (
    id          INTEGER PRIMARY KEY DEFAULT 1,
    -- keys stored as encrypted blobs (see §7)
    sarvam_key_hash    TEXT,       -- SHA-256 of key for display masking
    groq_key_hash      TEXT,
    elevenlabs_key_hash TEXT,
    -- per-provider override
    sarvam_tts_model   TEXT DEFAULT 'bulbul:v2',
    sarvam_stt_model   TEXT DEFAULT 'saarika:v2.5',
    groq_llm_model     TEXT DEFAULT 'llama-3.3-70b-versatile',
    groq_stt_model     TEXT DEFAULT 'whisper-large-v3',
    -- feature flags
    elevenlabs_enabled BOOLEAN DEFAULT 0,
    updated_at         DATETIME
);
```

### 5.2 Add Missing Indexes

```sql
-- Captures: speaker lookup and date range queries
CREATE INDEX IF NOT EXISTS idx_captures_profile   ON captures(identified_profile_id);
CREATE INDEX IF NOT EXISTS idx_captures_created   ON captures(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_captures_source    ON captures(source);

-- Generations: profile history (most common list query)
CREATE INDEX IF NOT EXISTS idx_generations_profile_date ON generations(profile_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_generations_favorited    ON generations(is_favorited) WHERE is_favorited = 1;

-- Story items: timeline loads
CREATE INDEX IF NOT EXISTS idx_story_items_story ON story_items(story_id, start_time_ms);
```

Add these in a `_migrate_indexes()` call in `migrations.py`. `CREATE INDEX IF NOT EXISTS` is idempotent in PostgreSQL.

### 5.3 Remove Stale Columns via Migration

```sql
-- generations: model_size is meaningless for cloud APIs
ALTER TABLE generations DROP COLUMN IF EXISTS model_size;  -- PostgreSQL

-- capture_settings: stt_model and llm_model no longer user-selectable
ALTER TABLE capture_settings DROP COLUMN stt_model;
ALTER TABLE capture_settings DROP COLUMN llm_model;
```

Use `_supports_drop_column()` already in `migrations.py`.

### 5.4 Voice Profile `voice_type` Cleanup

Remove the `designed` code path entirely. In `_migrate_profiles()`, coerce any `voice_type = 'designed'` rows to `'cloned'` (they have no prompt anyway since the feature was never shipped).

### 5.5 Capture Cleanup Policy

Add a `max_captures` setting to `capture_settings` (default: 500). Add a DB trigger or startup migration that prunes the oldest captures beyond the limit, keeping disk usage bounded.

---

## 6. UI/UX Improvements

### 6.1 First-Launch Onboarding

On first start (detect: `provider_config.sarvam_key_hash IS NULL`), show a full-screen onboarding modal before the main UI:

```
Step 1/2 — Connect Sarvam (required)
  [Get a free key at dashboard.sarvam.ai →]
  Sarvam API Key: [_________________] [Verify]
  ✓ Connected — TTS and STT ready

Step 2/2 — Connect Groq (recommended, free)
  [Get a free key at console.groq.com →]
  Groq API Key: [_________________] [Verify]
  ✓ Connected — Transcript refinement and personality ready
  
  [Skip for now]     [Get Started →]
```

Without this, users hit cryptic 503 errors. This is the single biggest UX gap today.

### 6.2 Replace ModelsTab with Provider Settings

New tab: **Providers** (replaces **Models**)

```
Providers
├── Core Providers
│   ├── Sarvam AI        [API Key ••••••] [Test ✓] — TTS + STT for EN/HI/TE
│   └── Groq             [API Key ••••••] [Test ✓] — Transcript cleanup + Personality
└── Premium
    └── ElevenLabs       [API Key        ] [Test]  — High-quality voice cloning
        ⚠ Requires paid ElevenLabs plan
```

### 6.3 Remove GPU Page from Settings

`ServerTab/GpuPage.tsx` — delete. Replace the tab slot in the sidebar with nothing (Providers tab covers what users actually need to configure).

### 6.4 Simplify Engine Picker in Voice Studio

Before: dropdown with 9 options + model size dropdown  
After: radio toggle

```
Voice Engine
  ● Standard  — Sarvam (fast, multilingual)
  ○ Premium   — ElevenLabs ← grey + "Add API key to unlock" if not configured
```

### 6.5 Provider Status in Header

Add a subtle pill in the app header (or status bar):

```
● Sarvam  ● Groq  ○ ElevenLabs
```

Clicking opens Provider Settings. If Sarvam is red (auth error), users see it immediately instead of discovering it on first generation.

### 6.6 Capture Speaker ID — Make It Discoverable

Currently the green badge appears only when a speaker is identified. Add a tooltip explaining what it is on hover: *"Speaker recognized from voice profile 'Ravi' (92% confidence)."*

Also add a "Who is this?" icon button on captures without a speaker label, which triggers manual re-identification.

### 6.7 Error Messages

Replace raw provider error strings with user-friendly messages:

| Provider error | Show user |
|----------------|-----------|
| 401 / auth error | "Sarvam API key is invalid. Update it in Providers settings." |
| 429 / rate limit | "Sarvam is rate-limiting requests. Trying again in a moment…" |
| 503 / unavailable | "Sarvam is temporarily unavailable. Your audio has been saved — retry when ready." |
| Missing key | "Sarvam API key not configured. Go to Providers settings to add one." |

---

## 7. Security Improvements

### 7.1 Encrypt API Keys at Rest

Do **not** store plaintext keys in the database or `.env`. Use the OS keychain via Tauri's `tauri-plugin-stronghold` (a vault backed by IOTA Stronghold):

```toml
# tauri/src-tauri/Cargo.toml
tauri-plugin-stronghold = "2.0"
```

```typescript
// app/src/lib/keychain.ts
import { Stronghold } from '@tauri-apps/plugin-stronghold'

export async function saveApiKey(provider: string, key: string) {
  const vault = await Stronghold.load('./voicetuner.hold', APP_SECRET)
  const store = vault.getStore('api-keys')
  await store.insert(provider, Array.from(new TextEncoder().encode(key)))
  await vault.save()
}

export async function getApiKey(provider: string): Promise<string | null> {
  const vault = await Stronghold.load('./voicetuner.hold', APP_SECRET)
  const store = vault.getStore('api-keys')
  const raw = await store.get(provider)
  return raw ? new TextDecoder().decode(new Uint8Array(raw)) : null
}
```

For the web deployment (no Stronghold), fall back to the `.env` file approach.

### 7.2 Backend Bound to Loopback Only

Already done — ensure `uvicorn` is always started with `--host 127.0.0.1`. Reject any `--host 0.0.0.0` in the PyInstaller build entrypoint. Add a check:

```python
# backend/server.py
if host not in ("127.0.0.1", "localhost", "::1"):
    raise SystemExit("VoiceTuner server must not be exposed to the network in desktop mode.")
```

### 7.3 MCP Endpoint Authentication

The MCP server at `/mcp` currently has no authentication. Any process on the machine can call it. Add a bearer token requirement for non-loopback connections:

```python
# backend/mcp_server/server.py
async def mcp_auth_middleware(request: Request, call_next):
    if request.client.host not in ("127.0.0.1", "::1"):
        token = request.headers.get("Authorization", "")
        if token != f"Bearer {get_mcp_token()}":
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
    return await call_next(request)
```

The MCP token is auto-generated on first launch and stored in the keychain.

### 7.4 Content Security Policy in Tauri Webview

```json
// tauri/src-tauri/tauri.conf.json
"security": {
  "csp": "default-src 'self'; connect-src 'self' http://127.0.0.1:17493 https://api.sarvam.ai https://api.groq.com https://api.elevenlabs.io; media-src 'self' blob:; img-src 'self' data:; style-src 'self' 'unsafe-inline'"
}
```

### 7.5 API Keys Never in Frontend Bundle

The Tauri bridge already enforces this — API calls go through the Python backend. Add a CI lint rule that fails if `SARVAM_API_KEY`, `GROQ_API_KEY`, or `ELEVENLABS_API_KEY` appears in any file under `app/src/` or `tauri/src/`:

```bash
# scripts/check-no-keys.sh (add to CI)
grep -r "SARVAM_API_KEY\|GROQ_API_KEY\|ELEVENLABS_API_KEY" app/src tauri/src && exit 1 || exit 0
```

### 7.6 Audio File Access Control

Audio files in `data/captures/` and `data/generations/` contain potentially sensitive speech. Ensure the directory is created with `0700` permissions (owner-only) and that the `/audio/` route validates the requested path stays within the data directory (path traversal guard):

```python
# backend/routes/audio.py — add to every file-serving path
resolved = config.resolve_storage_path(requested_path)
if resolved is None or not str(resolved).startswith(str(config.get_data_dir())):
    raise HTTPException(status_code=403, detail="Access denied")
```

### 7.7 Rate Limit the Local API

Even though the backend is loopback-only, add per-endpoint rate limiting to prevent runaway automation:

```python
# backend/app.py
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)

@router.post("/captures")
@limiter.limit("30/minute")   # 30 captures/min is plenty for dictation
async def create_capture(...):
    ...

@router.post("/generate")
@limiter.limit("10/minute")
async def generate(...):
    ...
```

---

## 8. Production Deployment Plan

### Phase 1 — Engine Purge (Week 1–2)
**Goal:** Remove all local ML from the codebase. No user-visible changes.

1. Delete 7 backend files (`chatterbox_backend.py`, `qwen_backend.py`, etc.) and `services/task_queue.py`, `services/llm.py`, `services/transcribe.py`, `services/tts.py`
2. Remove 27 ML packages from `requirements.txt`
3. Update `backends/__init__.py` to 2-engine factory
4. Replace serial queue in `generation.py` with `asyncio.Semaphore(5)`
5. Run `pytest` — all tests should still pass (no engine tests remain)
6. Verify `pip install -r requirements.txt` completes in < 60 s (vs 20+ min today)

**Acceptance:** `./start.sh` brings up the stack in < 10 s. Generation works via Sarvam.

### Phase 2 — Groq LLM Wiring (Week 2)
**Goal:** Replace local Qwen with Groq for refinement + personality.

1. Add `GroqLLMBackend` class to `adapters/groq.py`
2. Update `services/refinement.py` to call `GroqLLMBackend.complete()`
3. Update `services/personality.py` to call `GroqLLMBackend.complete()`
4. Remove `routes/llm.py` and `services/llm.py`
5. Remove `stt_model` / `llm_model` from `capture_settings`
6. Add `_migrate_remove_local_model_settings()` to `migrations.py`

**Acceptance:** Transcript refinement and personality compose work via Groq API.

### Phase 3 — Database & Backend Hardening (Week 3)
**Goal:** Performance, reliability, and security foundations.

1. ✅ PostgreSQL with connection pooling in `database/session.py` (done)
2. Add missing indexes via migration
3. Add `provider_config` table
4. Add `backend/errors.py` with provider error taxonomy
5. Add unified exception handler in `app.py`
6. Add retry decorator to `adapters/sarvam.py` and `adapters/groq.py`
7. Add path traversal guard to `routes/audio.py`
8. Add loopback enforcement to `server.py`

**Acceptance:** `GET /health` returns per-provider status. 429 errors trigger automatic retry. Path traversal returns 403.

### Phase 4 — UI Simplification (Week 4)
**Goal:** Remove dead UI, add provider settings, first-launch flow.

1. Delete `components/ModelsTab/ModelsTab.tsx`
2. Delete `components/ServerTab/GpuPage.tsx`
3. Create `components/ProvidersTab/` with API key form + live Test button
4. Build first-launch onboarding modal (triggered by empty `provider_config`)
5. Simplify `EngineModelSelector.tsx` to Standard/Premium radio
6. Add provider status pills to app header
7. Wire user-friendly error messages for all provider errors
8. Add "Who is this?" manual re-ID button on captures

**Acceptance:** A new user can install, configure keys, and make their first generation without reading documentation.

### Phase 5 — Security Hardening (Week 5)
**Goal:** Keys encrypted at rest, CSP, MCP auth, CI key-leak detection.

1. Add `tauri-plugin-stronghold` to Tauri and wire `keychain.ts`
2. Migrate key storage from `.env` / plaintext to Stronghold vault
3. Add CSP to `tauri.conf.json`
4. Add MCP bearer token middleware
5. Add `scripts/check-no-keys.sh` to CI (GitHub Actions)
6. Add `slowapi` rate limiting to `/captures` and `/generate`

**Acceptance:** Keys not visible in the database. `strings voicetuner` binary reveals no API keys. MCP endpoint returns 401 from non-loopback without a token.

### Phase 6 — Build Pipeline & Distribution (Week 6)
**Goal:** Signed, notarized, auto-updating desktop binaries.

1. Set up GitHub Actions workflow: `build.yml` per platform (macOS arm64, macOS x64, Windows)
2. PyInstaller spec updated — no `torch`/`transformers` to bundle; verify binary < 200 MB
3. macOS: configure Apple Developer signing + notarization
4. Windows: configure EV code signing
5. Configure Tauri updater endpoint pointing at new repo
6. Smoke test installer on clean machine (no Python, no .env)
7. Write `RELEASE.md` runbook

**Acceptance:** Clean-machine install via `.dmg` / `.msi` works end-to-end with only a Sarvam API key.

### Phase 7 — QA & Launch (Week 7)
**Goal:** Stable, tested, documented v1.0 release.

1. End-to-end test matrix:
   - macOS arm64, macOS x64, Windows 11
   - All 3 languages (en, hi, te)
   - All 3 capture sources (dictation, recording, file upload)
   - Voice cloning: Sarvam preset, ElevenLabs premium
   - Speaker ID: create profile, upload samples, verify badge on capture
   - MCP: Claude Code agent speaks in bound voice
2. Native-speaker QA of hi + te translations
3. Update `README.md`, `SETUP.md`, `docs/`
4. Tag `v1.0.0`, push to GitHub, trigger build pipeline
5. Publish release notes

---

## 9. Final Roadmap to v1.0

```
Week 1–2   Phase 1: Engine Purge
             ↳ Remove 7 local TTS engines + 27 ML deps
             ↳ Binary: 4 GB → 150 MB | Start: 90 s → 2 s

Week 2      Phase 2: Groq LLM
             ↳ Refinement + Personality → Groq API
             ↳ Remove local Qwen completely

Week 3      Phase 3: Backend Hardening
             ↳ WAL, indexes, error taxonomy, retry, path traversal guard

Week 4      Phase 4: UI Simplification
             ↳ Onboarding flow, Provider Settings tab, simplified engine picker

Week 5      Phase 5: Security
             ↳ Encrypted key storage (Stronghold), CSP, MCP auth, rate limiting

Week 6      Phase 6: Build Pipeline
             ↳ CI builds, signed installers, auto-update wired to new repo

Week 7      Phase 7: QA & Launch
             ↳ End-to-end matrix, native QA, v1.0.0 tag

Total: 7 weeks to production-ready v1.0
```

### Effort Estimate

| Phase | Effort | Risk |
|-------|--------|------|
| Engine Purge | 3 days | Low — pure deletion |
| Groq LLM | 2 days | Low — similar pattern to Groq STT |
| Backend Hardening | 3 days | Low — additive, well-understood |
| UI Simplification | 5 days | Medium — onboarding + new tab |
| Security | 4 days | Medium — Stronghold integration is new |
| Build Pipeline | 3 days | High — signing/notarization is fiddly |
| QA & Launch | 4 days | Medium — depends on test matrix size |
| **Total** | **~24 days** | |

---

## Summary Table

| Dimension | Before | After v1.0 |
|-----------|--------|------------|
| TTS engines | 9 (7 local + 2 cloud) | 2 (Sarvam + ElevenLabs) |
| STT engines | 3 (local Whisper + Sarvam + Groq) | 2 (Sarvam + Groq) |
| LLM | Local Qwen3 (0.6B–4B) | Groq (Llama 3.3 70B) |
| Python deps | ~50 packages | ~18 packages |
| Binary size | ~4 GB | ~150 MB |
| Cold start | 30–90 s | < 2 s |
| GPU required | Yes (for quality) | No |
| API keys needed | None (all local) | SARVAM_API_KEY (required) |
| Key storage | Plaintext .env | OS keychain (Stronghold) |
| MCP auth | None | Bearer token for non-loopback |
| CSP | None | Enforced in Tauri webview |
| Provider errors | Stack traces | User-friendly messages |
| First-launch UX | Drop to main UI | Guided onboarding |
| Languages | en/hi/te | en/hi/te |
| Speaker ID | ✅ | ✅ |
| Voice Studio | ✅ | ✅ |
| Dictation | ✅ | ✅ |
| MCP Integration | ✅ | ✅ |
| Desktop App | ✅ | ✅ |
