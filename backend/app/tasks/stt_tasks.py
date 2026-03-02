import asyncio
import base64
import logging

from app.core.celery_app import celery_app
from app.core.config import settings
from app.services.whisper_service import transcribe_audio

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=settings.CELERY_TASK_RETRY_BACKOFF,
    retry_jitter=settings.CELERY_TASK_RETRY_JITTER,
    retry_kwargs={"max_retries": settings.CELERY_TASK_MAX_RETRIES},
    name="app.tasks.stt.transcribe",
)
def transcribe_audio_task(self, payload: dict) -> dict:
    try:
        audio_bytes = base64.b64decode(payload.get("audio_b64", ""))
        language = payload.get("language", "en")
        return asyncio.run(transcribe_audio(audio_bytes, language=language))
    except Exception as exc:
        logger.exception("STT task failed: %s", exc)
        raise

