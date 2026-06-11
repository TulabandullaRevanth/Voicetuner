# VoiceTuner v1.0 — Enterprise Architecture & Solution Design

**Classification:** Internal Architecture Document  
**Version:** 1.0-DRAFT  
**Audience:** Principal Engineers, DevOps, Security, Enterprise Architects  
**Constraint:** On-premise first. Cloud providers are optional plugins, never dependencies.

---

## Table of Contents

1. [Enterprise Architecture Design](#1-enterprise-architecture-design)
2. [On-Prem Deployment Architecture](#2-on-prem-deployment-architecture)
3. [Local vs Cloud Provider Strategy](#3-local-vs-cloud-provider-strategy)
4. [Components to Keep](#4-components-to-keep)
5. [Components to Remove](#5-components-to-remove)
6. [Components to Refactor](#6-components-to-refactor)
7. [Database Improvements](#7-database-improvements)
8. [Security Hardening Plan](#8-security-hardening-plan)
9. [Role-Based Access Control Design](#9-role-based-access-control-design)
10. [Audit Logging Design](#10-audit-logging-design)
11. [Backup & Disaster Recovery Plan](#11-backup--disaster-recovery-plan)
12. [High Availability Recommendations](#12-high-availability-recommendations)
13. [Production Deployment Plan](#13-production-deployment-plan)
14. [VoiceTuner v1.0 Enterprise Roadmap](#14-voicetuner-v10-enterprise-roadmap)

---

## 1. Enterprise Architecture Design

### 1.1 Design Principles

| Principle | Implementation |
|-----------|---------------|
| **On-premise first** | Every feature works with zero internet access. Cloud providers are optional, additive. |
| **Provider agnosticism** | STT, TTS, LLM are interfaces. Any conforming backend plugs in. |
| **Deployment flexibility** | Same codebase runs on a laptop, a team server, or an air-gapped data center rack. |
| **No silent failures** | Every provider failure surfaces with a structured error and a fallback chain. |
| **Auditability** | Every state-changing operation is logged with user, timestamp, resource, and outcome. |
| **Least privilege** | RBAC enforced at the service layer, not just the route layer. |
| **Data sovereignty** | Audio files and transcripts never leave the machine unless explicitly configured. |

### 1.2 Three-Tier Deployment Model

```
┌─────────────────────────────────────────────────────────────────┐
│ Tier 1 — Desktop                                                │
│  Single user · SQLite · PyInstaller binary · Local models       │
│  Use case: Individual power user, laptop, air-gapped workstation│
└─────────────────────────────────────────────────────────────────┘
                              │ same codebase
┌─────────────────────────────────────────────────────────────────┐
│ Tier 2 — Team Server                                            │
│  2–50 users · PostgreSQL · Docker Compose · Shared model store  │
│  Use case: Dept deployment, shared voice profiles, team admin   │
└─────────────────────────────────────────────────────────────────┘
                              │ same codebase, additional config
┌─────────────────────────────────────────────────────────────────┐
│ Tier 3 — Enterprise HA                                          │
│  50–500 users · PostgreSQL cluster · Nginx LB · NFS/CEPH audio │
│  Use case: Organization-wide, RBAC, compliance, audit trails    │
└─────────────────────────────────────────────────────────────────┘
```

### 1.3 Provider Plugin Architecture

The single most important architectural change. Every speech capability becomes a typed interface; implementations are loaded at startup from a config-driven registry.

```
backend/
  providers/
    __init__.py          ← ProviderRegistry (singleton)
    base.py              ← SpeechProvider, TTSProvider, STTProvider, LLMProvider protocols
    registry.py          ← load_providers_from_config(config) → ProviderRegistry
    local/
      __init__.py
      whisper_stt.py     ← WhisperSTTProvider  (wraps existing mlx/pytorch backends)
      qwen_tts.py        ← QwenTTSProvider     (wraps qwen_backend.py)
      chatterbox_tts.py  ← ChatterboxTTSProvider
      kokoro_tts.py      ← KokoroTTSProvider
      luxtts_tts.py      ← LuxTTSProvider
      qwen_llm.py        ← QwenLLMProvider
    cloud/               ← all optional, loaded only if configured
      __init__.py
      sarvam.py          ← SarvamProvider (TTS + STT)
      groq.py            ← GroqProvider  (STT + LLM)
      elevenlabs.py      ← ElevenLabsProvider (TTS cloning)
```

**Provider Interface (`backend/providers/base.py`):**

```python
from __future__ import annotations
from typing import Protocol, runtime_checkable, Literal
from dataclasses import dataclass, field
import numpy as np

ProviderKind = Literal["local", "cloud"]
Capability = Literal["tts", "stt", "llm"]

@dataclass
class ProviderHealth:
    available: bool
    latency_ms: float | None = None
    error: str | None = None

@dataclass
class ProviderInfo:
    provider_id: str                          # "whisper", "sarvam", "groq"
    display_name: str
    kind: ProviderKind
    capabilities: set[Capability]
    languages: list[str]                      # ["en", "hi", "te"]
    requires_gpu: bool = False
    requires_network: bool = False

@runtime_checkable
class TTSProvider(Protocol):
    info: ProviderInfo

    async def health_check(self) -> ProviderHealth: ...

    async def generate(
        self,
        text: str,
        voice_prompt: dict,
        language: str,
        seed: int | None = None,
    ) -> tuple[np.ndarray, int]: ...           # (audio_array, sample_rate)

    async def create_voice_prompt(
        self,
        audio_paths: list[str],
        reference_texts: list[str],
    ) -> dict: ...

@runtime_checkable
class STTProvider(Protocol):
    info: ProviderInfo

    async def health_check(self) -> ProviderHealth: ...

    async def transcribe(
        self,
        audio_path: str,
        language: str | None,
    ) -> str: ...

@runtime_checkable
class LLMProvider(Protocol):
    info: ProviderInfo

    async def health_check(self) -> ProviderHealth: ...

    async def complete(
        self,
        system_prompt: str,
        user_message: str,
        temperature: float = 0.3,
        max_tokens: int = 1024,
    ) -> str: ...
```

**Provider Registry (`backend/providers/registry.py`):**

```python
from __future__ import annotations
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

@dataclass
class ProviderRegistry:
    _tts: dict[str, TTSProvider] = field(default_factory=dict)
    _stt: dict[str, STTProvider] = field(default_factory=dict)
    _llm: dict[str, LLMProvider] = field(default_factory=dict)

    def register_tts(self, provider: TTSProvider) -> None:
        self._tts[provider.info.provider_id] = provider
        logger.info("TTS provider registered: %s (%s)", provider.info.provider_id, provider.info.kind)

    def get_tts(self, provider_id: str) -> TTSProvider:
        p = self._tts.get(provider_id)
        if p is None:
            raise ProviderNotFoundError(f"TTS provider '{provider_id}' not registered. "
                                        f"Available: {list(self._tts)}")
        return p

    def list_tts(self) -> list[ProviderInfo]:
        return [p.info for p in self._tts.values()]

    def resolve_tts_for_language(self, language: str, preferred: str | None = None) -> TTSProvider:
        """Return the best available TTS provider for `language`.

        Priority: preferred → local providers supporting language → cloud providers.
        Raises ProviderNotFoundError if nothing supports the language.
        """
        if preferred and preferred in self._tts:
            p = self._tts[preferred]
            if language in p.info.languages:
                return p

        # Local-first fallback chain
        for kind in ("local", "cloud"):
            for p in self._tts.values():
                if p.info.kind == kind and language in p.info.languages:
                    return p

        raise ProviderNotFoundError(
            f"No TTS provider available for language '{language}'. "
            f"Install a local TTS engine or configure a cloud provider."
        )

    # Mirror for STT and LLM ...


# Module-level singleton, initialised once in app lifespan
_registry: ProviderRegistry | None = None

def get_registry() -> ProviderRegistry:
    if _registry is None:
        raise RuntimeError("ProviderRegistry not initialised. Call load_providers() first.")
    return _registry
```

**Provider config (`voicetuner.yaml` — the single deployment config file):**

```yaml
# voicetuner.yaml — committed to version control, secrets via env vars

deployment:
  tier: desktop          # desktop | team | enterprise
  data_dir: /var/voicetuner/data
  models_dir: /var/voicetuner/models
  log_level: INFO

providers:
  tts:
    - id: qwen
      kind: local
      enabled: true
      priority: 1
      languages: [en, hi, te]
      model_size: 1.7B         # 0.6B | 1.7B
    - id: chatterbox
      kind: local
      enabled: true
      priority: 2
      languages: [en, hi]
    - id: kokoro
      kind: local
      enabled: true
      priority: 3
      languages: [en]
    - id: luxtts
      kind: local
      enabled: true
      priority: 4
      languages: [en]
    - id: sarvam
      kind: cloud
      enabled: false           # flip to true + set SARVAM_API_KEY to activate
      languages: [en, hi, te]
    - id: elevenlabs
      kind: cloud
      enabled: false
      languages: [en, hi, te]

  stt:
    - id: whisper
      kind: local
      enabled: true
      priority: 1
      model_size: turbo        # base | small | medium | large | turbo
      languages: [en, hi, te]
    - id: sarvam
      kind: cloud
      enabled: false
    - id: groq
      kind: cloud
      enabled: false

  llm:
    - id: qwen_llm
      kind: local
      enabled: true
      priority: 1
      model_size: 1.7B
    - id: groq
      kind: cloud
      enabled: false

security:
  mcp_token_required: true
  audit_log_enabled: true
  max_captures_per_user: 1000
  session_ttl_seconds: 28800  # 8 hours

database:
  backend: sqlite              # sqlite | postgresql
  # For postgresql:
  # host: localhost
  # port: 5432
  # name: voicetuner
  # user: voicetuner
  # password: ${DB_PASSWORD}   # env var reference

server:
  host: 127.0.0.1
  port: 17493
  workers: 1                   # For team/enterprise: 4
```

---

## 2. On-Prem Deployment Architecture

### 2.1 Tier 1 — Desktop (Single User)

```
┌──────────────────────────────────────────────────────────┐
│  User Workstation / Laptop                                │
│                                                          │
│  ┌──────────────┐   IPC    ┌──────────────────────────┐  │
│  │ Tauri Shell  │ ──────▶  │ VoiceTuner Backend       │  │
│  │ (React UI)   │          │ 127.0.0.1:17493          │  │
│  └──────────────┘          │                          │  │
│                             │ ┌────────┐ ┌──────────┐ │  │
│  ┌──────────────┐           │ │SQLite  │ │ Models   │ │  │
│  │ voicetuner   │           │ │(local) │ │ (local)  │ │  │
│  │ -server      │           │ └────────┘ └──────────┘ │  │
│  │ (PyInstaller)│           └──────────────────────────┘  │
│  └──────────────┘                                         │
│                             Network: NONE required        │
└──────────────────────────────────────────────────────────┘
```

**Distribution:** `.dmg` (macOS) / `.msi` (Windows) — PyInstaller backend + Tauri frontend, all models bundled or downloaded on first run.

**Data isolation:** All data under `~/.voicetuner/` or `%APPDATA%\VoiceTuner\` — no shared state.

### 2.2 Tier 2 — Team Server (Docker Compose)

```
┌──────────────────────────────────────────────────────────────────┐
│ On-Prem Server (bare metal or VM)                                │
│                                                                  │
│  ┌─────────────────────────────────────────────────────────┐     │
│  │                     Docker Compose Stack                │     │
│  │                                                         │     │
│  │  ┌──────────┐  ┌──────────────┐  ┌──────────────────┐  │     │
│  │  │  Nginx   │  │  Backend     │  │  PostgreSQL 16   │  │     │
│  │  │  :443    │─▶│  :17493      │─▶│  :5432 (local)   │  │     │
│  │  │  (TLS)   │  │  (FastAPI)   │  │                  │  │     │
│  │  └──────────┘  └──────────────┘  └──────────────────┘  │     │
│  │                       │                                 │     │
│  │              ┌────────┴────────┐                        │     │
│  │              │   /mnt/data     │                        │     │
│  │              │   (audio files) │  ← volume mount        │     │
│  │              └─────────────────┘                        │     │
│  │                                                         │     │
│  │              ┌─────────────────┐                        │     │
│  │              │   /mnt/models   │                        │     │
│  │              │   (TTS/STT/LLM) │  ← volume mount        │     │
│  │              └─────────────────┘                        │     │
│  └─────────────────────────────────────────────────────────┘     │
│                                                                  │
│  Desktop clients connect via:  https://voicetuner.corp.local     │
│  MCP agents connect via:       https://voicetuner.corp.local/mcp │
└──────────────────────────────────────────────────────────────────┘
```

**`docker-compose.yml` (abbreviated):**

```yaml
version: "3.9"

services:
  nginx:
    image: nginx:1.27-alpine
    ports: ["443:443", "80:80"]
    volumes:
      - ./nginx/voicetuner.conf:/etc/nginx/conf.d/default.conf:ro
      - ./certs:/etc/nginx/certs:ro
    depends_on: [backend]

  backend:
    build: .
    environment:
      - VOICETUNER_CONFIG=/config/voicetuner.yaml
      - DB_PASSWORD=${DB_PASSWORD}
      - SARVAM_API_KEY=${SARVAM_API_KEY:-}
      - GROQ_API_KEY=${GROQ_API_KEY:-}
      - ELEVENLABS_API_KEY=${ELEVENLABS_API_KEY:-}
    volumes:
      - voicetuner_data:/var/voicetuner/data
      - voicetuner_models:/var/voicetuner/models
      - ./voicetuner.yaml:/config/voicetuner.yaml:ro
    depends_on:
      db:
        condition: service_healthy

  db:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: voicetuner
      POSTGRES_USER: voicetuner
      POSTGRES_PASSWORD: ${DB_PASSWORD}
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD", "pg_isready", "-U", "voicetuner"]
      interval: 5s

volumes:
  voicetuner_data:
  voicetuner_models:
  postgres_data:
```

### 2.3 Tier 3 — Enterprise HA

```
                  ┌─────────────────────────────┐
   Clients ──────▶│     HAProxy / Nginx LB       │
                  │     (active-active)           │
                  └──────────┬──────────────┬────┘
                             │              │
               ┌─────────────▼──┐    ┌──────▼─────────┐
               │  Backend Node 1│    │  Backend Node 2 │
               │  (FastAPI)     │    │  (FastAPI)      │
               │  +GPU (opt.)   │    │  +GPU (opt.)    │
               └───────┬────────┘    └────────┬────────┘
                       │                      │
                       └────────┬─────────────┘
                                │
              ┌─────────────────▼──────────────────┐
              │          PostgreSQL Cluster         │
              │   Primary ──replication── Standby   │
              └────────────────────────────────────┘
                                │
              ┌─────────────────▼──────────────────┐
              │   Shared Audio Storage (NFS/CEPH)   │
              │   /mnt/voicetuner/data              │
              └────────────────────────────────────┘
                                │
              ┌─────────────────▼──────────────────┐
              │   Shared Model Store (NFS read-only)│
              │   /mnt/voicetuner/models            │
              └────────────────────────────────────┘
```

**Redis** is added for Tier 3 to store:
- Session tokens (replaces in-process JWT verify)
- Rate-limit counters (per-user, per-endpoint)
- Provider health cache (avoid re-testing on every request)

---

## 3. Local vs Cloud Provider Strategy

### 3.1 Provider Priority Chains

```
                    Request arrives
                         │
                         ▼
               Is a preferred provider
               specified in request?
              /                    \
            Yes                    No
             │                      │
             ▼                      ▼
    Does it support         Run priority chain
    this language?          from voicetuner.yaml
         │   │              (local first, cloud last)
        Yes  No                    │
         │   │                     ▼
         │   └──────────────▶  First provider
         │                     that supports
         ▼                     the language
    Use it
         │
         ▼
    Does it succeed?
         │      │
        Yes     No ──── retryable? ──── Yes ──▶ Retry (3x backoff)
         │                  │                       │
         ▼                  No                      │
    Return result           │                       │
                            ▼                       │
                       Next provider in         If all retries
                       fallback chain           exhausted →
                            │                  next in chain
                            ▼
                       If no more providers:
                       ProviderExhaustedError
                       (structured, not a stack trace)
```

### 3.2 Language Coverage Matrix

| Language | Local TTS | Local STT | Local LLM | Cloud TTS (opt) | Cloud STT (opt) |
|----------|-----------|-----------|-----------|-----------------|-----------------|
| English  | Qwen, Chatterbox, Kokoro, LuxTTS | Whisper | Qwen3, Llama | Sarvam, ElevenLabs | Sarvam, Groq |
| Hindi    | Qwen | Whisper | Qwen3, Llama | Sarvam, ElevenLabs | Sarvam, Groq |
| Telugu   | Qwen | Whisper | Qwen3, Llama | Sarvam, ElevenLabs | Sarvam, Groq |

**Key point:** Qwen3-TTS covers en/hi/te locally. No language requires cloud. Cloud providers accelerate quality and speed but are never a hard dependency.

### 3.3 Hybrid Deployment Patterns

**Pattern A — Local primary, cloud acceleration for Indic:**
```yaml
tts:
  - id: qwen          # local, all languages, primary
  - id: sarvam        # cloud, hi/te only, higher quality if key present
```

**Pattern B — Air-gapped (no internet at all):**
```yaml
tts:
  - id: qwen          # local only
  - id: chatterbox    # local fallback
# No cloud entries
```

**Pattern C — Cloud-augmented desktop (power user):**
```yaml
tts:
  - id: sarvam        # cloud primary (fast)
  - id: qwen          # local fallback (if offline)
  - id: elevenlabs    # cloud premium (explicit opt-in)
```

---

## 4. Components to Keep

### Backend — Keep As-Is

| File | Keep |
|------|------|
| `backend/app.py` | ✅ — clean factory; add provider injection |
| `backend/main.py` | ✅ — add `--config` arg |
| `backend/config.py` | ✅ — extend with YAML loader |
| `backend/languages.py` | ✅ |
| `backend/models.py` | ✅ — extend with RBAC models |
| `backend/database/migrations.py` | ✅ — idempotent migration framework is solid |
| `backend/database/session.py` | ✅ — extend for PostgreSQL |
| `backend/mcp_server/` | ✅ — entire MCP server |
| `backend/routes/captures.py` | ✅ |
| `backend/routes/generations.py` | ✅ |
| `backend/routes/profiles.py` | ✅ |
| `backend/routes/effects.py` | ✅ |
| `backend/routes/stories.py` | ✅ |
| `backend/routes/speak.py` | ✅ |
| `backend/routes/mcp_bindings.py` | ✅ |
| `backend/routes/settings.py` | ✅ |
| `backend/routes/channels.py` | ✅ |
| `backend/routes/events.py` | ✅ |
| `backend/routes/health.py` | ✅ — extend with provider status |
| `backend/routes/history.py` | ✅ |
| `backend/routes/audio.py` | ✅ — add path traversal guard |
| `backend/routes/transcription.py` | ✅ |
| `backend/adapters/sarvam.py` | ✅ — becomes `providers/cloud/sarvam.py` |
| `backend/adapters/groq.py` | ✅ — becomes `providers/cloud/groq.py` |
| `backend/adapters/elevenlabs.py` | ✅ — becomes `providers/cloud/elevenlabs.py` |
| `backend/adapters/speaker_id.py` | ✅ |
| `backend/adapters/credentials.py` | ✅ |
| `backend/services/captures.py` | ✅ |
| `backend/services/profiles.py` | ✅ |
| `backend/services/effects.py` | ✅ |
| `backend/services/stories.py` | ✅ |
| `backend/services/versions.py` | ✅ |
| `backend/services/export_import.py` | ✅ |
| `backend/services/channels.py` | ✅ |
| `backend/services/settings.py` | ✅ |
| `backend/services/history.py` | ✅ |
| `backend/services/refinement.py` | ✅ — collapse_repetitive_artifacts is pure Python; keep |
| `backend/services/personality.py` | ✅ — rewire to provider interface |
| `backend/services/generation.py` | ✅ — rewire to provider registry |
| `backend/utils/audio.py` | ✅ |
| `backend/utils/effects.py` | ✅ |
| `backend/utils/chunked_tts.py` | ✅ |
| `backend/utils/capture_chords.py` | ✅ |
| `backend/utils/images.py` | ✅ |
| `backend/utils/cache.py` | ✅ — keep get_cache_dir |

### Local TTS Backends — Keep (4 of 7)

| Backend | Keep | Reason |
|---------|------|--------|
| `qwen_backend.py` + `mlx_backend.py` + `pytorch_backend.py` | ✅ | Primary multilingual cloning; covers en/hi/te |
| `chatterbox_backend.py` | ✅ | Highest quality cloning; 23 languages; production-proven |
| `kokoro_backend.py` | ✅ | 82M, fast, CPU-friendly preset voices; low resource use |
| `luxtts_backend.py` | ✅ | CPU-only option for resource-constrained nodes |

### Frontend — Keep

- All tabs: VoicesTab, MainEditor, CapturesTab, StoriesTab, EffectsTab
- DictateWindow, AudioPlayer, AudioStudio, AppFrame, Sidebar
- AccessibilityGate, InputMonitoringGate, ChordPicker
- i18n/locales (en, hi, te)
- All Zustand stores

---

## 5. Components to Remove

### Backend — Delete Entirely

```
backend/backends/chatterbox_turbo_backend.py  # Merge into chatterbox_backend.py as turbo=True param
backend/backends/hume_backend.py              # TADA (HumeAI) — experimental, unstable deps
backend/backends/qwen_custom_voice_backend.py # Duplicate of qwen_backend preset path
backend/routes/cuda.py                        # GPU management UI — not needed for enterprise
backend/routes/models.py                      # Model download UI — replaced by admin CLI
backend/routes/tasks.py                       # Exposes task queue internals — fold into /health
backend/services/task_queue.py                # Serial GPU queue — replaced by provider semaphore
backend/services/cuda.py                      # GPU query service
backend/services/tts.py                       # Delegation shim — replaced by ProviderRegistry
backend/services/transcribe.py               # Delegation shim — replaced by ProviderRegistry
backend/services/llm.py                       # Delegation shim — replaced by ProviderRegistry
backend/utils/hf_offline_patch.py            # HuggingFace patches — move to provider init
backend/utils/hf_progress.py                 # HF progress — move to provider init
backend/utils/dac_shim.py                    # TADA-specific; gone with hume_backend
backend/utils/platform_detect.py             # Move logic into qwen provider constructor
backend/utils/progress.py                    # HF download progress manager — move to provider
backend/utils/tasks.py                       # Task manager for model downloads — fold into admin CLI
backend/pyi_hooks/                           # PyInstaller ML hooks — reduced scope
backend/pyi_rth_numpy_compat.py
backend/pyi_rth_torch_compiler_disable.py
```

### Dependencies to Remove

```
# requirements.txt: remove these 12 packages (not: keeping torch, transformers for Qwen/Whisper)
hume-tada          # TADA engine
chatterbox-tts     # keep chatterbox but remove turbo sub-package
qwen-tts           # if Qwen is imported via transformers directly
linacodec          # TADA/LuxTTS git dep
Zipvoice           # TADA/LuxTTS git dep  
conformer          # chatterbox sub-dep — evaluate if still needed after removing turbo
diffusers          # used only by TADA
omegaconf          # used only by chatterbox_turbo
resemble-perth     # Perth watermark detector — not the speaker ID library
pykakasi           # Japanese romanization for misaki — not used for en/hi/te
misaki[ja,zh]      # Remove ja/zh language support from misaki
en_core_web_sm     # Keep (English G2P for Kokoro/misaki[en])
unidic-lite        # Japanese dict — remove if not bundling ja
spacy-pkuseg       # Chinese segmentation — remove
s3tokenizer        # Chatterbox dependency — evaluate
```

### Frontend — Delete

```
app/src/components/ModelsTab/ModelsTab.tsx    # Replace with AdminPanel/ProviderStatus
app/src/components/ServerTab/GpuPage.tsx      # No user-facing GPU management
```

---

## 6. Components to Refactor

### 6.1 `backend/backends/__init__.py` → `backend/providers/`

The entire backends package becomes the providers package. The key change: the factory function is replaced by the ProviderRegistry singleton (described in §1.3). The `ModelConfig` dataclass and `get_all_model_configs()` are moved to an admin CLI tool, not exposed via the main API.

### 6.2 `backend/services/generation.py` — Provider-aware

```python
# Before: hardcoded engine dispatch
backend = get_tts_backend_for_engine(engine)

# After: provider registry with fallback chain
from ..providers import get_registry
registry = get_registry()
tts = registry.resolve_tts_for_language(language, preferred=engine)
audio, sr = await tts.generate(text, voice_prompt, language, seed=seed)
```

Remove: `model_size` parameter, `task_queue.enqueue()`, 30 s model-load status updates.  
Add: `asyncio.Semaphore` per provider (cloud rate limiting), structured retry on `ProviderRateLimitError`.

### 6.3 `backend/services/speech_router.py` → Delegate to ProviderRegistry

```python
# After: thin wrappers that delegate to the registry
def resolve_tts_engine(requested: str | None, language: str) -> str:
    p = get_registry().resolve_tts_for_language(language, preferred=requested)
    return p.info.provider_id

def get_stt_backend_for_language(language: str | None) -> STTProvider:
    return get_registry().resolve_stt_for_language(language or "auto")
```

### 6.4 `backend/services/refinement.py` + `personality.py` — LLM Provider Interface

```python
# Before: from . import llm as llm_service
# After:
from ..providers import get_registry

async def refine_transcript(raw: str, flags: RefinementFlags, ...) -> str:
    llm = get_registry().resolve_llm()
    system = _build_system_prompt(flags)
    cleaned = collapse_repetitive_artifacts(raw)   # keep pure-Python pre-pass
    return await llm.complete(system, cleaned, temperature=0.2)
```

### 6.5 `backend/config.py` → YAML-based Configuration

```python
# backend/config.py — add YAML loader

import yaml
from pathlib import Path
from dataclasses import dataclass

@dataclass
class DeploymentConfig:
    tier: str = "desktop"
    data_dir: Path = Path("data")
    models_dir: Path = Path("models")
    log_level: str = "INFO"

@dataclass
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 17493
    workers: int = 1

@dataclass
class AppConfig:
    deployment: DeploymentConfig
    providers: dict         # raw provider list — parsed by ProviderRegistry
    security: dict
    database: dict
    server: ServerConfig

def load_config(path: str | Path | None = None) -> AppConfig:
    """Load from voicetuner.yaml, then env var overrides, then defaults."""
    ...
```

### 6.6 `backend/database/session.py` — PostgreSQL support

```python
def _build_db_url(cfg: dict) -> str:
    if cfg.get("backend") == "postgresql":
        return (
            f"postgresql+psycopg2://{cfg['user']}:{cfg['password']}"
            f"@{cfg['host']}:{cfg.get('port', 5432)}/{cfg['name']}"
        )
    db_path = get_db_path()
    return f"sqlite:///{db_path}"

def _apply_sqlite_pragmas(engine):
    from sqlalchemy import event
    @event.listens_for(engine, "connect")
    def set_pragmas(conn, _):
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA cache_size=-32000")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA temp_store=MEMORY")
```

### 6.7 `backend/routes/models.py` → Admin CLI (`voicetuner-admin`)

Move model download, unload, and disk usage management out of the runtime API into a standalone admin CLI:

```bash
voicetuner-admin models list
voicetuner-admin models download whisper --size turbo
voicetuner-admin models download qwen --size 1.7B
voicetuner-admin models delete kokoro
voicetuner-admin providers test sarvam
voicetuner-admin db backup --output /mnt/backup/
voicetuner-admin users create --role admin
```

This removes the `ModelsTab` from the end-user UI entirely. System admins manage models via CLI or a future admin web panel.

### 6.8 `backend/routes/cuda.py` → Fold into `/health`

GPU memory usage is a system concern, not a user concern. Move it into the structured `/health` response:

```json
{
  "status": "ok",
  "providers": { "whisper": "ok", "qwen": "ok", "sarvam": "not_configured" },
  "system": {
    "gpu_available": true,
    "gpu_memory_used_mb": 2048,
    "gpu_memory_total_mb": 8192
  }
}
```

### 6.9 `backend/services/captures.py` — Add User Ownership

```python
# Add user_id to every capture for RBAC enforcement
row = DBCapture(
    id=capture_id,
    user_id=current_user.id,    # NEW
    ...
)
```

And enforce in list/get:

```python
def list_captures(db, user_id: str, is_admin: bool, limit=50, offset=0):
    q = db.query(DBCapture)
    if not is_admin:
        q = q.filter(DBCapture.user_id == user_id)  # users see only their own
    ...
```

---

## 7. Database Improvements

### 7.1 New Tables

#### `users` — Authentication & identity

```sql
CREATE TABLE users (
    id           TEXT PRIMARY KEY,          -- UUID
    username     TEXT UNIQUE NOT NULL,
    email        TEXT UNIQUE,
    full_name    TEXT,
    password_hash TEXT NOT NULL,            -- bcrypt, cost=12
    role_id      TEXT NOT NULL REFERENCES roles(id),
    is_active    BOOLEAN NOT NULL DEFAULT 1,
    mfa_enabled  BOOLEAN NOT NULL DEFAULT 0,
    mfa_secret   TEXT,                      -- TOTP secret, encrypted at rest
    created_at   DATETIME NOT NULL,
    updated_at   DATETIME NOT NULL,
    last_login   DATETIME,
    last_login_ip TEXT
);
CREATE INDEX idx_users_username ON users(username);
CREATE INDEX idx_users_role     ON users(role_id);
```

#### `roles` — Permission definitions

```sql
CREATE TABLE roles (
    id           TEXT PRIMARY KEY,
    name         TEXT UNIQUE NOT NULL,   -- admin, voice_manager, user, readonly, api
    display_name TEXT NOT NULL,
    permissions  TEXT NOT NULL,          -- JSON array of permission strings
    is_system    BOOLEAN DEFAULT 0,      -- system roles cannot be deleted
    created_at   DATETIME NOT NULL
);
```

#### `sessions` — Server-side session tokens

```sql
CREATE TABLE sessions (
    id           TEXT PRIMARY KEY,       -- random 32-byte hex
    user_id      TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at   DATETIME NOT NULL,
    expires_at   DATETIME NOT NULL,
    last_used    DATETIME,
    ip_address   TEXT,
    user_agent   TEXT,
    revoked      BOOLEAN DEFAULT 0
);
CREATE INDEX idx_sessions_user    ON sessions(user_id);
CREATE INDEX idx_sessions_expires ON sessions(expires_at);
```

#### `audit_log` — Immutable audit trail

```sql
CREATE TABLE audit_log (
    id            TEXT PRIMARY KEY,      -- UUID
    timestamp     DATETIME NOT NULL,
    user_id       TEXT,                  -- NULL for system/unauthenticated
    username      TEXT,                  -- denormalized (user may be deleted later)
    action        TEXT NOT NULL,         -- PROFILE_CREATE, CAPTURE_DELETE, etc.
    resource_type TEXT,                  -- profile | generation | capture | user | setting
    resource_id   TEXT,
    ip_address    TEXT,
    user_agent    TEXT,
    result        TEXT NOT NULL,         -- success | failure | denied
    details       TEXT                   -- JSON blob with action-specific context
);
CREATE INDEX idx_audit_timestamp     ON audit_log(timestamp DESC);
CREATE INDEX idx_audit_user          ON audit_log(user_id);
CREATE INDEX idx_audit_action        ON audit_log(action);
CREATE INDEX idx_audit_resource      ON audit_log(resource_type, resource_id);
```

#### `provider_config` — Runtime provider state

```sql
CREATE TABLE provider_config (
    id                INTEGER PRIMARY KEY DEFAULT 1,
    -- active provider IDs (JSON lists), set from voicetuner.yaml at startup
    active_tts        TEXT DEFAULT '["qwen"]',
    active_stt        TEXT DEFAULT '["whisper"]',
    active_llm        TEXT DEFAULT '["qwen_llm"]',
    -- key hashes for display masking (never store plaintext keys here)
    sarvam_key_hash   TEXT,
    groq_key_hash     TEXT,
    elevenlabs_key_hash TEXT,
    updated_at        DATETIME
);
```

### 7.2 Modified Existing Tables

#### `profiles` — Add ownership

```sql
ALTER TABLE profiles ADD COLUMN owner_id TEXT REFERENCES users(id);
ALTER TABLE profiles ADD COLUMN visibility TEXT DEFAULT 'private';  -- private | org
```

#### `generations` — Add ownership + remove model_size

```sql
ALTER TABLE generations ADD COLUMN user_id TEXT REFERENCES users(id);
ALTER TABLE generations DROP COLUMN model_size;      -- SQLite 3.35+
```

#### `captures` — Add ownership

```sql
ALTER TABLE captures ADD COLUMN user_id TEXT REFERENCES users(id);
```

#### `capture_settings` — Per-user settings (break singleton)

Convert `id=1` singleton to per-user row:

```sql
ALTER TABLE capture_settings ADD COLUMN user_id TEXT UNIQUE REFERENCES users(id);
-- id=1 row becomes the org-level default; per-user rows override it
```

### 7.3 Missing Indexes

```sql
-- Performance indexes for common query patterns
CREATE INDEX IF NOT EXISTS idx_captures_user_date    ON captures(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_captures_profile_id   ON captures(identified_profile_id);
CREATE INDEX IF NOT EXISTS idx_generations_user_date ON generations(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_generations_profile   ON generations(profile_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_generations_favorited ON generations(is_favorited) WHERE is_favorited = 1;
CREATE INDEX IF NOT EXISTS idx_profiles_owner        ON profiles(owner_id);
CREATE INDEX IF NOT EXISTS idx_profiles_visibility   ON profiles(visibility);
CREATE INDEX IF NOT EXISTS idx_story_items_story     ON story_items(story_id, start_time_ms);
```

### 7.4 SQLite WAL + Tuning

```python
# backend/database/session.py
@event.listens_for(engine, "connect")
def configure_sqlite(conn, _):
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")   # safe with WAL
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA cache_size=-32000")    # 32 MB
    conn.execute("PRAGMA mmap_size=268435456")  # 256 MB mmap
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA wal_autocheckpoint=1000")
```

### 7.5 Automatic Data Retention

Add to `capture_settings` and `generation_settings`:

```sql
ALTER TABLE capture_settings    ADD COLUMN retention_days INTEGER DEFAULT 90;
ALTER TABLE generation_settings ADD COLUMN retention_days INTEGER DEFAULT 365;
```

Add startup migration that deletes rows older than `retention_days`:

```python
def _enforce_retention(engine, tables):
    with engine.connect() as conn:
        conn.execute(text(
            "DELETE FROM captures WHERE created_at < datetime('now', :delta)"
        ), {"delta": f"-{capture_retention} days"})
```

---

## 8. Security Hardening Plan

### 8.1 Authentication

**Session token flow (not JWT — avoids the "valid until expiry" problem):**

```
Client                          Backend
  │── POST /auth/login ────────▶ │
  │   {username, password}       │ 1. bcrypt.verify(password, hash)
  │                               │ 2. INSERT INTO sessions (id, user_id, expires_at)
  │◀── {session_token, expires} ──│
  │                               │
  │── GET /captures ─────────────▶│
  │   Authorization: Bearer <tok> │ 3. SELECT FROM sessions WHERE id=tok
  │                               │    AND expires_at > NOW()
  │                               │    AND revoked = 0
  │                               │ 4. UPDATE sessions SET last_used = NOW()
  │◀── captures list ─────────────│
```

**Session hardening:**
- 32-byte cryptographically random token (not predictable UUID)
- Server-side revocation (logout invalidates the token immediately)
- Session TTL: configurable, default 8 hours
- IP binding (optional, per `voicetuner.yaml`)
- Max concurrent sessions per user: configurable (default 3)

**Password policy:**
- Minimum 12 characters
- bcrypt cost factor 12 (re-evaluate annually; increase as hardware gets faster)
- No storage of password history in v1.0 — add in v1.1

**TOTP MFA (optional in v1.0, required for admin in v1.1):**
```python
import pyotp
secret = pyotp.random_base32()              # stored encrypted in users.mfa_secret
totp = pyotp.TOTP(secret)
totp.verify(user_provided_code)             # 30-second window
```

### 8.2 Encryption at Rest

**API keys — Tier 1 (Desktop):**  
`tauri-plugin-stronghold` — Tauri's IOTA Stronghold vault backed by the OS keychain. Keys never in SQLite, never in `.env`.

```typescript
// app/src/lib/keychain.ts
import { Stronghold } from '@tauri-apps/plugin-stronghold'

const vault = await Stronghold.load(`${appDataDir}/voicetuner.hold`, APP_PASSWORD)
const store = vault.getStore('credentials')

export const saveKey = async (name: string, value: string) =>
  store.insert(name, [...new TextEncoder().encode(value)])

export const getKey = async (name: string): Promise<string | null> => {
  const raw = await store.get(name)
  return raw ? new TextDecoder().decode(new Uint8Array(raw)) : null
}
```

**API keys — Tier 2/3 (Server):**  
Environment variables injected via Docker secrets or HashiCorp Vault. Never stored in the database.

**MFA secrets:**  
AES-256-GCM encrypted in `users.mfa_secret` using a key derived from a server secret (`SECRET_KEY` env var, 32-byte random, never in version control).

**Audio files:**  
File-system level encryption via LUKS (Linux) or BitLocker (Windows). Not application-level — defer to infrastructure.

### 8.3 Network Security

**Backend binds to loopback by default:**
```python
# backend/server.py — enforced, not optional
if host not in ("127.0.0.1", "localhost", "::1") and tier == "desktop":
    raise SystemExit(
        "Desktop mode: server must bind to loopback. "
        "Use --tier team or --tier enterprise for network binding."
    )
```

**TLS for Tier 2/3:**
Nginx handles TLS termination. Self-signed certs acceptable for internal deployments; provide Let's Encrypt + `certbot` config for internet-facing.

**CORS — locked down for production:**
```python
ALLOWED_ORIGINS = {
    "desktop": ["tauri://localhost", "https://tauri.localhost", "http://tauri.localhost"],
    "team": [os.environ["VOICETUNER_ORIGIN"]],        # must be set explicitly
    "enterprise": [os.environ["VOICETUNER_ORIGIN"]],
}
```

### 8.4 MCP Endpoint Authentication

```python
# backend/mcp_server/server.py
async def mcp_auth_middleware(request: Request, call_next):
    # Always allow loopback without auth (desktop MCP agents)
    if request.client.host in ("127.0.0.1", "::1"):
        return await call_next(request)
    # Non-loopback: require Bearer token
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer ") or auth[7:] != get_mcp_token():
        log_audit("MCP_AUTH_FAILURE", ip=request.client.host, result="denied")
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    return await call_next(request)
```

MCP token: 32-byte random hex, generated on first start, stored in keychain (Tier 1) or an env var (Tier 2/3).

### 8.5 Path Traversal Guard

```python
# backend/routes/audio.py — applied to EVERY file-serving route
def _safe_resolve(requested_path: str) -> Path:
    data_dir = config.get_data_dir().resolve()
    resolved = (data_dir / requested_path).resolve()
    if not str(resolved).startswith(str(data_dir)):
        raise HTTPException(status_code=403, detail="Access denied")
    if not resolved.exists():
        raise HTTPException(status_code=404)
    return resolved
```

### 8.6 Rate Limiting

```python
# backend/app.py — per-user, per-endpoint limits via slowapi
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=lambda req: req.state.user_id or get_remote_address(req))

# Applied at route level:
@limiter.limit("60/minute")    # captures — dictation: up to 1/s
@limiter.limit("20/minute")    # generations — TTS: up to ~1 every 3 s
@limiter.limit("600/minute")   # reads (list, get) — generous
```

### 8.7 Dependency Security

```bash
# Add to CI pipeline
pip-audit --vulnerability-service pypi -r backend/requirements.txt
```

Pin all dependencies to exact versions in production (`requirements.lock`), generated from `requirements.txt` via `pip-compile`.

### 8.8 CI Key-Leak Detection

```bash
# scripts/check-no-keys.sh — run in pre-commit and CI
patterns="SARVAM_API_KEY|GROQ_API_KEY|ELEVENLABS_API_KEY|password_hash|mfa_secret"
if grep -rE "$patterns" app/src tauri/src --include="*.ts" --include="*.tsx" --include="*.rs"; then
    echo "FAIL: sensitive string found in frontend/Rust source"
    exit 1
fi
```

---

## 9. Role-Based Access Control Design

### 9.1 Role Definitions

| Role | ID | Description |
|------|-----|-------------|
| System Admin | `admin` | Full access. Manage users, providers, system config, all data. |
| Voice Manager | `voice_manager` | Manage voice profiles and presets org-wide. Cannot manage users. |
| Power User | `power_user` | Full voice features for own data. Cannot manage users or org profiles. |
| Standard User | `user` | Dictation + limited generation (quotas apply). Cannot use Stories or export. |
| Read-Only | `readonly` | View own captures and generations. No creation. |
| API Agent | `api` | Programmatic access only. MCP tools. No UI. |

### 9.2 Permission Matrix

| Permission | admin | voice_manager | power_user | user | readonly | api |
|------------|:-----:|:-------------:|:----------:|:----:|:--------:|:---:|
| `profiles:read:own` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `profiles:read:org` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `profiles:create` | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ |
| `profiles:update:own` | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ |
| `profiles:update:any` | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |
| `profiles:delete:own` | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ |
| `profiles:delete:any` | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |
| `generations:create` | ✅ | ✅ | ✅ | ✅ | ❌ | ✅ |
| `generations:read:own` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `generations:read:any` | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |
| `captures:create` | ✅ | ✅ | ✅ | ✅ | ❌ | ✅ |
| `captures:read:own` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `captures:read:any` | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |
| `stories:manage` | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ |
| `effects:manage_presets` | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ |
| `export:create` | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ |
| `settings:read` | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ |
| `settings:write:org` | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| `users:read` | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| `users:manage` | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| `providers:configure` | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| `audit:read` | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| `mcp:speak` | ✅ | ✅ | ✅ | ✅ | ❌ | ✅ |

### 9.3 RBAC Enforcement Pattern

```python
# backend/auth/rbac.py

from functools import wraps
from fastapi import Depends, HTTPException
from .session import get_current_user

class RequirePermission:
    """FastAPI dependency that enforces a permission."""
    def __init__(self, permission: str):
        self.permission = permission

    async def __call__(self, user=Depends(get_current_user)):
        if not user.has_permission(self.permission):
            raise HTTPException(
                status_code=403,
                detail=f"Permission '{self.permission}' required.",
            )
        return user

# Usage in routes:
@router.delete("/profiles/{profile_id}")
async def delete_profile(
    profile_id: str,
    user=Depends(RequirePermission("profiles:delete:own")),
    db: Session = Depends(get_db),
):
    # Service layer does the own/any check using user.id
    success = await profiles.delete_profile(profile_id, db=db, requesting_user=user)
    ...
```

### 9.4 Resource Ownership Check Pattern

```python
# backend/services/profiles.py

async def delete_profile(profile_id: str, db: Session, requesting_user: User) -> bool:
    profile = db.query(DBVoiceProfile).filter_by(id=profile_id).first()
    if not profile:
        return False

    # Ownership check at service layer (not route layer)
    if profile.owner_id != requesting_user.id:
        if not requesting_user.has_permission("profiles:delete:any"):
            raise PermissionError("Cannot delete another user's profile.")

    # ... proceed with deletion
```

### 9.5 Tier 1 (Desktop) — Single-User Mode

In desktop mode, RBAC is bypassed entirely. A synthetic `admin` user is auto-created on first launch and all requests are attributed to it. No login screen in Tier 1 — the OS login is the authentication layer.

```python
# backend/app.py
if config.tier == "desktop":
    app.dependency_overrides[get_current_user] = lambda: DESKTOP_ADMIN_USER
```

---

## 10. Audit Logging Design

### 10.1 Action Taxonomy

```python
# backend/audit/actions.py

class AuditAction:
    # Authentication
    AUTH_LOGIN          = "AUTH_LOGIN"
    AUTH_LOGOUT         = "AUTH_LOGOUT"
    AUTH_LOGIN_FAILED   = "AUTH_LOGIN_FAILED"
    AUTH_MFA_ENABLED    = "AUTH_MFA_ENABLED"
    AUTH_SESSION_REVOKED = "AUTH_SESSION_REVOKED"

    # Users
    USER_CREATE         = "USER_CREATE"
    USER_UPDATE         = "USER_UPDATE"
    USER_DELETE         = "USER_DELETE"
    USER_ROLE_CHANGE    = "USER_ROLE_CHANGE"
    USER_DEACTIVATE     = "USER_DEACTIVATE"

    # Voice Profiles
    PROFILE_CREATE      = "PROFILE_CREATE"
    PROFILE_UPDATE      = "PROFILE_UPDATE"
    PROFILE_DELETE      = "PROFILE_DELETE"
    PROFILE_EXPORT      = "PROFILE_EXPORT"
    PROFILE_IMPORT      = "PROFILE_IMPORT"
    SAMPLE_ADD          = "SAMPLE_ADD"
    SAMPLE_DELETE       = "SAMPLE_DELETE"

    # Generations
    GENERATION_CREATE   = "GENERATION_CREATE"
    GENERATION_DELETE   = "GENERATION_DELETE"
    GENERATION_EXPORT   = "GENERATION_EXPORT"

    # Captures
    CAPTURE_CREATE      = "CAPTURE_CREATE"
    CAPTURE_DELETE      = "CAPTURE_DELETE"
    CAPTURE_EXPORT      = "CAPTURE_EXPORT"

    # System
    PROVIDER_CONFIG     = "PROVIDER_CONFIG"
    SETTINGS_CHANGE     = "SETTINGS_CHANGE"
    BACKUP_CREATE       = "BACKUP_CREATE"
    BACKUP_RESTORE      = "BACKUP_RESTORE"
    SYSTEM_STARTUP      = "SYSTEM_STARTUP"
    SYSTEM_SHUTDOWN     = "SYSTEM_SHUTDOWN"

    # Security
    PERMISSION_DENIED   = "PERMISSION_DENIED"
    RATE_LIMIT_HIT      = "RATE_LIMIT_HIT"
    MCP_AUTH_FAILURE    = "MCP_AUTH_FAILURE"
    SUSPICIOUS_PATH     = "SUSPICIOUS_PATH"
```

### 10.2 Audit Logger Service

```python
# backend/audit/logger.py

import uuid
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from ..database.models import AuditLog

class AuditLogger:
    def __init__(self, db_factory):
        self._db_factory = db_factory

    def log(
        self,
        action: str,
        *,
        user_id: str | None = None,
        username: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
        result: str = "success",
        details: dict | None = None,
    ) -> None:
        db = self._db_factory()
        try:
            db.add(AuditLog(
                id=str(uuid.uuid4()),
                timestamp=datetime.now(timezone.utc),
                user_id=user_id,
                username=username,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                ip_address=ip_address,
                user_agent=user_agent,
                result=result,
                details=json.dumps(details) if details else None,
            ))
            db.commit()
        except Exception:
            logger.error("Audit log write failed for action %s", action, exc_info=True)
            # Never let audit failure block the operation
        finally:
            db.close()
```

### 10.3 Audit Middleware

```python
# backend/audit/middleware.py

AUDITED_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
SKIP_PATHS = {"/health", "/audio/", "/events/"}

class AuditMiddleware:
    async def __call__(self, request: Request, call_next):
        if request.method not in AUDITED_METHODS:
            return await call_next(request)
        if any(request.url.path.startswith(p) for p in SKIP_PATHS):
            return await call_next(request)

        response = await call_next(request)
        result = "success" if response.status_code < 400 else "failure"

        audit.log(
            action=_infer_action(request.method, request.url.path),
            user_id=getattr(request.state, "user_id", None),
            username=getattr(request.state, "username", None),
            ip_address=request.client.host,
            user_agent=request.headers.get("User-Agent"),
            result=result,
        )
        return response
```

### 10.4 Audit Log Retention & Export

- Default retention: 2 years (configurable)
- Export format: NDJSON or CSV via admin CLI:
  ```bash
  voicetuner-admin audit export --from 2026-01-01 --to 2026-06-30 --output audit.ndjson
  voicetuner-admin audit export --user john.doe --output john_audit.csv
  ```
- Audit log is append-only; no `UPDATE` or `DELETE` permitted on `audit_log` table by application user
- PostgreSQL: grant `SELECT, INSERT` on `audit_log` to app user; deny `UPDATE, DELETE`

---

## 11. Backup & Disaster Recovery Plan

### 11.1 What to Back Up

| Asset | Location | Criticality | RPO |
|-------|----------|-------------|-----|
| SQLite/PostgreSQL database | `data/voicetuner.db` or PostgreSQL | Critical | 1 hour |
| Audio files (profiles, generations, captures) | `data/profiles/`, `data/generations/`, `data/captures/` | High | 4 hours |
| Config file | `voicetuner.yaml` | High | On change |
| TTS/STT/LLM models | `models/` | Medium | Weekly (re-downloadable) |
| API keys / credentials | OS keychain or env vars | Critical | On change (separate) |

### 11.2 Backup Procedures

**Tier 1 — Desktop:**

```bash
# voicetuner-admin backup create
# Creates a timestamped ZIP:
# voicetuner-backup-2026-06-11T14-30.zip
#   ├── voicetuner.db
#   ├── config/voicetuner.yaml
#   └── data/profiles/**   (audio files)
#   (excludes generations/captures by default — too large; add --include-media to include)
```

**Tier 2 — Team Server (Docker):**

```yaml
# docker-compose.yml — backup service
backup:
  image: postgres:16-alpine
  volumes:
    - postgres_data:/var/lib/postgresql/data:ro
    - voicetuner_data:/mnt/voicetuner:ro
    - ./backups:/backups
  command: |
    sh -c "
      pg_dump -h db -U voicetuner voicetuner | gzip > /backups/db-$(date +%Y%m%d-%H%M).sql.gz
      rsync -a /mnt/voicetuner/ /backups/data-$(date +%Y%m%d-%H%M)/
      find /backups -mtime +30 -delete
    "
  profiles: [backup]   # run manually: docker compose --profile backup run backup
```

**Tier 3 — Enterprise:**

```bash
# Cron: every hour (database), every 4 hours (audio)
0 * * * *  postgres: pg_basebackup -h primary -D /backup/pgbase --checkpoint=fast --wal-method=stream
0 */4 * *  rsync -av --delete /mnt/voicetuner/data/ backup-server:/voicetuner/data/
```

### 11.3 Recovery Procedures

**Full restore (Tier 1):**
```bash
voicetuner-admin backup restore voicetuner-backup-2026-06-11T14-30.zip
# 1. Stops server
# 2. Replaces data/ from ZIP
# 3. Runs migrations (idempotent)
# 4. Restarts server
```

**Database-only restore (Tier 2):**
```bash
docker compose stop backend
gunzip -c backups/db-20260611-1430.sql.gz | psql -h localhost -U voicetuner voicetuner
docker compose start backend
```

**Point-in-time recovery (Tier 3):**
PostgreSQL streaming replication + WAL archiving to S3/NFS. Recovery time objective: < 30 minutes.

### 11.4 Recovery Testing Schedule

| Test | Frequency | Owner |
|------|-----------|-------|
| Restore from backup to staging env | Monthly | DevOps |
| Database-only restore timing test | Quarterly | DevOps |
| Full DR failover simulation | Bi-annually | Architect + DevOps |
| Backup integrity check (checksum) | Weekly (automated) | CI/CD |

---

## 12. High Availability Recommendations

### 12.1 Tier 1 (Desktop) — Not Applicable

Single-user desktop. HA is the OS's concern. VoiceTuner should start cleanly after OS restart (systemd service or Tauri auto-start).

### 12.2 Tier 2 (Team Server) — Basic Resilience

```yaml
# docker-compose.yml — production settings
backend:
  restart: unless-stopped
  healthcheck:
    test: ["CMD", "curl", "-f", "http://localhost:17493/health"]
    interval: 30s
    timeout: 10s
    retries: 3
    start_period: 60s   # allow model warm-up

db:
  restart: unless-stopped
  healthcheck:
    test: ["CMD", "pg_isready", "-U", "voicetuner"]
    interval: 10s
    retries: 5
```

**Single point of failure:** the backend node. Acceptable for team tier. Mitigation: `restart: unless-stopped` recovers from crashes without human intervention.

### 12.3 Tier 3 (Enterprise) — Full HA

```
Clients
   │
   ▼
┌──────────────────────┐
│    HAProxy 2.x        │  Active-passive pair (Keepalived VIP)
│    Port 443 (TLS)     │  Health-check: GET /health → 200 required
└──────┬───────┬────────┘
       │       │
   ┌───▼──┐ ┌──▼───┐
   │ App1 │ │ App2 │  Two backend nodes (N+1 minimum)
   │ :8000│ │ :8000│  Each with local GPU or shared GPU node
   └──────┘ └──────┘
       │       │
       └───┬───┘
           │
    ┌──────▼───────┐
    │  PostgreSQL   │  Primary + 1 hot standby (streaming replication)
    │  Primary :5432│  Failover: Patroni or repmgr
    └──────────────┘
           │
    ┌──────▼───────┐
    │   NFS Server  │  Shared audio storage
    │   /mnt/audio  │  Mounted on both app nodes
    └──────────────┘

    ┌──────────────┐
    │  Redis 7      │  Session store, rate-limit counters, provider health cache
    │  (Sentinel HA)│
    └──────────────┘
```

**Key decisions:**
- Models on shared NFS (read-only mount on app nodes) — no need to download per-node
- Session tokens in Redis — node-stateless, any node handles any request
- Audio files on NFS — both nodes write to the same store
- PostgreSQL failover automated via Patroni — RTO < 30 seconds
- HAProxy health-check removes a node from rotation within 90 seconds of failure

**Scaling rules:**
- Add backend nodes horizontally. No application state on nodes.
- GPU nodes: one GPU-equipped node per 20 concurrent heavy users (TTS/STT inference)
- Non-GPU nodes: lightweight tasks (captures, list queries, MCP speak events)

---

## 13. Production Deployment Plan

### Phase 1 — Foundation Cleanup (Weeks 1–2)

**Goal:** Remove dead code. Establish stable base. No user-visible changes.

```
Deliverables:
  ✓ Delete chatterbox_turbo_backend.py (merge turbo=True into chatterbox_backend.py)
  ✓ Delete hume_backend.py (TADA — experimental, unstable deps)
  ✓ Delete qwen_custom_voice_backend.py (merge preset path into qwen_backend.py)
  ✓ Delete services/task_queue.py, services/cuda.py, services/tts.py,
         services/transcribe.py, services/llm.py (delegation shims)
  ✓ Delete routes/cuda.py, routes/models.py, routes/tasks.py
  ✓ Delete utils/hf_offline_patch.py, utils/hf_progress.py, utils/dac_shim.py,
         utils/platform_detect.py, utils/progress.py, utils/tasks.py
  ✓ Remove 12 dead dependencies from requirements.txt
  ✓ Run pytest — all existing tests pass
  ✓ Verify: just dev:backend starts in < 10 s (model cold-start separately)
```

**Acceptance criteria:** `pytest` green. Backend starts cleanly. No 500 errors on existing routes.

### Phase 2 — Provider Plugin Architecture (Weeks 2–4)

**Goal:** All speech capabilities go through the ProviderRegistry. Config-driven.

```
Deliverables:
  ✓ backend/providers/ package (base.py, registry.py, local/, cloud/)
  ✓ Wrap existing 4 TTS backends as TTSProvider implementations
  ✓ Wrap Whisper STT as STTProvider implementation
  ✓ Wrap Qwen3 LLM as LLMProvider implementation
  ✓ Move cloud adapters (sarvam, groq, elevenlabs) to providers/cloud/
  ✓ voicetuner.yaml configuration loading (backend/config.py)
  ✓ Update generation.py, speech_router.py, refinement.py, personality.py
       to use ProviderRegistry
  ✓ Update GET /health to return per-provider status
  ✓ Add ProviderNotFoundError → 503 HTTP mapping
```

**Acceptance criteria:** `voicetuner.yaml` with `sarvam.enabled: false` still works fully. Switching providers requires only config change, not code change.

### Phase 3 — User Auth & RBAC (Weeks 4–6)

**Goal:** Multi-user-capable backend with session authentication and RBAC.

```
Deliverables:
  ✓ DB: users, roles, sessions tables + migration
  ✓ backend/auth/ package: session.py, rbac.py, password.py, mfa.py
  ✓ POST /auth/login, /auth/logout, /auth/refresh
  ✓ RequirePermission dependency wired into all routes
  ✓ User ownership columns on profiles, generations, captures
  ✓ Desktop mode: bypass auth with synthetic DESKTOP_ADMIN_USER
  ✓ voicetuner-admin CLI: users create, users list, users set-role
  ✓ Seed default roles (admin, voice_manager, power_user, user, readonly, api)
```

**Acceptance criteria:** Desktop mode works identically. Team mode requires login. Permission denials return 403 (not 500).

### Phase 4 — Audit Logging (Week 6–7)

**Goal:** Every mutating operation leaves an immutable audit trail.

```
Deliverables:
  ✓ DB: audit_log table + migration
  ✓ backend/audit/ package: logger.py, middleware.py, actions.py
  ✓ AuditMiddleware registered in app.py
  ✓ Manual audit.log() calls in auth routes and sensitive operations
  ✓ GET /audit (admin-only) with pagination + filters
  ✓ voicetuner-admin audit export command
```

**Acceptance criteria:** Every POST/PUT/DELETE leaves a row in audit_log. Audit log write failure never blocks the original request.

### Phase 5 — Database Hardening (Week 7)

**Goal:** Performance, integrity, PostgreSQL support.

```
Deliverables:
  ✓ SQLite WAL mode + pragma tuning in session.py
  ✓ Missing indexes migration
  ✓ Retention policy migration
  ✓ PostgreSQL dialect support in session.py
  ✓ docker-compose.yml with PostgreSQL service
  ✓ Test: migrate existing SQLite data to PostgreSQL
```

**Acceptance criteria:** List queries on 10k generations < 50 ms. PostgreSQL backend passes all tests.

### Phase 6 — Security Hardening (Week 8)

**Goal:** Encrypted keys, MCP auth, CSP, rate limiting, path traversal guard.

```
Deliverables:
  ✓ tauri-plugin-stronghold for key storage (Tier 1)
  ✓ MCP bearer token middleware
  ✓ Content Security Policy in tauri.conf.json
  ✓ Path traversal guard in routes/audio.py
  ✓ slowapi rate limiting on /captures and /generate
  ✓ CI: pip-audit + check-no-keys.sh
  ✓ Loopback enforcement in server.py
```

**Acceptance criteria:** `strings voicetuner-server` binary shows no API keys. MCP returns 401 from non-loopback without token. `pip-audit` passes.

### Phase 7 — UI Simplification (Week 9–10)

**Goal:** Enterprise-ready UI — remove dead components, add provider status.

```
Deliverables:
  ✓ Delete ModelsTab.tsx → replace with ProviderStatus page
  ✓ Delete GpuPage.tsx → fold GPU info into /health display
  ✓ First-launch onboarding (Tier 2/3 only — prompt for admin user creation)
  ✓ Provider health badges in app header
  ✓ Engine picker simplified to Standard / Premium radio
  ✓ User-friendly error messages for all provider failure modes
  ✓ Login screen for Tier 2/3 (full-screen, no redirect to main UI until authenticated)
```

**Acceptance criteria:** A first-time Tier 2 deployment guides admin to create an account and test providers before seeing main UI.

### Phase 8 — Build Pipeline & Distribution (Week 11–12)

**Goal:** Signed, reproducible, automated builds for all tiers.

```
Deliverables:
  ✓ GitHub Actions: .github/workflows/build-desktop.yml (macOS arm64, macOS x64, Windows)
  ✓ GitHub Actions: .github/workflows/build-docker.yml
  ✓ PyInstaller spec updated — no TADA/turbo/custom-voice deps
  ✓ macOS: Apple Developer signing + notarization
  ✓ Windows: Authenticode signing
  ✓ Docker images: voicetuner/server:1.0, voicetuner/server:1.0-gpu
  ✓ Model pre-download script: scripts/download-models.sh (run at deploy time, not runtime)
  ✓ Helm chart (optional, for Kubernetes-based enterprise)
```

**Acceptance criteria:** Clean-machine install via `.dmg`/`.msi`/`docker compose up` works without internet after initial model download.

### Phase 9 — QA & Launch (Week 13–14)

**Goal:** Tested, documented, v1.0.0 tag.

```
Deliverables:
  ✓ End-to-end test matrix (see §14)
  ✓ Performance benchmarks (generation latency p50/p95/p99 per engine)
  ✓ Native-speaker review: hi + te translations
  ✓ Security penetration test (internal red team or third-party)
  ✓ Disaster recovery drill
  ✓ User documentation: admin guide, user guide, MCP integration guide
  ✓ v1.0.0 tag → build pipeline → release artifacts
```

---

## 14. VoiceTuner v1.0 Enterprise Roadmap

### Phase Timeline

```
Weeks 1-2    Phase 1: Foundation Cleanup
               Dead engine removal, dependency reduction
               Binary: ~4 GB → ~2 GB (still has torch for Qwen/Whisper)
               Startup: 90 s → 30 s (model load remains)

Weeks 2-4    Phase 2: Provider Plugin Architecture
               ProviderRegistry, YAML config, fallback chains
               Sarvam/Groq/ElevenLabs become optional plugins

Weeks 4-6    Phase 3: User Auth & RBAC
               Multi-user capable; desktop single-user still works

Weeks 6-7    Phase 4: Audit Logging
               Immutable trail for every mutation

Week 7       Phase 5: Database Hardening
               WAL, indexes, PostgreSQL, retention

Week 8       Phase 6: Security Hardening
               Encrypted keys, MCP auth, CSP, rate limiting

Weeks 9-10   Phase 7: UI Simplification
               Provider status, onboarding, clean engine picker

Weeks 11-12  Phase 8: Build Pipeline
               Signed installers, Docker images, Helm chart

Weeks 13-14  Phase 9: QA & Launch
               Test matrix, security review, DR drill, v1.0.0
```

### End-to-End Test Matrix

| Scenario | Tier 1 | Tier 2 | Tier 3 |
|----------|:------:|:------:|:------:|
| Air-gapped generation (Qwen TTS, all 3 languages) | ✅ | ✅ | ✅ |
| Air-gapped STT (Whisper turbo, all 3 languages) | ✅ | ✅ | ✅ |
| Speaker ID: create profile, upload samples, verify badge | ✅ | ✅ | ✅ |
| Dictation: hotkey, record, paste | ✅ | — | — |
| Stories: multi-voice timeline render | ✅ | ✅ | ✅ |
| Effects: reverb, pitch shift, compressor chain | ✅ | ✅ | ✅ |
| MCP: Claude Code agent speaks in bound voice | ✅ | ✅ | ✅ |
| RBAC: user cannot delete another user's profile | — | ✅ | ✅ |
| RBAC: admin can view audit log | — | ✅ | ✅ |
| Audit: every generation leaves a row | ✅ | ✅ | ✅ |
| Backup + restore (full) | ✅ | ✅ | ✅ |
| Optional Sarvam TTS (if key present) | ✅ | ✅ | ✅ |
| Optional Groq STT (if key present) | ✅ | ✅ | ✅ |
| HA failover: kill node 1, requests serve from node 2 | — | — | ✅ |
| PostgreSQL failover: kill primary, standby takes over | — | — | ✅ |

### Summary: Before vs After

| Dimension | Current | v1.0 Enterprise |
|-----------|---------|-----------------|
| TTS engines | 9 (7 local + 2 cloud) | 4 local + 3 optional cloud |
| STT engines | 3 | 1 local (Whisper) + 2 optional cloud |
| LLM | 1 local (Qwen3) | 1 local (Qwen3/Llama) + 1 optional cloud (Groq) |
| Provider architecture | hardcoded engine dispatch | ProviderRegistry + fallback chains |
| Deployment config | scattered env vars | single `voicetuner.yaml` |
| Database backends | SQLite only | SQLite (Tier 1) + PostgreSQL (Tier 2/3) |
| Authentication | none | session tokens + bcrypt + TOTP (opt.) |
| RBAC | none | 6 roles, 20+ permissions |
| Audit logging | none | immutable `audit_log` table |
| Backup | manual | CLI tool + Docker cron |
| High availability | none | N+1 nodes + PostgreSQL streaming replication |
| Key storage | plaintext `.env` | OS keychain (Stronghold) or Docker secrets |
| MCP auth | none (loopback only) | bearer token for non-loopback |
| Binary size | ~4 GB | ~2 GB (torch still needed for local models) |
| Cold start | 30–90 s | 15–30 s (Whisper/Qwen load) |
| Internet required | No | No (cloud providers optional) |
| Air-gap capable | Yes (all local models) | Yes (all local models) |
| Languages | en/hi/te | en/hi/te |
| Deployment tiers | 1 (desktop only) | 3 (desktop, team, enterprise) |
```
