# VoiceTuner — Project Overview

A production-quality trilingual voice studio (English / Hindi / Telugu) built as a Tauri 2 desktop app with a Python FastAPI backend, React frontend, and cloud speech integration via Sarvam AI.

---

## Quick Start

```bash
# Install all deps
just setup

# Start backend + frontend together (dev mode)
just dev

# Backend only
just dev:backend          # http://localhost:17493

# Run tests
just test
```

**Required:** Python 3.12+, Rust, Bun  
**Minimum API key needed:** `SARVAM_API_KEY` (free tier) — put it in `.env` at the repo root.

---

## Repo Layout

```
voicetuner/
├── app/          React + TypeScript frontend (shared by desktop + web)
├── backend/      Python FastAPI server (TTS, STT, LLM, speaker ID)
├── tauri/        Tauri 2 desktop shell (Rust — hotkeys, clipboard, audio capture)
├── web/          Thin web-only wrapper around /app
├── landing/      Next.js marketing site (voicetuner.app)
├── docs/         Fumadocs documentation (MDX)
├── scripts/      Build / release automation
├── justfile      Task runner
├── .env          API keys (gitignored — never committed)
└── SETUP.md      Free-tier quickstart
```

---

## Architecture in One Sentence

> The **Tauri shell** captures mic audio globally, sends it to the **FastAPI backend** for STT + speaker ID, stores it as a **Capture**, optionally refines the transcript with a local LLM, auto-pastes the result, and also drives TTS generation via 9 local or 3 cloud engines — all persisted in a local **SQLite** database.

---

## Backend Deep Dive (`/backend`)

### Entry Points

| File | Purpose |
|------|---------|
| `backend/main.py` | Uvicorn entry — starts FastAPI app on port 17493 |
| `backend/app.py` | FastAPI factory, CORS, middleware, router mounts |
| `backend/server.py` | PyInstaller wrapper for the frozen binary |

### Routes (19 endpoint modules in `backend/routes/`)

| Route file | Prefix | What it does |
|------------|--------|-------------|
| `captures.py` | `/captures` | Record/upload audio, STT, speaker ID |
| `generations.py` | `/generate` | TTS generation + audio streaming |
| `profiles.py` | `/profiles` | Voice profile CRUD + preset catalog |
| `transcription.py` | `/transcribe` | Standalone STT endpoint |
| `speak.py` | `/speak` | Agent voice output (MCP path) |
| `effects.py` | `/effects` | Audio effects chains |
| `models.py` | `/models` | Download/manage local TTS models |
| `settings.py` | `/settings` | User preferences (capture, generation) |
| `channels.py` | `/channels` | Audio output routing |
| `mcp_bindings.py` | `/mcp-bindings` | Per-agent voice assignments |
| `stories.py` | `/stories` | Multi-track timeline projects |
| `history.py` | `/history` | Generation history |
| `events.py` | `/events` | SSE stream for speak events |
| `health.py` | `/health` | Server health check |

### Services (`backend/services/`)

| File | Responsibility |
|------|---------------|
| `captures.py` | Persist audio, run STT, run speaker ID, store result |
| `profiles.py` | Profile CRUD + GE2E embedding rebuild on sample changes |
| `generation.py` | Full TTS pipeline: chunk text → generate → crossfade → effects |
| `tts.py` | Unified TTS interface across all engines |
| `transcribe.py` | STT coordination |
| `speech_router.py` | Language → engine routing (`te` → Sarvam, `en` → local) |
| `refinement.py` | LLM transcript cleanup (remove filler words etc.) |
| `personality.py` | LLM-rewrite text through profile character before TTS |
| `task_queue.py` | Serial GPU queue — one generation at a time |
| `effects.py` | Pedalboard effects (pitch, reverb, delay, chorus, compression) |
| `versions.py` | Generation version tracking (takes + effects variants) |
| `export_import.py` | ZIP-based profile import/export |

### TTS Engines (`backend/backends/`)

| Engine | Type | Best for |
|--------|------|---------|
| `qwen_backend.py` (0.6B / 1.7B) | Local | Primary cloning engine, multilingual |
| `qwen_custom_voice_backend.py` | Local | Preset voices with expressive delivery |
| `luxtts_backend.py` | Local | Lightweight, CPU-friendly English |
| `chatterbox_backend.py` | Local | 23 languages, highest quality cloning |
| `chatterbox_turbo_backend.py` | Local | Paralinguistic tags `[laugh]` `[sigh]` |
| `hume_backend.py` (TADA) | Local | Long-form speech (700 s+), 1B/3B models |
| `kokoro_backend.py` | Local | 82 M model, 50 preset voices, very fast |

### Cloud Adapters (`backend/adapters/`)

| Adapter | Service | Free Tier |
|---------|---------|-----------|
| `sarvam.py` | Sarvam AI — Bulbul TTS + Saarika STT | ✅ Yes (primary) |
| `groq.py` | Groq — Whisper-large-v3 STT | ✅ Yes (STT fallback) |
| `elevenlabs.py` | ElevenLabs — Instant Voice Cloning | ❌ Paid only |
| `speaker_id.py` | Resemblyzer — GE2E d-vectors | ✅ Local, no cost |
| `credentials.py` | API key loader (reads `.env` in multiple locations) | — |

### Language → Engine Routing

```
English  STT  → local Whisper (MLX on Apple Silicon / PyTorch elsewhere)
Hindi    STT  → Sarvam Saarika  (cloud, free)
Telugu   STT  → Sarvam Saarika  (cloud, free)

English  TTS  → Qwen / LuxTTS / Chatterbox / Kokoro / TADA (local)
Hindi    TTS  → Sarvam Bulbul  (cloud, free)
Telugu   TTS  → Sarvam Bulbul  (cloud, free — ONLY option)
```

Override via env vars: `TTS_PROVIDER`, `STT_PROVIDER`

---

## Database Schema (`backend/database/`)

SQLite file lives at `~/.voicetuner/data/voicetuner.db` (or `VOICETUNER_DATA_DIR`).  
Migrations are custom idempotent helpers in `migrations.py` — they run at startup, no Alembic.

### Core Tables

#### `profiles`
| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID | PK |
| `name` | String | Unique display name (e.g. "Ravi") |
| `language` | String | `en` / `hi` / `te` |
| `voice_type` | String | `cloned` / `preset` / `designed` |
| `preset_engine` | String | e.g. `kokoro`, `sarvam` |
| `preset_voice_id` | String | e.g. `hitesh-te` |
| `personality` | Text | Free-form LLM character description |
| `speaker_embedding` | Text | JSON list[float] — GE2E d-vector for speaker ID |
| `effects_chain` | Text | JSON — default audio effects |

#### `profile_samples`
| Column | Notes |
|--------|-------|
| `profile_id` | FK → profiles |
| `audio_path` | Relative path to WAV |
| `reference_text` | Transcript of the sample |

#### `captures`
| Column | Notes |
|--------|-------|
| `audio_path` | Relative path to WAV |
| `source` | `dictation` / `recording` / `file` |
| `language` | Detected or specified |
| `transcript_raw` | STT output |
| `transcript_refined` | LLM-cleaned version |
| `identified_profile_id` | FK → profiles (speaker ID result) |
| `identified_profile_name` | Denormalized — profile name at ID time |
| `speaker_confidence` | Float 0–1 (cosine similarity) |

#### `generations`
| Column | Notes |
|--------|-------|
| `profile_id` | FK → profiles |
| `text` | Input text |
| `engine` | Which TTS engine was used |
| `audio_path` | Output WAV |
| `status` | `queued` → `loading_model` → `generating` → `completed` / `failed` |
| `source` | `manual` / `personality_speak` |

#### Singleton Config Tables
- **`capture_settings`** — STT model, hotkey chords, auto-refine, auto-paste
- **`generation_settings`** — chunk size, crossfade, normalize
- **`mcp_client_bindings`** — per-agent voice profile assignments

---

## Frontend (`/app`)

**Stack:** React 18, TypeScript, Tailwind CSS, TanStack Router, Zustand, TanStack Query, Vite

### Key Components (`app/src/components/`)

| Component | What the user sees |
|-----------|-------------------|
| `VoicesTab/` | Create / edit voice profiles, upload samples |
| `MainEditor/` | TTS generation form + effects picker |
| `CapturesTab/` | Dictation history — transcript, speaker badge, playback |
| `StoriesTab/` | Multi-track timeline editor |
| `EffectsTab/` | Audio effects chains and presets |
| `ModelsTab/` | Download / unload local TTS/STT models |
| `DictateWindow/` | Floating pill shown while recording (hotkey-triggered) |
| `AudioPlayer/` | WaveSurfer.js waveform + playback |
| `ServerTab/` | Server settings, GPU status, about page |

### Zustand Stores (`app/src/stores/`)

| Store | Holds |
|-------|-------|
| `generationStore` | Active generation + queue |
| `playerStore` | Playback state (which audio is playing) |
| `storyStore` | Timeline editing state |
| `effectsStore` | Effects UI state |
| `serverStore` | Backend health, server URL |
| `uiStore` | Theme, panel sizes |

### API Client (`app/src/lib/api/client.ts`)
Single `apiClient` instance with ~40 typed methods. Key ones:

```ts
apiClient.listProfiles()
apiClient.uploadProfileSample(profileId, file, text)
apiClient.generateSpeech(params)
apiClient.listCaptures()
apiClient.createCapture(formData)      // triggers STT + speaker ID
apiClient.refineCapture(id, flags)
apiClient.getModelStatus()
```

### Localization (`app/src/i18n/locales/`)
Three complete translation files — 828 string leaves each:
- `en/translation.json`
- `hi/translation.json`
- `te/translation.json`

---

## Desktop Shell (`/tauri`)

Rust + Tauri v2. Wraps the React frontend and bundles a PyInstaller backend binary.

### Key Rust Modules (`tauri/src-tauri/src/`)

| Module | Does |
|--------|------|
| `hotkey_monitor.rs` | Global keyboard tap (keytap crate) — detects chord |
| `audio_capture/` | Mic recording via cpal (screencapturekit on macOS) |
| `audio_output.rs` | Speaker playback |
| `clipboard.rs` | Read / write clipboard + Cmd+V injection |
| `accessibility.rs` | AXUIElement focus detection for auto-paste |
| `input_monitoring.rs` | macOS Input Monitoring permission check |
| `speak_monitor.rs` | Floating pill state machine |

### Bundled External Binaries
- `voicetuner-server` — PyInstaller-frozen FastAPI backend
- `voicetuner-mcp` — stdio MCP shim for agent connections

---

## Speaker Identification Flow

When a voice sample is uploaded for a profile named "Ravi":
1. Resemblyzer extracts a GE2E d-vector (256 floats) from the audio
2. Mean of all sample embeddings is stored as `profiles.speaker_embedding`

When a capture arrives (mic, file, etc.):
1. STT runs first → raw transcript
2. `identify_speaker()` in `adapters/speaker_id.py` extracts d-vector from capture audio
3. Cosine similarity is computed against every stored profile embedding
4. If best match ≥ **0.82** threshold → capture is tagged with that profile's name + confidence
5. Frontend shows a green **"Ravi"** badge in the capture list  
   and **"Ravi (92%)"** in the detail panel

Tune the threshold in `backend/adapters/speaker_id.py` → `SIMILARITY_THRESHOLD`.  
Requires `pip install resemblyzer` (downloads ~25 MB model on first run).

---

## Capture-to-Transcript Flow (End-to-End)

```
[User holds hotkey]
       ↓
[Tauri: mic → WAV via cpal]
       ↓
POST /captures (multipart WAV + language + source)
       ↓
[backend/services/captures.py]
  1. Save raw bytes → data/captures/{uuid}.wav
  2. Decode with librosa (handles webm/opus/m4a)
  3. Transcode to WAV if needed
  4. STT:  get_stt_backend_for_language(language)
           → local Whisper (en) or Sarvam Saarika (hi/te)
  5. Speaker ID: identify_speaker(audio, all_profile_embeddings)
  6. INSERT INTO captures (transcript_raw, identified_profile_name, ...)
  7. [Optional] LLM refine → transcript_refined
       ↓
[Tauri: AXUIElement auto-paste into focused text field]
       ↓
[CapturesTab: shows transcript + green "Ravi (92%)" badge]
```

---

## MCP Server (Agent Integration)

FastMCP mounted at `/mcp` — lets AI agents (Claude Code, Cursor, etc.) call VoiceTuner over HTTP or stdio.

### Tools

| Tool | What it does |
|------|-------------|
| `voicetuner.speak(text, ...)` | Generate speech in the bound voice, play it |
| `voicetuner.transcribe(audio_path)` | Transcribe an audio file |
| `voicetuner.list_profiles()` | List available voice profiles |
| `voicetuner.list_captures()` | Browse recorded captures |

### Per-Agent Voice Binding
Agents identify themselves via `X-VoiceTuner-Client-Id` header.  
Use the **MCP Bindings** tab in Settings to assign a voice per agent:
- Claude Code → "Morgan"
- Cursor → "Scarlett"

---

## Sarvam Voice Catalog (21 Preset Voices)

Voices follow the pattern `<speaker>-<lang>` (e.g. `hitesh-te`).  
Fetch live: `GET /profiles/presets/sarvam`

**Speakers:** Meera, Neel, Hitesh, Pavithra, Maitreyi, Arvind, Amol (7 total)  
**Languages per speaker:** en, hi, te

---

## API Keys Setup

```bash
# .env (repo root — gitignored)
SARVAM_API_KEY=your_key_here        # Required for Hindi/Telugu
GROQ_API_KEY=your_key_here          # Optional — STT cloud fallback
ELEVENLABS_API_KEY=your_key_here    # Optional — paid Telugu cloning
```

Get a free Sarvam key at [dashboard.sarvam.ai](https://dashboard.sarvam.ai).

---

## Common Tasks

| Task | Command / Location |
|------|--------------------|
| Add a new TTS engine | Create `backend/backends/my_engine.py` implementing `TTSBackend` protocol; register in `backend/backends/__init__.py` |
| Add a language | Edit `backend/languages.py` + add translation JSON in `app/src/i18n/locales/` |
| Change speaker ID threshold | `backend/adapters/speaker_id.py` → `SIMILARITY_THRESHOLD` |
| Add a migration | Append `_migrate_*()` helper to `backend/database/migrations.py` and call it from `run_migrations()` |
| Build desktop binary | `cd tauri && bun run tauri build` (needs Rust + PyInstaller) |
| Run backend tests | `cd backend && pytest` |
| Lint backend | `cd backend && ruff check .` |

---

## Key Design Decisions

1. **No Alembic** — custom idempotent migration helpers run at startup. Simple for a single-user SQLite app shipped as a binary.
2. **Serial GPU queue** — one TTS generation at a time to avoid GPU memory thrashing.
3. **Storage path abstraction** — all file paths stored *relative* to the data dir so the app is portable across machines.
4. **Telugu requires Sarvam** — no open-source local TTS supports Telugu; Sarvam Bulbul is the only free option.
5. **Speaker embedding in profile** — one mean d-vector per profile (averaged from all samples). No per-sample lookup needed at identification time.
6. **MCP client bindings** — per-agent voice assignment lets multiple AI tools share the same VoiceTuner instance without stepping on each other's voice.
7. **Preset vs cloned vs designed voices** — three `voice_type` values keep the UI clean: upload samples (cloned), pick from catalog (preset), describe in text (designed — future).
