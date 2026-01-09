from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.db.session import engine


router = APIRouter()


@router.get("/health")
async def health_check():
    return {"status": "ok"}


@router.get("/healthz")
async def healthz_check():
    return {"status": "ok"}


@router.get("/health/db")
async def health_db_check():
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return {"status": "ok"}
    except Exception as exc:
        return JSONResponse(status_code=503, content={"status": "error", "details": str(exc)})