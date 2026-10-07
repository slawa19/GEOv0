from __future__ import annotations

from contextlib import asynccontextmanager
import asyncio
import logging
import time

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

from app.api.router import api_router
from app.api.v1 import health
from app.config import settings
from app.db.session import engine
from app.utils.error_codes import ERROR_MESSAGES, ErrorCode
from app.utils.exceptions import GeoException
from app.utils.request_id import new_request_id, request_id_var, validate_request_id
from app.core.maintenance_jobs import _start_configured_background_tasks
from app.openapi_postprocess import install_openapi


# 029 `F-029-3`: the one logging configuration of the application - level from `LOG_LEVEL`, one format with time,
# level and logger name. A no-op when the root logger already has handlers (a deployment's own `--log-config`).
logging.basicConfig(level=settings.LOG_LEVEL.upper(), format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)


async def _require_redis_noeviction(client) -> None:
    """029 `T2991`: refuse a Redis that may evict keys. Under a `volatile-*` or `allkeys-*` policy the revocation
    of a used refresh token (a key with a TTL) can be evicted while the store id stays, and the token works again.
    Checks the policy at start only; where CONFIG is not permitted (managed Redis) the operator guarantees it."""
    from redis.exceptions import ResponseError

    try:
        policy = (await client.config_get("maxmemory-policy")).get("maxmemory-policy")
    except ResponseError:
        logger.error("lifespan.redis_eviction_policy_unverified CONFIG GET refused; maxmemory-policy must be noeviction")
        return
    if policy != "noeviction":
        raise RuntimeError(f"Redis maxmemory-policy must be noeviction (used refresh tokens would revive), got {policy!r}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.redis = None
    app.state._bg_stop_event = asyncio.Event()
    app.state._bg_tasks = []
    app.state.background_jobs = {}

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
        try:
            await _require_redis_noeviction(client)
        except BaseException:
            await client.aclose()
            raise

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
        # (important for pytest TestClient runs on Windows).
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


def _error_with_request_id(content: dict) -> dict:
    """The GEO error envelope with the request id beside the code (024 `T2414.2`, AGENTS.md §12)."""
    rid = request_id_var.get()
    if rid is not None:
        content["error"]["request_id"] = rid
    return content


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    incoming_rid = request.headers.get("X-Request-ID")
    rid = validate_request_id(incoming_rid) or new_request_id()
    token = request_id_var.set(rid)
    # Also on the request state: an exception no handler answered reaches `unhandled_exception_handler` in
    # Starlette's outermost middleware, after this function - and the context variable - have been left.
    request.state.request_id = rid
    try:
        response = await call_next(request)
    finally:
        request_id_var.reset(token)

    response.headers["X-Request-ID"] = rid
    return response


@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    if not settings.METRICS_ENABLED:
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


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """An exception no handler answered (024 `T2414.2`): logged once under the request id with its traceback; the
    caller gets the internal-error envelope with that id and none of the exception's text. Starlette still re-raises
    the exception to the server after this response, as it did before."""
    rid = getattr(request.state, "request_id", None) or new_request_id()
    logger.error(
        "http.unhandled_error request_id=%s method=%s path=%s",
        rid,
        request.method,
        request.url.path,
        exc_info=(type(exc), exc, exc.__traceback__),
    )
    internal = {"error": {"code": ErrorCode.E010.value, "message": ERROR_MESSAGES[ErrorCode.E010], "request_id": rid}}
    return JSONResponse(status_code=500, content=internal, headers={"X-Request-ID": rid})


@app.exception_handler(GeoException)
async def geo_exception_handler(request: Request, exc: GeoException):
    body = exc.to_dict()
    if exc.code == ErrorCode.E010.value:
        # 028 `F-028-42`: an internal error's own text may carry internals (a Python repr); it is logged under the
        # request id and the caller gets the code's meaning.
        logger.error("http.internal_error request_id=%s message=%s", request_id_var.get(), exc.message)
        body["error"]["message"] = ERROR_MESSAGES[ErrorCode.E010]
    return JSONResponse(status_code=exc.status_code, content=_error_with_request_id(body))


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
        content=_error_with_request_id(
            {
                "error": {
                    "code": ErrorCode.E009.value,
                    "message": ERROR_MESSAGES[ErrorCode.E009],
                    "details": {"errors": exc.errors()},
                }
            }
        ),
    )


app.include_router(api_router, prefix="/api/v1")
# The same three public health routes at the root, without the /api/v1 rate limit: the container healthcheck
# and probes call /health, /healthz and /health/db. One router, `app/api/v1/health.py` (024 `T2414.2`).
app.include_router(health.router, tags=["Health"])


if settings.METRICS_ENABLED:

    @app.get("/metrics")
    async def metrics():
        from app.utils.metrics import render_metrics

        payload, content_type = render_metrics()
        return Response(content=payload, media_type=content_type)


install_openapi(app)
