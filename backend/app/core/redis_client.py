import logging
from typing import Optional

from redis import Redis

from app.core.config import settings

logger = logging.getLogger(__name__)

_redis_client: Optional[Redis] = None


def _redis_url() -> str:
    return settings.CELERY_BROKER_URL or settings.REDIS_URL or "redis://localhost:6379/0"


def get_redis_client() -> Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = Redis.from_url(
            _redis_url(),
            socket_connect_timeout=settings.REDIS_SOCKET_TIMEOUT_SECONDS,
            socket_timeout=settings.REDIS_SOCKET_TIMEOUT_SECONDS,
            retry_on_timeout=False,
            decode_responses=True,
        )
    return _redis_client


def ping() -> bool:
    try:
        return bool(get_redis_client().ping())
    except Exception as exc:
        logger.debug("Redis ping failed: %s", exc)
        return False


def queue_length(queue_name: str = "celery") -> Optional[int]:
    try:
        return int(get_redis_client().llen(queue_name))
    except Exception as exc:
        logger.debug("Redis queue length read failed: %s", exc)
        return None

