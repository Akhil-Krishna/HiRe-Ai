import asyncio
from fastapi import APIRouter

from app.core.celery_app import celery_app
from app.core.config import settings
from app.core.redis_client import ping as redis_ping, queue_length
from app.api.v1.endpoints.webrtc import room_manager

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
            worker_count_seen = len(ping_result or [])
            if worker_reachable:
                reg = await asyncio.to_thread(celery_app.control.inspect().registered)
                if reg:
                    seen = set()
                    for tasks in reg.values():
                        seen.update(tasks or [])
                    registered_tasks_ok = required_tasks.issubset(seen)
        except Exception:
            worker_reachable = False
            worker_count_seen = 0
    else:
        worker_count_seen = 0

    q_len = queue_length("celery")
    degraded_reasons = []
    if not settings.CELERY_ENABLED:
        degraded_reasons.append("celery_disabled")
    if not redis_reachable:
        degraded_reasons.append("redis_unreachable")
    if settings.CELERY_ENABLED and not worker_reachable:
        degraded_reasons.append("worker_unreachable")
    if settings.CELERY_ENABLED and worker_reachable and not registered_tasks_ok:
        degraded_reasons.append("task_registration_mismatch")
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
        "realtime_mode": "celery" if settings.CELERY_REALTIME_ENABLED else "local",
        "redis_reachable": redis_reachable,
        "worker_reachable": worker_reachable,
        "queue_length": q_len,
        "registered_tasks_ok": registered_tasks_ok,
        "worker_count_seen": worker_count_seen,
        "room_backend": room_manager.backend_name(),
        "vision_persist_enabled": settings.VISION_PERSIST_ENABLED,
        "vision_persist_sampling_n": max(1, int(settings.VISION_LOG_SAMPLE_EVERY_N)),
        "degraded_reasons": degraded_reasons,
        "degraded": degraded,
    }
