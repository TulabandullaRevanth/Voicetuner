"""
Sarvam AI adapter — primary cloud provider for Indic speech (en/hi/te).

Sarvam is purpose-built for Indian languages and is the only provider in this
stack that natively serves Telugu TTS. Two backends are exposed:

  - SarvamTTSBackend:  Bulbul TTS  (preset speakers; no arbitrary cloning)
  - SarvamSTTBackend:  Saarika STT (Indic-tuned transcription)

Both implement the TTSBackend / STTBackend protocols from backends/__init__.py
so they drop into the existing engine registry.

API: https://docs.sarvam.ai  (auth header: `api-subscription-key`)
"""

from __future__ import annotations

import base64
import io
import logging
import os
import re
from typing import List, Optional, Tuple

import numpy as np

from ..languages import PROVIDER_LOCALE, coerce_supported
from .credentials import get_sarvam_key

logger = logging.getLogger(__name__)

_BASE_URL = os.environ.get("SARVAM_BASE_URL", "https://api.sarvam.ai")
_TTS_URL = f"{_BASE_URL}/text-to-speech"
_STT_URL = f"{_BASE_URL}/speech-to-text"

# Bulbul v2 multilingual preset speakers; override with SARVAM_TTS_SPEAKER.
_DEFAULT_SPEAKER = os.environ.get("SARVAM_TTS_SPEAKER", "anushka")
_DEFAULT_TTS_MODEL = os.environ.get("SARVAM_TTS_MODEL", "bulbul:v2")
_DEFAULT_STT_MODEL = os.environ.get("SARVAM_STT_MODEL", "saarika:v2.5")

# Bulbul caps per-request input length; chunk longer text on sentence bounds.
_MAX_TTS_CHARS = 450
_HTTP_TIMEOUT = float(os.environ.get("SARVAM_HTTP_TIMEOUT", "60"))

# ── Preset speaker catalog ───────────────────────────────────────────
# Bulbul v2 multilingual speakers. Each works across en/hi/te, so the
# catalog is expanded per (speaker x language) for a clean UI pick, with a
# unique voice_id ("<speaker>-<lang>") that maps back to the real speaker.
SARVAM_SPEAKERS = [
    ("anushka", "Anushka", "female"),
    ("manisha", "Manisha", "female"),
    ("vidya", "Vidya", "female"),
    ("arya", "Arya", "female"),
    ("abhilash", "Abhilash", "male"),
    ("karun", "Karun", "male"),
    ("hitesh", "Hitesh", "male"),
]
_LANG_LABEL = {"en": "English", "hi": "Hindi", "te": "Telugu"}

# (voice_id, display_name, gender, language, description)
SARVAM_VOICES = [
    (
        f"{spk}-{lang}",
        f"{name} ({_LANG_LABEL[lang]})",
        gender,
        lang,
        f"Sarvam Bulbul {_LANG_LABEL[lang]} preset voice",
    )
    for spk, name, gender in SARVAM_SPEAKERS
    for lang in ("en", "hi", "te")
]

# voice_id ("anushka-te") -> real Sarvam speaker ("anushka")
SARVAM_VOICE_TO_SPEAKER = {
    f"{spk}-{lang}": spk
    for spk, _name, _gender in SARVAM_SPEAKERS
    for lang in ("en", "hi", "te")
}


def resolve_speaker(voice_id: str | None) -> str:
    """Map a catalog voice_id (or raw speaker) to a Sarvam speaker name."""
    if not voice_id:
        return _DEFAULT_SPEAKER
    return SARVAM_VOICE_TO_SPEAKER.get(voice_id, voice_id)


class SarvamError(RuntimeError):
    """Raised when Sarvam is unusable (missing key or API failure)."""


def _require_key() -> str:
    key = get_sarvam_key()
    if not key:
        raise SarvamError(
            "Sarvam API key not configured. Set SARVAM_API_KEY in .env to enable "
            "Hindi/Telugu speech."
        )
    return key


def _locale(language: str) -> str:
    return PROVIDER_LOCALE.get(coerce_supported(language), "en-IN")


def _chunk_text(text: str, limit: int = _MAX_TTS_CHARS) -> List[str]:
    text = text.strip()
    if len(text) <= limit:
        return [text] if text else []
    # Split on sentence-ish boundaries (incl. Devanagari/Telugu danda).
    parts = re.split(r"(?<=[.!?।])\s+|\n+", text)
    chunks: List[str] = []
    buf = ""
    for part in parts:
        if not part:
            continue
        if len(buf) + len(part) + 1 <= limit:
            buf = f"{buf} {part}".strip()
        else:
            if buf:
                chunks.append(buf)
            # A single oversized sentence: hard-split.
            while len(part) > limit:
                chunks.append(part[:limit])
                part = part[limit:]
            buf = part
    if buf:
        chunks.append(buf)
    return chunks


class SarvamTTSBackend:
    """Sarvam Bulbul TTS backend (cloud, preset speakers)."""

    model_size = "default"

    def __init__(self):
        self._ready = False

    # ── protocol surface ────────────────────────────────────────────
    def is_loaded(self) -> bool:
        return self._ready

    def _is_model_cached(self, model_size: str = "default") -> bool:
        # "Cached" == credential available; nothing to download.
        return get_sarvam_key() is not None

    async def load_model(self, model_size: str = "default") -> None:
        _require_key()
        self._ready = True

    def unload_model(self) -> None:
        self._ready = False

    async def create_voice_prompt(
        self,
        audio_path: str,
        reference_text: str,
        use_cache: bool = True,
    ) -> Tuple[dict, bool]:
        """Sarvam Bulbul uses preset speakers, not arbitrary cloning.

        We ignore the reference audio and record the chosen preset speaker.
        For true voice cloning in Telugu, route to the ElevenLabs adapter.
        """
        return {"speaker": _DEFAULT_SPEAKER}, False

    async def generate(
        self,
        text: str,
        voice_prompt: dict,
        language: str = "en",
        seed: Optional[int] = None,
        instruct: Optional[str] = None,
    ) -> Tuple[np.ndarray, int]:
        await self.load_model()
        vp = voice_prompt or {}
        # Honor a preset speaker from a preset voice profile, else default.
        # Catalog voice_ids look like "anushka-te" -> real speaker "anushka".
        speaker = vp.get("speaker") or resolve_speaker(vp.get("preset_voice_id"))
        target = _locale(language)

        chunks = _chunk_text(text)
        if not chunks:
            return np.zeros(0, dtype=np.float32), 22050

        audio_parts: List[np.ndarray] = []
        sample_rate = 22050
        for chunk in chunks:
            arr, sample_rate = await self._tts_request(chunk, target, speaker)
            audio_parts.append(arr)

        audio = (
            np.concatenate(audio_parts) if len(audio_parts) > 1 else audio_parts[0]
        )
        return audio.astype(np.float32), sample_rate

    # ── HTTP ────────────────────────────────────────────────────────
    async def _tts_request(
        self, text: str, target_language_code: str, speaker: str
    ) -> Tuple[np.ndarray, int]:
        import httpx
        import soundfile as sf

        payload = {
            "text": text,
            "target_language_code": target_language_code,
            "speaker": speaker,
            "model": _DEFAULT_TTS_MODEL,
        }
        headers = {"api-subscription-key": _require_key()}
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
                resp = await client.post(_TTS_URL, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPStatusError as e:
            body = e.response.text[:300]
            raise SarvamError(f"Sarvam TTS {e.response.status_code}: {body}") from e
        except httpx.HTTPError as e:
            raise SarvamError(f"Sarvam TTS request failed: {e}") from e

        audios = data.get("audios") or []
        if not audios:
            raise SarvamError("Sarvam TTS returned no audio")
        wav_bytes = base64.b64decode(audios[0])
        arr, sr = sf.read(io.BytesIO(wav_bytes), dtype="float32")
        if arr.ndim > 1:  # downmix to mono
            arr = arr.mean(axis=1)
        return arr, int(sr)


class SarvamSTTBackend:
    """Sarvam Saarika STT backend (cloud, Indic-tuned)."""

    model_size = "saarika:v2.5"

    def __init__(self):
        self._ready = False

    def is_loaded(self) -> bool:
        return self._ready

    def _is_model_cached(self, model_size: str = "default") -> bool:
        return get_sarvam_key() is not None

    async def load_model(self, model_size: str = "default") -> None:
        _require_key()
        self._ready = True

    def unload_model(self) -> None:
        self._ready = False

    async def transcribe(
        self,
        audio_path: str,
        language: Optional[str] = None,
        model_size: Optional[str] = None,
    ) -> str:
        import httpx

        await self.load_model()
        # "unknown" lets Saarika auto-detect among Indic languages.
        lang_code = "unknown"
        if language and language != "auto":
            lang_code = _locale(language)

        headers = {"api-subscription-key": _require_key()}
        data = {"model": model_size or _DEFAULT_STT_MODEL, "language_code": lang_code}
        try:
            with open(audio_path, "rb") as fh:
                files = {"file": (os.path.basename(audio_path), fh, "audio/wav")}
                async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
                    resp = await client.post(
                        _STT_URL, data=data, files=files, headers=headers
                    )
                    resp.raise_for_status()
                    body = resp.json()
        except httpx.HTTPStatusError as e:
            detail = e.response.text[:300]
            raise SarvamError(f"Sarvam STT {e.response.status_code}: {detail}") from e
        except httpx.HTTPError as e:
            raise SarvamError(f"Sarvam STT request failed: {e}") from e

        return (body.get("transcript") or "").strip()
