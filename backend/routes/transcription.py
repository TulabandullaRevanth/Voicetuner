"""Transcription endpoints.

Short clips go through ``POST /transcribe`` synchronously. Long uploads
(multi-hour recordings) should use ``POST /transcribe/jobs``, which transcribes
the audio in bounded chunks on a background task and is polled via
``GET /transcribe/jobs/{id}``. Every finished transcript is persisted and served
back from ``GET /transcriptions``.
"""

import asyncio
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from .. import models
from ..database import Transcription as DBTranscription, get_db
from ..services import transcription_jobs
from ..services.speech_router import get_stt_backend_for_language
from ..services.task_queue import create_background_task
from ..utils.tasks import get_task_manager

router = APIRouter()

UPLOAD_CHUNK_SIZE = 1024 * 1024  # 1MB


async def _stream_upload_to_temp(file: UploadFile) -> str:
    """Spool an uploaded file to a temp .wav on disk, returning its path.
    Streams in chunks so even multi-GB uploads never sit fully in memory."""
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        while chunk := await file.read(UPLOAD_CHUNK_SIZE):
            tmp.write(chunk)
        return tmp.name


@router.post("/transcribe", response_model=models.TranscriptionResponse)
async def transcribe_audio(
    file: UploadFile = File(...),
    language: str | None = Form(None),
    model: str | None = Form(None),
):
    """Transcribe a (short) audio file to text synchronously."""
    tmp_path = await _stream_upload_to_temp(file)

    try:
        from ..utils.audio import load_audio
        from ..backends import WHISPER_HF_REPOS

        audio, sr = await asyncio.to_thread(load_audio, tmp_path)
        duration = len(audio) / sr

        # Route to the right STT provider (Sarvam/Groq for hi/te, local Whisper for en).
        whisper_model = get_stt_backend_for_language(language)

        # Cloud STT backends (Sarvam/Groq) have no downloadable Whisper sizes.
        if not hasattr(whisper_model, "load_model_async"):
            text = await whisper_model.transcribe(tmp_path, language, None)
        else:
            model_size = model if model else whisper_model.model_size

            valid_sizes = list(WHISPER_HF_REPOS.keys())
            if model_size not in valid_sizes:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid model size '{model_size}'. Must be one of: {', '.join(valid_sizes)}",
                )

            already_loaded = whisper_model.is_loaded() and whisper_model.model_size == model_size
            if not already_loaded and not whisper_model._is_model_cached(model_size):
                progress_model_name = f"whisper-{model_size}"
                task_manager = get_task_manager()

                async def download_whisper_background():
                    try:
                        await whisper_model.load_model_async(model_size)
                        task_manager.complete_download(progress_model_name)
                    except Exception as e:
                        task_manager.error_download(progress_model_name, str(e))

                task_manager.start_download(progress_model_name)
                create_background_task(download_whisper_background())

                raise HTTPException(
                    status_code=202,
                    detail={
                        "message": f"Whisper model {model_size} is being downloaded. Please wait and try again.",
                        "model_name": progress_model_name,
                        "downloading": True,
                    },
                )

            text = await whisper_model.transcribe(tmp_path, language, model_size)

        # Persist so short dictations appear in the saved-transcripts list too.
        await asyncio.to_thread(
            transcription_jobs._persist_transcription,
            (text or "").strip(),
            language or "en",
            duration,
        )

        return models.TranscriptionResponse(
            text=text,
            duration=duration,
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        Path(tmp_path).unlink(missing_ok=True)


@router.post("/transcribe/jobs")
async def start_transcription_job(
    file: UploadFile = File(...),
    language: str | None = Form(None),
    model: str | None = Form(None),
):
    """Start a background, chunked transcription for a long audio file.

    Returns a ``job_id`` to poll via ``GET /transcribe/jobs/{job_id}``. The
    upload is spooled to disk and processed one chunk at a time, so files of any
    length (5+ hours) are supported without exhausting memory or timing out.
    """
    tmp_path = await _stream_upload_to_temp(file)
    job_id = transcription_jobs.start_transcription_job(
        tmp_path, language or "en", model
    )
    return {"job_id": job_id, "status": "pending"}


@router.get("/transcribe/jobs/{job_id}")
async def get_transcription_job(job_id: str):
    """Poll the status/progress/result of a background transcription job."""
    job = transcription_jobs.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Transcription job not found")
    return job.to_dict()


@router.get("/transcriptions")
async def list_transcriptions(db: Session = Depends(get_db)):
    """List saved transcripts, newest first."""
    rows = (
        db.query(DBTranscription)
        .order_by(DBTranscription.created_at.desc())
        .all()
    )
    items = [
        {
            "id": r.id,
            "text": r.text,
            "language": r.language,
            "duration": r.duration,
            "created_at": (r.created_at.isoformat() if r.created_at else None),
        }
        for r in rows
    ]
    return {"items": items}


@router.delete("/transcriptions/{transcription_id}")
async def delete_transcription(transcription_id: str, db: Session = Depends(get_db)):
    """Delete a saved transcript."""
    row = db.query(DBTranscription).filter_by(id=transcription_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Transcription not found")
    db.delete(row)
    db.commit()
    return {"status": "deleted", "id": transcription_id}
