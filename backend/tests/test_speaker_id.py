"""
Unit tests for backend.adapters.speaker_id.

All tests mock ``_embed_file`` so they run without resemblyzer or any GPU.
The integration block at the bottom is skipped when resemblyzer is absent.
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest
import soundfile as sf

sys.path.insert(0, str(Path(__file__).parent.parent))

from adapters.speaker_id import (  # noqa: E402
    SIMILARITY_THRESHOLD,
    build_mean_embedding,
    identify_speaker,
)


# ── helpers ──────────────────────────────────────────────────────────


def _unit_vec(dim: int = 256, seed: int = 0) -> np.ndarray:
    """Return a deterministic L2-normalised float32 vector."""
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(dim).astype(np.float32)
    return v / (np.linalg.norm(v) + 1e-9)


def _make_wav(tmp_path: Path, seed: int = 0, duration_s: float = 3.0, sr: int = 16000) -> Path:
    """Write a deterministic sine-tone WAV and return its path."""
    rng = np.random.default_rng(seed)
    audio = (rng.standard_normal(int(sr * duration_s)) * 0.1).astype(np.float32)
    p = tmp_path / f"audio_{seed}.wav"
    sf.write(str(p), audio, sr)
    return p


# ── identify_speaker ─────────────────────────────────────────────────


def test_best_match_returned(tmp_path):
    """The candidate with the highest cosine score above threshold is returned."""
    query = _unit_vec(seed=0)
    ref_match = query.copy()          # cosine similarity == 1.0
    ref_other = _unit_vec(seed=42)    # unrelated direction

    candidates = [
        ("id-other", "Alice", json.dumps(ref_other.tolist())),
        ("id-match", "Bob",   json.dumps(ref_match.tolist())),
    ]

    wav = _make_wav(tmp_path, seed=0)
    with patch("adapters.speaker_id._embed_file", return_value=query):
        result = identify_speaker(str(wav), candidates)

    assert result is not None
    pid, name, conf = result
    assert pid == "id-match"
    assert name == "Bob"
    assert conf >= 0.99


def test_below_threshold_returns_none(tmp_path):
    """When the best cosine score is below the threshold, None is returned."""
    query = _unit_vec(seed=0)
    ref   = _unit_vec(seed=99)        # orthogonal-ish direction

    candidates = [("id-1", "Alice", json.dumps(ref.tolist()))]
    wav = _make_wav(tmp_path)

    with patch("adapters.speaker_id._embed_file", return_value=query):
        result = identify_speaker(str(wav), candidates, threshold=0.99)

    assert result is None


def test_empty_candidates_returns_none(tmp_path):
    wav = _make_wav(tmp_path)
    with patch("adapters.speaker_id._embed_file", return_value=_unit_vec()):
        assert identify_speaker(str(wav), []) is None


def test_embed_failure_returns_none(tmp_path):
    """If the audio cannot be embedded (too short, corrupt), None is returned."""
    wav = _make_wav(tmp_path)
    candidates = [("id-1", "Alice", json.dumps(_unit_vec().tolist()))]

    with patch("adapters.speaker_id._embed_file", return_value=None):
        assert identify_speaker(str(wav), candidates) is None


def test_invalid_json_candidate_skipped(tmp_path):
    """Candidates with malformed JSON embeddings are silently skipped."""
    query = _unit_vec(seed=0)
    candidates = [
        ("id-bad",  "Corrupt", "not-valid-json"),
        ("id-good", "Valid",   json.dumps(query.tolist())),
    ]
    wav = _make_wav(tmp_path)

    with patch("adapters.speaker_id._embed_file", return_value=query):
        result = identify_speaker(str(wav), candidates)

    assert result is not None
    assert result[0] == "id-good"


def test_wrong_shape_candidate_skipped(tmp_path):
    """Embeddings with a dimension mismatch are silently skipped."""
    query     = _unit_vec(dim=256, seed=0)
    wrong_dim = _unit_vec(dim=128, seed=1)

    candidates = [
        ("id-wrong", "WrongDim", json.dumps(wrong_dim.tolist())),
        ("id-right", "RightDim", json.dumps(query.tolist())),
    ]
    wav = _make_wav(tmp_path)

    with patch("adapters.speaker_id._embed_file", return_value=query):
        result = identify_speaker(str(wav), candidates)

    assert result is not None
    assert result[0] == "id-right"


def test_confidence_rounded_to_4dp(tmp_path):
    """Returned confidence score is rounded to 4 decimal places."""
    query = _unit_vec(seed=7)
    ref   = _unit_vec(seed=8)
    wav   = _make_wav(tmp_path)

    with patch("adapters.speaker_id._embed_file", return_value=query):
        result = identify_speaker(str(wav), [("id-1", "T", json.dumps(ref.tolist()))], threshold=0.0)

    assert result is not None
    _, _, conf = result
    assert conf == round(conf, 4)


def test_default_threshold_is_0_82():
    assert SIMILARITY_THRESHOLD == 0.82


def test_exact_self_match_confidence_is_1(tmp_path):
    """Embedding matched against an identical stored vector → confidence ≈ 1.0."""
    v   = _unit_vec(seed=5)
    wav = _make_wav(tmp_path)

    with patch("adapters.speaker_id._embed_file", return_value=v):
        result = identify_speaker(str(wav), [("id-1", "Me", json.dumps(v.tolist()))])

    assert result is not None
    assert result[2] >= 0.9999


# ── build_mean_embedding ─────────────────────────────────────────────


def test_mean_embedding_averages_two_samples(tmp_path):
    v1 = _unit_vec(seed=1)
    v2 = _unit_vec(seed=2)
    expected = v1 + v2
    expected /= np.linalg.norm(expected) + 1e-9

    paths = [_make_wav(tmp_path, seed=1), _make_wav(tmp_path, seed=2)]

    with patch("adapters.speaker_id._embed_file", side_effect=[v1, v2]):
        result = build_mean_embedding([str(p) for p in paths])

    assert result is not None
    np.testing.assert_allclose(np.array(result, dtype=np.float32), expected, atol=1e-5)


def test_mean_embedding_single_sample_equals_input(tmp_path):
    v    = _unit_vec(seed=5)
    path = _make_wav(tmp_path, seed=5)

    with patch("adapters.speaker_id._embed_file", return_value=v):
        result = build_mean_embedding([str(path)])

    assert result is not None
    np.testing.assert_allclose(np.array(result, dtype=np.float32), v, atol=1e-5)


def test_mean_embedding_empty_paths_returns_none():
    assert build_mean_embedding([]) is None


def test_mean_embedding_all_files_unembeddable_returns_none(tmp_path):
    path = _make_wav(tmp_path, seed=99)
    with patch("adapters.speaker_id._embed_file", return_value=None):
        assert build_mean_embedding([str(path)]) is None


def test_mean_embedding_returns_plain_list(tmp_path):
    v    = _unit_vec(seed=10)
    path = _make_wav(tmp_path, seed=10)

    with patch("adapters.speaker_id._embed_file", return_value=v):
        result = build_mean_embedding([str(path)])

    assert isinstance(result, list)
    assert all(isinstance(x, float) for x in result[:5])


def test_mean_embedding_is_json_roundtrippable(tmp_path):
    v    = _unit_vec(seed=11)
    path = _make_wav(tmp_path, seed=11)

    with patch("adapters.speaker_id._embed_file", return_value=v):
        result = build_mean_embedding([str(path)])

    assert json.loads(json.dumps(result)) == result


def test_mean_embedding_is_unit_normalised(tmp_path):
    """Output vector has L2 norm ≈ 1.0 (required for cosine similarity)."""
    v    = _unit_vec(seed=3)
    path = _make_wav(tmp_path, seed=3)

    with patch("adapters.speaker_id._embed_file", return_value=v):
        result = build_mean_embedding([str(path)])

    assert abs(float(np.linalg.norm(result)) - 1.0) < 1e-4


def test_mean_embedding_skips_failed_files_uses_good_ones(tmp_path):
    """If some files fail to embed, the mean of the successful ones is returned."""
    v_good = _unit_vec(seed=20)
    paths  = [_make_wav(tmp_path, seed=i) for i in range(3)]

    # First two files fail; third succeeds
    with patch("adapters.speaker_id._embed_file", side_effect=[None, None, v_good]):
        result = build_mean_embedding([str(p) for p in paths])

    assert result is not None
    np.testing.assert_allclose(np.array(result, dtype=np.float32), v_good, atol=1e-5)


# ── integration (skipped when resemblyzer is absent) ─────────────────


@pytest.fixture(scope="module")
def resemblyzer_available():
    try:
        import resemblyzer  # noqa: F401
        return True
    except ImportError:
        return False


def test_integration_same_audio_encodes_consistently(tmp_path, resemblyzer_available):
    """
    Same audio file embedded twice should produce near-identical vectors.
    Skipped when resemblyzer is not installed.
    """
    if not resemblyzer_available:
        pytest.skip("resemblyzer not installed")

    sr    = 16000
    t     = np.linspace(0, 3.0, int(sr * 3.0), dtype=np.float32)
    audio = (0.2 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
    path  = tmp_path / "sine.wav"
    sf.write(str(path), audio, sr)

    result = build_mean_embedding([str(path), str(path)])
    assert result is not None

    vec = np.array(result, dtype=np.float32)
    # cosine of a vector with itself is 1.0
    score = float(np.dot(vec, vec) / (np.linalg.norm(vec) ** 2 + 1e-9))
    assert score >= 0.99


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
