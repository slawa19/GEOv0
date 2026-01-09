from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, get_current_participant
from app.core.balance.service import BalanceService
from app.db.models.participant import Participant
from app.schemas.balance import BalanceSummary, DebtsDetails

router = APIRouter()

@router.get("", response_model=BalanceSummary)
async def get_balance(
    current_participant: Participant = Depends(get_current_participant),
    db: AsyncSession = Depends(get_db)
):
    """
    Get aggregated balance by equivalents.
    """
    service = BalanceService(db)
    return await service.get_summary(current_participant.id)

@router.get("/debts", response_model=DebtsDetails)
async def get_debts(
    equivalent: str = Query(..., description="Equivalent code"),
    direction: Literal['outgoing', 'incoming', 'all'] = Query('all'),
    current_participant: Participant = Depends(get_current_participant),
    db: AsyncSession = Depends(get_db)
):
    """
    Get debts details for a specific equivalent.
    """
    service = BalanceService(db)
    return await service.get_debts(current_participant.id, equivalent, direction)