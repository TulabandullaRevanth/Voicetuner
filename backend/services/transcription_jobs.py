"""
Background, chunked speech-to-text for long audio.

The synchronous ``/transcribe`` endpoint loads the whole file and blocks the
request until Whisper finishes — fine for a short dictation clip, but unusable
for a multi-hour recording (it would exhaust memory and blow past HTTP
timeouts). This module transcribes such files as a background job:

  * the audio is read one bounded chunk at a time (so memory stays flat
    regardless of total length),
  * each chunk is transcribed by whichever STT provider serves the language
    (local Whisper, or Sarvam/Groq for hi/te), and the text is stitched,
  * progress is tracked in an in-memory registry the API polls.

On completion the transcript is persisted as a :class:`Transcription` row so it
shows up in the saved-transcripts list alongside short dictations.
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from ..utils.audio import get_audio_duration, load_audio, save_audio
from .speech_router import get_stt_backend_for_language
from .task_queue import create_background_task

logger = logging.getLogger(__name__)

# Read/transcribe this many seconds at a time. Bounds peak memory (~120s of
# 24kHz mono audio ≈ 11 MB) no matter how long the source file is.
CHUNK_SECONDS = 120.0
# Files at or below this length skip chunking and go through in one pass.
SINGLE_PASS_MAX_SECONDS = CHUNK_SECONDS


@dataclass
class TranscriptionJob:
    """In-memory state for one background transcription."""

    id: str
    language: str
    model: Optional[str] = None
    status: str = "pending"  # pending | running | completed | error
    progress: float = 0.0  # 0.0 – 1.0
    processed_seconds: float = 0.0
    total_seconds: float = 0.0
    text: str = ""
    error: Optional[str] = None
    transcription_id: Optional[str] = None
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "job_id": self.id,
            "status": self.status,
            "progress": round(self.progress, 4),
            "processed_seconds": round(self.processed_seconds, 2),
            "total_seconds": round(self.total_seconds, 2),
            "text": self.text,
            "duration": round(self.total_seconds, 2),
            "language": self.language,
            "error": self.error,
            "transcription_id": self.transcription_id,
        }


_jobs: Dict[str, TranscriptionJob] = {}


def get_job(job_id: str) -> Optional[TranscriptionJob]:
    return _jobs.get(job_id)


def _prune_old_jobs(max_age_seconds: float = 3600.0) -> None:
    """Drop finished jobs older than an hour so the registry can't grow without
    bound across a long-lived server."""
    now = time.time()
    stale = [
        jid
        for jid, job in _jobs.items()
        if job.status in ("completed", "error") and now - job.created_at > max_age_seconds
    ]
    for jid in stale:
        _jobs.pop(jid, None)


async def _resolve_local_model(backend, model: Optional[str]) -> str:
    """Pick + ensure the Whisper model size is loaded before the chunk loop, so
    we don't pay model-load cost per chunk."""
    from ..backends import WHISPER_HF_REPOS

    model_size = model or backend.model_size
    valid_sizes = list(WHISPER_HF_REPOS.keys())
    if model_size not in valid_sizes:
        raise ValueError(
            f"Invalid model size '{model_size}'. Must be one of: {', '.join(valid_sizes)}"
        )
    already_loaded = backend.is_loaded() and backend.model_size == model_size
    if not already_loaded:
        await backend.load_model_async(model_size)
    return model_size


async def _transcribe_chunk(backend, is_local: bool, chunk_path: str,
                            language: str, model_size: Optional[str]) -> str:
    if is_local:
        return await backend.transcribe(chunk_path, language, model_size)
    return await backend.transcribe(chunk_path, language, None)


async def _run_job(job: TranscriptionJob, audio_path: str) -> None:
    job.status = "running"
    try:
        total = await asyncio.to_thread(get_audio_duration, audio_path)
        job.total_seconds = total

        backend = get_stt_backend_for_language(job.language)
        is_local = hasattr(backend, "load_model_async")
        model_size = await _resolve_local_model(backend, job.model) if is_local else None

        parts: List[str] = []
        offset = 0.0
        while offset < total:
            length = min(CHUNK_SECONDS, total - offset)
            audio, sr = await asyncio.to_thread(
                load_audio, audio_path, 24000, True, offset, length
            )

            chunk_path = None
            try:
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                    chunk_path = tmp.name
                await asyncio.to_thread(save_audio, audio, chunk_path, sr)
                text = await _transcribe_chunk(
                    backend, is_local, chunk_path, job.language, model_size
                )
            finally:
                if chunk_path:
                    Path(chunk_path).unlink(missing_ok=True)

            if text and text.strip():
                parts.append(text.strip())

            offset += length
            job.processed_seconds = min(offset, total)
            job.progress = (job.processed_seconds / total) if total > 0 else 1.0
            # Stream partial text so the UI can show progress as it goes.
            job.text = " ".join(parts)

        job.text = " ".join(parts)
        job.transcription_id = await asyncio.to_thread(
            _persist_transcription, job.text, job.language, total
        )
        job.progress = 1.0
        job.status = "completed"
    except Exception as e:  # noqa: BLE001 — surface any failure to the client
        logger.exception("Transcription job %s failed", job.id)
        job.error = str(e)
        job.status = "error"
    finally:
        Path(audio_path).unlink(missing_ok=True)
        _prune_old_jobs()


def _persist_transcription(text: str, language: str, duration: float) -> str:
    """Save a finished transcript and return its id. Runs off the event loop.

    Uses ``get_db()`` (not a direct ``SessionLocal`` import) because the
    sessionmaker is only bound inside ``init_db()`` at startup — importing the
    re-exported ``SessionLocal`` captures the pre-init ``None``."""
    from ..database import Transcription, get_db

    db = next(get_db())
    try:
        row = Transcription(text=text, language=language, duration=duration)
        db.add(row)
        db.commit()
        db.refresh(row)
        return row.id
    finally:
        db.close()


def start_transcription_job(
    audio_path: str, language: str, model: Optional[str] = None
) -> str:
    """Schedule a chunked transcription of ``audio_path`` and return its job id.
    Takes ownership of ``audio_path`` (deletes it when the job ends)."""
    job = TranscriptionJob(id=uuid.uuid4().hex, language=language, model=model)
    _jobs[job.id] = job
    create_background_task(_run_job(job, audio_path))
    return job.id
