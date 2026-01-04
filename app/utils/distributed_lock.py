from __future__ import annotations

import asyncio
import secrets
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Optional

from app.utils.exceptions import ConflictException


_UNLOCK_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('del', KEYS[1])
else
  return 0
end
""".strip()


@asynccontextmanager
async def redis_distributed_lock(
    redis_client: Optional[Any],
    key: str,
    *,
    ttl_seconds: int = 15,
    wait_timeout_seconds: float = 2.0,
    poll_interval_seconds: float = 0.05,
) -> AsyncIterator[None]:
    """Best-effort distributed lock.

    If redis_client is None, this becomes a no-op.

    Raises ConflictException if the lock can't be acquired within wait_timeout_seconds.
    """
    if redis_client is None:
        yield
        return

    if ttl_seconds <= 0:
        raise ValueError("ttl_seconds must be positive")
    if wait_timeout_seconds < 0:
        raise ValueError("wait_timeout_seconds must be non-negative")
    if poll_interval_seconds <= 0:
        raise ValueError("poll_interval_seconds must be positive")

    token = secrets.token_urlsafe(16)
    deadline = time.monotonic() + wait_timeout_seconds

    acquired = False
    try:
        while True:
            # Redis-py: returns True if set, None/False otherwise.
            ok = await redis_client.set(key, token, nx=True, ex=int(ttl_seconds))
            if ok:
                acquired = True
                break

            if time.monotonic() >= deadline:
                raise ConflictException(
                    "Resource is busy",
                    details={
                        "lock_key": key,
                        "wait_timeout_seconds": wait_timeout_seconds,
                    },
                )

            await asyncio.sleep(poll_interval_seconds)

        yield
    finally:
        if acquired:
            try:
                await redis_client.eval(_UNLOCK_LUA, 1, key, token)
            except Exception:
                # Best-effort: don't mask the original exception.
                pass
