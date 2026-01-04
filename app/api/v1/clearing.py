from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.core.clearing.service import ClearingService
from app.schemas.clearing import ClearingAutoResponse, ClearingCyclesResponse

router = APIRouter()


@router.get("/clearing/cycles", response_model=ClearingCyclesResponse)
async def list_cycles(
    equivalent: str = Query(..., description="Equivalent code"),
    max_depth: int = Query(6, ge=3, le=10),
    db: AsyncSession = Depends(deps.get_db),
    _current_participant=Depends(deps.get_current_participant),
):
    service = ClearingService(db)
    cycles = await service.find_cycles(equivalent, max_depth=max_depth)
    return {"cycles": cycles}


@router.post("/clearing/auto", response_model=ClearingAutoResponse)
async def auto_clear(
    equivalent: str = Query(..., description="Equivalent code"),
    db: AsyncSession = Depends(deps.get_db),
    _current_participant=Depends(deps.get_current_participant),
):
    service = ClearingService(db)
    cleared = await service.auto_clear(equivalent)
    return {"equivalent": equivalent, "cleared_cycles": cleared}
