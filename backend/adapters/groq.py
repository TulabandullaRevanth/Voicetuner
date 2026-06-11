"""
Groq adapter — hosted Whisper-large-v3 STT (fast cloud transcription).

Implements the STTBackend protocol so it can stand in for the local Whisper
backend. Covers en/hi/te (Whisper-large-v3 is multilingual). Used as the STT
fallback / fast path; Sarvam Saarika remains the Indic-tuned default.

API: https://console.groq.com/docs/speech-text  (OpenAI-compatible)
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from ..languages import coerce_supported
from .credentials import get_groq_key

logger = logging.getLogger(__name__)

_BASE_URL = os.environ.get("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
_STT_URL = f"{_BASE_URL}/audio/transcriptions"
_DEFAULT_MODEL = os.environ.get("GROQ_STT_MODEL", "whisper-large-v3")
_HTTP_TIMEOUT = float(os.environ.get("GROQ_HTTP_TIMEOUT", "60"))


class GroqError(RuntimeError):
    """Raised when Groq is unusable (missing key or API failure)."""


def _require_key() -> str:
    key = get_groq_key()
    if not key:
        raise GroqError("Groq API key not configured. Set GROQ_API_KEY in .env.")
    return key


class GroqSTTBackend:
    """Groq-hosted Whisper-large-v3 STT backend (cloud)."""

    model_size = "whisper-large-v3"

    def __init__(self):
        self._ready = False

    def is_loaded(self) -> bool:
        return self._ready

    def _is_model_cached(self, model_size: str = "default") -> bool:
        return get_groq_key() is not None

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
        headers = {"Authorization": f"Bearer {_require_key()}"}
        data = {"model": model_size or _DEFAULT_MODEL, "response_format": "json"}
        # Whisper expects ISO-639-1; only pin it when not auto-detecting.
        if language and language != "auto":
            data["language"] = coerce_supported(language)

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
            raise GroqError(f"Groq STT {e.response.status_code}: {detail}") from e
        except httpx.HTTPError as e:
            raise GroqError(f"Groq STT request failed: {e}") from e

        return (body.get("text") or "").strip()
