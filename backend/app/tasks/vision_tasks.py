import asyncio
import logging

from app.core.celery_app import celery_app
from app.core.config import settings
from app.services.vision_service import analyze_frame

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=settings.CELERY_TASK_RETRY_BACKOFF,
    retry_jitter=settings.CELERY_TASK_RETRY_JITTER,
    retry_kwargs={"max_retries": settings.CELERY_TASK_MAX_RETRIES},
    name="app.tasks.vision.analyze_frame",
)
def analyze_vision_frame_task(self, payload: dict) -> dict:
    try:
        return asyncio.run(analyze_frame(payload["frame"]))
    except Exception as exc:
        logger.exception("Vision task failed: %s", exc)
        raise

