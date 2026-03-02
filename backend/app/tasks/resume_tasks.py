import base64
import logging

from app.core.celery_app import celery_app
from app.core.config import settings
from app.services.resume_service import extract_resume_text

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=settings.CELERY_TASK_RETRY_BACKOFF,
    retry_jitter=settings.CELERY_TASK_RETRY_JITTER,
    retry_kwargs={"max_retries": settings.CELERY_TASK_MAX_RETRIES},
    name="app.tasks.resume.extract_text",
)
def extract_resume_text_task(self, payload: dict) -> dict:
    try:
        content = base64.b64decode(payload.get("content_b64", ""))
        filename = payload.get("filename", "resume.pdf")
        text = extract_resume_text(content, filename=filename)
        return {"text": text}
    except Exception as exc:
        logger.exception("Resume extraction task failed: %s", exc)
        raise

