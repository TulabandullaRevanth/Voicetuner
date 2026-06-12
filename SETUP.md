# VoiceTuner — Setup (Free-Tier Demo)

VoiceTuner is a trilingual voice platform supporting **English (en), Hindi (hi),
and Telugu (te)**. It runs the full voice I/O stack — text-to-speech,
speech-to-text, and voice cloning — locally where possible, with a cloud tier
(Sarvam) for the Indic languages that local engines can't serve.

This guide gets you running on **free API tiers only**.

---

## 1. Prerequisites

- **Docker** (https://docs.docker.com/get-docker/) — runs PostgreSQL + the FastAPI backend
- **Bun** (https://bun.sh) — runs the Vite dev server
- **Rust** toolchain (for the desktop app via Tauri) — optional if you only run
  the web UI

---

## 2. API keys

Create a `.env` in the repo root (copy from `.env.example`). For a free demo you
only need **one** key:

```bash
cp .env.example .env
```

| Key | Required? | Free? | Used for | Get it |
|-----|-----------|-------|----------|--------|
| `SARVAM_API_KEY` | **Yes** | ✅ free tier | Telugu/Hindi/English TTS + STT | https://dashboard.sarvam.ai |
| `GROQ_API_KEY` | Optional | ✅ free tier | Faster cloud STT (Whisper-large-v3) | https://console.groq.com/keys |
| `ELEVENLABS_API_KEY` | Optional | ❌ **paid** | Telugu/Hindi *voice cloning* only | https://elevenlabs.io |

> The `.env` is gitignored. Never commit real keys. The loader also accepts the
> lowercase names `Sarvam_apikey` / `groq_apikey` / `Elevenlabs_apikey`.

Language policy and provider routing are configurable in `.env` (defaults shown):

```ini
SUPPORTED_LANGUAGES=en,hi,te
TTS_PROVIDER=auto      # en -> local, hi/te -> Sarvam
STT_PROVIDER=auto      # en -> local Whisper, hi/te -> Sarvam/Groq
VOICE_CLONE_PROVIDER=auto
```

---

## 3. Install & run

```bash
cp .env.example .env    # add SARVAM_API_KEY (minimum required)
./start.sh              # PostgreSQL + backend in Docker, then Vite dev server
```

`./start.sh` starts PostgreSQL and the FastAPI backend in Docker (`docker-compose.dev.yml`), waits for the health check, then launches the Vite dev server locally. Press Ctrl+C to stop everything.

- Web UI: `http://localhost:5173`
- Backend API + docs: `http://localhost:17493/docs`

Backend only:

```bash
docker compose -f docker-compose.dev.yml up -d
```

---

## 4. What works on the free tier

| Capability | English | Hindi | Telugu | Provider |
|---|:--:|:--:|:--:|---|
| **Speech-to-text (dictation)** | ✅ | ✅ | ✅ | Sarvam Saarika / Groq / local Whisper |
| **Text-to-speech** | ✅ | ✅ | ✅ | Sarvam Bulbul (preset voices) / local |
| **Voice cloning** | ✅ | ✅ | ⛔ | local Chatterbox (en/hi) |

⛔ **Telugu voice *cloning*** is the only paid-gated feature — it requires an
ElevenLabs paid plan (Instant Voice Cloning). Telugu users get high-quality
**preset-voice TTS** for free instead. English/Hindi cloning runs locally and
needs no cloud key.

---

## 5. Demoing Telugu / Hindi

**Dictation (STT)** — no setup beyond the key:
1. Open **Captures** → set language to **Telugu** (or Auto).
2. Record/upload Telugu audio → it transcribes via Sarvam.

**Text-to-speech (preset voice):**
1. **Voices → New Voice → Built-in voice**.
2. Engine: **Sarvam (Indic)**.
3. Pick a voice, e.g. **Hitesh (Telugu)** or **Anushka (Hindi)**.
4. Save, then **Generate** with Telugu/Hindi text.

**Voice cloning (English/Hindi, local):**
1. **Voices → New Voice → Clone**.
2. Upload a reference sample, engine **Chatterbox**.
3. Generate in English or Hindi.

---

## 6. Notes & limitations

- **First-pass translations.** The Hindi/Telugu UI strings were machine-
  translated (Sarvam) and need a native-speaker review pass before production.
- **Cloud dependency.** Telugu TTS/STT require network access to Sarvam. The
  app degrades gracefully (clear error) if a key is missing or a provider is
  down; English remains fully local.
- **Free-tier quotas.** Sarvam/Groq free tiers have rate and usage limits —
  fine for a demo, not for production load.
- **Models download on first use.** Local engines (Whisper, Chatterbox, etc.)
  pull weights from Hugging Face on first run.

---

## 7. Configuration reference

See `backend/languages.py` for the single source of truth on supported
languages, and `backend/services/speech_router.py` for provider routing logic.
