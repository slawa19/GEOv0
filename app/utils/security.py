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
# 029 `F-029-31`: drawn once per process. The memory store above dies with the process, so a refresh token is
# honoured only by the process that issued it - a used one cannot come back to life after a restart.
_process_start_marker = uuid.uuid4().hex
_REFRESH_STORE_ID_KEY = "geo:refresh_store_id"


def _revocations_live_in_redis() -> bool:
    return bool(settings.REDIS_ENABLED and _redis_client is not None)


async def refresh_store_id() -> str:
    """The `rsm` claim every refresh token carries and must present: the id of the store that will know it was used.

    In memory (also with Redis enabled but no client) - this process's marker. With Redis - an id kept IN Redis,
    drawn once by `SET NX` and shared by every worker: a Redis recreated empty has a new id, so the tokens whose
    revocations it lost are refused instead of revived. A token without the claim (issued before 029) is refused
    by both. Detects a replay by an outside holder of a token; no barrier against code inside the process.
    """
    if not _revocations_live_in_redis():
        return _process_start_marker
    await _redis_client.set(_REFRESH_STORE_ID_KEY, uuid.uuid4().hex, nx=True)
    stored = await _redis_client.get(_REFRESH_STORE_ID_KEY)
    if not stored:  # lost between the two commands: refuse loudly rather than issue or accept on a guess
        raise RuntimeError("Redis did not return the refresh store id")
    return stored.decode() if isinstance(stored, bytes) else str(stored)


def _exp_to_epoch_seconds(exp: Any) -> int:
    if isinstance(exp, (int, float)):
        return int(exp)
    if isinstance(exp, datetime):
        return int(exp.replace(tzinfo=timezone.utc).timestamp())
    return 0


async def claim_jti(jti: str, *, exp: Any) -> bool:
    """Revoke `jti` only if it is not revoked yet; True for the one caller that revoked it (028 `F-028-22`).

    A refresh token is used once: of two requests presenting it at the same time exactly one claims it - `SET NX`
    in Redis, the check and the write under one lock in memory. The in-memory store is per process and bounded:
    expired entries leave it on every write (029 `F-029-1`); for restarts see `refresh_store_id`.
    """

    exp_epoch = _exp_to_epoch_seconds(exp)
    now_epoch = int(time.time())
    if not jti or (exp_epoch and exp_epoch <= now_epoch):
        return False
    if _revocations_live_in_redis():
        ttl = exp_epoch - now_epoch if exp_epoch else int(settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS * 24 * 3600)
        return bool(await _redis_client.set(f"jwt:jti:revoked:{jti}", "1", ex=max(1, int(ttl)), nx=True))
    async with _revoked_jti_lock:
        known = _revoked_jti.get(jti)
        if known is not None and (not known or known > now_epoch):
            return False
        for stale in [key for key, until in _revoked_jti.items() if until and until <= now_epoch]:
            del _revoked_jti[stale]
        _revoked_jti[jti] = exp_epoch
        return True


async def is_jti_revoked(jti: str) -> bool:
    if not jti:
        return False

    if _revocations_live_in_redis():
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


async def create_refresh_token(subject: str | Any) -> str:
    expire = datetime.now(timezone.utc) + timedelta(days=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS)
    payload = {
        "exp": expire,
        "sub": str(subject),
        "type": "refresh",
        "jti": uuid.uuid4().hex,
        "rsm": await refresh_store_id(),
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