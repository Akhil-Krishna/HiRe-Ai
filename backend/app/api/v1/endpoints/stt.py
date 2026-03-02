
import logging
import os
import time
import base64

from fastapi import APIRouter, UploadFile, File, Depends, Form
from app.core.deps import get_current_user
from app.core.task_runner import run_task_with_fallback
from app.models.user import User
from app.services.whisper_service import transcribe_audio
from app.tasks.stt_tasks import transcribe_audio_task

router = APIRouter(prefix="/stt", tags=["stt"])
logger = logging.getLogger(__name__)


@router.post("/transcribe")
async def transcribe(
    audio: UploadFile = File(..., description="Audio blob — webm/ogg/wav"),
    language: str = Form(default="en"),
    current_user: User = Depends(get_current_user),
):
    """
    Receive audio chunk from browser, transcribe with Whisper, return text.
    Called every time the candidate stops speaking (silence detected client-side).
    """
    started = time.perf_counter()
    audio_bytes = await audio.read()
    if not audio_bytes:
        return {
            "text": "",
            "available": False,
            "processing_ms": 0.0,
            "model": os.getenv("STT_MODEL", "base"),
        }

    async def _fallback():
        return await transcribe_audio(audio_bytes, language=language)

    result = await run_task_with_fallback(
        transcribe_audio_task,
        payload={
            "audio_b64": base64.b64encode(audio_bytes).decode("ascii"),
            "language": language,
        },
        fallback_callable=_fallback,
        endpoint_name="/stt/transcribe",
        realtime=True,
    )
    if "processing_ms" not in result:
        result["processing_ms"] = round((time.perf_counter() - started) * 1000.0, 1)
    logger.info(
        "STT endpoint user=%s bytes=%d available=%s model=%s processing_ms=%s",
        current_user.id,
        len(audio_bytes),
        result.get("available"),
        result.get("model"),
        result.get("processing_ms"),
    )
    return result
