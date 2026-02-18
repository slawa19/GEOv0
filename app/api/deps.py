import asyncio
import re
import time
from dataclasses import dataclass
from typing import AsyncGenerator, Literal, Optional

from fastapi import Depends, Header, HTTPException, Request, status
from app.utils.exceptions import (
    TooManyRequestsException,
    UnauthorizedException,
    ForbiddenException,
    GeoException,
)
from app.utils.error_codes import ErrorCode
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.db.session import get_db_session
from app.utils.security import decode_token
from app.db.models.participant import Participant
from app.config import settings

reusable_oauth2 = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")
optional_oauth2 = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)


_rate_limit_lock = asyncio.Lock()
_rate_limit_counters: dict[tuple[int, str], int] = {}


# Paths that are explicitly exempt from rate-limiting.
# /session/ensure is called on every page reload by anonymous visitors; applying
# the standard rate-limit would cause false 429s for normal browsing behaviour.
_RATE_LIMIT_EXEMPT_PATHS: frozenset[str] = frozenset(
    {
        "/api/v1/simulator/session/ensure",
    }
)


async def rate_limit(request: Request) -> None:
    if not settings.RATE_LIMIT_ENABLED:
        return

    # Exempt well-known high-frequency endpoints that should never be throttled.
    request_path: str = request.url.path
    if request_path in _RATE_LIMIT_EXEMPT_PATHS:
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


async def require_participant_or_admin(
    db: AsyncSession = Depends(get_db),
    token: str | None = Depends(optional_oauth2),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> Participant | None:
    # Admin token allows calling protected endpoints without participant auth.
    if x_admin_token is not None:
        if x_admin_token != settings.ADMIN_TOKEN:
            raise ForbiddenException("Admin token required")
        return None

    if not token:
        raise UnauthorizedException("Could not validate credentials")

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
    request: Request,
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> None:
    # Strict token path (preferred)
    if x_admin_token is not None:
        if x_admin_token != settings.ADMIN_TOKEN:
            raise ForbiddenException("Admin token required")
        return

    # Dev-only convenience: allow missing token for trusted client IPs.
    if getattr(settings, "ENV", "dev") == "dev" and bool(getattr(settings, "ADMIN_DEV_MODE", False)):
        client_host = (request.client.host if request.client else None) or ""
        allow_raw = str(getattr(settings, "ADMIN_DEV_ALLOWLIST", "") or "")
        allow = {h.strip() for h in allow_raw.split(",") if h.strip()}
        if client_host and client_host in allow:
            return

    raise ForbiddenException("Admin token required")


# ---------------------------------------------------------------------------
# SimulatorActor — unified identity for simulator endpoints
# ---------------------------------------------------------------------------

_SIMULATOR_OWNER_HEADER_RE = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")


@dataclass
class SimulatorActor:
    kind: Literal["admin", "participant", "anon"]
    owner_id: str  # "admin", "pid:<sub>", "anon:<sid>", "cli:<label>"
    is_admin: bool
    participant_pid: Optional[str] = None


def _check_csrf_origin(request: Request, actor: "SimulatorActor") -> None:
    """CSRF Origin check for cookie-auth (anon) actors on state-changing requests.

    Spec §11: For cookie-auth actors, POST/PUT/PATCH/DELETE requests must include
    an Origin header that is in SIMULATOR_CSRF_ORIGIN_ALLOWLIST.
    Empty allowlist = allow all (dev mode).

    Errors use GeoException (ForbiddenException) with code E006.
    """
    if actor.kind != "anon":
        return  # CSRF only applies to cookie-auth
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return  # Safe methods are exempt

    origin = request.headers.get("origin")
    if not origin:
        raise ForbiddenException(
            "Missing Origin header for cookie-auth request",
            details={"reason": "csrf_origin"},
        )

    allowlist_raw = (settings.SIMULATOR_CSRF_ORIGIN_ALLOWLIST or "").strip()
    if not allowlist_raw:
        return  # Empty allowlist = allow all (dev mode)

    allowed = {o.strip() for o in allowlist_raw.split(",") if o.strip()}
    if origin not in allowed:
        raise ForbiddenException(
            f"Origin {origin!r} not in CSRF allowlist",
            details={"reason": "csrf_origin", "origin": origin},
        )


async def require_simulator_actor(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
    x_simulator_owner: str | None = Header(default=None, alias="X-Simulator-Owner"),
    token: str | None = Depends(optional_oauth2),
) -> SimulatorActor:
    """Resolve actor identity for simulator endpoints.

    Priority:
    1. X-Admin-Token → admin (check X-Simulator-Owner for override)
    2. Authorization: Bearer JWT → participant
    3. Cookie geo_sim_sid → anon
    4. → 401

    After resolving the actor, applies CSRF Origin check for cookie-auth (anon)
    actors on state-changing (non-safe) requests per spec §11.
    """
    from app.core.simulator.session import COOKIE_NAME, validate_session

    _actor: Optional[SimulatorActor] = None

    # 1. Admin token
    if x_admin_token is not None:
        if x_admin_token != settings.ADMIN_TOKEN:
            raise ForbiddenException("Admin token required")
        if x_simulator_owner is not None:
            # FIX-7: trim whitespace before validation (§9)
            owner_override = x_simulator_owner.strip()
            if not owner_override or not _SIMULATOR_OWNER_HEADER_RE.match(owner_override):
                raise GeoException(
                    "Invalid X-Simulator-Owner header",
                    code=ErrorCode.E009,
                    details={
                        "header": "X-Simulator-Owner",
                        "value": x_simulator_owner,
                    },
                    status_code=422,
                )
            _actor = SimulatorActor(
                kind="admin",
                owner_id=f"cli:{owner_override}",
                is_admin=True,
            )
        else:
            _actor = SimulatorActor(kind="admin", owner_id="admin", is_admin=True)

    # 2. Bearer JWT → participant
    if _actor is None and token:
        payload = await decode_token(token)
        if payload:
            pid: str | None = payload.get("sub")
            if pid:
                # Validate participant exists and is active.
                result = await db.execute(select(Participant).where(Participant.pid == pid))
                participant = result.scalar_one_or_none()
                if not participant:
                    raise UnauthorizedException("Participant not found")
                if participant.status != "active":
                    raise ForbiddenException("Participant account is not active")
                _actor = SimulatorActor(
                    kind="participant",
                    owner_id=f"pid:{pid}",
                    is_admin=False,
                    participant_pid=pid,
                )

    # 3. Cookie geo_sim_sid → anon
    if _actor is None:
        cookie_value = request.cookies.get(COOKIE_NAME)
        if cookie_value:
            session_info = validate_session(
                cookie_value,
                settings.SIMULATOR_SESSION_SECRET,
                settings.SIMULATOR_SESSION_TTL_SEC,
                settings.SIMULATOR_SESSION_CLOCK_SKEW_SEC,
            )
            if session_info:
                _actor = SimulatorActor(
                    kind="anon",
                    owner_id=session_info.owner_id,
                    is_admin=False,
                )

    # 4. No valid credentials
    if _actor is None:
        raise UnauthorizedException("No valid credentials")

    # 5. CSRF Origin check for cookie-auth actors on state-changing requests (spec §11).
    _check_csrf_origin(request, _actor)

    return _actor