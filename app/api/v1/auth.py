import logging

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.api.audit import add_audit_entry
from app.core.auth.service import AuthService
from app.schemas.auth import ChallengeRequest, ChallengeResponse, LoginRequest, RefreshRequest, TokenPair
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
            device_info=(request.device_info.model_dump(exclude_none=True) if request.device_info else None),
        )
    except GeoException:
        logger.warning(
            "auth.login failed pid=%s ip=%s device=%s",
            request.pid,
            client_host,
            request.device_info.model_dump(exclude_none=True) if request.device_info else None,
        )
        raise
    except Exception:
        logger.exception(
            "auth.login crashed pid=%s ip=%s device=%s",
            request.pid,
            client_host,
            request.device_info.model_dump(exclude_none=True) if request.device_info else None,
        )
        raise

    logger.info(
        "auth.login success pid=%s ip=%s device=%s",
        request.pid,
        client_host,
        request.device_info.model_dump(exclude_none=True) if request.device_info else None,
    )

    # Best-effort audit entry to give device_info minimal semantics; the row is built by the one audit writer
    # (`app/api/audit.py`, 032 A-8). Best-effort is the login's choice: the tokens are already issued.
    try:
        add_audit_entry(
            db,
            request=http_request,
            action="auth.login",
            actor_role=None,
            object_type="participant",
            object_id=request.pid,
            after_state={
                "device_info": request.device_info.model_dump(exclude_none=True)
                if request.device_info
                else None
            },
        )
        await db.commit()
    except Exception:
        logger.warning("auth.login audit_failed pid=%s", request.pid, exc_info=True)
        await db.rollback()

    return tokens


@router.post("/refresh", response_model=TokenPair)
async def refresh(
    request: RefreshRequest,
    http_request: Request,
    db: AsyncSession = Depends(deps.get_db),
):
    service = AuthService(db)
    client_host = (http_request.client.host if http_request.client else None) or "unknown"
    try:
        tokens = await service.refresh_tokens(request.refresh_token)
    except GeoException:
        logger.warning("auth.refresh failed ip=%s", client_host)
        raise
    except Exception:
        logger.exception("auth.refresh crashed ip=%s", client_host)
        raise

    logger.info("auth.refresh success ip=%s", client_host)
    return tokens