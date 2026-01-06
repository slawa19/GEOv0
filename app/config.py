from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", case_sensitive=True, extra="ignore"
    )

    # Database
    DATABASE_URL: str = "sqlite+aiosqlite:///./geov0.db"

    # Database pool (applies to client/server DBs like Postgres; SQLite uses NullPool)
    DB_POOL_PRE_PING: bool = True
    DB_POOL_SIZE: int = 5
    DB_MAX_OVERFLOW: int = 10
    DB_POOL_TIMEOUT_SECONDS: int = 30
    DB_POOL_RECYCLE_SECONDS: int = 1800

    # Database transaction isolation
    # Applied for Postgres connections only.
    DB_POSTGRES_ISOLATION_LEVEL: str = "SERIALIZABLE"

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"
    REDIS_ENABLED: bool = False

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

    # Recovery (startup + periodic cleanup)
    RECOVERY_ENABLED: bool = True
    RECOVERY_INTERVAL_SECONDS: int = 60
    PAYMENT_TX_STUCK_TIMEOUT_SECONDS: int = 120

    # Payment Routing (MVP limits)
    ROUTING_MAX_HOPS: int = 6
    ROUTING_MAX_PATHS: int = 3
    # Spec-aligned timeouts
    ROUTING_PATH_FINDING_TIMEOUT_MS: int = 500
    ROUTING_GRAPH_CACHE_TTL_SECONDS: int = 0

    # Payment execution timeouts (spec section 6.9)
    PREPARE_TIMEOUT_SECONDS: int = 3
    COMMIT_TIMEOUT_SECONDS: int = 5
    PAYMENT_TOTAL_TIMEOUT_SECONDS: int = 10

    # Commit retry configuration (used where applicable)
    COMMIT_RETRY_ATTEMPTS: int = 3
    COMMIT_RETRY_DELAY_SECONDS: float = 0.5

    # Max-flow diagnostics (MVP limits)
    MAX_FLOW_MAX_HOPS: int = 7

    # Balance
    BALANCE_SUMMARY_CACHE_TTL_SECONDS: int = 0

    # Rate limiting (in-memory, best-effort)
    RATE_LIMIT_ENABLED: bool = True
    RATE_LIMIT_WINDOW_SECONDS: int = 60
    RATE_LIMIT_REQUESTS_PER_WINDOW: int = 120

    # Observability
    METRICS_ENABLED: bool = True

    # Integrity checkpoints
    INTEGRITY_CHECKPOINT_ENABLED: bool = True
    INTEGRITY_CHECKPOINT_INTERVAL_SECONDS: int = 300
    # Optional: used to serialize integrity checkpoint runs across replicas (Redis lock TTL).
    # 0 = auto (defaults to max(30, INTEGRITY_CHECKPOINT_INTERVAL_SECONDS)).
    INTEGRITY_CHECKPOINT_LOCK_TTL_SECONDS: int = 0


settings = Settings()