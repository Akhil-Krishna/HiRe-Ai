import asyncio
from fastapi import APIRouter

from app.core.celery_app import celery_app
from app.core.config import settings
from app.core.redis_client import ping as redis_ping, queue_length

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/celery")
async def celery_health():
    redis_reachable = redis_ping()

    worker_reachable = False
    registered_tasks_ok = False
    required_tasks = {
        "app.tasks.ai.generate_response",
        "app.tasks.ai.generate_evaluation",
        "app.tasks.vision.analyze_frame",
        "app.tasks.stt.transcribe",
        "app.tasks.email.send_interview_invite",
        "app.tasks.email.send_interviewer_notification",
        "app.tasks.resume.extract_text",
        "app.tasks.recording.process_metadata",
    }
    if settings.CELERY_ENABLED:
        try:
            ping_result = await asyncio.to_thread(celery_app.control.ping, timeout=settings.REDIS_HEALTH_TIMEOUT_SECONDS)
            worker_reachable = bool(ping_result)
            if worker_reachable:
                reg = await asyncio.to_thread(celery_app.control.inspect().registered)
                if reg:
                    seen = set()
                    for tasks in reg.values():
                        seen.update(tasks or [])
                    registered_tasks_ok = required_tasks.issubset(seen)
        except Exception:
            worker_reachable = False

    q_len = queue_length("celery")
    degraded = (
        (not settings.CELERY_ENABLED)
        or (not redis_reachable)
        or (not worker_reachable)
        or (settings.CELERY_ENABLED and worker_reachable and not registered_tasks_ok)
    )
    return {
        "celery_enabled": settings.CELERY_ENABLED,
        "celery_realtime_enabled": settings.CELERY_REALTIME_ENABLED,
        "celery_background_enabled": settings.CELERY_BACKGROUND_ENABLED,
        "redis_reachable": redis_reachable,
        "worker_reachable": worker_reachable,
        "queue_length": q_len,
        "registered_tasks_ok": registered_tasks_ok,
        "degraded": degraded,
    }
