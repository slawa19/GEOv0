import logging

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.core.auth.service import AuthService
from app.db.models.audit_log import AuditLog
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

    # Best-effort audit entry to give device_info minimal semantics.
    try:
        db.add(
            AuditLog(
                actor_id=None,
                actor_role=None,
                action="auth.login",
                object_type="participant",
                object_id=request.pid,
                reason=None,
                before_state=None,
                after_state={
                    "device_info": request.device_info.model_dump(exclude_none=True)
                    if request.device_info
                    else None
                },
                request_id=http_request.headers.get("X-Request-ID"),
                ip_address=client_host,
                user_agent=http_request.headers.get("user-agent"),
            )
        )
        await db.commit()
    except Exception:
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