"""
Audio processing utilities.
"""

import numpy as np
import soundfile as sf
import librosa
from typing import Tuple, Optional


def normalize_audio(
    audio: np.ndarray,
    target_db: float = -20.0,
    peak_limit: float = 0.85,
) -> np.ndarray:
    """
    Normalize audio to target loudness with peak limiting.
    
    Args:
        audio: Input audio array
        target_db: Target RMS level in dB
        peak_limit: Peak limit (0.0-1.0)
        
    Returns:
        Normalized audio array
    """
    # Convert to float32
    audio = audio.astype(np.float32)
    
    # Calculate current RMS
    rms = np.sqrt(np.mean(audio**2))
    
    # Calculate target RMS
    target_rms = 10**(target_db / 20)
    
    # Apply gain
    if rms > 0:
        gain = target_rms / rms
        audio = audio * gain
    
    # Peak limiting
    audio = np.clip(audio, -peak_limit, peak_limit)
    
    return audio


def normalize_audio_file(
    path: str,
    target_db: float = -20.0,
    peak_limit: float = 0.85,
    block_frames: int = 48000 * 10,  # 10-second blocks
) -> None:
    """Normalize a WAV file in-place using two streaming passes.

    Avoids loading the whole file into RAM — safe for multi-hour audio.
    Pass 1: compute RMS and peak across all blocks.
    Pass 2: apply gain + peak clamp and write to a temp file, then replace.
    """
    import os

    # Pass 1: scan RMS and peak
    sum_sq = 0.0
    total_frames = 0
    peak = 0.0
    with sf.SoundFile(path, "r") as f:
        for block in f.blocks(blocksize=block_frames, dtype="float32"):
            sum_sq += float(np.sum(block.astype(np.float64) ** 2))
            total_frames += len(block)
            block_peak = float(np.abs(block).max())
            if block_peak > peak:
                peak = block_peak

    if total_frames == 0:
        return

    rms = np.sqrt(sum_sq / total_frames)
    target_rms = 10 ** (target_db / 20)
    gain = (target_rms / rms) if rms > 0 else 1.0

    # Cap gain so peak stays within limit after amplification
    if peak * gain > peak_limit:
        gain = peak_limit / peak if peak > 0 else gain

    if abs(gain - 1.0) < 1e-4:
        return  # Essentially unchanged, skip the write pass

    # Pass 2: apply gain and write to temp file
    tmp = path + ".norm.tmp"
    try:
        with sf.SoundFile(path, "r") as src, \
             sf.SoundFile(tmp, "w", samplerate=src.samplerate,
                          channels=src.channels, format="WAV", subtype="FLOAT") as dst:
            for block in src.blocks(blocksize=block_frames, dtype="float32"):
                block = np.clip(block * gain, -peak_limit, peak_limit)
                dst.write(block)
        os.replace(tmp, path)
    except Exception:
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except Exception:
            pass
        raise


def load_audio(
    path: str,
    sample_rate: int = 24000,
    mono: bool = True,
    offset: float = 0.0,
    duration: Optional[float] = None,
) -> Tuple[np.ndarray, int]:
    """
    Load audio file with normalization.

    Args:
        path: Path to audio file
        sample_rate: Target sample rate
        mono: Convert to mono
        offset: Start reading this many seconds into the file
        duration: Only load this many seconds (None = until the end).
            Lets callers bound memory when reading a slice of a long file.

    Returns:
        Tuple of (audio_array, sample_rate)
    """
    audio, sr = librosa.load(
        path, sr=sample_rate, mono=mono, offset=offset, duration=duration
    )
    return audio, sr


def get_audio_duration(path: str) -> float:
    """Return an audio file's duration in seconds without loading the samples."""
    return float(librosa.get_duration(path=path))


def _pick_loudest_window(
    audio: np.ndarray, sr: int, window_seconds: float
) -> np.ndarray:
    """
    Return the contiguous ``window_seconds`` slice of ``audio`` with the
    highest RMS energy. Used to pick a representative voice-cloning reference
    from a longer recording instead of blindly taking the first N seconds
    (which may be silence or noise).
    """
    window = int(window_seconds * sr)
    if window <= 0 or len(audio) <= window:
        return audio

    hop = max(1, int(0.5 * sr))  # scan in 0.5s steps
    best_start = 0
    best_energy = -1.0
    for start in range(0, len(audio) - window + 1, hop):
        seg = audio[start : start + window]
        energy = float(np.mean(seg.astype(np.float64) ** 2))
        if energy > best_energy:
            best_energy = energy
            best_start = start
    return audio[best_start : best_start + window]


def save_audio(
    audio: np.ndarray,
    path: str,
    sample_rate: int = 24000,
) -> None:
    """
    Save audio file with atomic write and error handling.

    Writes to a temporary file first, then atomically renames to the
    target path.  This prevents corrupted/partial WAV files if the
    process is interrupted mid-write.

    Args:
        audio: Audio array
        path: Output path
        sample_rate: Sample rate

    Raises:
        OSError: If file cannot be written
    """
    from pathlib import Path
    import os

    temp_path = f"{path}.tmp"
    try:
        # Ensure parent directory exists
        Path(path).parent.mkdir(parents=True, exist_ok=True)

        # Write to temporary file first (explicit format since .tmp
        # extension is not recognised by soundfile)
        sf.write(temp_path, audio, sample_rate, format='WAV')

        # Atomic rename to final path
        os.replace(temp_path, path)

    except Exception as e:
        # Clean up temp file on failure
        try:
            if Path(temp_path).exists():
                Path(temp_path).unlink()
        except Exception:
            pass  # Best effort cleanup

        raise OSError(f"Failed to save audio to {path}: {e}") from e


def trim_tts_output(
    audio: np.ndarray,
    sample_rate: int = 24000,
    frame_ms: int = 20,
    silence_threshold_db: float = -40.0,
    min_silence_ms: int = 200,
    max_internal_silence_ms: int = 1000,
    fade_ms: int = 30,
) -> np.ndarray:
    """
    Trim trailing silence and post-silence hallucination from TTS output.

    Chatterbox sometimes produces ``[speech][silence][hallucinated noise]``.
    This detects internal silence gaps longer than *max_internal_silence_ms*
    and cuts the audio at that boundary, then trims trailing silence and
    applies a short cosine fade-out.

    Args:
        audio: Input audio array (mono float32)
        sample_rate: Sample rate in Hz
        frame_ms: Frame size for RMS energy calculation
        silence_threshold_db: dB threshold below which a frame is silence
        min_silence_ms: Minimum trailing silence to keep
        max_internal_silence_ms: Cut after any silence gap longer than this
        fade_ms: Cosine fade-out duration in ms

    Returns:
        Trimmed audio array
    """
    frame_len = int(sample_rate * frame_ms / 1000)
    if frame_len == 0 or len(audio) < frame_len:
        return audio

    n_frames = len(audio) // frame_len
    threshold_linear = 10 ** (silence_threshold_db / 20)

    # Compute per-frame RMS
    rms = np.array(
        [
            np.sqrt(np.mean(audio[i * frame_len : (i + 1) * frame_len] ** 2))
            for i in range(n_frames)
        ]
    )
    is_speech = rms >= threshold_linear

    # Find first speech frame
    first_speech = 0
    for i, s in enumerate(is_speech):
        if s:
            first_speech = max(0, i - 1)  # keep 1 frame padding
            break

    # Walk forward from first speech; cut at long internal silence gaps
    max_silence_frames = int(max_internal_silence_ms / frame_ms)
    consecutive_silence = 0
    cut_frame = n_frames

    for i in range(first_speech, n_frames):
        if is_speech[i]:
            consecutive_silence = 0
        else:
            consecutive_silence += 1
            if consecutive_silence >= max_silence_frames:
                cut_frame = i - consecutive_silence + 1
                break

    # Trim trailing silence from the cut point
    min_silence_frames = int(min_silence_ms / frame_ms)
    end_frame = cut_frame
    while end_frame > first_speech and not is_speech[end_frame - 1]:
        end_frame -= 1
    # Keep a short tail
    end_frame = min(end_frame + min_silence_frames, cut_frame)

    # Convert frames back to samples
    start_sample = first_speech * frame_len
    end_sample = min(end_frame * frame_len, len(audio))

    trimmed = audio[start_sample:end_sample].copy()

    # Cosine fade-out
    fade_samples = int(sample_rate * fade_ms / 1000)
    if fade_samples > 0 and len(trimmed) > fade_samples:
        fade = np.cos(np.linspace(0, np.pi / 2, fade_samples)) ** 2
        trimmed[-fade_samples:] *= fade

    return trimmed


def preprocess_reference_audio(
    audio: np.ndarray,
    sample_rate: int,
    peak_target: float = 0.95,
    trim_top_db: float = 40.0,
    edge_padding_ms: int = 100,
) -> np.ndarray:
    """
    Clean up a reference-audio sample before validation/storage.

    Removes DC offset, trims leading/trailing silence, and caps the peak so a
    slightly-hot recording doesn't get rejected downstream as "clipping". The
    goal is to accept reasonable real-world recordings — not to repair badly
    distorted ones. True clipping artifacts inside the waveform can't be
    recovered by peak scaling and will still sound bad.

    Args:
        audio: Mono audio array.
        sample_rate: Sample rate of ``audio`` in Hz.
        peak_target: Peak amplitude cap in [0, 1]. Applied only if the input
            peak exceeds this value.
        trim_top_db: Silence threshold for edge trimming, in dB below peak.
            40 dB sits below normal speech dynamic range (≈30 dB) so soft
            trailing syllables are preserved, while still catching obvious
            leading/trailing silence. Lower values are more aggressive;
            librosa's own default is 60.
        edge_padding_ms: Milliseconds of padding to add back at each edge
            *only if* trimming shortened the waveform, so TTS engines have a
            brief silence to anchor on without ever making the output longer
            than the input.

    Returns:
        Preprocessed audio array (float32).
    """
    audio = audio.astype(np.float32, copy=False)

    if audio.size == 0:
        return audio

    audio = audio - float(np.mean(audio))

    trimmed, _ = librosa.effects.trim(audio, top_db=trim_top_db)
    if 0 < trimmed.size < audio.size:
        pad_each = int(sample_rate * edge_padding_ms / 1000)
        # Never pad past the original length — for near-max-duration uploads
        # an unconditional pad would push them over the 30 s ceiling and
        # trigger a spurious "too long" rejection.
        headroom = (audio.size - trimmed.size) // 2
        pad = min(pad_each, max(headroom, 0))
        if pad > 0:
            trimmed = np.pad(trimmed, (pad, pad), mode="constant")
        audio = trimmed

    peak = float(np.abs(audio).max())
    if peak > peak_target and peak > 0:
        audio = audio * (peak_target / peak)

    return audio


def validate_reference_audio(
    audio_path: str,
    min_duration: float = 2.0,
    max_duration: float = 30.0,
    min_rms: float = 0.01,
) -> Tuple[bool, Optional[str]]:
    """
    Validate reference audio for voice cloning.

    Args:
        audio_path: Path to audio file
        min_duration: Minimum duration in seconds
        max_duration: Maximum duration in seconds
        min_rms: Minimum RMS level

    Returns:
        Tuple of (is_valid, error_message)
    """
    result = validate_and_load_reference_audio(
        audio_path, min_duration, max_duration, min_rms
    )
    return (result[0], result[1])


def validate_and_load_reference_audio(
    audio_path: str,
    min_duration: float = 2.0,
    max_duration: float = 30.0,
    min_rms: float = 0.01,
) -> Tuple[bool, Optional[str], Optional[np.ndarray], Optional[int]]:
    """
    Validate and load reference audio in a single pass.

    Applies :func:`preprocess_reference_audio` before checks so that
    slightly-hot recordings aren't rejected as clipping. Duration and RMS
    checks run on the preprocessed waveform.

    Long uploads (e.g. a multi-hour recording) are *not* rejected — only a
    short, representative segment is needed to clone a voice. When the file is
    longer than ``max_duration`` we read just a bounded probe window from the
    start of the file (so memory stays small even for hours-long audio) and
    keep the loudest ``max_duration`` slice of it as the reference.

    Returns:
        Tuple of (is_valid, error_message, audio_array, sample_rate)
    """
    try:
        # Cheaply check the full duration without decoding the whole file.
        try:
            total_duration = get_audio_duration(audio_path)
        except Exception:
            total_duration = None

        if total_duration is not None and total_duration > max_duration:
            # Read only a bounded probe window instead of the full file, so a
            # 5-hour upload doesn't pull gigabytes of samples into memory.
            probe_seconds = min(total_duration, max(max_duration * 3.0, 90.0))
            audio, sr = load_audio(audio_path, duration=probe_seconds)
            audio = preprocess_reference_audio(audio, sr)
            audio = _pick_loudest_window(audio, sr, max_duration)
        else:
            audio, sr = load_audio(audio_path)
            audio = preprocess_reference_audio(audio, sr)

        duration = len(audio) / sr

        if duration < min_duration:
            return False, f"Audio too short (minimum {min_duration} seconds)", None, None

        rms = np.sqrt(np.mean(audio**2))
        if rms < min_rms:
            return False, "Audio is too quiet or silent", None, None

        return True, None, audio, sr
    except Exception as e:
        return False, f"Error validating audio: {str(e)}", None, None
