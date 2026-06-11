"""Speaker identification via GE2E d-vector embeddings (Resemblyzer).

Workflow:
  1. When voice profile samples are uploaded, call ``rebuild_profile_embedding``
     to compute and store a mean d-vector in the profiles table.
  2. When a capture arrives, call ``identify_speaker`` to match its audio
     against every stored embedding using cosine similarity.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

# Resemblyzer is lazy-imported so the module loads even when the package
# isn't installed (frozen builds that don't need speaker ID).
_encoder = None

SIMILARITY_THRESHOLD = 0.82  # cosine similarity above which we tag a speaker


def _get_encoder():
    global _encoder
    if _encoder is None:
        try:
            from resemblyzer import VoiceEncoder
            _encoder = VoiceEncoder()
        except ImportError:
            logger.warning("resemblyzer not installed — speaker identification disabled")
            return None
        except Exception as exc:
            logger.warning("Could not load VoiceEncoder: %s", exc)
            return None
    return _encoder


def _embed_file(audio_path: str | Path) -> Optional[np.ndarray]:
    """Extract a d-vector from a single audio file. Returns None on failure."""
    enc = _get_encoder()
    if enc is None:
        return None
    try:
        from resemblyzer import preprocess_wav
        wav = preprocess_wav(Path(audio_path))
        if len(wav) < 1600:  # < 0.1 s at 16 kHz — too short to embed
            return None
        return enc.embed_utterance(wav)
    except Exception as exc:
        logger.debug("Could not embed %s: %s", audio_path, exc)
        return None


def build_mean_embedding(audio_paths: list[str | Path]) -> Optional[list[float]]:
    """Compute a mean d-vector over multiple audio files.

    Returns a plain Python list of floats (JSON-serialisable) or None if no
    file could be embedded.
    """
    vecs = [v for p in audio_paths if (v := _embed_file(p)) is not None]
    if not vecs:
        return None
    mean = np.mean(np.stack(vecs, axis=0), axis=0)
    mean /= np.linalg.norm(mean) + 1e-9
    return mean.tolist()


def identify_speaker(
    audio_path: str | Path,
    candidates: list[tuple[str, str, str]],  # [(profile_id, profile_name, embedding_json)]
    threshold: float = SIMILARITY_THRESHOLD,
) -> Optional[tuple[str, str, float]]:
    """Return (profile_id, profile_name, confidence) for the best match, or None.

    ``candidates`` is a list of (profile_id, profile_name, embedding_json) tuples
    where embedding_json is a JSON string encoding a list[float] d-vector.
    """
    if not candidates:
        return None

    query_vec = _embed_file(audio_path)
    if query_vec is None:
        return None

    best_id, best_name, best_score = None, None, -1.0
    for profile_id, profile_name, emb_json in candidates:
        try:
            ref = np.array(json.loads(emb_json), dtype=np.float32)
        except (ValueError, TypeError):
            continue
        if ref.shape != query_vec.shape:
            continue
        score = float(np.dot(query_vec, ref) / (np.linalg.norm(ref) + 1e-9))
        if score > best_score:
            best_score, best_id, best_name = score, profile_id, profile_name

    if best_score >= threshold:
        return best_id, best_name, round(best_score, 4)
    return None
