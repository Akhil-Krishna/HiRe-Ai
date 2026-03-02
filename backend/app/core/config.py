from pydantic import field_validator
from pydantic_settings import BaseSettings
from functools import lru_cache
from typing import Optional


class Settings(BaseSettings):
    APP_NAME: str = "HirE.AI"
    APP_ENV: str = "development"
    DEBUG: bool = True

    SECRET_KEY: str = "dev-secret-key-CHANGE-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 480  # 8 hours

    DATABASE_URL: str = "sqlite+aiosqlite:///./hireai.db"

    EMAIL_PROVIDER: str = "log"
    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    EMAIL_FROM: str = ""
    SENDGRID_API_KEY: str = ""
    SENDGRID_FROM_EMAIL: str = ""

    # LLM: groq | ollama | openai | mock
    LLM_PROVIDER: str = "groq"
    GROQ_API_KEY: str = ""
    GROQ_MODEL: str = "llama-3.3-70b-versatile"
    GROQ_TIMEOUT: float = 30.0
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "llama3.2:1b"
    OLLAMA_TIMEOUT: float = 60.0
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4o-mini"
    OPENAI_TIMEOUT: float = 30.0

    # Vision: deepface | mock
    VISION_PROVIDER: str = "deepface"
    VISION_PERSIST_ENABLED: bool = True
    VISION_LOG_SAMPLE_EVERY_N: int = 3

    FRONTEND_URL: str = "http://localhost:8000"
    RECORDINGS_DIR: str = "recordings"
    UPLOADS_DIR: str = "uploads"

    # Redis — optional, required for multi-worker WebRTC signaling.
    # Leave empty (default) to use in-memory signaling (single-worker only).
    # Set to redis://localhost:6379/0 for multi-worker deployments.
    # Install: pip install redis[asyncio]
    REDIS_URL: Optional[str] = None
    REDIS_SOCKET_TIMEOUT_SECONDS: float = 1.5
    REDIS_HEALTH_TIMEOUT_SECONDS: float = 1.0
    REDIS_STREAM_DB: int = 0

    # Celery / background execution
    CELERY_ENABLED: bool = True
    CELERY_BROKER_URL: Optional[str] = None
    CELERY_RESULT_BACKEND: Optional[str] = None
    CELERY_WAIT_TIMEOUT_SECONDS: float = 3.0
    CELERY_ENQUEUE_TIMEOUT_SECONDS: float = 0.75
    CELERY_SOFT_TIME_LIMIT_SECONDS: int = 25
    CELERY_TIME_LIMIT_SECONDS: int = 35
    CELERY_TASK_MAX_RETRIES: int = 3
    CELERY_TASK_RETRY_BACKOFF: bool = True
    CELERY_TASK_RETRY_JITTER: bool = True

    # RTC / Meeting room
    RTC_ROOM_CAPACITY: int = 12
    RTC_SIGNAL_TIMEOUT_SECONDS: int = 45
    RTC_JOIN_RATE_LIMIT: int = 6
    RTC_JOIN_WINDOW_SECONDS: int = 30

    # Whisper STT
    STT_MODEL: str = "base"    # tiny | base | small | medium
    STT_DEVICE: str = "cpu"    # cpu | cuda
    STT_COMPUTE: str = "int8"  # int8 | float16 | float32
    STT_BEAM_SIZE: int = 2
    STT_VAD_FILTER: bool = True
    STT_VAD_MIN_SILENCE_MS: int = 200
    ENABLE_VISION_WARMUP: bool = False

    class Config:
        env_file = ".env"
        extra = "ignore"

    CELERY_REALTIME_ENABLED: bool = False
    CELERY_BACKGROUND_ENABLED: bool = True
    CELERY_FALLBACK_COOLDOWN_SECONDS: float = 10.0
    STT_LOCAL_FASTPATH_ENABLED: bool = True
    AI_LOCAL_FASTPATH_ENABLED: bool = True
    ENABLE_GLOBAL_ERROR_ENVELOPE: bool = True
    ENABLE_REQUEST_ID_MIDDLEWARE: bool = True
    ENABLE_IDEMPOTENCY: bool = True
    ROOM_BACKEND: str = "auto"  # auto | memory | redis
    RTC_HEARTBEAT_SECONDS: int = 10
    RTC_STALE_PARTICIPANT_TTL_SECONDS: int = 45

    @field_validator(
        "DEBUG",
        "CELERY_ENABLED",
        "CELERY_REALTIME_ENABLED",
        "CELERY_BACKGROUND_ENABLED",
        "ENABLE_GLOBAL_ERROR_ENVELOPE",
        "ENABLE_REQUEST_ID_MIDDLEWARE",
        "ENABLE_IDEMPOTENCY",
        "ENABLE_VISION_WARMUP",
        "VISION_PERSIST_ENABLED",
        "STT_VAD_FILTER",
        "STT_LOCAL_FASTPATH_ENABLED",
        "AI_LOCAL_FASTPATH_ENABLED",
        mode="before",
    )
    @classmethod
    def _normalize_bool_strings(cls, value):
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            v = value.strip().lower()
            if v in {"1", "true", "yes", "on", "debug"}:
                return True
            if v in {"0", "false", "no", "off", "release", "prod", "production"}:
                return False
        return value

    @field_validator("ROOM_BACKEND", mode="before")
    @classmethod
    def _normalize_room_backend(cls, value):
        if value is None:
            return "auto"
        v = str(value).strip().lower()
        if v in {"auto", "memory", "redis"}:
            return v
        return "auto"


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
