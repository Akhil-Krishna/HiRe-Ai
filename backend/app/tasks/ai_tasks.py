import asyncio
import logging

from app.core.celery_app import celery_app
from app.core.config import settings
from app.services.ai_service import (
    generate_final_evaluation_from_payload,
    get_ai_response_from_payload,
)

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=settings.CELERY_TASK_RETRY_BACKOFF,
    retry_jitter=settings.CELERY_TASK_RETRY_JITTER,
    retry_kwargs={"max_retries": settings.CELERY_TASK_MAX_RETRIES},
    name="app.tasks.ai.generate_response",
)
def generate_ai_response_task(self, payload: dict) -> dict:
    try:
        return asyncio.run(get_ai_response_from_payload(payload))
    except Exception as exc:
        logger.exception("AI response task failed: %s", exc)
        raise


@celery_app.task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=settings.CELERY_TASK_RETRY_BACKOFF,
    retry_jitter=settings.CELERY_TASK_RETRY_JITTER,
    retry_kwargs={"max_retries": settings.CELERY_TASK_MAX_RETRIES},
    name="app.tasks.ai.generate_evaluation",
)
def generate_final_evaluation_task(self, payload: dict) -> dict:
    try:
        return asyncio.run(generate_final_evaluation_from_payload(payload))
    except Exception as exc:
        logger.exception("AI evaluation task failed: %s", exc)
        raise

