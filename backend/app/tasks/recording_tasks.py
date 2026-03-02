import logging

from app.core.celery_app import celery_app
from app.core.config import settings
from app.services.recording_service import process_recording_metadata

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=settings.CELERY_TASK_RETRY_BACKOFF,
    retry_jitter=settings.CELERY_TASK_RETRY_JITTER,
    retry_kwargs={"max_retries": settings.CELERY_TASK_MAX_RETRIES},
    name="app.tasks.recording.process_metadata",
)
def process_recording_metadata_task(self, payload: dict) -> dict:
    try:
        return process_recording_metadata(
            recording_path=payload.get("recording_path", ""),
            size_bytes=int(payload.get("size_bytes", 0)),
        )
    except Exception as exc:
        logger.exception("Recording metadata task failed: %s", exc)
        raise

