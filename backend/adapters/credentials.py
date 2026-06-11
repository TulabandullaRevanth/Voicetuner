"""
Zero-dependency credential loading for cloud speech providers.

The backend does not use python-dotenv, so we ship a minimal `.env` parser that
populates os.environ on first import. This also works inside frozen PyInstaller
builds (where the `.env` may sit next to the executable).

Key names are read leniently: both the canonical SCREAMING_SNAKE_CASE names and
the lowercase variants the operator may have written by hand are accepted.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_LOADED = False


def _candidate_env_paths() -> list[Path]:
    """Locations to search for a .env file, in priority order."""
    here = Path(__file__).resolve()
    candidates = [
        Path.cwd() / ".env",
        here.parent.parent / ".env",          # backend/.env
        here.parent.parent.parent / ".env",   # repo root .env
    ]
    # Frozen build: .env next to the executable.
    try:
        import sys

        if getattr(sys, "frozen", False):
            candidates.append(Path(sys.executable).resolve().parent / ".env")
    except Exception:
        pass
    # De-dupe while preserving order.
    seen: set[Path] = set()
    out: list[Path] = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def load_env(force: bool = False) -> None:
    """Parse the first .env found and set any vars not already in the env."""
    global _LOADED
    if _LOADED and not force:
        return
    _LOADED = True

    for path in _candidate_env_paths():
        if not path.is_file():
            continue
        try:
            for raw in path.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
            logger.info("Loaded credentials from %s", path)
        except Exception as e:  # never let a malformed .env crash startup
            logger.warning("Failed to read %s: %s", path, e)
        break


def _read_key(*names: str) -> str | None:
    """Return the first non-empty env var among the given candidate names."""
    load_env()
    for name in names:
        val = os.environ.get(name)
        if val and val.strip():
            return val.strip()
    return None


def get_sarvam_key() -> str | None:
    return _read_key("SARVAM_API_KEY", "Sarvam_apikey", "sarvam_apikey")


def get_groq_key() -> str | None:
    return _read_key("GROQ_API_KEY", "groq_apikey", "Groq_apikey")


def get_elevenlabs_key() -> str | None:
    return _read_key("ELEVENLABS_API_KEY", "Elevenlabs_apikey", "elevenlabs_apikey")
