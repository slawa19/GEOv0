from __future__ import annotations

from contextlib import asynccontextmanager
import asyncio
import logging
import os
import time
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from sqlalchemy import text
from sqlalchemy.engine.url import make_url

from app.api.router import api_router
from app.config import settings
from app.db.session import engine
from app.utils.error_codes import ERROR_MESSAGES, ErrorCode
from app.utils.exceptions import GeoException


logger = logging.getLogger(__name__)


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
            "Delete ./geov0.db (or point DATABASE_URL to a fresh file) and restart."
        ) from exc


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.redis = None
    app.state._bg_stop_event = asyncio.Event()
    app.state._bg_tasks = []

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

    # Background tasks (best-effort)
    if getattr(settings, "RECOVERY_ENABLED", True):
        try:
            from app.core.recovery import recovery_loop
            from app.db.session import AsyncSessionLocal

            task = asyncio.create_task(
                recovery_loop(session_factory=AsyncSessionLocal, stop_event=app.state._bg_stop_event)
            )
            app.state._bg_tasks.append(task)
        except Exception:
            pass

    if getattr(settings, "INTEGRITY_CHECKPOINT_ENABLED", True):

        async def _integrity_loop():
            from app.core.integrity import compute_and_store_integrity_checkpoints
            from app.db.session import AsyncSessionLocal
            from app.utils.distributed_lock import redis_distributed_lock
            from app.utils.exceptions import ConflictException
            from app.utils.metrics import RECOVERY_EVENTS_TOTAL

            interval = int(getattr(settings, "INTEGRITY_CHECKPOINT_INTERVAL_SECONDS", 300) or 300)
            lock_ttl_seconds = int(getattr(settings, "INTEGRITY_CHECKPOINT_LOCK_TTL_SECONDS", 0) or 0)
            if lock_ttl_seconds <= 0:
                lock_ttl_seconds = max(30, interval)

            def _emit(result: str) -> None:
                try:
                    RECOVERY_EVENTS_TOTAL.labels(event="integrity_checkpoints", result=result).inc()
                except Exception:
                    pass

            async def _run_once(*, reason: str) -> None:
                _emit(f"{reason}_start")
                redis_client = getattr(app.state, "redis", None)
                try:
                    async with redis_distributed_lock(
                        redis_client,
                        "geo:integrity:checkpoints",
                        ttl_seconds=lock_ttl_seconds,
                        wait_timeout_seconds=0.0,
                    ):
                        async with AsyncSessionLocal() as session:
                            await compute_and_store_integrity_checkpoints(session)
                    _emit(f"{reason}_success")
                except ConflictException:
                    _emit(f"{reason}_skipped_locked")
                except Exception:
                    logger.exception("integrity.checkpoints_failed reason=%s", reason)
                    _emit(f"{reason}_error")

            try:
                await _run_once(reason="startup")
            except Exception:
                pass

            while not app.state._bg_stop_event.is_set():
                try:
                    await asyncio.wait_for(app.state._bg_stop_event.wait(), timeout=interval)
                    break
                except asyncio.TimeoutError:
                    pass

                try:
                    await _run_once(reason="periodic")
                except Exception:
                    pass

        try:
            task = asyncio.create_task(_integrity_loop())
            app.state._bg_tasks.append(task)
        except Exception:
            pass

    try:
        yield
    finally:
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

        try:
            app.state._bg_stop_event.set()
        except Exception:
            pass
        tasks = list(getattr(app.state, "_bg_tasks", []) or [])
        if tasks:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        app.state._bg_tasks = []

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


@app.get("/health")
async def health_check():
    return {
        "status": "ok",
        "version": _best_effort_version(),
        "uptime_seconds": int(max(0.0, time.time() - _START_TIME)),
        "timestamp": _utc_now_iso(),
    }


@app.get("/healthz")
async def healthz_check():
    return {"status": "ok"}


@app.get("/health/db")
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
    except Exception as exc:
        return JSONResponse(
            status_code=503,
            content={
                "status": "error",
                "db": {"reachable": False, "latency_ms": None},
                "details": str(exc),
                "timestamp": _utc_now_iso(),
            },
        )
