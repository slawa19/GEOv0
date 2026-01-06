from __future__ import annotations

from contextlib import asynccontextmanager
import asyncio
import logging
import time

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import text

from app.api.router import api_router
from app.config import settings
from app.db.session import engine
from app.utils.exceptions import GeoException


logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.redis = None
    app.state._bg_stop_event = asyncio.Event()
    app.state._bg_tasks = []

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


app = FastAPI(title="GEO Hub Backend", debug=settings.DEBUG, lifespan=lifespan)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    from app.utils.request_id import request_id_var, new_request_id

    rid = request.headers.get("X-Request-ID") or new_request_id()
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
        path_label = getattr(route, "path", request.url.path)
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


app.include_router(api_router, prefix="/api/v1")


if getattr(settings, "METRICS_ENABLED", True):

    @app.get("/metrics")
    async def metrics():
        from app.utils.metrics import render_metrics

        payload, content_type = render_metrics()
        return Response(content=payload, media_type=content_type)


@app.get("/health")
async def health_check():
    return {"status": "ok"}


@app.get("/health/db")
async def health_db_check():
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return {"status": "ok"}
    except Exception as exc:
        return JSONResponse(status_code=503, content={"status": "error", "details": str(exc)})