from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.core.auth.service import AuthService
from app.schemas.auth import ChallengeRequest, ChallengeResponse, LoginRequest, TokenPair

router = APIRouter()

@router.post("/challenge", response_model=ChallengeResponse)
async def create_challenge(
    request: ChallengeRequest,
    db: AsyncSession = Depends(deps.get_db),
):
    service = AuthService(db)
    return await service.create_challenge(request.pid)

@router.post("/login", response_model=TokenPair)
async def login(
    request: LoginRequest,
    db: AsyncSession = Depends(deps.get_db),
):
    service = AuthService(db)
    tokens = await service.login(
        pid=request.pid,
        challenge=request.challenge,
        signature=request.signature
    )
    return tokens