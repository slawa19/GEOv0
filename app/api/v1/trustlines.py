from typing import Optional, Literal
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.core.trustlines.service import TrustLineService
from app.schemas.trustline import TrustLine, TrustLineCreateRequest, TrustLineUpdateRequest, TrustLinesList
from app.db.models.participant import Participant

router = APIRouter()

@router.post("", response_model=TrustLine, status_code=status.HTTP_201_CREATED)
async def create_trustline(
    data: TrustLineCreateRequest,
    current_participant: Participant = Depends(deps.get_current_participant),
    db: AsyncSession = Depends(deps.get_db)
):
    if data.limit < 0:
        raise HTTPException(status_code=400, detail="Limit must be >= 0")
    service = TrustLineService(db)
    return await service.create(current_participant.id, data)

@router.get("", response_model=TrustLinesList)
async def get_trustlines(
    direction: Literal['outgoing', 'incoming', 'all'] = Query('all'),
    equivalent: Optional[str] = Query(None),
    current_participant: Participant = Depends(deps.get_current_participant),
    db: AsyncSession = Depends(deps.get_db)
):
    service = TrustLineService(db)
    items = await service.get_by_participant(current_participant.id, direction=direction, equivalent=equivalent)
    return TrustLinesList(items=items)


@router.get("/{id}", response_model=TrustLine)
async def get_trustline(
    id: UUID,
    current_participant: Participant = Depends(deps.get_current_participant),
    db: AsyncSession = Depends(deps.get_db),
):
    service = TrustLineService(db)
    trustline = await service.get_one(id)

    if (
        trustline.from_participant_id != current_participant.id
        and trustline.to_participant_id != current_participant.id
    ):
        raise HTTPException(status_code=404, detail="Trustline not found")

    return trustline

@router.patch("/{id}", response_model=TrustLine)
async def update_trustline(
    id: UUID,
    data: TrustLineUpdateRequest,
    current_participant: Participant = Depends(deps.get_current_participant),
    db: AsyncSession = Depends(deps.get_db)
):
    if data.limit is not None and data.limit < 0:
        raise HTTPException(status_code=400, detail="Limit must be >= 0")
    service = TrustLineService(db)
    return await service.update(id, current_participant.id, data)

@router.delete("/{id}", status_code=status.HTTP_200_OK)
async def close_trustline(
    id: UUID,
    current_participant: Participant = Depends(deps.get_current_participant),
    db: AsyncSession = Depends(deps.get_db)
):
    service = TrustLineService(db)
    await service.close(id, current_participant.id)
    return {"status": "success", "message": "Trustline closed"}