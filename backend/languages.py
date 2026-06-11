"""
Single source of truth for VoiceTuner's supported languages.

VoiceTuner restricts the platform to English (en), Hindi (hi), and Telugu (te).
Every validation layer, engine registry, and route should derive its allow-list
from here rather than hard-coding ISO code lists.

The set can be overridden at deploy time via the SUPPORTED_LANGUAGES env var
(comma-separated), but do NOT add a code the speech stack cannot actually serve
-- see backend/adapters for which providers cover which languages.
"""

from __future__ import annotations

import os

# Canonical allow-list. Override with SUPPORTED_LANGUAGES="en,hi,te".
SUPPORTED_LANGUAGES: list[str] = [
    c.strip().lower()
    for c in os.getenv("SUPPORTED_LANGUAGES", "en,hi,te").split(",")
    if c.strip()
]

# Whisper / engine-facing language *names* (lowercase, as some backends expect).
LANGUAGE_NAMES: dict[str, str] = {
    "en": "english",
    "hi": "hindi",
    "te": "telugu",
}

# Human-readable labels for UI / API responses (native script).
LANGUAGE_LABELS: dict[str, str] = {
    "en": "English",
    "hi": "हिन्दी",
    "te": "తెలుగు",
}

# Provider locale tags (used by Sarvam / cloud adapters that want BCP-47-ish codes).
PROVIDER_LOCALE: dict[str, str] = {
    "en": "en-IN",
    "hi": "hi-IN",
    "te": "te-IN",
}

# Regex fragment for pydantic Field(pattern=...). e.g. "^(en|hi|te)$"
LANGUAGE_PATTERN: str = f"^({'|'.join(SUPPORTED_LANGUAGES)})$"

# Same, but allowing the STT "auto" sentinel (auto-detect).
LANGUAGE_PATTERN_WITH_AUTO: str = f"^(auto|{'|'.join(SUPPORTED_LANGUAGES)})$"


def is_supported(code: str | None) -> bool:
    return code is not None and code.lower() in SUPPORTED_LANGUAGES


def assert_supported(code: str) -> str:
    """Return the normalized code or raise ValueError. Use in service layers."""
    norm = (code or "").lower()
    if norm not in SUPPORTED_LANGUAGES:
        raise ValueError(
            f"Unsupported language '{code}'. Allowed: {SUPPORTED_LANGUAGES}"
        )
    return norm


def coerce_supported(code: str | None, default: str = "en") -> str:
    """Best-effort: map unknown/legacy codes to the default supported language."""
    norm = (code or "").lower()
    return norm if norm in SUPPORTED_LANGUAGES else default
