import asyncio
import inspect
import logging
import time
from typing import Any, Awaitable, Callable, Optional

from celery.exceptions import TimeoutError as CeleryTimeoutError

from app.core.config import settings

logger = logging.getLogger(__name__)
_celery_skip_until: float = 0.0


async def _execute_fallback(fallback_callable: Callable[[], Any] | Callable[[], Awaitable[Any]]) -> Any:
    if inspect.iscoroutinefunction(fallback_callable):
        return await fallback_callable()
    return await asyncio.to_thread(fallback_callable)


async def run_task_with_fallback(
    task_sig: Any,
    payload: dict,
    fallback_callable: Callable[[], Any] | Callable[[], Awaitable[Any]],
    wait_timeout: Optional[float] = None,
    endpoint_name: str = "",
    realtime: bool = False,
) -> Any:
    global _celery_skip_until
    if not settings.CELERY_ENABLED:
        return await _execute_fallback(fallback_callable)
    if realtime and not settings.CELERY_REALTIME_ENABLED:
        return await _execute_fallback(fallback_callable)
    if not realtime and not settings.CELERY_BACKGROUND_ENABLED:
        return await _execute_fallback(fallback_callable)
    now = time.monotonic()
    if now < _celery_skip_until:
        return await _execute_fallback(fallback_callable)

    timeout = wait_timeout if wait_timeout is not None else settings.CELERY_WAIT_TIMEOUT_SECONDS
    enqueue_timeout = max(0.1, float(settings.CELERY_ENQUEUE_TIMEOUT_SECONDS))
    started = time.perf_counter()
    try:
        async_result = await asyncio.wait_for(
            asyncio.to_thread(task_sig.apply_async, kwargs={"payload": payload}),
            timeout=enqueue_timeout,
        )
        logger.info(
            "Celery task enqueued",
            extra={
                "event": "task_enqueue",
                "component": "task_runner",
                "task_name": getattr(task_sig, "name", "unknown"),
                "endpoint": endpoint_name,
                "fallback": False,
                "error": str(getattr(async_result, "id", "")),
            },
        )
        output = await asyncio.wait_for(
            asyncio.to_thread(async_result.get, timeout=timeout),
            timeout=max(timeout + 0.25, 0.5),
        )
        elapsed = round((time.perf_counter() - started) * 1000.0, 1)
        logger.info(
            "Celery task completed",
            extra={"event": "task_complete", "component": "task_runner", "task_name": getattr(task_sig, "name", "unknown"), "endpoint": endpoint_name, "fallback": False, "latency_ms": elapsed},
        )
        _celery_skip_until = 0.0
        return output
    except (CeleryTimeoutError, TimeoutError, asyncio.TimeoutError) as exc:
        _celery_skip_until = time.monotonic() + float(settings.CELERY_FALLBACK_COOLDOWN_SECONDS)
        logger.warning(
            "Celery task timed out; using fallback",
            extra={"event": "task_timeout_fallback", "component": "task_runner", "task_name": getattr(task_sig, "name", "unknown"), "endpoint": endpoint_name, "fallback": True, "error": str(exc)},
        )
        return await _execute_fallback(fallback_callable)
    except Exception as exc:
        _celery_skip_until = time.monotonic() + float(settings.CELERY_FALLBACK_COOLDOWN_SECONDS)
        logger.warning(
            "Celery task failed; using fallback",
            extra={"event": "task_error_fallback", "component": "task_runner", "task_name": getattr(task_sig, "name", "unknown"), "endpoint": endpoint_name, "fallback": True, "error": str(exc)},
        )
        return await _execute_fallback(fallback_callable)


async def enqueue_task_with_fallback(
    task_sig: Any,
    payload: dict,
    fallback_callable: Callable[[], Any] | Callable[[], Awaitable[Any]],
    endpoint_name: str = "",
) -> Any:
    global _celery_skip_until
    if not settings.CELERY_ENABLED:
        return await _execute_fallback(fallback_callable)
    if not settings.CELERY_BACKGROUND_ENABLED:
        return await _execute_fallback(fallback_callable)
    now = time.monotonic()
    if now < _celery_skip_until:
        return await _execute_fallback(fallback_callable)

    enqueue_timeout = max(0.1, float(settings.CELERY_ENQUEUE_TIMEOUT_SECONDS))
    try:
        await asyncio.wait_for(
            asyncio.to_thread(task_sig.apply_async, kwargs={"payload": payload}),
            timeout=enqueue_timeout,
        )
        logger.info(
            "Celery task enqueued",
            extra={"event": "task_enqueue", "component": "task_runner", "task_name": getattr(task_sig, "name", "unknown"), "endpoint": endpoint_name, "fallback": False},
        )
        _celery_skip_until = 0.0
        return None
    except Exception as exc:
        _celery_skip_until = time.monotonic() + float(settings.CELERY_FALLBACK_COOLDOWN_SECONDS)
        logger.warning(
            "Celery enqueue failed; using fallback",
            extra={"event": "task_enqueue_fallback", "component": "task_runner", "task_name": getattr(task_sig, "name", "unknown"), "endpoint": endpoint_name, "fallback": True, "error": str(exc)},
        )
        return await _execute_fallback(fallback_callable)
