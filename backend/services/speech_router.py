"""
Speech provider router — decides which TTS/STT provider serves each language.

Policy (overridable via env TTS_PROVIDER / STT_PROVIDER = auto|local|sarvam|groq):

  TTS:
    - en  -> local-first (respect the requested engine; offline capable)
    - hi  -> Sarvam (cloud) by default; local Kokoro/Chatterbox fallback if no key
    - te  -> Sarvam ONLY (no local engine can synthesize Telugu)
    An explicit cloud engine request (sarvam/elevenlabs) is always honored.

  STT:
    - en  -> local Whisper (offline-first)
    - hi/te -> Sarvam Saarika if keyed, else Groq Whisper, else local Whisper

When a language has no usable provider (e.g. Telugu with no Sarvam key),
NoSpeechProviderError is raised; route handlers should surface it as HTTP 503.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from ..adapters.credentials import get_elevenlabs_key, get_groq_key, get_sarvam_key
from ..languages import coerce_supported

logger = logging.getLogger(__name__)

_CLOUD_TTS_ENGINES = {"sarvam", "elevenlabs"}


class NoSpeechProviderError(RuntimeError):
    """No provider can serve the requested language/modality."""

    def __init__(self, language: str, modality: str):
        self.language = language
        self.modality = modality
        super().__init__(
            f"No {modality} provider available for '{language}'. "
            f"Configure a cloud key (Sarvam) to enable it."
        )


def _tts_provider_pref() -> str:
    return os.environ.get("TTS_PROVIDER", "auto").strip().lower()


def _stt_provider_pref() -> str:
    return os.environ.get("STT_PROVIDER", "auto").strip().lower()


def _engine_supports(engine: str, lang: str) -> bool:
    """Whether a TTS engine can serve the given language."""
    if engine in _CLOUD_TTS_ENGINES:
        return True  # cloud covers en/hi/te
    from ..backends import get_tts_model_configs

    for cfg in get_tts_model_configs():
        if cfg.engine == engine and lang in cfg.languages:
            return True
    return False


def _first_local_engine_supporting(lang: str) -> Optional[str]:
    from ..backends import get_tts_model_configs

    for cfg in get_tts_model_configs():
        if lang in cfg.languages:
            return cfg.engine
    return None


def resolve_tts_engine(requested_engine: Optional[str], language: str) -> str:
    """Return the engine name that should actually serve this request.

    Raises NoSpeechProviderError if nothing can.
    """
    lang = coerce_supported(language)
    requested = requested_engine or "qwen"
    pref = _tts_provider_pref()

    # Explicit cloud engine selection always wins (if usable).
    if requested in _CLOUD_TTS_ENGINES:
        if requested == "sarvam" and not get_sarvam_key():
            raise NoSpeechProviderError(lang, "TTS")
        if requested == "elevenlabs" and not get_elevenlabs_key():
            raise NoSpeechProviderError(lang, "TTS")
        return requested

    # Hard provider override via env.
    if pref == "sarvam":
        if not get_sarvam_key():
            raise NoSpeechProviderError(lang, "TTS")
        return "sarvam"
    if pref == "local":
        if _engine_supports(requested, lang):
            return requested
        # Local can't serve (e.g. Telugu) -> cloud is the only option.
        if get_sarvam_key():
            return "sarvam"
        raise NoSpeechProviderError(lang, "TTS")

    # auto
    if lang == "en":
        return requested if _engine_supports(requested, "en") else "qwen"

    # hi / te -> prefer Sarvam.
    if get_sarvam_key():
        return "sarvam"
    # No Sarvam key: Hindi may fall back to a capable local engine; Telugu can't.
    if _engine_supports(requested, lang):
        return requested
    fallback = _first_local_engine_supporting(lang)
    if fallback:
        logger.info("No Sarvam key; falling back to local '%s' for %s", fallback, lang)
        return fallback
    raise NoSpeechProviderError(lang, "TTS")


def get_stt_backend_for_language(language: Optional[str]):
    """Return an STTBackend instance appropriate for the language."""
    from ..backends import get_stt_backend  # local Whisper

    lang = (language or "auto").strip().lower()
    pref = _stt_provider_pref()

    if pref == "groq" and get_groq_key():
        return _groq_stt()
    if pref == "sarvam" and get_sarvam_key():
        return _sarvam_stt()
    if pref == "local":
        return get_stt_backend()

    # auto: Indic languages prefer Sarvam, then Groq, then local Whisper.
    if lang in ("hi", "te"):
        if get_sarvam_key():
            return _sarvam_stt()
        if get_groq_key():
            return _groq_stt()
    return get_stt_backend()


# ── cached cloud STT singletons ─────────────────────────────────────
_sarvam_stt_instance = None
_groq_stt_instance = None


def _sarvam_stt():
    global _sarvam_stt_instance
    if _sarvam_stt_instance is None:
        from ..adapters.sarvam import SarvamSTTBackend

        _sarvam_stt_instance = SarvamSTTBackend()
    return _sarvam_stt_instance


def _groq_stt():
    global _groq_stt_instance
    if _groq_stt_instance is None:
        from ..adapters.groq import GroqSTTBackend

        _groq_stt_instance = GroqSTTBackend()
    return _groq_stt_instance
