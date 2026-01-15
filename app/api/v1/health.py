from __future__ import annotations

import os
import time
from datetime import datetime, timezone

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.engine.url import make_url

from app.db.session import engine
from app.config import settings


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
    return (os.getenv("GEO_ENV") or os.getenv("ENV") or "dev").strip() or "dev"


@router.get("/health")
async def health_check():
    return {
        "status": "ok",
        "version": _best_effort_version(),
        "environment": _best_effort_environment(),
        "uptime_seconds": int(max(0.0, time.time() - _START_TIME)),
        "timestamp": _utc_now_iso(),
    }


@router.get("/healthz")
async def healthz_check():
    return {"status": "ok"}


@router.get("/health/db")
async def health_db_check():
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