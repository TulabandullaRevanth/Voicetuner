"""
Cloud speech provider adapters for VoiceTuner.

Local engines cannot serve Telugu (and only partially serve Hindi), so the
trilingual product relies on cloud providers for hi/te:

  - sarvam:     primary TTS + STT for en/hi/te (purpose-built for Indic)
  - groq:       fast Whisper-large-v3 STT alternative
  - elevenlabs: voice cloning + premium English (added separately)

Each adapter implements the same TTSBackend / STTBackend protocol used by the
local engines so it plugs into the existing registry in backends/__init__.py.
"""

from .credentials import (
    get_elevenlabs_key,
    get_groq_key,
    get_sarvam_key,
    load_env,
)

__all__ = [
    "get_sarvam_key",
    "get_groq_key",
    "get_elevenlabs_key",
    "load_env",
]
