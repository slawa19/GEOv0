from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", case_sensitive=True, extra="ignore"
    )

    # Database
    DATABASE_URL: str = "sqlite+aiosqlite:///./geov0.db"

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # JWT
    JWT_SECRET: str = "dev-secret-change-me-please-32chars!!"
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # Application
    LOG_LEVEL: str = "INFO"
    DEBUG: bool = False

    # Challenge
    AUTH_CHALLENGE_EXPIRE_SECONDS: int = 300

    # Payment Engine
    PREPARE_LOCK_TTL_SECONDS: int = 30


settings = Settings()