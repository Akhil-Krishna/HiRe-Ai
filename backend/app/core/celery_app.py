from celery import Celery

from app.core.config import settings


def _default_redis_url() -> str:
    return settings.REDIS_URL or "redis://localhost:6379/0"


def _broker_url() -> str:
    return settings.CELERY_BROKER_URL or _default_redis_url()


def _result_backend() -> str:
    return settings.CELERY_RESULT_BACKEND or _default_redis_url()


celery_app = Celery("hireai", broker=_broker_url(), backend=_result_backend())

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_track_started=True,
    broker_connection_retry_on_startup=True,
    task_soft_time_limit=settings.CELERY_SOFT_TIME_LIMIT_SECONDS,
    task_time_limit=settings.CELERY_TIME_LIMIT_SECONDS,
    worker_max_tasks_per_child=200,
    result_expires=3600,
    timezone="UTC",
    imports=(
        "app.tasks.ai_tasks",
        "app.tasks.vision_tasks",
        "app.tasks.stt_tasks",
        "app.tasks.email_tasks",
        "app.tasks.resume_tasks",
        "app.tasks.recording_tasks",
    ),
)

celery_app.autodiscover_tasks(["app.tasks"])
