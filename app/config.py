import logging
from typing import Any, ClassVar, FrozenSet

from pydantic_settings import BaseSettings, SettingsConfigDict

_logger = logging.getLogger(__name__)


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
    # NOTE: This default is intentionally insecure and must never be used outside dev/test.
    DEFAULT_JWT_SECRET: ClassVar[str] = "dev-secret-change-me-please-32chars!!"
    JWT_SECRET: str = DEFAULT_JWT_SECRET
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
    # Base backoff delay (exponential with jitter). Values are intentionally small:
    # SERIALIZABLE conflicts should be rare, and we want quick recovery.
    COMMIT_RETRY_BASE_DELAY_MS: int = 50
    # Cap the exponential backoff to avoid unbounded latency.
    COMMIT_RETRY_MAX_DELAY_MS: int = 500

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

    # --- Simulator session (anonymous visitors) ---
    SIMULATOR_SESSION_SECRET: str = "change-me-in-production"
    SIMULATOR_SESSION_TTL_SEC: int = 604800  # 7 days
    SIMULATOR_SESSION_CLOCK_SKEW_SEC: int = 300  # 5 min tolerance
    SIMULATOR_MAX_ACTIVE_RUNS_PER_OWNER: int = 1
    SIMULATOR_CSRF_ORIGIN_ALLOWLIST: str = ""  # comma-separated origins, empty = allow all in dev

    # Admin API (minimal MVP)
    # NOTE: Shared secret to unblock Admin UI integration in MVP.
    # Replace with proper role-based auth in production.
    # NOTE: This default is intentionally insecure and must never be used outside dev/test.
    DEFAULT_ADMIN_TOKEN: ClassVar[str] = "dev-admin-token-change-me"
    ADMIN_TOKEN: str = DEFAULT_ADMIN_TOKEN

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

    # --- Guardrails ---
    # Fail-fast on obviously insecure secrets outside dev/test.
    _SAFE_ENVS: ClassVar[FrozenSet[str]] = frozenset({"dev", "development", "test", "testing"})
    _UNSAFE_PLACEHOLDERS: ClassVar[FrozenSet[str]] = frozenset(
        {
            "change-me-in-production",
            "change-me",
            "changeme",
            "",
        }
    )

    def model_post_init(self, __context: Any) -> None:
        # Runs on every Settings() instantiation (including module-level `settings = Settings()`).
        self._guardrail_default_secrets()
        self._guardrail_simulator_session_secret()

    def _guardrail_simulator_session_secret(self) -> None:
        """Fail-fast when SIMULATOR_SESSION_SECRET uses the insecure default in non-dev (§4)."""
        env = (self.ENV or "").strip().lower()
        if env in self._SAFE_ENVS:
            return
        sim_secret = (self.SIMULATOR_SESSION_SECRET or "").strip()
        if "change-me" in sim_secret.lower():
            raise RuntimeError(
                "SIMULATOR_SESSION_SECRET must be changed in non-dev environment. "
                f"Got ENV={self.ENV!r}. "
                "Set a secure secret via SIMULATOR_SESSION_SECRET env var."
            )

    def _guardrail_default_secrets(self) -> None:
        env = (self.ENV or "").strip().lower()
        if env in self._SAFE_ENVS:
            return

        jwt_secret = (self.JWT_SECRET or "").strip()
        admin_token = (self.ADMIN_TOKEN or "").strip()

        def _is_unsafe(value: str, *, default_value: str) -> bool:
            v = (value or "").strip()
            vl = v.lower()
            if v == default_value:
                return True
            if vl in self._UNSAFE_PLACEHOLDERS:
                return True
            if "change-me" in vl:
                return True
            return False

        problems: list[str] = []
        if _is_unsafe(jwt_secret, default_value=self.DEFAULT_JWT_SECRET):
            problems.append("JWT_SECRET")
        if _is_unsafe(admin_token, default_value=self.DEFAULT_ADMIN_TOKEN):
            problems.append("ADMIN_TOKEN")

        if problems:
            fields = ", ".join(problems)
            raise RuntimeError(
                "Refusing to start with insecure default/placeholder secrets outside dev/test: "
                f"{fields}. "
                f"Got ENV={self.ENV!r}. "
                "Set secure values via environment variables (JWT_SECRET / ADMIN_TOKEN), "
                "or run with ENV=dev/test."
            )


settings = Settings()


def get_settings() -> Settings:
    """Return the global settings instance.

    Provided as a callable for FastAPI Depends() and test mocking convenience.
    """
    return settings
