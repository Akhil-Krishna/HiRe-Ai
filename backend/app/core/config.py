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

    FRONTEND_URL: str = "http://localhost:8000"
    RECORDINGS_DIR: str = "recordings"
    UPLOADS_DIR: str = "uploads"

    # Redis — optional, required for multi-worker WebRTC signaling.
    # Leave empty (default) to use in-memory signaling (single-worker only).
    # Set to redis://localhost:6379/0 for multi-worker deployments.
    # Install: pip install redis[asyncio]
    REDIS_URL: Optional[str] = None

    # RTC / Meeting room
    RTC_ROOM_CAPACITY: int = 12
    RTC_SIGNAL_TIMEOUT_SECONDS: int = 45
    RTC_JOIN_RATE_LIMIT: int = 6
    RTC_JOIN_WINDOW_SECONDS: int = 30

    # Whisper STT
    STT_MODEL: str = "base"    # tiny | base | small | medium
    STT_DEVICE: str = "cpu"    # cpu | cuda
    STT_COMPUTE: str = "int8"  # int8 | float16 | float32

    class Config:
        env_file = ".env"
        extra = "ignore"


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
