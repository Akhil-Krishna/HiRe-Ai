import logging
import time

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.request_context import ensure_request_id

logger = logging.getLogger(__name__)


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = ensure_request_id(request.headers.get("X-Request-ID"))
        request.state.request_id = request_id
        started = time.perf_counter()
        logger.info(
            "request started",
            extra={
                "event": "request_start",
                "component": "api",
                "endpoint": request.url.path,
                "request_id": request_id,
            },
        )
        response = None
        try:
            response = await call_next(request)
            return response
        except Exception as exc:
            logger.exception(
                "request failed",
                extra={
                    "event": "request_error",
                    "component": "api",
                    "endpoint": request.url.path,
                    "request_id": request_id,
                    "error": str(exc),
                },
            )
            raise
        finally:
            elapsed = round((time.perf_counter() - started) * 1000.0, 1)
            logger.info(
                "request completed",
                extra={
                    "event": "request_end",
                    "component": "api",
                    "endpoint": request.url.path,
                    "request_id": request_id,
                    "latency_ms": elapsed,
                },
            )
            if response is not None:
                response.headers["X-Request-ID"] = request_id
