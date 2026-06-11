"""
ElevenLabs adapter — voice cloning for en/hi/te.

Sarvam gives Telugu/Hindi TTS+STT but only with preset speakers. ElevenLabs
Instant Voice Cloning (IVC) is what lets a user clone *their own* voice in
Hindi/Telugu/English. This backend implements the TTSBackend protocol so a
cloned voice profile can select engine="elevenlabs".

Design notes:
- create_voice_prompt uploads the reference audio once and creates an IVC
  voice, then caches hash->voice_id on disk so repeat generations reuse the
  same voice (IVC slots are limited and creation is slow/costly).
- generate requests raw PCM (pcm_24000) to avoid bundling an mp3 decoder.

API: https://elevenlabs.io/docs  (auth header: `xi-api-key`)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np

from ..languages import coerce_supported
from .credentials import get_elevenlabs_key

logger = logging.getLogger(__name__)

_BASE_URL = os.environ.get("ELEVENLABS_BASE_URL", "https://api.elevenlabs.io/v1")
# multilingual_v2 covers en/hi; flash/turbo v2.5 add more Indic incl. Telugu.
_MODEL = os.environ.get("ELEVENLABS_MODEL", "eleven_multilingual_v2")
_OUTPUT_FORMAT = os.environ.get("ELEVENLABS_OUTPUT_FORMAT", "pcm_24000")
_SAMPLE_RATE = int(_OUTPUT_FORMAT.split("_")[-1]) if "_" in _OUTPUT_FORMAT else 24000
_HTTP_TIMEOUT = float(os.environ.get("ELEVENLABS_HTTP_TIMEOUT", "120"))


class ElevenLabsError(RuntimeError):
    """Raised when ElevenLabs is unusable (missing key or API failure)."""


def _require_key() -> str:
    key = get_elevenlabs_key()
    if not key:
        raise ElevenLabsError(
            "ElevenLabs API key not configured. Set ELEVENLABS_API_KEY in .env "
            "to enable voice cloning."
        )
    return key


def _model_supports_language_code(model: str) -> bool:
    m = model.lower()
    return "flash" in m or "turbo" in m or "v2_5" in m or "v2.5" in m


def _cache_path() -> Path:
    try:
        from ..config import get_data_dir

        base = Path(get_data_dir())
    except Exception:
        base = Path("data")
    cache_dir = base / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / "elevenlabs_voices.json"


def _load_cache() -> dict:
    p = _cache_path()
    if p.is_file():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_cache(cache: dict) -> None:
    try:
        _cache_path().write_text(json.dumps(cache, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning("Failed to persist ElevenLabs voice cache: %s", e)


class ElevenLabsTTSBackend:
    """ElevenLabs voice-cloning TTS backend (cloud)."""

    model_size = "default"

    def __init__(self):
        self._ready = False

    # ── protocol surface ────────────────────────────────────────────
    def is_loaded(self) -> bool:
        return self._ready

    def _is_model_cached(self, model_size: str = "default") -> bool:
        return get_elevenlabs_key() is not None

    async def load_model(self, model_size: str = "default") -> None:
        _require_key()
        self._ready = True

    def unload_model(self) -> None:
        self._ready = False

    async def combine_voice_prompts(
        self, audio_paths: List[str], reference_texts: List[str]
    ) -> Tuple[np.ndarray, str]:
        from ..backends.base import combine_voice_prompts

        return await combine_voice_prompts(audio_paths, reference_texts)

    async def create_voice_prompt(
        self,
        audio_path: str,
        reference_text: str,
        use_cache: bool = True,
    ) -> Tuple[dict, bool]:
        """Create (or reuse) an IVC voice from the reference audio."""
        data = Path(audio_path).read_bytes()
        digest = hashlib.sha256(data).hexdigest()[:16]

        cache = _load_cache()
        if use_cache and digest in cache:
            return {"voice_id": cache[digest], "model_id": _MODEL}, True

        voice_id = await self._create_ivc_voice(audio_path, f"voicetuner-{digest}")
        cache[digest] = voice_id
        _save_cache(cache)
        return {"voice_id": voice_id, "model_id": _MODEL}, False

    async def generate(
        self,
        text: str,
        voice_prompt: dict,
        language: str = "en",
        seed: Optional[int] = None,
        instruct: Optional[str] = None,
    ) -> Tuple[np.ndarray, int]:
        import httpx

        await self.load_model()
        voice_id = (voice_prompt or {}).get("voice_id")
        if not voice_id:
            raise ElevenLabsError("ElevenLabs generate requires a cloned voice_id")
        model = (voice_prompt or {}).get("model_id") or _MODEL

        body = {"text": text, "model_id": model}
        if _model_supports_language_code(model):
            body["language_code"] = coerce_supported(language)

        url = f"{_BASE_URL}/text-to-speech/{voice_id}"
        headers = {"xi-api-key": _require_key()}
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
                resp = await client.post(
                    url, params={"output_format": _OUTPUT_FORMAT},
                    json=body, headers=headers,
                )
                resp.raise_for_status()
                content = resp.content
        except httpx.HTTPStatusError as e:
            raise ElevenLabsError(
                f"ElevenLabs TTS {e.response.status_code}: {e.response.text[:300]}"
            ) from e
        except httpx.HTTPError as e:
            raise ElevenLabsError(f"ElevenLabs TTS request failed: {e}") from e

        # pcm_* is signed 16-bit little-endian mono.
        audio = np.frombuffer(content, dtype="<i2").astype(np.float32) / 32768.0
        return audio, _SAMPLE_RATE

    # ── IVC management ──────────────────────────────────────────────
    async def _create_ivc_voice(self, audio_path: str, name: str) -> str:
        import httpx

        headers = {"xi-api-key": _require_key()}
        try:
            with open(audio_path, "rb") as fh:
                files = {"files": (os.path.basename(audio_path), fh, "audio/wav")}
                async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
                    resp = await client.post(
                        f"{_BASE_URL}/voices/add",
                        data={"name": name}, files=files, headers=headers,
                    )
                    resp.raise_for_status()
                    body = resp.json()
        except httpx.HTTPStatusError as e:
            raise ElevenLabsError(
                f"ElevenLabs voice clone {e.response.status_code}: {e.response.text[:300]}"
            ) from e
        except httpx.HTTPError as e:
            raise ElevenLabsError(f"ElevenLabs voice clone failed: {e}") from e

        voice_id = body.get("voice_id")
        if not voice_id:
            raise ElevenLabsError(f"ElevenLabs returned no voice_id: {body}")
        logger.info("Created ElevenLabs IVC voice %s (%s)", voice_id, name)
        return voice_id

    async def delete_voice(self, voice_id: str) -> None:
        """Delete an IVC voice (frees a slot). Best-effort."""
        import httpx

        headers = {"xi-api-key": _require_key()}
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.delete(f"{_BASE_URL}/voices/{voice_id}", headers=headers)
            resp.raise_for_status()
