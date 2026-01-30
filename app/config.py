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

    # Environment
    # Used for guardrails (e.g. dev-only auth helpers). Suggested values: dev|staging|prod.
    ENV: str = "dev"

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

    # Simulator (DB-first UI state)
    # When enabled, simulator persists runs/metrics/bottlenecks/artifacts indexes to the main DB.
    SIMULATOR_DB_ENABLED: bool = True

    # Real-mode SSE viz patches: refresh cached quantiles every N ticks.
    # Higher value = fewer DB scans, less accurate viz_size/viz_width_key.
    SIMULATOR_VIZ_QUANTILE_REFRESH_TICKS: int = 10

    # Admin API (minimal MVP)
    # NOTE: Shared secret to unblock Admin UI integration in MVP.
    # Replace with proper role-based auth in production.
    ADMIN_TOKEN: str = "dev-admin-token-change-me"

    # Dev-only admin auth convenience (guarded)
    # When enabled (ENV=dev AND ADMIN_DEV_MODE=True), allow calling admin endpoints
    # without X-Admin-Token from trusted client IPs.
    ADMIN_DEV_MODE: bool = False
    # Comma-separated allowlist of client IPs.
    ADMIN_DEV_ALLOWLIST: str = "127.0.0.1,::1"

    # Graph extras include caps (for /admin/graph/*?include=...)
    ADMIN_GRAPH_INCLUDE_MAX_INCIDENTS: int = 50
    ADMIN_GRAPH_INCLUDE_MAX_AUDIT_EVENTS: int = 50
    ADMIN_GRAPH_INCLUDE_MAX_TRANSACTIONS: int = 50

    # Feature flags (runtime mutable via /admin/feature-flags)
    FEATURE_FLAGS_MULTIPATH_ENABLED: bool = True
    FEATURE_FLAGS_FULL_MULTIPATH_ENABLED: bool = False
    CLEARING_ENABLED: bool = True

    # Integrity checkpoints
    INTEGRITY_CHECKPOINT_ENABLED: bool = True
    INTEGRITY_CHECKPOINT_INTERVAL_SECONDS: int = 300
    # Optional: used to serialize integrity checkpoint runs across replicas (Redis lock TTL).
    # 0 = auto (defaults to max(30, INTEGRITY_CHECKPOINT_INTERVAL_SECONDS)).
    INTEGRITY_CHECKPOINT_LOCK_TTL_SECONDS: int = 0


settings = Settings()