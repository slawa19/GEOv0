import re
from typing import Any, ClassVar, FrozenSet
from urllib.parse import urlsplit

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def canonicalize_http_origin(value: str) -> str | None:
    """Return browser-style serialization for one exact HTTP(S) origin."""
    origin = value.strip()
    if not origin or any(character.isspace() for character in origin):
        return None
    try:
        parsed = urlsplit(origin)
        port = parsed.port
    except ValueError:
        return None
    scheme = parsed.scheme.lower()
    hostname = parsed.hostname
    if (
        scheme not in {"http", "https"}
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        return None

    if ":" in hostname:
        serialized_host = f"[{hostname.lower()}]"
    else:
        try:
            serialized_host = hostname.encode("idna").decode("ascii").lower()
        except UnicodeError:
            return None
    default_port = 80 if scheme == "http" else 443
    serialized_port = "" if port is None or port == default_port else f":{port}"
    return f"{scheme}://{serialized_host}{serialized_port}"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
        populate_by_name=True,
    )

    # Database
    DEFAULT_SQLITE_DATABASE_URL: ClassVar[str] = (
        "sqlite+aiosqlite:///./.local-run/geov0.db"
    )
    DATABASE_URL: str = DEFAULT_SQLITE_DATABASE_URL

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
    # ENV is canonical. ENVIRONMENT is a compatibility alias for old deployments.
    # Conflicting keys fail startup. Values normalize to dev|test|staging|prod.
    ENV: str | None = None
    LEGACY_ENVIRONMENT: str | None = Field(
        default=None,
        validation_alias="ENVIRONMENT",
        exclude=True,
        repr=False,
    )

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
    _ENV_ALIASES: ClassVar[dict[str, str]] = {
        "dev": "dev",
        "development": "dev",
        "test": "test",
        "testing": "test",
        "stage": "staging",
        "staging": "staging",
        "prod": "prod",
        "production": "prod",
    }
    _SAFE_ENVS: ClassVar[FrozenSet[str]] = frozenset({"dev", "test"})
    _MIN_SECRET_LENGTH: ClassVar[int] = 32
    _UNSAFE_SECRET_PLACEHOLDERS: ClassVar[FrozenSet[str]] = frozenset(
        {
            "change-me-in-production",
            "change_me_in_production",
            "change_in_production",
            "change-me",
            "change_me",
            "changeme",
            "replace-me",
            "replace_me",
            "replaceme",
            "todo",
            "tbd",
            "secret",
            "",
        }
    )
    _UNSAFE_SECRET_PATTERN: ClassVar[re.Pattern[str]] = re.compile(
        r"^(?:(?:change|replace)[_\-\s]*me.*|your[_\-\s]+secret.*|todo.*)$",
        re.IGNORECASE,
    )

    @field_validator("ENV", mode="before")
    @classmethod
    def _normalize_environment(cls, value: object) -> str | None:
        if value is None:
            return None
        raw = str(value or "").strip().lower()
        try:
            return cls._ENV_ALIASES[raw]
        except KeyError as exc:
            supported = ", ".join(sorted(cls._ENV_ALIASES))
            raise ValueError(
                f"unsupported environment {value!r}; use one of: {supported}"
            ) from exc

    @field_validator("LEGACY_ENVIRONMENT", mode="before")
    @classmethod
    def _normalize_legacy_environment(cls, value: object) -> str | None:
        if value is None:
            return None
        return cls._normalize_environment(value)

    def model_post_init(self, __context: Any) -> None:
        # Runs on every Settings() instantiation (including module-level `settings = Settings()`).
        self._resolve_environment_alias()
        self._guardrail_default_secrets()
        self._guardrail_simulator_session_secret()
        self._guardrail_csrf_allowlist()

    def _resolve_environment_alias(self) -> None:
        canonical_env = self.ENV
        legacy_env = self.LEGACY_ENVIRONMENT
        if canonical_env is None and legacy_env is None:
            raise RuntimeError(
                "ENV must be explicitly set to dev, test, staging, or prod "
                "(legacy ENVIRONMENT is also accepted)"
            )
        if canonical_env is not None and legacy_env is not None and canonical_env != legacy_env:
            raise RuntimeError(
                "ENV and legacy ENVIRONMENT select different environments; "
                "remove ENVIRONMENT and keep the canonical ENV value"
            )
        self.ENV = canonical_env or legacy_env

    def _guardrail_simulator_session_secret(self) -> None:
        """Fail-fast when SIMULATOR_SESSION_SECRET is empty or insecure in non-dev (§4).

        FIX-CR5: also check for empty string (not just "change-me" substring).
        """
        env = (self.ENV or "").strip().lower()
        if env in self._SAFE_ENVS:
            return
        sim_secret = self.SIMULATOR_SESSION_SECRET
        if self._is_unsafe_secret(sim_secret):
            raise RuntimeError(
                "SIMULATOR_SESSION_SECRET must be set to a strong secret in non-dev environment. "
                f"Got ENV={self.ENV!r}. "
                "Set a secure secret via SIMULATOR_SESSION_SECRET env var."
            )

    def _guardrail_csrf_allowlist(self) -> None:
        """Fail-fast if SIMULATOR_CSRF_ORIGIN_ALLOWLIST is empty in non-dev environment.

        Empty allowlist = allow all origins for anon cookie endpoints.
        This is intentional in dev/test but unsafe in production.
        """
        env = (self.ENV or "").strip().lower()
        allowlist_raw = (self.SIMULATOR_CSRF_ORIGIN_ALLOWLIST or "").strip()
        if not allowlist_raw and env in self._SAFE_ENVS:
            return
        normalized_origins: list[str] = []
        for raw_origin in allowlist_raw.split(","):
            normalized_origin = canonicalize_http_origin(raw_origin)
            if normalized_origin is None:
                normalized_origins = []
                break
            if normalized_origin not in normalized_origins:
                normalized_origins.append(normalized_origin)
        if not normalized_origins:
            raise RuntimeError(
                "SIMULATOR_CSRF_ORIGIN_ALLOWLIST must be set in non-dev environment. "
                f"Got ENV={self.ENV!r}. "
                "Set explicit origins via SIMULATOR_CSRF_ORIGIN_ALLOWLIST env var."
            )
        self.SIMULATOR_CSRF_ORIGIN_ALLOWLIST = ",".join(normalized_origins)

    def _guardrail_default_secrets(self) -> None:
        env = (self.ENV or "").strip().lower()
        if env in self._SAFE_ENVS:
            return

        jwt_secret = (self.JWT_SECRET or "").strip()
        admin_token = (self.ADMIN_TOKEN or "").strip()

        problems: list[str] = []
        if self._is_unsafe_secret(jwt_secret, default_value=self.DEFAULT_JWT_SECRET):
            problems.append("JWT_SECRET")
        if self._is_unsafe_secret(admin_token, default_value=self.DEFAULT_ADMIN_TOKEN):
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

    @classmethod
    def _is_unsafe_secret(
        cls,
        value: str | None,
        *,
        default_value: str | None = None,
    ) -> bool:
        normalized = (value or "").strip().lower()
        if default_value is not None and normalized == default_value.strip().lower():
            return True
        if len(normalized) < cls._MIN_SECRET_LENGTH:
            return True
        for pattern_length in range(1, min(4, len(normalized) // 2) + 1):
            if len(normalized) % pattern_length == 0:
                pattern = normalized[:pattern_length]
                if pattern * (len(normalized) // pattern_length) == normalized:
                    return True
        if normalized in cls._UNSAFE_SECRET_PLACEHOLDERS:
            return True
        return cls._UNSAFE_SECRET_PATTERN.fullmatch(normalized) is not None


settings = Settings()


def get_settings() -> Settings:
    """Return the global settings instance.

    Provided as a callable for FastAPI Depends() and test mocking convenience.
    """
    return settings
