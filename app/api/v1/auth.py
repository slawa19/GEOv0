import logging

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.core.auth.service import AuthService
from app.schemas.auth import ChallengeRequest, ChallengeResponse, LoginRequest, TokenPair
from app.utils.exceptions import GeoException

router = APIRouter()

logger = logging.getLogger(__name__)

@router.post("/challenge", response_model=ChallengeResponse)
async def create_challenge(
    request: ChallengeRequest,
    db: AsyncSession = Depends(deps.get_db),
):
    service = AuthService(db)
    result = await service.create_challenge(request.pid)
    logger.info("auth.challenge created for pid=%s", request.pid)
    return result

@router.post("/login", response_model=TokenPair)
async def login(
    request: LoginRequest,
    http_request: Request,
    db: AsyncSession = Depends(deps.get_db),
):
    service = AuthService(db)
    client_host = (http_request.client.host if http_request.client else None) or "unknown"
    try:
        tokens = await service.login(
            pid=request.pid,
            challenge=request.challenge,
            signature=request.signature,
        )
    except GeoException:
        logger.warning("auth.login failed pid=%s ip=%s", request.pid, client_host)
        raise
    except Exception:
        logger.exception("auth.login crashed pid=%s ip=%s", request.pid, client_host)
        raise

    logger.info("auth.login success pid=%s ip=%s", request.pid, client_host)
    return tokens