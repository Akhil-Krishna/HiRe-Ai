import logging
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.request_context import get_request_id

logger = logging.getLogger(__name__)


def _error_body(code: str, message: str, detail: Any = None) -> dict:
    body = {
        "error": {
            "code": code,
            "message": message,
            "request_id": get_request_id(),
            "detail": detail,
        }
    }
    return body


def register_exception_handlers(app: FastAPI) -> None:
    if not settings.ENABLE_GLOBAL_ERROR_ENVELOPE:
        return

    @app.exception_handler(HTTPException)
    async def _http_exception_handler(request: Request, exc: HTTPException):  # noqa: ANN001
        logger.warning(
            "http exception",
            extra={
                "event": "http_exception",
                "component": "api",
                "endpoint": request.url.path,
                "request_id": get_request_id(),
                "error": str(exc.detail),
            },
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body("http_error", str(exc.detail), detail=exc.detail),
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_exception_handler(request: Request, exc: RequestValidationError):  # noqa: ANN001
        logger.warning(
            "validation exception",
            extra={
                "event": "validation_error",
                "component": "api",
                "endpoint": request.url.path,
                "request_id": get_request_id(),
                "error": str(exc.errors()),
            },
        )
        return JSONResponse(
            status_code=422,
            content=_error_body("validation_error", "Request validation failed", detail=exc.errors()),
        )

    @app.exception_handler(Exception)
    async def _unhandled_exception_handler(request: Request, exc: Exception):  # noqa: ANN001
        logger.exception(
            "unhandled exception",
            extra={
                "event": "unhandled_exception",
                "component": "api",
                "endpoint": request.url.path,
                "request_id": get_request_id(),
                "error": str(exc),
            },
        )
        return JSONResponse(
            status_code=500,
            content=_error_body("internal_error", "Internal server error"),
        )
