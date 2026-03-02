import contextvars
import uuid
from typing import Optional


_request_id_ctx: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("request_id", default=None)


def set_request_id(request_id: Optional[str]) -> None:
    _request_id_ctx.set(request_id)


def get_request_id() -> Optional[str]:
    return _request_id_ctx.get()


def ensure_request_id(existing: Optional[str] = None) -> str:
    rid = (existing or "").strip()
    if not rid:
        rid = str(uuid.uuid4())
    set_request_id(rid)
    return rid
