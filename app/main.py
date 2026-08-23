from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
import asyncio
import inspect
import logging
import os
import time
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse, Response
from sqlalchemy import text
from sqlalchemy.engine.url import make_url

from app.api.router import api_router
from app.config import settings
from app.db.session import engine
from app.schemas.common import ErrorEnvelope, HealthResponse
from app.utils.error_codes import ERROR_MESSAGES, ErrorCode
from app.utils.exceptions import GeoException
from app.utils.background_jobs import (
    background_health_status,
    background_job_states as _background_job_states,
)


logger = logging.getLogger(__name__)


def _record_background_job_event(
    app: FastAPI,
    *,
    name: str,
    status: str,
    event: str,
    error: BaseException | None = None,
) -> None:
    state = {"status": status, "event": event}
    if error is not None:
        state["error_type"] = type(error).__name__
    _background_job_states(app)[name] = state

    try:
        from app.utils.metrics import BACKGROUND_JOB_EVENTS_TOTAL

        BACKGROUND_JOB_EVENTS_TOTAL.labels(job=name, event=event).inc()
    except Exception:
        # Job state remains authoritative even if metrics collection is degraded.
        logger.debug(
            "background_job.metric_failed name=%s event=%s",
            name,
            event,
            exc_info=True,
        )


def _on_background_task_done(app: FastAPI, name: str, task: asyncio.Task) -> None:
    stop_event = getattr(app.state, "_bg_stop_event", None)
    stopping = bool(stop_event is not None and stop_event.is_set())

    if task.cancelled():
        if stopping:
            _record_background_job_event(
                app,
                name=name,
                status="stopped",
                event="cancelled_for_shutdown",
            )
        else:
            error = RuntimeError("background task was cancelled unexpectedly")
            _record_background_job_event(
                app,
                name=name,
                status="failed",
                event="unexpected_exit",
                error=error,
            )
            logger.error("background_job.unexpected_exit name=%s cancelled=true", name)
        return

    error = task.exception()
    if error is None and stopping:
        _record_background_job_event(
            app,
            name=name,
            status="stopped",
            event="stopped",
        )
        return

    if error is None:
        error = RuntimeError("background task exited before shutdown")

    _record_background_job_event(
        app,
        name=name,
        status="failed",
        event="unexpected_exit",
        error=error,
    )
    logger.error(
        "background_job.unexpected_exit name=%s",
        name,
        exc_info=(type(error), error, error.__traceback__),
    )


def _start_supervised_background_task(
    app: FastAPI,
    *,
    name: str,
    coroutine_factory: Callable[[], Awaitable[None]],
) -> asyncio.Task | None:
    coroutine: Awaitable[None] | None = None
    _record_background_job_event(
        app,
        name=name,
        status="starting",
        event="starting",
    )
    try:
        coroutine = coroutine_factory()
        task = asyncio.create_task(coroutine, name=f"geo:{name}")
    except Exception as error:
        if inspect.iscoroutine(coroutine):
            coroutine.close()
        _record_background_job_event(
            app,
            name=name,
            status="failed",
            event="start_failed",
            error=error,
        )
        logger.exception("background_job.start_failed name=%s", name)
        return None

    app.state._bg_tasks.append(task)
    _record_background_job_event(
        app,
        name=name,
        status="running",
        event="started",
    )
    task.add_done_callback(
        lambda completed, job_name=name: _on_background_task_done(
            app,
            job_name,
            completed,
        )
    )
    return task


def _emit_integrity_metric(result: str) -> None:
    try:
        from app.utils.metrics import RECOVERY_EVENTS_TOTAL

        RECOVERY_EVENTS_TOTAL.labels(
            event="integrity_checkpoints",
            result=result,
        ).inc()
    except Exception:
        logger.debug(
            "integrity.checkpoints_metric_failed result=%s",
            result,
            exc_info=True,
        )


async def _run_integrity_checkpoints_once(app: FastAPI, *, reason: str) -> bool:
    from app.core.integrity import compute_and_store_integrity_checkpoints
    from app.db.session import AsyncSessionLocal
    from app.utils.distributed_lock import redis_distributed_lock
    from app.utils.exceptions import ConflictException

    interval = int(
        getattr(settings, "INTEGRITY_CHECKPOINT_INTERVAL_SECONDS", 300) or 300
    )
    lock_ttl_seconds = int(
        getattr(settings, "INTEGRITY_CHECKPOINT_LOCK_TTL_SECONDS", 0) or 0
    )
    if lock_ttl_seconds <= 0:
        lock_ttl_seconds = max(30, interval)

    _emit_integrity_metric(f"{reason}_start")
    try:
        async with redis_distributed_lock(
            getattr(app.state, "redis", None),
            "geo:integrity:checkpoints",
            ttl_seconds=lock_ttl_seconds,
            wait_timeout_seconds=0.0,
        ):
            async with AsyncSessionLocal() as session:
                await compute_and_store_integrity_checkpoints(session)
    except ConflictException:
        _emit_integrity_metric(f"{reason}_skipped_locked")
        _record_background_job_event(
            app,
            name="integrity",
            status="running",
            event=f"{reason}_skipped_locked",
        )
        return True
    except Exception as error:
        logger.exception("integrity.checkpoints_failed reason=%s", reason)
        _emit_integrity_metric(f"{reason}_error")
        _record_background_job_event(
            app,
            name="integrity",
            status="failed",
            event=f"{reason}_error",
            error=error,
        )
        return False

    _emit_integrity_metric(f"{reason}_success")
    _record_background_job_event(
        app,
        name="integrity",
        status="running",
        event=f"{reason}_success",
    )
    return True


def _record_recovery_iteration(
    app: FastAPI,
    reason: str,
    error: BaseException | None,
) -> None:
    succeeded = error is None
    _record_background_job_event(
        app,
        name="recovery",
        status="running" if succeeded else "failed",
        event=f"{reason}_{'success' if succeeded else 'error'}",
        error=error,
    )


async def _integrity_loop(app: FastAPI) -> None:
    interval = int(
        getattr(settings, "INTEGRITY_CHECKPOINT_INTERVAL_SECONDS", 300) or 300
    )
    await _run_integrity_checkpoints_once(app, reason="startup")

    while not app.state._bg_stop_event.is_set():
        try:
            await asyncio.wait_for(app.state._bg_stop_event.wait(), timeout=interval)
            break
        except asyncio.TimeoutError:
            await _run_integrity_checkpoints_once(app, reason="periodic")


def _start_configured_background_tasks(app: FastAPI) -> None:
    if getattr(settings, "RECOVERY_ENABLED", True):
        try:
            from app.core.recovery import recovery_loop
            from app.db.session import AsyncSessionLocal
        except Exception as error:
            _record_background_job_event(
                app,
                name="recovery",
                status="failed",
                event="start_failed",
                error=error,
            )
            logger.exception("background_job.start_failed name=recovery")
        else:
            _start_supervised_background_task(
                app,
                name="recovery",
                coroutine_factory=lambda: recovery_loop(
                    session_factory=AsyncSessionLocal,
                    stop_event=app.state._bg_stop_event,
                    on_iteration=lambda reason, error: _record_recovery_iteration(
                        app,
                        reason,
                        error,
                    ),
                ),
            )

    if getattr(settings, "INTEGRITY_CHECKPOINT_ENABLED", True):
        _start_supervised_background_task(
            app,
            name="integrity",
            coroutine_factory=lambda: _integrity_loop(app),
        )


async def _sqlite_ensure_debts_version_column() -> None:
    """Lightweight SQLite-only schema fix for dev DB files.

    Alembic migrations are primarily designed for Postgres in this repo.
    When running locally with the default SQLite DATABASE_URL, older DB files
    may be missing newly added columns (e.g. debts.version).
    """

    try:
        dialect = make_url(settings.DATABASE_URL).get_backend_name()
    except Exception:
        return

    if dialect != "sqlite":
        return

    try:
        async with engine.begin() as conn:
            table_exists = await conn.execute(
                text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='debts'")
            )
            if table_exists.scalar() is None:
                return

            cols = await conn.execute(text("PRAGMA table_info(debts)"))
            col_names = {row[1] for row in cols.fetchall()}
            if "version" in col_names:
                return

            logger.warning(
                "SQLite DB is missing debts.version; applying compatibility ALTER TABLE"
            )
            await conn.execute(
                text(
                    "ALTER TABLE debts ADD COLUMN version INTEGER NOT NULL DEFAULT 0"
                )
            )
    except Exception as exc:
        # Fail fast with a clearer message than the later SQLAlchemy error.
        raise RuntimeError(
            "SQLite DB schema appears outdated and auto-fix failed. "
            "For the default local database, replace ./.local-run/geov0.db; "
            "for an explicit DATABASE_URL, repair it or point to a fresh file."
        ) from exc


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.redis = None
    app.state._bg_stop_event = asyncio.Event()
    app.state._bg_tasks = []
    app.state.background_jobs = {}

    await _sqlite_ensure_debts_version_column()

    # §12 Recovery reconciliation: mark simulator runs that were still active
    # before the previous server process died as 'error'.  Best-effort — any
    # exception here must NOT prevent the server from starting.
    try:
        from app.core.simulator.storage import reconcile_stale_runs

        _reconciled = await reconcile_stale_runs()
        if _reconciled:
            logger.warning(
                "lifespan.simulator_reconcile reconciled=%d stale run(s) on startup",
                _reconciled,
            )
    except Exception:
        logger.exception("lifespan.simulator_reconcile_failed (non-fatal)")

    if settings.REDIS_ENABLED:
        import redis.asyncio as redis
        from app.utils import security

        client = redis.from_url(settings.REDIS_URL, encoding="utf-8", decode_responses=True)
        try:
            await client.ping()
        except Exception as exc:
            await client.aclose()
            raise RuntimeError("Redis enabled but unavailable") from exc

        app.state.redis = client
        security.set_redis_client(client)

    # These maintenance jobs are degradable: startup continues, while job state,
    # logs, metrics, and /health expose their absence or unexpected exit.
    _start_configured_background_tasks(app)

    try:
        yield
    finally:
        # Mark shutdown before awaiting other components so normal task exits and
        # cancellations are not reported as unexpected failures.
        app.state._bg_stop_event.set()

        # Maintenance jobs can still be inside a DB session or Redis-backed
        # distributed lock. Cancel and join them before tearing down either
        # resource so their context-manager cleanup remains valid.
        tasks = list(getattr(app.state, "_bg_tasks", []) or [])
        if tasks:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        app.state._bg_tasks = []

        # Simulator runtime graceful shutdown (best-effort).
        try:
            from app.core.simulator.runtime import runtime

            await runtime.shutdown()
        except Exception:
            logger.exception("simulator.runtime.shutdown_failed")

        from app.utils import security

        security.set_redis_client(None)
        client = getattr(app.state, "redis", None)
        if client is not None:
            try:
                await client.aclose()
            finally:
                app.state.redis = None

        # Ensure DB connections/threads are cleaned up when the app shuts down
        # (important for pytest TestClient runs on Windows + aiosqlite).
        try:
            await engine.dispose()
        except Exception:
            pass


app = FastAPI(title="GEO Hub Backend", debug=settings.DEBUG, lifespan=lifespan)

# CORS middleware configuration for dev environment
app.add_middleware(
    CORSMiddleware,
    # Allow local dev servers (Vite) on any port, but only on localhost.
    # This avoids fragile port-specific CORS issues on Windows.
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    from app.utils.request_id import request_id_var, new_request_id, validate_request_id

    incoming_rid = request.headers.get("X-Request-ID")
    rid = validate_request_id(incoming_rid) or new_request_id()
    token = request_id_var.set(rid)
    try:
        response = await call_next(request)
    finally:
        request_id_var.reset(token)

    response.headers["X-Request-ID"] = rid
    return response


@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    if not getattr(settings, "METRICS_ENABLED", True):
        return await call_next(request)

    start = time.perf_counter()
    response = await call_next(request)
    elapsed_s = time.perf_counter() - start

    try:
        from app.utils.metrics import HTTP_REQUESTS_TOTAL, HTTP_REQUEST_DURATION_SECONDS

        route = request.scope.get("route")
        # IMPORTANT: keep Prometheus label cardinality low.
        # - Matched routes: use the route template (e.g. "/api/v1/payments/{payment_id}").
        # - Unmatched routes (no route in scope / no template): use a fixed label.
        route_path = getattr(route, "path", None)
        if isinstance(route_path, str) and route_path:
            path_label = route_path
        else:
            path_label = "__unmatched__"
        method = request.method
        status = str(getattr(response, "status_code", 0))

        HTTP_REQUESTS_TOTAL.labels(method=method, path=path_label, status=status).inc()
        HTTP_REQUEST_DURATION_SECONDS.labels(method=method, path=path_label).observe(elapsed_s)
    except Exception:
        pass

    return response


@app.exception_handler(GeoException)
async def geo_exception_handler(request: Request, exc: GeoException):
    return JSONResponse(status_code=exc.status_code, content=exc.to_dict())


@app.exception_handler(RequestValidationError)
async def request_validation_exception_handler(
    request: Request, exc: RequestValidationError
):
    path = str(getattr(request.url, "path", "") or "")

    # Simulator Interact Mode action endpoints use a different error envelope.
    # Keep schema validation errors stable and aligned with simulator-ui expectations.
    if path.startswith("/api/v1/simulator/runs/") and "/actions/" in path:
        from app.schemas.simulator import SimulatorActionError

        payload = SimulatorActionError(
            code="INVALID_REQUEST",
            message="Invalid request",
            details={"errors": exc.errors()},
        ).model_dump(mode="json")
        return JSONResponse(status_code=400, content=payload)

    # Default: unify FastAPI/Pydantic validation errors into GEO error envelope.
    # Spec: E009 (Invalid input).
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": ErrorCode.E009.value,
                "message": ERROR_MESSAGES[ErrorCode.E009],
                "details": {"errors": exc.errors()},
            }
        },
    )


app.include_router(api_router, prefix="/api/v1")

_START_TIME = time.time()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _best_effort_version() -> str:
    v = (os.getenv("GEO_APP_VERSION") or os.getenv("APP_VERSION") or "").strip()
    return v or "dev"


if getattr(settings, "METRICS_ENABLED", True):

    @app.get("/metrics")
    async def metrics():
        from app.utils.metrics import render_metrics

        payload, content_type = render_metrics()
        return Response(content=payload, media_type=content_type)


@app.get(
    "/health",
    response_model=HealthResponse,
    response_model_exclude_none=True,
    responses={503: {"model": HealthResponse, "description": "Service degraded"}},
)
async def health_check(response: Response):
    status = background_health_status(app)
    if status == "degraded":
        response.status_code = 503
    return {
        "status": status,
        "version": _best_effort_version(),
        "uptime_seconds": int(max(0.0, time.time() - _START_TIME)),
        "timestamp": _utc_now_iso(),
    }


@app.get("/healthz")
async def healthz_check():
    return {"status": "ok"}


@app.get(
    "/health/db",
    responses={
        503: {
            # 2026-08-22 / p011_t1105.  Declared inline rather than as ErrorEnvelope because
            # the canonical shape for this route is its own `{status: error}` object
            # (`api/openapi.yaml:108-118`); a mismatched schema would keep the operation in
            # the drift under a different heading instead of removing it.
            "description": "DB unavailable",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "required": ["status"],
                        "properties": {"status": {"type": "string", "enum": ["error"]}},
                    }
                }
            },
        }
    },
)
async def health_db_check():
    try:
        t0 = time.perf_counter()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        latency_ms = int(round((time.perf_counter() - t0) * 1000.0))
        return {
            "status": "ok",
            "db": {"reachable": True, "latency_ms": latency_ms},
            "timestamp": _utc_now_iso(),
        }
    except Exception:
        return JSONResponse(
            status_code=503,
            content={
                "status": "error",
                "db": {"reachable": False, "latency_ms": None},
                "timestamp": _utc_now_iso(),
            },
        )


def _register_openapi_model_schema(document: dict, model: type) -> None:
    schemas = document.setdefault("components", {}).setdefault("schemas", {})
    model_schema = model.model_json_schema(
        ref_template="#/components/schemas/{model}"
    )
    definitions = model_schema.pop("$defs", {})
    for name, schema in definitions.items():
        schemas.setdefault(name, schema)
    schemas.setdefault(model.__name__, model_schema)


def _is_default_fastapi_validation_response(response: object) -> bool:
    if not isinstance(response, dict):
        return False
    schema = (
        ((response.get("content") or {}).get("application/json") or {}).get("schema")
    )
    return schema == {"$ref": "#/components/schemas/HTTPValidationError"}


def _custom_openapi() -> dict:
    if app.openapi_schema is not None:
        return app.openapi_schema

    document = get_openapi(
        title=app.title,
        version=app.version,
        openapi_version=app.openapi_version,
        description=app.description,
        routes=app.routes,
    )
    from app.schemas.simulator import SimulatorActionError

    _register_openapi_model_schema(document, ErrorEnvelope)
    _register_openapi_model_schema(document, SimulatorActionError)

    for path, path_item in (document.get("paths") or {}).items():
        if not isinstance(path_item, dict):
            continue
        is_simulator_action = path.startswith("/api/v1/simulator/runs/") and (
            "/actions/" in path
        )
        for operation in path_item.values():
            if not isinstance(operation, dict):
                continue
            responses = operation.get("responses") or {}
            validation_response = responses.get("422")
            if not _is_default_fastapi_validation_response(validation_response):
                continue
            responses.pop("422")
            if is_simulator_action:
                responses.setdefault(
                    "400",
                    {
                        "description": "Simulator action validation error",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "$ref": "#/components/schemas/SimulatorActionError"
                                }
                            }
                        },
                    },
                )
            responses["422"] = {
                "description": (
                    "Invalid simulator identity transport"
                    if is_simulator_action
                    else "Validation error"
                ),
                "content": {
                    "application/json": {
                        "schema": {"$ref": "#/components/schemas/ErrorEnvelope"}
                    }
                },
            }

    app.openapi_schema = document
    return document


app.openapi = _custom_openapi
