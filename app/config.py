import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, ClassVar, FrozenSet
from urllib.parse import urlsplit

from pydantic import AliasChoices, Field, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError


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


#: The only driver the application runs on (programme 017: PostgreSQL is the only engine).
REQUIRED_DATABASE_DRIVER = "postgresql+asyncpg"

_DATABASE_URL_HOW_TO = (
    "Start PostgreSQL - `docker compose up -d db` publishes it on 127.0.0.1:5432, or without Docker "
    "see docs/ru/backend/postgres-local-portable.md - and then either run scripts/run_local.ps1 "
    "(it sets DATABASE_URL to its own database, geov0_dev_<DbSlug> on 127.0.0.1) or set "
    "DATABASE_URL=postgresql+asyncpg://<user>:<password>@127.0.0.1:5432/<database> yourself. "
    "A database file from before programme 017, such as .local-run/geov0.db, is no longer read; "
    "its data is not migrated."
)


def _require_postgresql_database_url(value: str) -> None:
    """Refuse, at settings construction, any `DATABASE_URL` that is not `postgresql+asyncpg`.

    The messages name the driver and never the URL: it carries the password.
    """
    if not value or not value.strip():
        raise RuntimeError(
            "DATABASE_URL is not set. The application runs only on PostgreSQL "
            f"({REQUIRED_DATABASE_DRIVER}) and has no default database. {_DATABASE_URL_HOW_TO}"
        )
    try:
        driver = make_url(value.strip()).drivername
    except (ArgumentError, ValueError):
        # `from None`: SQLAlchemy's message quotes the whole string, password included.
        raise RuntimeError(
            f"DATABASE_URL is not a valid database URL. {_DATABASE_URL_HOW_TO}"
        ) from None
    if driver != REQUIRED_DATABASE_DRIVER:
        raise RuntimeError(
            f"DATABASE_URL uses {driver!r}; the application runs only on PostgreSQL through "
            f"asyncpg, so the URL must start with {REQUIRED_DATABASE_DRIVER}://. "
            f"{_DATABASE_URL_HOW_TO}"
        )


#: The only transaction isolation the application runs PostgreSQL at: READ COMMITTED since 027 stage 2
#: (`T2704`, 2026-10-03); SERIALIZABLE from 2026-09-25 (programme 019 P1-A) until then.
REQUIRED_POSTGRES_ISOLATION_LEVEL = "READ COMMITTED"


def _require_read_committed_isolation(value: str) -> str:
    """Refuse, at settings construction, any `DB_POSTGRES_ISOLATION_LEVEL` other than READ COMMITTED.

    Returns the canonical spelling. The money writers lock the lines of every pair they check `FOR UPDATE` and
    read after the lock (027 stage 2, `app/core/money_boundary.py`); that holds only if a read after a lock wait
    sees what the holder committed. REPEATABLE READ and SERIALIZABLE take their snapshot before the wait - a
    payment reading a fresh pair's opposite debt as absent writes the second direction - and SSI does not cover
    a READ COMMITTED partner, so a mixed load is not serializable either.
    """
    normalised = " ".join(str(value or "").split()).upper()
    if normalised != REQUIRED_POSTGRES_ISOLATION_LEVEL:
        raise RuntimeError(
            f"DB_POSTGRES_ISOLATION_LEVEL is {value!r}; the application runs PostgreSQL at "
            f"{REQUIRED_POSTGRES_ISOLATION_LEVEL} only. The money writers read after their row locks; a "
            "snapshot taken before the lock wait (REPEATABLE READ, SERIALIZABLE) reads a debt the lock "
            "holder has since changed. Unset the setting or set it to READ COMMITTED."
        )
    return REQUIRED_POSTGRES_ISOLATION_LEVEL


#: Integer simulator knobs parsed by the lenient rule of the raw reads they replace (024 `T2414.1`).
_LENIENT_SIMULATOR_INTS = (
    "SIMULATOR_TICK_MS_BASE",
    "SIMULATOR_ACTIONS_PER_TICK_MAX",
    "SIMULATOR_CLEARING_EVERY_N_TICKS",
    "SIMULATOR_MAX_ACTIVE_RUNS",
    "SIMULATOR_MAX_RUN_RECORDS",
    "SIMULATOR_RUN_PERSIST_EVERY_MS",
    "SIMULATOR_RUN_PERSIST_DIRTY_EVERY_MS",
    "SIMULATOR_ARTIFACTS_TTL_HOURS",
    "SIMULATOR_ARTIFACT_SHA_MAX_BYTES",
    "SIMULATOR_EVENT_BUFFER_SIZE",
    "SIMULATOR_EVENT_BUFFER_TTL_SEC",
    "SIMULATOR_SSE_SUB_QUEUE_MAX",
    "SIMULATOR_SSE_MAX_CONNECTIONS",
    "SIMULATOR_SSE_MAX_CONNECTIONS_PER_RUN",
    "SIMULATOR_REAL_MAX_IN_FLIGHT",
    "SIMULATOR_REAL_MAX_CONSEC_TICK_FAILURES",
    "SIMULATOR_REAL_MAX_TIMEOUTS_PER_TICK",
    "SIMULATOR_REAL_MAX_ERRORS_TOTAL",
    "SIMULATOR_CLEARING_MAX_EDGES_FOR_FX",
    "SIMULATOR_REAL_ENABLE_INJECT",
    "SIMULATOR_REAL_MONEY_REPLAY_ATTEMPTS",
    "SIMULATOR_REAL_MAX_CONSEC_MONEY_NO_PROGRESS",
    "SIMULATOR_REAL_DB_METRICS_EVERY_N_TICKS",
    "SIMULATOR_REAL_DB_BOTTLENECKS_EVERY_N_TICKS",
    "SIMULATOR_REAL_LAST_TICK_WRITE_EVERY_MS",
    "SIMULATOR_REAL_ARTIFACTS_SYNC_EVERY_MS",
    "SIMULATOR_REAL_CLEARING_TIME_BUDGET_MS",
    "SIMULATOR_REAL_CLEARING_HARD_TIMEOUT_SEC",
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
        populate_by_name=True,
    )

    # Database. REQUIRED, and PostgreSQL through asyncpg only (programme 017, T1704): there is no
    # default and no second engine. Declared with an empty default on purpose so that the refusal is
    # ours (`_require_postgresql_database_url`, a RuntimeError naming only the driver) rather than
    # pydantic's "Field required" / "Value error", whose `input_value` would echo the URL - password
    # included - or, for a missing field, every other setting (AGENTS.md section 12).
    DATABASE_URL: str = ""

    # Database pool
    DB_POOL_PRE_PING: bool = True
    DB_POOL_SIZE: int = 5
    DB_MAX_OVERFLOW: int = 10
    DB_POOL_TIMEOUT_SECONDS: int = 30
    DB_POOL_RECYCLE_SECONDS: int = 1800

    # Database transaction isolation. READ COMMITTED ONLY (027 stage 2, 2026-10-03; SERIALIZABLE only from
    # 2026-09-25, 019 P1-A): any other value is refused at construction (`_require_read_committed_isolation`).
    # The name is kept; it is no longer a choice.
    DB_POSTGRES_ISOLATION_LEVEL: str = REQUIRED_POSTGRES_ISOLATION_LEVEL

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

    # Payment Routing (MVP limits). 054-D-7: a route has at least one edge and a search looks for at least one path;
    # 0 or a negative value is refused at start and by `PATCH /admin/config`, where it used to be taken silently.
    ROUTING_MAX_HOPS: int = Field(default=6, ge=1)
    ROUTING_MAX_PATHS: int = Field(default=3, ge=1)
    # Spec-aligned timeouts
    ROUTING_PATH_FINDING_TIMEOUT_MS: int = 500
    # 027 stage 1: the route graph is a hint cached per equivalent; money commits no longer drop it, so this bounds
    # how stale a cached capacity can be (the core's final check re-reads the pair). 0 disables the cache.
    ROUTING_GRAPH_CACHE_TTL_SECONDS: int = 2

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
    SIMULATOR_CSRF_ORIGIN_ALLOWLIST: str = (
        ""  # comma-separated origins, empty = allow all in dev
    )

    # --- Simulator runtime knobs (024 `T2414.1`: until then raw `os.getenv` reads beside Settings) ---
    # Names and defaults are the ones the raw reads had. So is the parsing: an empty or unparseable value
    # falls back to the default instead of refusing to start (`_lenient_simulator_value`). What changed: they
    # are read with every other setting - from the environment and from `.env` - once, at process start.
    SIMULATOR_ACTIONS_ENABLE: bool = (
        False  # Interact mode; true only for "1", "true", "TRUE", "yes"
    )
    SIMULATOR_SCENARIO_ALLOWLIST: str = (
        ""  # empty: the canonical demo presets; "*"/"all": every scenario
    )
    SIMULATOR_TICK_MS_BASE: int = 1000
    SIMULATOR_ACTIONS_PER_TICK_MAX: int = 20
    SIMULATOR_CLEARING_EVERY_N_TICKS: int = 25
    SIMULATOR_MAX_ACTIVE_RUNS: int = 1
    SIMULATOR_MAX_RUN_RECORDS: int = 200
    SIMULATOR_RUN_PERSIST_EVERY_MS: int = 5000
    SIMULATOR_RUN_PERSIST_DIRTY_EVERY_MS: int = 1000
    SIMULATOR_ARTIFACTS_TTL_HOURS: int = 0
    # 034 S1c: where the simulator keeps its runtime state - `runs/<run_id>/artifacts` and uploaded `scenarios/`.
    # Empty (the default): the checkout's `.local-run/simulator`, as before. A relative path is taken from the
    # repository root, never from the working directory. The directory MUST BE THE SIMULATOR'S OWN: whatever
    # cleans it - the runtime at start when `SIMULATOR_ARTIFACTS_TTL_HOURS` > 0, `scripts/cleanup_simulator_runs.py`
    # - treats every directory under its `runs/` as a run and removes it. Sessions that share a checkout but must
    # not share this state set it; the test tier does (`tests/conftest.py`). One rule turns the value into a
    # path: `simulator_state_dir()` at the end of this module.
    SIMULATOR_STATE_DIR: str = ""
    SIMULATOR_ARTIFACT_SHA_MAX_BYTES: int = 524288
    SIMULATOR_EVENT_BUFFER_SIZE: int = 2000
    SIMULATOR_EVENT_BUFFER_TTL_SEC: int = 600
    SIMULATOR_SSE_SUB_QUEUE_MAX: int = 500
    SIMULATOR_SSE_MAX_CONNECTIONS: int = 50
    SIMULATOR_SSE_MAX_CONNECTIONS_PER_RUN: int = 10
    # 044 stage 2 (decision 2): a run with no SSE subscriber for this long is stopped by its own heartbeat, reason
    # `idle_no_subscribers` (paused runs too; data and artifacts stay). Counted from the last subscriber leaving or the
    # run (re)entering `running`, whichever is later. 0 disables.
    SIMULATOR_RUN_IDLE_STOP_SEC: int = 600
    # None: the runtime's own default (`app/core/simulator/runtime_utils.py`, `REAL_MAX_*_DEFAULT`).
    SIMULATOR_REAL_MAX_IN_FLIGHT: int | None = None
    SIMULATOR_REAL_MAX_CONSEC_TICK_FAILURES: int | None = None
    SIMULATOR_REAL_MAX_TIMEOUTS_PER_TICK: int | None = None
    SIMULATOR_REAL_MAX_ERRORS_TOTAL: int | None = None
    SIMULATOR_CLEARING_MAX_EDGES_FOR_FX: int = 30
    SIMULATOR_REAL_AMOUNT_CAP: Decimal | None = (
        None  # opt-in; positive; applied in each equivalent's step (028)
    )
    SIMULATOR_REAL_ENABLE_INJECT: int = 0  # enabled at >= 1
    SIMULATOR_REAL_MONEY_REPLAY_ATTEMPTS: int = 3
    SIMULATOR_REAL_MAX_CONSEC_MONEY_NO_PROGRESS: int = 10
    SIMULATOR_REAL_DB_METRICS_EVERY_N_TICKS: int = 5
    SIMULATOR_REAL_DB_BOTTLENECKS_EVERY_N_TICKS: int = 10
    SIMULATOR_REAL_LAST_TICK_WRITE_EVERY_MS: int = 500
    SIMULATOR_REAL_ARTIFACTS_SYNC_EVERY_MS: int = 5000
    SIMULATOR_REAL_CLEARING_TIME_BUDGET_MS: int = 0
    SIMULATOR_REAL_CLEARING_HARD_TIMEOUT_SEC: int = 60

    # Reported by `/health`. GEO_APP_VERSION wins over the older APP_VERSION; neither set: "dev".
    GEO_APP_VERSION: str = Field(
        default="", validation_alias=AliasChoices("GEO_APP_VERSION", "APP_VERSION")
    )

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
    ADMIN_GRAPH_INCLUDE_MAX_AUDIT_EVENTS: int = 50
    ADMIN_GRAPH_INCLUDE_MAX_TRANSACTIONS: int = 50

    # Feature flags (runtime mutable via PATCH /admin/config; /admin/feature-flags was removed by 032 F-6)
    FEATURE_FLAGS_MULTIPATH_ENABLED: bool = True
    FEATURE_FLAGS_FULL_MULTIPATH_ENABLED: bool = False
    CLEARING_ENABLED: bool = True

    # Periodic clearing (programme 023, decisions 7, 9, R1; `app/core/clearing/runner.py`). OFF by default - general,
    # dev/test and simulator stands. A SEPARATE hub deployment turns it on explicitly (`docker-compose.yml` passes
    # `CLEARING_PERIODIC_ENABLED`, default false); it is never inferred from `ENV`. Supported only on a hub database
    # without simulator data (decision 9, form (b)): the runner refuses on a database holding a real-mode run.
    CLEARING_PERIODIC_ENABLED: bool = False
    CLEARING_PERIODIC_INTERVAL_SECONDS: int = 300

    # Integrity checkpoints and the debt reconciliation after them (`app/core/maintenance_jobs.py`). The loop runs
    # once at process start and then every interval after the previous run completes.
    INTEGRITY_CHECKPOINT_ENABLED: bool = True
    # 2026-10-18, programme 047 `T4701`, owner decision «B»: once a day, was 300 s. No class of discrepancy is caught
    # by the periodic run that one run does not catch (047 F-047-3); each run reads the equivalent's whole history
    # (044 stage 2: ~6.5 KB of memory per operation). An operator who touched the database runs
    # `scripts/reconcile_debts.py` instead of waiting (`docs/ru/runbook-integrity-hold.md` §3). The freshness policy
    # of a stored result follows this value (`result_freshness_threshold`, 2 x interval + 5 min). A deployment may set
    # another value (an explicit 0 means 300 s, as before 047); `INTEGRITY_CHECKPOINT_ENABLED=false` would also drop
    # the start-up run.
    INTEGRITY_CHECKPOINT_INTERVAL_SECONDS: int = 86400
    # Optional: used to serialize integrity checkpoint runs across replicas (Redis lock TTL).
    # 0 = auto: 300 s, independent of the interval since the 047 fix-delta (2026-10-18; it was max(30, interval), a day
    # at the daily default, and a killed run's lock then silenced the replacement's start-up run for a day).
    # `INTEGRITY_LOCK_TTL_DEFAULT_SECONDS` in `app/core/maintenance_jobs.py` holds the value and its limitation.
    INTEGRITY_CHECKPOINT_LOCK_TTL_SECONDS: int = 0
    # 047 class-1 fix, 2026-10-18 (Codex §15 static review): an integrity attempt that blocks must END, as a failed
    # attempt, not hang - a hung attempt stopped detection with the job still `running` while money moved.
    # MEASURED (one equivalent, full `run_scheduled_reconciliation`): 8 000 payments (≈9 600 journal entries) - run
    # 0.61-0.67 s, slowest statement 118-140 ms (the operations read with their intents), the publication's INSERT
    # 1.6 ms (this machine, 2026-10-18, spec 047 Changelog); 044 stage 2 - 4.0 -> 11.1 s at 1 522 -> 7 436 simulator
    # operations (≈1.5 s per 1 000, under `tracemalloc`), peak memory ≈6.5 KB per operation; 015 step 5b - 19.4 s at
    # 100 000 journal rows. The rate taken is the WORST measured, 1.5 s per 1 000 operations.
    # The deadline of one attempt (checkpoints + reconciliation + reactions; `app/core/maintenance_jobs.py`).
    # PREMISE REWRITTEN 2026-10-19 (047 `T4704`). It read "peak memory fails before the deadline does"; since 047
    # `T4703` the history is streamed and the peak of a clean history no longer grows with it, so memory is no longer
    # the expected earlier limit. (Other bounds still end PART of an attempt earlier: a lock wait or a statement past
    # `INTEGRITY_DB_LOCK_TIMEOUT_MS` / `INTEGRITY_DB_STATEMENT_TIMEOUT_MS` below is an error of that equivalent.) What
    # bounds an attempt now is PASSES x HISTORY: since the fail-closed narrowing of `T4704` exactly one full pass per
    # equivalent - a FAILED is published and held in that pass, with no confirmation re-run - and ZERO passes for an
    # equivalent whose stored FAILED is not yet held (it is held first, and verified by the next run). So a FAILED is
    # held in the transaction that publishes it, and a stored one at the start of its turn, whatever the passes after
    # it cost; a publication-and-hold transaction that fails or is cut stores neither, and the next run verifies that
    # equivalent again. What the deadline still limits is CAPACITY: when the sum of single passes exceeds it, the
    # equivalents after the cut are not verified in that attempt, and a divergence there is not found (accepted
    # ceiling, spec 047 `T4704`).
    # MEASURED (spec 047 Changelog, `T4703`, this machine, streaming): 80 033 journal entries of one equivalent - the
    # verifier 5.23-5.42 s, ≈0.066 ms per entry; 1 800 s is then ≈27 M entries in one pass. At the 044 public-demo
    # load (≈2 909 entries/min in one equivalent, a 29-min window: `docs/ru/simulator/backend/real-mode-runbook.md`
    # §6.6) that is ≈270 s of pass per day of history - ≈6.5 days of continuous demo history (extrapolation, not a
    # measured week). A run cut by it is `<reason>_timed_out`, visible, and the job reads `failed`; on a repeated
    # `_timed_out` the operator measures one pass (`scripts/reconcile_debts.py`) and raises this value above it
    # (`docs/ru/runbook-integrity-hold.md` §1) - never disables the loop. The value is not changed by `T4704`.
    INTEGRITY_ATTEMPT_TIMEOUT_SECONDS: float = 1800.0
    # `lock_timeout` / `statement_timeout` of each transaction the scheduled reconciliation and the checkpoints open
    # (`SET LOCAL`, `app/core/ledger/reconciliation.py` `bound_the_waits`); the money writers keep their own.
    # Lock: the longest LEGITIMATE holder of a lock these transactions wait for is the admin's hold clear (the
    # equivalent `FOR UPDATE` and its latest result `FOR SHARE` for one full verification: 11.1 s at the worst measured
    # rate and size) or a money writer's `FOR SHARE` on the equivalent (bounded by `PAYMENT_TOTAL_TIMEOUT_SECONDS`,
    # 10 s); 60 s is ≈5x the first and 6x the second. Statement: the slowest statement measured, 140 ms at 8 000
    # operations, is ≈17.5 s at 1 M by linear extrapolation; 120 s is ≈7x that. A wait or a statement past its bound
    # is an error of that equivalent alone (55P03 / 57014); the next equivalent is still verified.
    INTEGRITY_DB_LOCK_TIMEOUT_MS: int = 60_000
    INTEGRITY_DB_STATEMENT_TIMEOUT_MS: int = 120_000
    # One Redis round trip of the integrity lock (`SET NX EX`, the release `EVAL`): the same bound the clearing lease
    # already uses for one round trip on this client (`renew_timeout_seconds`, `app/core/clearing/runner.py`, 023).
    # NOT measured on this path (the tier runs without Redis): a precedent, not a measurement.
    INTEGRITY_REDIS_CALL_TIMEOUT_SECONDS: float = 5.0

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
        # Pydantic has already merged constructor, process-environment and dotenv
        # sources before field validation, so source origin is not available here.
        # Apply one deterministic rule to every source: supported legacy aliases
        # participate in conflict resolution; unsupported values are retained so
        # _resolve_environment_alias can diagnose them when canonical ENV is absent.
        raw = str(value or "").strip().lower()
        return cls._ENV_ALIASES.get(raw, raw)

    @field_validator(*_LENIENT_SIMULATOR_INTS, mode="before")
    @classmethod
    def _lenient_simulator_value(cls, value: object, info: ValidationInfo) -> object:
        """The rule of the raw reads these replace: empty or not an integer -> the default."""
        text = "" if value is None else str(value).strip()
        try:
            return int(text)
        except ValueError:
            return cls.model_fields[info.field_name].default

    @field_validator("INTEGRITY_CHECKPOINT_INTERVAL_SECONDS")
    @classmethod
    def _integrity_interval(cls, value: int) -> int:
        """047 (2026-10-18, Codex §15 P3): not positive -> 300 s, the meaning an explicit 0 already had (`or 300` in its
        readers). A negative interval was the loop's wait as is - runs back to back, the stop unheard in that wait.
        """
        return value if value > 0 else 300

    @field_validator("SIMULATOR_ACTIONS_ENABLE", mode="before")
    @classmethod
    def _actions_enable(cls, value: object) -> bool:
        if isinstance(value, bool):
            return value
        return str(value or "").strip() in {"1", "true", "TRUE", "yes"}

    @field_validator("SIMULATOR_REAL_AMOUNT_CAP", mode="before")
    @classmethod
    def _amount_cap(cls, value: object) -> Decimal | None:
        """Empty, not a number, not finite or not positive -> None (no cap). Not quantized (028 `F-028-32`): the
        planner floors each amount to its equivalent's step, so the cap is applied in that step.
        """
        # One boundary around the whole parse, as the raw read had: quantize() refuses Infinity and 1e100 too.
        try:
            cap = Decimal(str(value if value is not None else "").strip())
            if not cap.is_finite() or cap <= 0:
                return None
            return cap
        except InvalidOperation:
            return None

    def model_post_init(self, __context: Any) -> None:
        # Runs on every Settings() instantiation (including module-level `settings = Settings()`).
        self._resolve_environment_alias()
        _require_postgresql_database_url(self.DATABASE_URL)
        self.DB_POSTGRES_ISOLATION_LEVEL = _require_read_committed_isolation(
            self.DB_POSTGRES_ISOLATION_LEVEL
        )
        self._guardrail_default_secrets()
        self._guardrail_simulator_session_secret()
        self._guardrail_csrf_allowlist()
        self._guardrail_debug_mode()

    def _resolve_environment_alias(self) -> None:
        canonical_env = self.ENV
        legacy_env = self.LEGACY_ENVIRONMENT
        legacy_env_supported = legacy_env in self._ENV_ALIASES.values()
        if canonical_env is None and legacy_env is None:
            raise RuntimeError(
                "ENV must be explicitly set to dev, test, staging, or prod "
                "(legacy ENVIRONMENT is also accepted)"
            )
        if canonical_env is None and not legacy_env_supported:
            raise RuntimeError(
                "ENV must be explicitly set to dev, test, staging, or prod; "
                "legacy ENVIRONMENT is set to an unsupported value"
            )
        if (
            canonical_env is not None
            and legacy_env_supported
            and canonical_env != legacy_env
        ):
            raise RuntimeError(
                "ENV and legacy ENVIRONMENT select different environments; "
                "remove ENVIRONMENT and keep the canonical ENV value"
            )
        # Canonical ENV wins over an unsupported compatibility value regardless
        # of whether that value came from init kwargs, process env, or dotenv.
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
        if not allowlist_raw:
            if env in self._SAFE_ENVS:
                return
            raise RuntimeError(
                "SIMULATOR_CSRF_ORIGIN_ALLOWLIST must be set in non-dev environment. "
                f"Got ENV={self.ENV!r}. "
                "Set explicit origins via SIMULATOR_CSRF_ORIGIN_ALLOWLIST env var."
            )
        normalized_origins: list[str] = []
        for entry_index, raw_origin in enumerate(allowlist_raw.split(","), start=1):
            normalized_origin = canonicalize_http_origin(raw_origin)
            if normalized_origin is None:
                raise RuntimeError(
                    "SIMULATOR_CSRF_ORIGIN_ALLOWLIST "
                    f"entry {entry_index} is invalid. "
                    "Each entry must be an exact HTTP(S) origin with scheme, host, "
                    "and optional port, without credentials, embedded whitespace, path "
                    "(including a trailing slash), query, or fragment."
                )
            if normalized_origin not in normalized_origins:
                normalized_origins.append(normalized_origin)
        self.SIMULATOR_CSRF_ORIGIN_ALLOWLIST = ",".join(normalized_origins)

    def _guardrail_debug_mode(self) -> None:
        """Fail-fast when DEBUG is on outside dev/test (054-C-8, 2026-10-30).

        `app/main.py` passes DEBUG to `FastAPI(debug=...)`: Starlette then answers an unhandled
        exception with its traceback page, sending source lines and locals to the client.
        """
        env = (self.ENV or "").strip().lower()
        if env in self._SAFE_ENVS or not self.DEBUG:
            return
        raise RuntimeError(
            "Refusing to start with DEBUG=true outside dev/test: debug mode sends tracebacks to clients. "
            f"Got ENV={self.ENV!r}. "
            "Set DEBUG=false, or run with ENV=dev/test."
        )

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


def simulator_state_dir() -> Path:
    """The simulator's runtime state directory: THE ONE RULE that turns `SIMULATOR_STATE_DIR` into a path.

    Empty: `.local-run/simulator` of the repository this file belongs to, as before the setting existed. Relative:
    taken from that repository root, never from the working directory. Absolute: as given.

    WHY IT LIVES HERE (034 S1c, review of `a08f85a4`). The runtime asks through
    `app.core.simulator.runtime_utils.local_state_dir()`, which delegates to this. The scripts that only need the
    path - `scripts/cleanup_simulator_runs.py`, `scripts/check_latest_simulator_artifacts.py` - must NOT import
    anything from `app.core.simulator`: that package's `__init__` builds the runtime singleton, whose constructor
    applies the start-up cleanup of run directories. A script that imported the rule from there deleted old runs
    before it had read its own arguments. This module imports nothing of the application.
    """

    configured = str(settings.SIMULATOR_STATE_DIR or "").strip()
    repository_root = (
        Path(__file__).resolve().parents[1]
    )  # app/config.py -> the repository root
    if not configured:
        return repository_root / ".local-run" / "simulator"
    path = Path(configured)
    return path if path.is_absolute() else repository_root / path
