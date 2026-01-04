from typing import AsyncGenerator
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.db.session import get_db_session
from app.utils.security import decode_token
from app.db.models.participant import Participant
from app.utils.exceptions import UnauthorizedException

reusable_oauth2 = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async for session in get_db_session():
        yield session

async def get_current_participant(
    db: AsyncSession = Depends(get_db),
    token: str = Depends(reusable_oauth2)
) -> Participant:
    payload = decode_token(token)
    if not payload:
        raise UnauthorizedException("Could not validate credentials")
    
    pid: str = payload.get("sub")
    if pid is None:
        raise UnauthorizedException("Could not validate credentials")
    
    result = await db.execute(select(Participant).where(Participant.pid == pid))
    participant = result.scalar_one_or_none()
    
    if not participant:
        raise UnauthorizedException("Participant not found")
    
    if participant.status != 'active':
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Participant account is not active"
        )
        
    return participant