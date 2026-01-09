import asyncio
import time
from typing import AsyncGenerator

from fastapi import Depends, Header, Request, status
from app.utils.exceptions import TooManyRequestsException, UnauthorizedException
from app.utils.exceptions import ForbiddenException
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.db.session import get_db_session
from app.utils.security import decode_token
from app.db.models.participant import Participant
from app.config import settings
from app.utils.exceptions import TooManyRequestsException, UnauthorizedException

reusable_oauth2 = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


_rate_limit_lock = asyncio.Lock()
_rate_limit_counters: dict[tuple[int, str], int] = {}


async def rate_limit(request: Request) -> None:
    if not settings.RATE_LIMIT_ENABLED:
        return

    redis_client = getattr(getattr(request, "app", None), "state", None)
    redis_client = getattr(redis_client, "redis", None)
    if settings.REDIS_ENABLED and redis_client is not None:
        client_host = (request.client.host if request.client else None) or "unknown"
        window_seconds = max(1, int(settings.RATE_LIMIT_WINDOW_SECONDS))
        limit = max(1, int(settings.RATE_LIMIT_REQUESTS_PER_WINDOW))
        bucket = int(time.time() // window_seconds)
        key = f"rl:{client_host}:{bucket}"

        current = await redis_client.incr(key)
        if current == 1:
            # Ensure key expires after the window.
            await redis_client.expire(key, window_seconds + 1)

        if current > limit:
            raise TooManyRequestsException(
                details={
                    "window_seconds": window_seconds,
                    "limit": limit,
                }
            )
        return

    client_host = (request.client.host if request.client else None) or "unknown"
    window_seconds = max(1, int(settings.RATE_LIMIT_WINDOW_SECONDS))
    limit = max(1, int(settings.RATE_LIMIT_REQUESTS_PER_WINDOW))
    bucket = int(time.monotonic() // window_seconds)
    key = (bucket, client_host)

    async with _rate_limit_lock:
        current = _rate_limit_counters.get(key, 0) + 1
        _rate_limit_counters[key] = current

        # Best-effort cleanup of previous window for the same host
        prev_key = (bucket - 1, client_host)
        _rate_limit_counters.pop(prev_key, None)

    if current > limit:
        raise TooManyRequestsException(
            details={
                "window_seconds": window_seconds,
                "limit": limit,
            }
        )


async def get_redis_client(request: Request):
    state = getattr(getattr(request, "app", None), "state", None)
    return getattr(state, "redis", None)

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async for session in get_db_session():
        yield session

async def get_current_participant(
    db: AsyncSession = Depends(get_db),
    token: str = Depends(reusable_oauth2)
) -> Participant:
    payload = await decode_token(token)
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
        raise ForbiddenException("Participant account is not active")
        
    return participant


async def require_admin(
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> None:
    if not x_admin_token or x_admin_token != settings.ADMIN_TOKEN:
        raise ForbiddenException("Admin token required")