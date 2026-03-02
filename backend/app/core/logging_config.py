import json
import logging
import sys
from datetime import datetime, timezone

from app.core.request_context import get_request_id


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in ("event", "component", "task_name", "endpoint", "request_id", "fallback", "latency_ms", "error"):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        payload.setdefault("request_id", get_request_id())
        return json.dumps(payload, ensure_ascii=True)


def setup_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonLogFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
