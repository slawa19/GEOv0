from typing import Optional
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.core.participants.service import ParticipantService
from app.schemas.participant import Participant, ParticipantCreateRequest, ParticipantsList

router = APIRouter()

@router.get("", response_model=ParticipantsList)
async def list_participants(
    q: Optional[str] = None,
    type: Optional[str] = None,
    limit: int = Query(20, ge=1, le=200),
    db: AsyncSession = Depends(deps.get_db),
    current_participant: Participant = Depends(deps.get_current_participant),
):
    service = ParticipantService(db)
    items = await service.list_participants(query=q, type_filter=type, limit=limit)
    return ParticipantsList(items=items)

@router.post("", response_model=Participant, status_code=status.HTTP_201_CREATED)
async def register_participant(
    request: ParticipantCreateRequest,
    db: AsyncSession = Depends(deps.get_db),
):
    service = ParticipantService(db)
    return await service.create_participant(request)

@router.get("/{pid:path}", response_model=Participant)
async def get_participant(
    pid: str,
    db: AsyncSession = Depends(deps.get_db),
    current_participant: Participant = Depends(deps.get_current_participant),
):
    service = ParticipantService(db)
    return await service.get_participant(pid)