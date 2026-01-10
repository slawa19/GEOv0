from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.core.clearing.service import ClearingService
from app.config import settings
from app.schemas.clearing import ClearingAutoResponse, ClearingCyclesResponse
from app.utils.distributed_lock import redis_distributed_lock
from app.utils.exceptions import BadRequestException
from app.utils.validation import validate_equivalent_code

router = APIRouter()


@router.get("/cycles", response_model=ClearingCyclesResponse)
async def list_cycles(
    equivalent: str = Query(..., description="Equivalent code"),
    max_depth: int = Query(6, ge=3, le=10),
    db: AsyncSession = Depends(deps.get_db),
    _current_participant=Depends(deps.get_current_participant),
):
    validate_equivalent_code(equivalent)
    service = ClearingService(db)
    cycles = await service.find_cycles(equivalent, max_depth=max_depth)
    return {"cycles": cycles}


@router.post("/auto", response_model=ClearingAutoResponse)
async def auto_clear(
    equivalent: str = Query(..., description="Equivalent code"),
    max_depth: int = Query(6, ge=3, le=10),
    db: AsyncSession = Depends(deps.get_db),
    _current_participant=Depends(deps.get_current_participant),
    redis_client=Depends(deps.get_redis_client),
):
    if not bool(getattr(settings, "CLEARING_ENABLED", True)):
        raise BadRequestException("Clearing is disabled")
    validate_equivalent_code(equivalent)
    service = ClearingService(db)
    lock_key = f"dlock:clearing:{equivalent}"
    async with redis_distributed_lock(redis_client, lock_key, ttl_seconds=30, wait_timeout_seconds=2.0):
        cleared = await service.auto_clear(equivalent, max_depth=max_depth)
    return {"equivalent": equivalent, "cleared_cycles": cleared}
