from __future__ import annotations

import os
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import text
from sqlalchemy.engine.url import make_url

from app.api import deps
from app.db.session import engine
from app.config import settings
from app.schemas.common import AdminDbHealthResponse, ErrorEnvelope, HealthResponse
from app.utils.background_jobs import background_health_status


router = APIRouter()

_START_TIME = time.time()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _best_effort_version() -> str:
    v = (os.getenv("GEO_APP_VERSION") or os.getenv("APP_VERSION") or "").strip()
    if v:
        return v
    # No packaging metadata in this repo; default to a dev marker.
    return "dev"


def _best_effort_environment() -> str:
    # Optional, but useful for the Admin UI cards.
    return settings.ENV or "dev"


@router.get(
    "/health",
    response_model=HealthResponse,
    response_model_exclude_none=True,
    responses={503: {"model": HealthResponse, "description": "Service degraded"}},
)
async def health_check(request: Request, response: Response):
    status = background_health_status(request.app)
    if status == "degraded":
        response.status_code = 503
    return {
        "status": status,
        "version": _best_effort_version(),
        "environment": _best_effort_environment(),
        "uptime_seconds": int(max(0.0, time.time() - _START_TIME)),
        "timestamp": _utc_now_iso(),
    }


@router.get("/healthz")
async def healthz_check():
    return {"status": "ok"}


@router.get(
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
            "db": {
                "reachable": True,
                "latency_ms": latency_ms,
            },
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


@router.get(
    "/admin/health/db",
    tags=["Admin"],
    responses={
        200: {"model": AdminDbHealthResponse, "description": "DB OK"},
        403: {"model": ErrorEnvelope, "description": "Admin token required"},
        503: {"model": AdminDbHealthResponse, "description": "Database unavailable"},
    },
)
async def health_db_diagnostic(
    request: Request,
    x_admin_token: str = Header(alias="X-Admin-Token"),
):
    await deps.require_admin(request, x_admin_token)
    try:
        t0 = time.perf_counter()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        latency_ms = int(round((time.perf_counter() - t0) * 1000.0))

        dialect = make_url(settings.DATABASE_URL).get_backend_name()
        return {
            "status": "ok",
            "db": {
                "dialect": dialect,
                "reachable": True,
                "latency_ms": latency_ms,
            },
            "timestamp": _utc_now_iso(),
        }
    except Exception as exc:
        dialect = make_url(settings.DATABASE_URL).get_backend_name()
        return JSONResponse(
            status_code=503,
            content={
                "status": "error",
                "db": {"dialect": dialect, "reachable": False, "latency_ms": None},
                "details": str(exc),
                "timestamp": _utc_now_iso(),
            },
        )
