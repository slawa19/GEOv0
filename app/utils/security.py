from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt

from app.config import settings


_redis_client = None


def set_redis_client(client) -> None:
    global _redis_client
    _redis_client = client


_revoked_jti_lock = asyncio.Lock()
_revoked_jti: dict[str, int] = {}


def _exp_to_epoch_seconds(exp: Any) -> int:
    if isinstance(exp, (int, float)):
        return int(exp)
    if isinstance(exp, datetime):
        return int(exp.replace(tzinfo=timezone.utc).timestamp())
    return 0


async def revoke_jti(jti: str, *, exp: Any) -> None:
    if not jti:
        return

    exp_epoch = _exp_to_epoch_seconds(exp)
    now_epoch = int(time.time())
    if exp_epoch and exp_epoch <= now_epoch:
        return

    if settings.REDIS_ENABLED and _redis_client is not None:
        ttl = exp_epoch - now_epoch if exp_epoch else int(settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS * 24 * 3600)
        ttl = max(1, int(ttl))
        await _redis_client.set(f"jwt:jti:revoked:{jti}", "1", ex=ttl)
        return

    async with _revoked_jti_lock:
        _revoked_jti[jti] = exp_epoch


async def is_jti_revoked(jti: str) -> bool:
    if not jti:
        return False

    if settings.REDIS_ENABLED and _redis_client is not None:
        return bool(await _redis_client.exists(f"jwt:jti:revoked:{jti}"))

    now_epoch = int(time.time())
    async with _revoked_jti_lock:
        exp_epoch = _revoked_jti.get(jti)
        if exp_epoch is None:
            return False
        if exp_epoch and exp_epoch <= now_epoch:
            _revoked_jti.pop(jti, None)
            return False
        return True


def create_access_token(subject: str | Any) -> str:
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES
    )
    payload = {
        "exp": expire,
        "sub": str(subject),
        "type": "access",
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def create_refresh_token(subject: str | Any) -> str:
    expire = datetime.now(timezone.utc) + timedelta(days=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS)
    payload = {
        "exp": expire,
        "sub": str(subject),
        "type": "refresh",
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


async def decode_token(token: str, *, expected_type: str = "access") -> dict[str, Any] | None:
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET,
            algorithms=[settings.JWT_ALGORITHM],
            options={"require": ["exp", "sub", "type"]},
        )
    except jwt.PyJWTError:
        return None

    if payload.get("type") != expected_type:
        return None

    jti = payload.get("jti")
    if isinstance(jti, str) and jti:
        if await is_jti_revoked(jti):
            return None
    return payload