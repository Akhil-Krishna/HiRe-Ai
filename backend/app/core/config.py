from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    APP_NAME: str = "HirE.AI"
    APP_ENV: str = "development"
    DEBUG: bool = True

    SECRET_KEY: str = "supersecretkey"  # Change
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 480  # 8 hours

    DATABASE_URL: str = "sqlite+aiosqlite:///./hireai.db"

    EMAIL_PROVIDER: str = "log"
    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    EMAIL_FROM: str = ""

    # LLM: groq | ollama | openai | mock
    LLM_PROVIDER: str = "groq"
    GROQ_API_KEY: str = ""
    GROQ_MODEL: str = "llama-3.3-70b-versatile"
    GROQ_TIMEOUT: float = 30.0
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "llama3.2"
    OLLAMA_TIMEOUT: float = 60.0
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4o-mini"
    OPENAI_TIMEOUT: float = 30.0

    # Vision: deepface | mock
    VISION_PROVIDER: str = "deepface"

    FRONTEND_URL: str = "http://localhost:8000"
    RECORDINGS_DIR: str = "recordings"
    UPLOADS_DIR: str = "uploads"

    class Config:
        env_file = ".env"
        extra = "ignore"


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
