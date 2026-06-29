#!/usr/bin/env python3
"""
Offline stub backend for VoiceTuner / Voicebox.

Stands in for the real FastAPI backend (backend/app.py) when you have no
Docker / Postgres / GPU / models / network. It implements just enough of the
HTTP API the web UI calls (app/src/lib/api/client.ts + services/) so the
frontend "works properly" offline.

Real TTS: uses macOS `say` + `afconvert` to synthesize actual speech offline.
Real STT: uses faster-whisper (local Whisper, CPU int8) if it's importable in
the running interpreter — genuine speech-to-text, no cloud. Falls back to a
canned transcript only if faster-whisper isn't installed. Install + run with:

    python3 -m venv data/offline-stub/.venv-stt
    data/offline-stub/.venv-stt/bin/pip install faster-whisper
    data/offline-stub/.venv-stt/bin/python scripts/offline-stub-server.py

Demo content (on first run): demo voices, a TTS demo story and a multilingual
demo story on the Stories timeline, plus a transcribed voice sample for the STT
flow. Zero third-party dependencies — pure Python stdlib.

    python3 scripts/offline-stub-server.py            # binds 127.0.0.1:17493
"""

import json
import math
import os
import shutil
import struct
import subprocess
import tempfile
import threading
import time
import uuid
import wave
import zipfile
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse, parse_qs

HOST = os.environ.get("STUB_HOST", "127.0.0.1")
PORT = int(os.environ.get("STUB_PORT", "17493"))

ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = ROOT / "data" / "offline-stub"
STATE_FILE = STATE_DIR / "state.json"

RATE = 22050
_lock = threading.Lock()



def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id() -> str:
    return uuid.uuid4().hex


# --------------------------------------------------------------------------- #
# Audio synthesis — REAL offline speech via macOS `say` + `afconvert`.
# --------------------------------------------------------------------------- #
# macOS ships English (Samantha) and Hindi (Lekha) voices offline. There is no
# native Telugu voice, so Telugu falls back to the Hindi synthesizer (closest
# Indic) — pronunciation will be approximate. `say -v '?'` lists what's
# installed; add more under System Settings > Accessibility > Spoken Content.
VOICE_BY_LANG = {
    "en": os.environ.get("STUB_VOICE_EN", "Samantha"),
    "hi": os.environ.get("STUB_VOICE_HI", "Lekha"),
    "te": os.environ.get("STUB_VOICE_TE", "Lekha"),
}
# Default male English voice for profiles with male-sounding names
_MALE_VOICE_EN = os.environ.get("STUB_VOICE_EN_MALE", "Reed (English (US))")

_HAVE_SAY = bool(shutil.which("say") and shutil.which("afconvert"))

_FEMALE_NAMES = {
    "samantha", "karen", "fiona", "moira", "tessa", "veena", "victoria",
    "flo", "sandy", "shelley", "grandma", "ava", "allison", "susan",
    "zoe", "kate", "serena", "laura", "alice", "amelie", "anna", "kathy",
    "lekha", "kanya", "damayanti", "mei-jia", "yuna", "kyoko",
    "sin-ji", "ting-ting", "joana", "paulina", "monica", "nora",
    "zosia", "ioana", "luciana", "carmit", "milena", "mariam",
}
# Profile first-name hints for gender detection
_FEMALE_PROFILE_HINTS = {"aria", "anushka", "priya", "neha", "divya", "pooja", "kavya",
                         "meera", "sara", "sarah", "emily", "emma", "sophia", "lisa"}
_MALE_PROFILE_HINTS   = {"revanth", "hitesh", "rohit", "arjun", "vikram", "suresh",
                         "ramesh", "raj", "ravi", "arun", "kiran", "sai", "charan"}

# Novelty/sound-effect voices to exclude from the UI picker
_NOVELTY_VOICES = {
    "bahh", "bells", "boing", "bubbles", "cellos", "good news", "bad news",
    "jester", "junior", "organ", "superstar", "trinoids", "whisper",
    "wobble", "zarvox", "albert", "ralph",
}


def _voice_for_profile(profile: dict) -> str:
    """Return the best macOS `say` voice for a profile.

    Checks (in order): explicit stub_voice on the profile → language mapping
    with gender hint from profile name → language default.
    """
    if profile.get("stub_voice"):
        return profile["stub_voice"]
    lang = profile.get("language", "en")
    if lang != "en":
        return VOICE_BY_LANG.get(lang, VOICE_BY_LANG["en"])
    # For English, guess gender from first word of profile name
    first = profile.get("name", "").split()[0].lower() if profile.get("name") else ""
    if first in _FEMALE_PROFILE_HINTS:
        return VOICE_BY_LANG["en"]          # female → Samantha
    if first in _MALE_PROFILE_HINTS:
        return _MALE_VOICE_EN               # male → Reed (English (US))
    # Fall back to female default for English
    return VOICE_BY_LANG["en"]

_SAY_VOICES_CACHE = None

def _list_say_voices():
    global _SAY_VOICES_CACHE
    if _SAY_VOICES_CACHE is not None:
        return _SAY_VOICES_CACHE
    if not _HAVE_SAY:
        _SAY_VOICES_CACHE = []
        return _SAY_VOICES_CACHE
    try:
        result = subprocess.run(["say", "-v", "?"], capture_output=True, text=True, timeout=5)
        voices = []
        for line in result.stdout.splitlines():
            if "#" not in line:
                continue
            name_lang = line.split("#")[0].strip()
            tokens = name_lang.split()
            if not tokens:
                continue
            lang = tokens[-1]          # e.g. en_US
            name = " ".join(tokens[:-1])  # e.g. "Reed (English (US))"
            if not name or not lang:
                continue
            lang_prefix = lang.split("_")[0].lower()
            base = name.split("(")[0].strip().lower()
            if base in _NOVELTY_VOICES:
                continue
            gender = "female" if base in _FEMALE_NAMES else "male"
            voices.append({"name": name, "lang": lang, "lang_prefix": lang_prefix,
                           "gender": gender})
        _SAY_VOICES_CACHE = sorted(voices, key=lambda v: (v["lang_prefix"], v["gender"], v["name"]))
    except Exception:
        _SAY_VOICES_CACHE = []
    return _SAY_VOICES_CACHE


def make_wav(seconds: float = 1.6, freq: float = 320.0, rate: int = RATE) -> bytes:
    """Fallback tone — only used if `say` is unavailable or fails."""
    n = int(seconds * rate)
    buf = BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        frames = bytearray()
        for i in range(n):
            env = min(1.0, i / (rate * 0.05), (n - i) / (rate * 0.05))
            val = int(0.25 * env * 32767 * math.sin(2 * math.pi * freq * i / rate))
            frames += struct.pack("<h", val)
        w.writeframes(bytes(frames))
    return buf.getvalue()


def synth(text: str, language: str = "en", voice: str = None) -> bytes:
    """Synthesize real speech for `text` and return WAV bytes."""
    text = (text or "").strip()
    if not text or not _HAVE_SAY:
        return make_wav()
    if voice is None:
        voice = VOICE_BY_LANG.get(language, VOICE_BY_LANG["en"])
    try:
        with tempfile.TemporaryDirectory() as d:
            txt = Path(d) / "in.txt"
            aiff = Path(d) / "out.aiff"
            wav = Path(d) / "out.wav"
            txt.write_text(text, encoding="utf-8")
            subprocess.run(["say", "-v", voice, "-r", "150", "-f", str(txt), "-o", str(aiff)],
                           check=True, timeout=120, capture_output=True)
            subprocess.run(["afconvert", "-f", "WAVE", "-d", f"LEI16@{RATE}",
                            str(aiff), str(wav)],
                           check=True, timeout=120, capture_output=True)
            return wav.read_bytes()
    except Exception as exc:  # noqa: BLE001
        print(f"[offline-stub] say failed ({exc!r}); tone fallback")
        return make_wav()


# --------------------------------------------------------------------------- #
# Real offline STT via faster-whisper (loaded once, kept warm).
# --------------------------------------------------------------------------- #
STT_MODEL_NAME = os.environ.get("STT_MODEL", "base.en")  # English-only: faster + more accurate
try:
    from faster_whisper import WhisperModel  # noqa: E402
    _HAVE_STT = True
except Exception:  # noqa: BLE001
    _HAVE_STT = False

_stt_model = None
_stt_lock = threading.Lock()


def get_stt_model():
    global _stt_model
    if _stt_model is None:
        with _stt_lock:
            if _stt_model is None:
                print(f"[offline-stub] loading whisper '{STT_MODEL_NAME}' (cpu/int8)...")
                _stt_model = WhisperModel(STT_MODEL_NAME, device="cpu", compute_type="int8")
                print("[offline-stub] whisper ready")
    return _stt_model


def transcribe_audio(audio: bytes, language: str = None):
    """Return (text, duration_seconds) for uploaded audio bytes."""
    if not audio:
        return ("", 0.0)
    if not _HAVE_STT:
        demos = _STT_DEMOS.get(language or "en", _STT_DEMOS["en"])
        _stt_counter["n"] = (_stt_counter["n"] + 1) % len(demos)
        return (demos[_stt_counter["n"]], 3.2)
    with tempfile.TemporaryDirectory() as d:
        src = Path(d) / "in.bin"
        wav = Path(d) / "in16k.wav"
        src.write_bytes(audio)
        audio_path = str(src)
        try:  # normalize to 16 kHz mono WAV when the container is decodable
            subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1",
                            str(src), str(wav)], check=True, capture_output=True, timeout=60)
            audio_path = str(wav)
        except Exception:
            pass  # let faster-whisper's PyAV decoder handle webm/opus/etc.
        try:
            # English-only models (".en") must always be decoded as English.
            if STT_MODEL_NAME.endswith(".en"):
                lang = "en"
            else:
                lang = language if language in ("en", "hi", "te") else None
            segments, info = get_stt_model().transcribe(audio_path, language=lang, beam_size=1)
            text = " ".join(s.text.strip() for s in segments).strip()
            dur = round(float(getattr(info, "duration", 0.0) or 0.0), 2)
            return (text or "[no speech detected]", dur)
        except Exception as exc:  # noqa: BLE001
            print(f"[offline-stub] whisper transcribe failed: {exc!r}")
            return ("[transcription failed]", 0.0)


def make_zip(files: dict) -> bytes:
    """Build a zip from {name: bytes} for profile/generation export downloads."""
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


def wav_duration(blob: bytes) -> float:
    try:
        with wave.open(BytesIO(blob)) as w:
            return round(w.getnframes() / float(w.getframerate()), 2)
    except Exception:
        return 1.6


def chunk_text(text: str, max_chars: int = 600) -> list:
    """Split long text into <=max_chars chunks on sentence/space boundaries."""
    import re
    text = (text or "").strip()
    if not text:
        return []
    sentences = re.split(r"(?<=[.!?।])\s+", text)
    chunks, cur = [], ""
    for s in sentences:
        while len(s) > max_chars:  # a single very long sentence: hard-split on spaces
            cut = s.rfind(" ", 0, max_chars) or max_chars
            cut = cut if cut > 0 else max_chars
            chunks.append(s[:cut].strip())
            s = s[cut:].strip()
        if len(cur) + len(s) + 1 <= max_chars:
            cur = (cur + " " + s).strip()
        else:
            if cur:
                chunks.append(cur)
            cur = s
    if cur:
        chunks.append(cur)
    return chunks


def synth_long(text: str, language: str = "en", progress=None, voice: str = None) -> bytes:
    """Synthesize arbitrarily long text by chunking + concatenating, so a 30-min
    script never hits the per-call `say` timeout. `progress(done, total)` is
    called after each chunk for SSE updates."""
    chunks = chunk_text(text)
    if len(chunks) <= 1:
        if progress:
            progress(1, 1)
        return synth(text, language, voice=voice)
    parts = []
    for i, c in enumerate(chunks):
        parts.append(synth(c, language, voice=voice))
        if progress:
            progress(i + 1, len(chunks))
    return concat_wavs(parts)


def _trim_wav_silence(frames: bytes, rate: int, threshold: int = 200, max_trim_ms: int = 120) -> bytes:
    """Strip leading and trailing silence from raw 16-bit mono PCM frames."""
    import struct
    max_samples = int(rate * max_trim_ms / 1000)
    samples = len(frames) // 2
    # find first non-silent sample
    start = 0
    for i in range(min(samples, max_samples)):
        if abs(struct.unpack_from("<h", frames, i * 2)[0]) > threshold:
            start = i
            break
    # find last non-silent sample
    end = samples
    for i in range(samples - 1, max(samples - max_samples - 1, -1), -1):
        if abs(struct.unpack_from("<h", frames, i * 2)[0]) > threshold:
            end = i + 1
            break
    return frames[start * 2: end * 2]


def concat_wavs(blobs: list) -> bytes:
    """Concatenate same-format mono 16-bit WAVs into one (for story export)."""
    out = BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        for b in blobs:
            try:
                with wave.open(BytesIO(b)) as r:
                    frames = r.readframes(r.getnframes())
                    w.writeframes(_trim_wav_silence(frames, RATE))
            except Exception:
                continue
    return out.getvalue()


# --------------------------------------------------------------------------- #
# Persistent state  (audio blobs are NOT persisted; re-synthesized on demand)
# --------------------------------------------------------------------------- #
def _profile(name, desc, lang):
    t = now_iso()
    return {"id": new_id(), "name": name, "description": desc, "language": lang,
            "created_at": t, "updated_at": t}


def _make_generation(state, profile, text, language):
    """Synthesize a clip, register it in history + audio cache, return it."""
    gid = new_id()
    audio = synth(text, language)
    gen = {
        "id": gid, "profile_id": profile["id"], "profile_name": profile["name"],
        "text": text, "language": language,
        "audio_path": f"generations/{gid}.wav", "duration": wav_duration(audio),
        "seed": None, "instruct": None, "engine": "qwen", "model_size": "1.7B",
        "created_at": now_iso(),
    }
    state["audio"][gid] = audio
    state["history"].append(gen)
    return gen


def _story_item_from_gen(story_id, gen, start_ms, track=0):
    dur_ms = int(gen["duration"] * 1000)
    return {
        "id": new_id(), "story_id": story_id, "generation_id": gen["id"],
        "version_id": None, "start_time_ms": start_ms, "track": track,
        "trim_start_ms": 0, "trim_end_ms": dur_ms, "created_at": now_iso(),
        "profile_id": gen["profile_id"], "profile_name": gen["profile_name"],
        "text": gen["text"], "language": gen["language"],
        "audio_path": gen["audio_path"], "duration": gen["duration"],
        "seed": None, "instruct": None, "engine": "qwen", "volume": 1.0,
        "generation_created_at": gen["created_at"], "versions": [],
        "active_version_id": None,
    }


def _build_story(state, name, description, lines):
    """lines = [(profile, text, language), ...] laid out sequentially on track 0."""
    sid = new_id()
    items = []
    cursor = 0
    for profile, text, language in lines:
        gen = _make_generation(state, profile, text, language)
        item = _story_item_from_gen(sid, gen, cursor)
        items.append(item)
        cursor += int(gen["duration"] * 1000) + 250  # 250ms gap between clips
    t = now_iso()
    return {"id": sid, "name": name, "description": description,
            "created_at": t, "updated_at": t, "items": items}


def _seed_state() -> dict:
    """Build demo content from scratch (first run only)."""
    print("[offline-stub] seeding demo voices, stories and STT sample...")
    state = {"profiles": [], "samples": {}, "history": [], "audio": {},
             "stories": [], "transcriptions": [], "jobs": {}}

    aria = _profile("Aria (Demo)", "Built-in English preset — offline demo.", "en")
    anushka = _profile("Anushka (Hindi)", "Sarvam Bulbul preset — offline demo.", "hi")
    hitesh = _profile("Hitesh (Telugu)", "Sarvam Bulbul preset — offline demo.", "te")
    state["profiles"] = [aria, anushka, hitesh]

    # --- TTS demo story: English narration across several spoken clips -------
    state["stories"].append(_build_story(
        state,
        "🎙️ TTS Demo — Welcome to Voicebox",
        "Several text-to-speech clips stitched on one timeline. Press play.",
        [
            (aria, "Welcome to Voicebox, your offline voice studio.", "en"),
            (aria, "This story was assembled from several text to speech clips.", "en"),
            (aria, "Each clip on the timeline is real synthesized speech.", "en"),
            (aria, "Press play to hear them stitched together.", "en"),
        ],
    ))

    # --- TTS demo story: multilingual, voice changes per clip ----------------
    state["stories"].append(_build_story(
        state,
        "🌐 TTS Demo — Multilingual",
        "Different voices and languages on the same timeline.",
        [
            (aria, "Voicebox can switch voices per clip.", "en"),
            (anushka, "यह हिंदी आवाज़ का एक उदाहरण है।", "hi"),
            (aria, "All synthesized offline on your machine.", "en"),
        ],
    ))

    # --- STT demo: a voice sample whose reference text was "transcribed" ------
    stt_gen_text = "This reference clip was transcribed by the offline STT demo."
    sample_audio = synth(stt_gen_text, "en")
    sample_id = new_id()
    state["samples"][aria["id"]] = [{
        "id": sample_id, "profile_id": aria["id"],
        "audio_path": f"samples/{sample_id}.wav",
        "reference_text": stt_gen_text,
    }]
    state["audio"][f"sample-{sample_id}"] = sample_audio

    return state


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            data = json.loads(STATE_FILE.read_text())
            for k, default in (("profiles", []), ("samples", {}),
                               ("history", []), ("stories", []), ("audio", {}),
                               ("transcriptions", []), ("jobs", {})):
                data.setdefault(k, default)
            data["jobs"] = {}  # transient: never restore in-flight jobs
            return data
        except Exception:
            pass
    return _seed_state()


def save_state(state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    persist = {k: v for k, v in state.items() if k not in ("audio", "jobs")}
    STATE_FILE.write_text(json.dumps(persist, indent=2, ensure_ascii=False))


# --------------------------------------------------------------------------- #
# Lookups
# --------------------------------------------------------------------------- #
_STUB_MAX_CHARS = 1800  # ~60-90 s via macOS say; stays under the 120 s timeout

def _stub_text(text: str) -> str:
    """Truncate to the last word boundary within _STUB_MAX_CHARS."""
    text = (text or "").strip()
    if len(text) <= _STUB_MAX_CHARS:
        return text
    return text[:_STUB_MAX_CHARS].rsplit(None, 1)[0]


def _sync_duration_from_path(gen_id: str, path) -> None:
    """Sync stored duration from a WAV file on disk (reads header only)."""
    try:
        with wave.open(str(path), "rb") as w:
            actual = w.getnframes() / w.getframerate()
    except Exception:
        return
    _sync_duration_value(gen_id, actual)


def _sync_duration_value(gen_id: str, actual: float) -> None:
    changed = False
    for h in STATE["history"]:
        if h["id"] == gen_id and abs(h.get("duration", 0) - actual) > 1:
            h["duration"] = actual
            changed = True
    for story in STATE.get("stories", []):
        for it in story.get("items", []):
            if it.get("generation_id") == gen_id and abs(it.get("duration", 0) - actual) > 1:
                it["duration"] = actual
                changed = True
    if changed:
        save_state(STATE)


def _sync_duration(gen_id: str, blob: bytes) -> None:
    """Update stored duration in history/stories to match the actual WAV blob."""
    _sync_duration_value(gen_id, wav_duration(blob))


def _merge_wavs_native(paths) -> bytes:
    """Concatenate WAV files preserving their native sample rate (no resampling)."""
    blobs = [p.read_bytes() for p in paths]
    if not blobs:
        return b""
    # Detect params from first file
    with wave.open(BytesIO(blobs[0])) as ref:
        nch = ref.getnchannels()
        sw = ref.getsampwidth()
        fr = ref.getframerate()
    out = BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(nch)
        w.setsampwidth(sw)
        w.setframerate(fr)
        for b in blobs:
            try:
                with wave.open(BytesIO(b)) as r:
                    w.writeframes(r.readframes(r.getnframes()))
            except Exception:
                continue
    return out.getvalue()


def _load_chunks(gen_id: str):
    """Concatenate real-backend chunk files ({gen_id}_{hash}.wav) if present.

    After merging, writes the result to the canonical disk path so future
    requests are served via the fast streaming route without re-merging.
    """
    gen_dir = ROOT / "data" / "generations"
    chunks = sorted(gen_dir.glob(f"{gen_id}_*.wav"))
    if not chunks:
        return None
    blob = _merge_wavs_native(chunks)
    # Cache on disk so next request uses the streaming route
    disk_path = gen_dir / f"{gen_id}.wav"
    try:
        disk_path.write_bytes(blob)
    except Exception:
        pass
    return blob


def get_audio(gen_id: str) -> bytes:
    blob = STATE["audio"].get(gen_id)
    if blob is not None:
        return blob
    # Prefer real-backend chunk files over the stub-synthesised single file
    blob = _load_chunks(gen_id)
    if blob is not None:
        STATE["audio"][gen_id] = blob
        _sync_duration(gen_id, blob)
        return blob
    # Serve from disk if the file was persisted by the real backend
    disk_path = ROOT / "data" / "generations" / f"{gen_id}.wav"
    if disk_path.exists():
        blob = disk_path.read_bytes()
        STATE["audio"][gen_id] = blob
        _sync_duration(gen_id, blob)
        return blob
    for h in STATE["history"]:
        if h["id"] == gen_id:
            blob = synth(_stub_text(h.get("text", "")), h.get("language", "en"))
            STATE["audio"][gen_id] = blob
            # Persist so the next play (or after a restart) is instant.
            disk_path.parent.mkdir(parents=True, exist_ok=True)
            disk_path.write_bytes(blob)
            _sync_duration(gen_id, blob)
            return blob
    for story in STATE["stories"]:           # story items may reference a gen
        for it in story["items"]:
            if it["generation_id"] == gen_id:
                blob = synth(_stub_text(it.get("text", "")), it.get("language", "en"))
                STATE["audio"][gen_id] = blob
                disk_path.parent.mkdir(parents=True, exist_ok=True)
                disk_path.write_bytes(blob)
                _sync_duration(gen_id, blob)
                return blob
    blob = make_wav()
    STATE["audio"][gen_id] = blob
    return blob


def find_story(sid):
    for s in STATE["stories"]:
        if s["id"] == sid:
            return s
    return None


def story_summary(s):
    return {"id": s["id"], "name": s["name"], "description": s.get("description"),
            "created_at": s["created_at"], "updated_at": s["updated_at"],
            "item_count": len(s["items"])}


def profile_name(pid):
    for p in STATE["profiles"]:
        if p["id"] == pid:
            return p["name"]
    return "Unknown"


def find_generation(gid):
    for h in STATE["history"]:
        if h["id"] == gid:
            return h
    return None


def run_generation_job(gid: str):
    """Background worker: synthesize (chunked) and mark the job completed.
    Keeps long text off the request thread so /generate returns instantly."""
    with _lock:
        job = STATE["jobs"].get(gid)
    if not job:
        return

    def progress(done, total):
        with _lock:
            j = STATE["jobs"].get(gid)
            if j:
                j["progress"] = round(done / total, 3)
                j["chunks_done"], j["chunks_total"] = done, total

    try:
        stub_voice = job.get("stub_voice") or None
        with _lock:
            prof = next((p for p in STATE["profiles"] if p["id"] == job["profile_id"]), None)
            prof_samples = STATE.get("samples", {}).get(job["profile_id"], []) if prof else []

        if not stub_voice and prof:
            stub_voice = _voice_for_profile(prof)
        audio = synth_long(job["text"], job["language"], progress=progress,
                           voice=stub_voice)
        dur = wav_duration(audio)
        gen = {
            "id": gid, "profile_id": job["profile_id"],
            "profile_name": profile_name(job["profile_id"]),
            "text": job["text"], "language": job["language"],
            "audio_path": f"generations/{gid}.wav", "duration": dur,
            "seed": job.get("seed"), "instruct": job.get("instruct"),
            "engine": "qwen", "model_size": job.get("model_size") or "1.7B",
            "created_at": job["created_at"],
        }
        # Persist to disk so the audio survives server restarts without re-synthesis.
        gen_file = ROOT / "data" / "generations" / f"{gid}.wav"
        gen_file.parent.mkdir(parents=True, exist_ok=True)
        gen_file.write_bytes(audio)
        with _lock:
            STATE["audio"][gid] = audio
            STATE["history"].append(gen)
            job["status"] = "completed"
            job["duration"] = dur
            save_state(STATE)
    except Exception as exc:  # noqa: BLE001
        print(f"[offline-stub] generation {gid} failed: {exc!r}")
        with _lock:
            j = STATE["jobs"].get(gid)
            if j:
                j["status"] = "failed"
                j["error"] = str(exc)


def _m(name, display, size_mb, loaded=False, downloaded=True):
    return {"model_name": name, "display_name": display,
            "downloaded": downloaded, "downloading": False,
            "size_mb": size_mb, "loaded": loaded}

MODELS = [
    # Voice generation
    _m("qwen-tts-1.7B",          "Qwen3-TTS 1.7B",              3400, loaded=True),
    _m("qwen-tts-0.6B",          "Qwen3-TTS 0.6B",              1200),
    _m("luxtts",                  "LuxTTS",                        900),
    _m("chatterbox-tts",          "Chatterbox TTS",               2100),
    _m("chatterbox-turbo",        "Chatterbox Turbo",             1400),
    _m("tada-1b",                 "TADA 1B",                      2000),
    _m("tada-3b-ml",              "TADA 3B Multilingual",         6000),
    _m("kokoro",                  "Kokoro 82M",                    330),
    _m("qwen-custom-voice-1.7B",  "Qwen CustomVoice 1.7B",       3400),
    _m("qwen-custom-voice-0.6B",  "Qwen CustomVoice 0.6B",       1200),
    # Transcription
    _m("whisper-base",   "Whisper Base",   74,  loaded=True),
    _m("whisper-small",  "Whisper Small",  244),
    _m("whisper-medium", "Whisper Medium", 769),
    _m("whisper-large",  "Whisper Large",  1500),
    _m("whisper-turbo",  "Whisper Turbo",  1500),
    # Language models
    _m("qwen3-0.6b", "Qwen3 0.6B", 400),
    _m("qwen3-1.7b", "Qwen3 1.7B", 1100),
    _m("qwen3-4b",   "Qwen3 4B",   2500),
]

# STT demo transcripts (macOS has no offline STT CLI; these are canned).
_STT_DEMOS = {
    "en": [
        "Hello, this is a demo transcription from the offline speech to text engine.",
        "The quick brown fox jumps over the lazy dog.",
        "Voicebox turns your recording into text right here in the app.",
    ],
    "hi": ["यह ऑफ़लाइन वाक् से पाठ डेमो का एक उदाहरण है।"],
    "te": ["ఇది ఆఫ్‌లైన్ స్పీచ్ టు టెక్స్ట్ డెమో."],
}
_stt_counter = {"n": 0}


STATE = load_state()
save_state(STATE)


STT_DEMO_SAMPLE_TEXT = "Voicebox runs speech to text completely offline on your machine."

STT_DEMO_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Voicebox — STT Demo</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { margin:0; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
         background:#0b0b0d; color:#ececf1; display:flex; justify-content:center; padding:48px 16px; }
  .wrap { width:100%; max-width:680px; }
  h1 { font-size:26px; margin:0 0 4px; }
  h1 .mic { color:#d9a441; }
  p.sub { color:#9a9aa5; margin:0 0 28px; }
  .card { background:#151518; border:1px solid #26262c; border-radius:14px; padding:20px; margin-bottom:18px; }
  .card h2 { font-size:14px; text-transform:uppercase; letter-spacing:.06em; color:#9a9aa5; margin:0 0 14px; }
  button { font-size:15px; font-weight:600; border:none; border-radius:10px; padding:11px 18px;
           cursor:pointer; background:#d9a441; color:#1a1205; transition:filter .15s; }
  button:hover { filter:brightness(1.08); }
  button:disabled { opacity:.5; cursor:not-allowed; }
  button.ghost { background:#26262c; color:#ececf1; }
  .row { display:flex; gap:10px; align-items:center; flex-wrap:wrap; }
  input[type=file] { color:#9a9aa5; font-size:14px; }
  audio { width:100%; margin-top:14px; }
  .result { margin-top:26px; }
  .result .label { font-size:12px; text-transform:uppercase; letter-spacing:.06em; color:#9a9aa5; margin-bottom:8px; }
  .transcript { background:#0f0f12; border:1px solid #2c2c34; border-radius:12px; padding:18px 20px;
                font-size:18px; line-height:1.5; min-height:64px; white-space:pre-wrap; }
  .status { margin-top:10px; font-size:13px; color:#9a9aa5; min-height:18px; }
  .status.err { color:#f08a8a; }
  .dot { display:inline-block; width:8px; height:8px; border-radius:50%; background:#d9a441; margin-right:7px;
         animation:pulse 1s infinite; vertical-align:middle; }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.3} }
</style>
</head>
<body>
<div class="wrap">
  <h1><span class="mic">&#127908;</span> Voicebox &mdash; STT Demo</h1>
  <p class="sub">Local offline speech-to-text via Whisper. Try a sample, record your voice, or upload a file.</p>

  <div class="card">
    <h2>1 &middot; One-click sample</h2>
    <div class="row">
      <button id="sampleBtn">Transcribe a sample clip</button>
      <span style="color:#9a9aa5;font-size:13px">plays a synthesized sentence, then transcribes it</span>
    </div>
  </div>

  <div class="card">
    <h2>2 &middot; Record your voice</h2>
    <div class="row">
      <button id="recBtn" class="ghost">&#9679; Start recording</button>
      <span id="recHint" style="color:#9a9aa5;font-size:13px"></span>
    </div>
  </div>

  <div class="card">
    <h2>3 &middot; Upload an audio file</h2>
    <div class="row">
      <input type="file" id="fileInput" accept="audio/*">
      <button id="uploadBtn" class="ghost">Transcribe file</button>
    </div>
  </div>

  <audio id="player" controls hidden></audio>

  <div class="result">
    <div class="label">Transcript</div>
    <div class="transcript" id="out">&mdash;</div>
    <div class="status" id="status"></div>
  </div>
</div>

<script>
const out = document.getElementById('out');
const statusEl = document.getElementById('status');
const player = document.getElementById('player');

function setStatus(msg, isErr){ statusEl.className = 'status' + (isErr ? ' err' : ''); statusEl.innerHTML = msg; }
function busy(msg){ setStatus('<span class="dot"></span>' + msg); out.textContent = '…'; }

async function transcribe(blob){
  busy('Transcribing locally with Whisper…');
  const fd = new FormData();
  fd.append('file', blob, 'audio.wav');
  fd.append('language', 'en');
  try {
    const t0 = performance.now();
    const r = await fetch('/transcribe', { method:'POST', body: fd });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const j = await r.json();
    out.textContent = j.text || '(no speech detected)';
    setStatus('Done in ' + ((performance.now()-t0)/1000).toFixed(1) + 's · audio ' + (j.duration||0) + 's');
  } catch(e){ out.textContent = '—'; setStatus('Failed: ' + e.message, true); }
}

document.getElementById('sampleBtn').onclick = async () => {
  busy('Loading sample clip…');
  const r = await fetch('/stt-demo/sample.wav');
  const blob = await r.blob();
  player.hidden = false; player.src = URL.createObjectURL(blob); player.play().catch(()=>{});
  transcribe(blob);
};

document.getElementById('uploadBtn').onclick = () => {
  const f = document.getElementById('fileInput').files[0];
  if (!f) { setStatus('Pick an audio file first.', true); return; }
  player.hidden = false; player.src = URL.createObjectURL(f);
  transcribe(f);
};

let mediaRecorder, chunks = [];
document.getElementById('recBtn').onclick = async () => {
  const btn = document.getElementById('recBtn');
  if (mediaRecorder && mediaRecorder.state === 'recording') { mediaRecorder.stop(); return; }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio:true });
    mediaRecorder = new MediaRecorder(stream); chunks = [];
    mediaRecorder.ondataavailable = e => chunks.push(e.data);
    mediaRecorder.onstop = () => {
      stream.getTracks().forEach(t => t.stop());
      const blob = new Blob(chunks, { type: mediaRecorder.mimeType || 'audio/webm' });
      player.hidden = false; player.src = URL.createObjectURL(blob);
      btn.textContent = '● Start recording'; btn.classList.add('ghost');
      transcribe(blob);
    };
    mediaRecorder.start();
    btn.textContent = '■ Stop recording'; btn.classList.remove('ghost');
    setStatus('Recording… speak, then click stop.');
  } catch(e){ setStatus('Mic blocked: ' + e.message, true); }
};
</script>
</body>
</html>
"""


def parse_multipart(raw: bytes, content_type: str) -> dict:
    """Minimal multipart/form-data parser -> {name: {'data':bytes}|{'value':str}}."""
    import re
    m = re.search(r"boundary=([^;]+)", content_type)
    if not m:
        return {}
    delim = b"--" + m.group(1).strip().strip('"').encode()
    out = {}
    for part in raw.split(delim):
        if b"\r\n\r\n" not in part:
            continue
        head, _, data = part.partition(b"\r\n\r\n")
        if data.endswith(b"\r\n"):
            data = data[:-2]
        headers = head.decode("utf-8", "replace")
        nm = re.search(r'name="([^"]*)"', headers)
        if not nm:
            continue
        if re.search(r'filename="', headers):
            out[nm.group(1)] = {"data": data}
        else:
            out[nm.group(1)] = {"value": data.decode("utf-8", "replace")}
    return out


# --------------------------------------------------------------------------- #
# Request handler
# --------------------------------------------------------------------------- #
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def _cors(self):
        origin = self.headers.get("Origin", "*")
        self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Credentials", "true")
        self.send_header("Access-Control-Allow-Headers",
                         self.headers.get("Access-Control-Request-Headers", "*"))
        self.send_header("Access-Control-Allow-Methods",
                         "GET,POST,PUT,DELETE,OPTIONS,PATCH")

    def _json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self._cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _stream_file(self, path, ctype):
        """Serve a file with Range support without loading it fully into memory."""
        total = path.stat().st_size
        range_header = self.headers.get("Range")
        chunk_size = 256 * 1024  # 256 KB per read
        if range_header and range_header.startswith("bytes="):
            try:
                parts = range_header[6:].split("-")
                start = int(parts[0]) if parts[0] else 0
                end = int(parts[1]) if len(parts) > 1 and parts[1] else total - 1
                end = min(end, total - 1)
                length = end - start + 1
                self.send_response(206)
                self._cors()
                self.send_header("Content-Type", ctype)
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Range", f"bytes {start}-{end}/{total}")
                self.send_header("Content-Length", str(length))
                self.end_headers()
                with open(path, "rb") as f:
                    f.seek(start)
                    remaining = length
                    while remaining > 0:
                        chunk = f.read(min(chunk_size, remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
                return
            except Exception:
                pass
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(total))
        self.end_headers()
        with open(path, "rb") as f:
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                self.wfile.write(chunk)

    def _bytes(self, data, ctype, status=200):
        range_header = self.headers.get("Range")
        total = len(data)
        if range_header and range_header.startswith("bytes="):
            try:
                parts = range_header[6:].split("-")
                start = int(parts[0]) if parts[0] else 0
                end = int(parts[1]) if len(parts) > 1 and parts[1] else total - 1
                end = min(end, total - 1)
                chunk = data[start:end + 1]
                self.send_response(206)
                self._cors()
                self.send_header("Content-Type", ctype)
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Range", f"bytes {start}-{end}/{total}")
                self.send_header("Content-Length", str(len(chunk)))
                self.end_headers()
                self.wfile.write(chunk)
                return
            except Exception:
                pass
        self.send_response(status)
        self._cors()
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(total))
        self.end_headers()
        self.wfile.write(data)

    def _sse(self, obj):
        self.wfile.write(("data: " + json.dumps(obj) + "\n\n").encode("utf-8"))
        self.wfile.flush()

    def _read_json(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        if not length:
            return {}
        raw = self.rfile.read(length)
        if "application/json" in self.headers.get("Content-Type", ""):
            try:
                return json.loads(raw or b"{}")
            except Exception:
                return {}
        return {"_raw_len": len(raw)}  # multipart (audio upload)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    # ---- GET ------------------------------------------------------------ #
    def do_GET(self):
        u = urlparse(self.path)
        path, parts = u.path, [p for p in u.path.split("/") if p]
        q = parse_qs(u.query)

        if path == "/":
            return self._json({"name": "voicetuner", "status": "ok", "offline_stub": True})
        if path in ("/stt-demo", "/stt-demo/"):
            return self._bytes(STT_DEMO_HTML.encode("utf-8"), "text/html; charset=utf-8")
        if path == "/stt-demo/sample.wav":
            return self._bytes(synth(STT_DEMO_SAMPLE_TEXT, "en"), "audio/wav")
        if path == "/health":
            return self._json({"status": "healthy", "model_loaded": True,
                               "model_downloaded": True, "model_size": "1.7B",
                               "gpu_available": False, "vram_used_mb": None,
                               "offline_stub": True})
        if path == "/stub/voices":
            return self._json(_list_say_voices())
        if path == "/profiles":
            with _lock:
                return self._json(list(STATE["profiles"]))
        if len(parts) == 2 and parts[0] == "profiles":
            with _lock:
                for p in STATE["profiles"]:
                    if p["id"] == parts[1]:
                        return self._json(p)
            return self._json({"detail": "Not found"}, 404)
        if len(parts) == 3 and parts[0] == "profiles" and parts[2] == "samples":
            with _lock:
                return self._json(STATE["samples"].get(parts[1], []))
        if len(parts) == 3 and parts[0] == "profiles" and parts[2] == "export":
            with _lock:
                prof = next((p for p in STATE["profiles"] if p["id"] == parts[1]), None)
                samples = STATE["samples"].get(parts[1], [])
            if not prof:
                return self._json({"detail": "Not found"}, 404)
            files = {"profile.json": json.dumps(prof, indent=2, ensure_ascii=False)}
            for i, s in enumerate(samples):
                files[f"samples/{i}.json"] = json.dumps(s, ensure_ascii=False)
                files[f"samples/{i}.wav"] = synth(s.get("reference_text", ""), prof["language"])
            return self._bytes(make_zip(files), "application/zip")

        if path == "/history":
            with _lock:
                items = list(STATE["history"])
            pid = (q.get("profile_id") or [None])[0]
            search = (q.get("search") or [None])[0]
            if pid:
                items = [h for h in items if h["profile_id"] == pid]
            if search:
                items = [h for h in items if search.lower() in h["text"].lower()]
            total = len(items)
            off = int((q.get("offset") or ["0"])[0])
            lim = int((q.get("limit") or ["100"])[0])
            return self._json({"items": list(reversed(items))[off:off + lim], "total": total})
        if path == "/history/stats":
            with _lock:
                return self._json({"total_generations": len(STATE["history"]),
                                   "total_duration": sum(h.get("duration", 0) for h in STATE["history"]),
                                   "total_profiles": len(STATE["profiles"])})
        if path == "/history/failed":
            return self._json({"items": [], "total": 0})
        if path == "/transcriptions":
            with _lock:
                items = list(reversed(STATE["transcriptions"]))
            return self._json({"items": items, "total": len(items)})
        if path == "/tasks/active":
            return self._json({"downloads": [], "generations": []})
        if len(parts) == 3 and parts[0] == "history" and parts[2] == "export-audio":
            gen_id = parts[1]
            gen_dir = ROOT / "data" / "generations"
            disk_path = gen_dir / f"{gen_id}.wav"
            chunks = sorted(gen_dir.glob(f"{gen_id}_*.wav"))
            if chunks and (not disk_path.exists()
                           or disk_path.stat().st_size < chunks[0].stat().st_size):
                with _lock:
                    get_audio(gen_id)
            if disk_path.exists():
                with _lock:
                    _sync_duration_from_path(gen_id, disk_path)
                return self._stream_file(disk_path, "audio/wav")
            with _lock:
                return self._bytes(get_audio(gen_id), "audio/wav")
        if len(parts) == 3 and parts[0] == "history" and parts[2] == "export":
            with _lock:
                gen = find_generation(parts[1])
                audio = get_audio(parts[1])
            if not gen:
                return self._json({"detail": "Not found"}, 404)
            files = {"generation.json": json.dumps(gen, indent=2, ensure_ascii=False),
                     "audio.wav": audio}
            return self._bytes(make_zip(files), "application/zip")
        if len(parts) == 2 and parts[0] == "history":
            with _lock:
                for h in STATE["history"]:
                    if h["id"] == parts[1]:
                        return self._json(h)
            return self._json({"detail": "Not found"}, 404)

        if len(parts) == 3 and parts[0] == "generate" and parts[2] == "status":
            gid = parts[1]
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.close_connection = True
            waited = 0.0
            try:
                while True:
                    with _lock:
                        job = STATE["jobs"].get(gid)
                    if job is None:
                        # job not registered yet, or unknown id — treat short grace
                        if waited < 2:
                            self._sse({"status": "generating"})
                            time.sleep(0.5); waited += 0.5; continue
                        self._sse({"status": "not_found"}); break
                    st = job.get("status")
                    if st == "completed":
                        self._sse({"status": "completed", "duration": job.get("duration", 0),
                                   "source": job.get("source")}); break
                    if st == "failed":
                        self._sse({"status": "failed", "error": job.get("error", "synthesis failed")}); break
                    self._sse({"status": "generating", "progress": job.get("progress", 0)})
                    time.sleep(0.7); waited += 0.7
                    if waited > 3600:
                        self._sse({"status": "failed", "error": "timeout"}); break
            except (BrokenPipeError, ConnectionResetError):
                pass
            return

        if len(parts) == 2 and parts[0] == "audio":
            gen_id = parts[1]
            gen_dir = ROOT / "data" / "generations"
            disk_path = gen_dir / f"{gen_id}.wav"
            chunks = sorted(gen_dir.glob(f"{gen_id}_*.wav"))
            # Merge real-backend chunks when disk file is absent or is the
            # small stub re-synthesis (smaller than any individual chunk).
            if chunks and (not disk_path.exists()
                           or disk_path.stat().st_size < chunks[0].stat().st_size):
                with _lock:
                    get_audio(gen_id)  # merges chunks → disk
            if disk_path.exists():
                with _lock:
                    _sync_duration_from_path(gen_id, disk_path)
                return self._stream_file(disk_path, "audio/wav")
            with _lock:
                return self._bytes(get_audio(gen_id), "audio/wav")

        if len(parts) == 2 and parts[0] == "samples":
            sample_id = parts[1]
            with _lock:
                # Check in-memory cache first (keyed as "sample-{id}")
                blob = STATE["audio"].get(f"sample-{sample_id}")
                if blob is None:
                    # Find sample record to locate its profile_id → disk file
                    for samples_list in STATE["samples"].values():
                        for s in samples_list:
                            if s["id"] == sample_id:
                                disk = STATE_DIR / "samples" / f"{s['profile_id']}.wav"
                                if disk.exists():
                                    blob = disk.read_bytes()
                                    STATE["audio"][f"sample-{sample_id}"] = blob
                                break
                        if blob is not None:
                            break
                if blob is None:
                    blob = make_wav()
            return self._bytes(blob, "audio/wav")

        if path == "/stories":
            with _lock:
                return self._json([story_summary(s) for s in STATE["stories"]])
        if len(parts) == 2 and parts[0] == "stories":
            with _lock:
                s = find_story(parts[1])
            if not s:
                return self._json({"detail": "Not found"}, 404)
            return self._json({"id": s["id"], "name": s["name"],
                               "description": s.get("description"),
                               "created_at": s["created_at"], "updated_at": s["updated_at"],
                               "items": s["items"]})
        if len(parts) == 3 and parts[0] == "stories" and parts[2] == "export-audio":
            with _lock:
                s = find_story(parts[1])
                blobs = [get_audio(it["generation_id"]) for it in s["items"]] if s else []
            return self._bytes(concat_wavs(blobs) if blobs else make_wav(), "audio/wav")

        if path == "/models/status":
            return self._json({"models": MODELS})
        if len(parts) == 3 and parts[:2] == ["models", "progress"]:
            return self._json({"model_name": parts[2], "progress": 100,
                               "downloaded": True, "downloading": False})
        if path == "/backend/cuda-status":
            return self._json({"available": False, "installed": False, "downloading": False})

        return self._json({"detail": f"Stub: no GET handler for {path}"}, 404)

    # ---- POST ----------------------------------------------------------- #
    def do_POST(self):
        u = urlparse(self.path)
        path, parts = u.path, [p for p in u.path.split("/") if p]
        ctype = self.headers.get("Content-Type", "")

        # Multipart handlers must read from rfile before body = self._read_json() consumes it
        if path == "/transcribe" or (len(parts) == 3 and parts[0] == "profiles" and parts[2] == "samples"):
            length = int(self.headers.get("Content-Length", "0") or "0")
            raw = self.rfile.read(length) if length else b""
            fields = parse_multipart(raw, ctype) if "multipart" in ctype else {}

            if path == "/transcribe":
                language = (fields.get("language") or {}).get("value")
                audio = (fields.get("file") or {}).get("data")
                text, dur = transcribe_audio(audio, language)
                rec = {"id": new_id(), "text": text, "language": language or "en",
                       "duration": dur, "created_at": now_iso()}
                with _lock:
                    STATE["transcriptions"].append(rec)
                    save_state(STATE)
                return self._json({"id": rec["id"], "text": text, "duration": dur})

            # POST /profiles/{id}/samples — voice sample upload
            ref_text = (fields.get("reference_text") or {}).get("value") or ""
            audio_bytes = (fields.get("file") or {}).get("data") or b""
            sid = new_id()
            profile_id = parts[1]
            audio_path = f"samples/{profile_id}.wav"
            sample = {"id": sid, "profile_id": profile_id,
                      "audio_path": audio_path, "reference_text": ref_text}
            # Persist to two locations:
            #   1. STATE_DIR/samples/{profile_id}.wav — stub reference
            #   2. data/profiles/{profile_id}/{sample_id}.wav — real backend compatible
            if audio_bytes:
                sample_file = STATE_DIR / "samples" / f"{profile_id}.wav"
                sample_file.parent.mkdir(parents=True, exist_ok=True)
                sample_file.write_bytes(audio_bytes)
                real_dir = ROOT / "data" / "profiles" / profile_id
                real_dir.mkdir(parents=True, exist_ok=True)
                (real_dir / f"{sid}.wav").write_bytes(audio_bytes)
            with _lock:
                STATE["samples"].setdefault(profile_id, []).append(sample)
                if audio_bytes:
                    STATE["audio"][f"sample-{sid}"] = audio_bytes
                save_state(STATE)
            return self._json(sample, 201)

        body = self._read_json()

        if path == "/profiles":
            t = now_iso()
            prof = {"id": new_id(), "name": body.get("name", "Untitled Voice"),
                    "description": body.get("description"),
                    "language": body.get("language", "en"),
                    "created_at": t, "updated_at": t}
            with _lock:
                STATE["profiles"].append(prof)
                save_state(STATE)
            return self._json(prof, 201)

        if path == "/generate":
            # Async: return immediately with status=generating, synthesize in a
            # background thread, and let the client stream /generate/{id}/status.
            gid = new_id()
            text, language = body.get("text", ""), body.get("language", "en")
            job = {
                "id": gid, "status": "generating", "text": text, "language": language,
                "profile_id": body.get("profile_id", ""), "seed": body.get("seed"),
                "instruct": body.get("instruct"), "model_size": body.get("model_size"),
                "source": body.get("source"), "created_at": now_iso(),
                "stub_voice": body.get("stub_voice"),
                "progress": 0.0, "duration": 0,
            }
            with _lock:
                STATE["jobs"][gid] = job
            threading.Thread(target=run_generation_job, args=(gid,), daemon=True).start()
            return self._json({
                "id": gid, "status": "generating", "profile_id": job["profile_id"],
                "profile_name": profile_name(job["profile_id"]), "text": text,
                "language": language, "audio_path": f"generations/{gid}.wav",
                "duration": 0, "seed": job["seed"], "instruct": job["instruct"],
                "engine": "qwen", "model_size": job["model_size"] or "1.7B",
                "created_at": job["created_at"],
            }, 201)

        if path == "/stories":
            t = now_iso()
            s = {"id": new_id(), "name": body.get("name", "Untitled Story"),
                 "description": body.get("description"), "created_at": t,
                 "updated_at": t, "items": []}
            with _lock:
                STATE["stories"].append(s)
                save_state(STATE)
            return self._json(story_summary(s), 201)

        if len(parts) == 3 and parts[0] == "stories" and parts[2] == "items":
            with _lock:
                s = find_story(parts[1])
                if not s:
                    return self._json({"detail": "Story not found"}, 404)
                gen = find_generation(body.get("generation_id", ""))
                if not gen:
                    return self._json({"detail": "Generation not found"}, 404)
                start = body.get("start_time_ms")
                if start is None:
                    start = max([i["start_time_ms"] + int(i["duration"] * 1000)
                                 for i in s["items"]] or [0])
                item = _story_item_from_gen(s["id"], gen, start, body.get("track", 0))
                s["items"].append(item)
                s["updated_at"] = now_iso()
                save_state(STATE)
            return self._json(item, 201)

        if len(parts) == 5 and parts[0] == "stories" and parts[2] == "items" and parts[4] == "duplicate":
            with _lock:
                s = find_story(parts[1])
                src = next((i for i in s["items"] if i["id"] == parts[3]), None) if s else None
                if not src:
                    return self._json({"detail": "Not found"}, 404)
                dup = dict(src)
                dup["id"] = new_id()
                dup["start_time_ms"] = src["start_time_ms"] + int(src["duration"] * 1000) + 100
                s["items"].append(dup)
                save_state(STATE)
            return self._json(dup, 201)

        if len(parts) == 5 and parts[0] == "stories" and parts[2] == "items" and parts[4] == "split":
            with _lock:
                s = find_story(parts[1])
                items = s["items"] if s else []
            return self._json(items)  # no-op split: return items unchanged

        if path in ("/models/load", "/models/unload", "/models/download"):
            return self._json({"status": "ok", "offline_stub": True})

        return self._json({"detail": f"Stub: no POST handler for {path}"}, 404)

    # ---- PUT ------------------------------------------------------------ #
    def do_PUT(self):
        u = urlparse(self.path)
        parts = [p for p in u.path.split("/") if p]
        body = self._read_json()

        if len(parts) == 2 and parts[0] == "profiles":
            with _lock:
                for p in STATE["profiles"]:
                    if p["id"] == parts[1]:
                        for k in ("name", "description", "language"):
                            if k in body:
                                p[k] = body[k]
                        p["updated_at"] = now_iso()
                        save_state(STATE)
                        return self._json(p)
            return self._json({"detail": "Not found"}, 404)

        if len(parts) == 2 and parts[0] == "stories":
            with _lock:
                s = find_story(parts[1])
                if not s:
                    return self._json({"detail": "Not found"}, 404)
                for k in ("name", "description"):
                    if k in body:
                        s[k] = body[k]
                s["updated_at"] = now_iso()
                save_state(STATE)
                return self._json(story_summary(s))

        if len(parts) >= 4 and parts[0] == "stories" and parts[2] == "items":
            with _lock:
                s = find_story(parts[1])
                if not s:
                    return self._json({"detail": "Not found"}, 404)

                if parts[3] == "times":  # batch time update
                    upd = {u_["generation_id"]: u_["start_time_ms"]
                           for u_ in body.get("updates", [])}
                    for it in s["items"]:
                        if it["generation_id"] in upd:
                            it["start_time_ms"] = upd[it["generation_id"]]
                    save_state(STATE)
                    return self._json(None)

                if parts[3] == "reorder":
                    order = body.get("generation_ids", [])
                    s["items"].sort(key=lambda it: order.index(it["generation_id"])
                                    if it["generation_id"] in order else 1e9)
                    cursor = 0
                    for it in s["items"]:
                        it["start_time_ms"] = cursor
                        cursor += int(it["duration"] * 1000) + 250
                    save_state(STATE)
                    return self._json(s["items"])

                item = next((i for i in s["items"] if i["id"] == parts[3]), None)
                if not item:
                    return self._json({"detail": "Item not found"}, 404)
                action = parts[4] if len(parts) >= 5 else None
                if action == "move":
                    item["start_time_ms"] = body.get("start_time_ms", item["start_time_ms"])
                    item["track"] = body.get("track", item["track"])
                elif action == "trim":
                    item["trim_start_ms"] = body.get("trim_start_ms", item["trim_start_ms"])
                    item["trim_end_ms"] = body.get("trim_end_ms", item["trim_end_ms"])
                elif action == "volume":
                    item["volume"] = body.get("volume", item["volume"])
                elif action == "version":
                    item["active_version_id"] = body.get("version_id")
                save_state(STATE)
                return self._json(item)

        return self._json({"detail": f"Stub: no PUT handler for {u.path}"}, 404)

    # ---- DELETE --------------------------------------------------------- #
    def do_DELETE(self):
        u = urlparse(self.path)
        parts = [p for p in u.path.split("/") if p]
        with _lock:
            if len(parts) == 2 and parts[0] == "profiles":
                STATE["profiles"] = [p for p in STATE["profiles"] if p["id"] != parts[1]]
                STATE["samples"].pop(parts[1], None)
                save_state(STATE)
                return self._json({"status": "deleted"})
            if len(parts) == 3 and parts[:2] == ["profiles", "samples"]:
                for pid in STATE["samples"]:
                    STATE["samples"][pid] = [x for x in STATE["samples"][pid] if x["id"] != parts[2]]
                save_state(STATE)
                return self._json({"status": "deleted"})
            if len(parts) == 2 and parts[0] == "history":
                STATE["history"] = [h for h in STATE["history"] if h["id"] != parts[1]]
                STATE["audio"].pop(parts[1], None)
                save_state(STATE)
                return self._json({"status": "deleted"})
            if len(parts) == 2 and parts[0] == "transcriptions":
                STATE["transcriptions"] = [x for x in STATE["transcriptions"] if x["id"] != parts[1]]
                save_state(STATE)
                return self._json({"status": "deleted"})
            if len(parts) == 2 and parts[0] == "stories":
                STATE["stories"] = [s for s in STATE["stories"] if s["id"] != parts[1]]
                save_state(STATE)
                return self._json({"status": "deleted"})
            if len(parts) == 4 and parts[0] == "stories" and parts[2] == "items":
                s = find_story(parts[1])
                if s:
                    s["items"] = [i for i in s["items"] if i["id"] != parts[3]]
                    save_state(STATE)
                return self._json({"status": "deleted"})
        return self._json({"detail": f"Stub: no DELETE handler for {u.path}"}, 404)


def main():
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"[offline-stub] backend on http://{HOST}:{PORT}  "
          f"(tts={'on' if _HAVE_SAY else 'OFF'}, stt={'whisper:' + STT_MODEL_NAME if _HAVE_STT else 'canned'})")
    print(f"[offline-stub] stories: {len(STATE['stories'])}, "
          f"voices: {len(STATE['profiles'])}, state: {STATE_FILE}")
    if _HAVE_STT:  # warm the whisper model in the background so first STT is fast
        threading.Thread(target=get_stt_model, daemon=True).start()
    if _HAVE_SAY:  # pre-synthesize any history items missing audio so first play is instant
        def _prewarm_audio():
            with _lock:
                missing = [h for h in STATE["history"]
                           if h["id"] not in STATE["audio"]
                           and not (ROOT / "data" / "generations" / f"{h['id']}.wav").exists()]
            for h in missing:
                gid = h["id"]
                disk = ROOT / "data" / "generations" / f"{gid}.wav"
                print(f"[offline-stub] pre-warming audio for {h.get('profile_name','?')} ({gid[:8]})")
                blob = synth_long(h.get("text", ""), h.get("language", "en"))
                disk.parent.mkdir(parents=True, exist_ok=True)
                disk.write_bytes(blob)
                _sync_duration(gid, blob)
                with _lock:
                    STATE["audio"][gid] = blob
                print(f"[offline-stub] pre-warm done: {gid[:8]} ({len(blob)//1024}KB)")
        threading.Thread(target=_prewarm_audio, daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[offline-stub] bye")


if __name__ == "__main__":
    main()
