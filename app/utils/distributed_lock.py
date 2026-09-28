from __future__ import annotations

import asyncio
import logging
import secrets
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Callable, Optional

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


# --------------------------------------------------------------------------------------------------------------
# Programme 023 slice (c), decision 7: a RENEWABLE owner-token lease. Additive: `redis_distributed_lock` above is
# shared with payments and the integrity loop and keeps its semantics (fixed TTL, no renewal, no-op without Redis).

_RENEW_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('pexpire', KEYS[1], ARGV[2])
else
  return 0
end
""".strip()

_lease_logger = logging.getLogger(__name__)


class RenewableLease:
    """One owner's lease on `key`: acquired with a random owner token, renewed only while the token is still
    ours, released only if it is still ours.

    `lost` is what a holder asks before starting new work. It is True once a renewal found another owner's
    token (terminal: a lost lease is never re-acquired behind the holder's back), and ALSO once the last
    CONFIRMED validity has run out - `send time of the last successful SET/PEXPIRE + TTL - margin`, on `clock` -
    even if Redis still holds our token. That is fail-closed: a renewal that failed transiently, hung, or never
    ran because the loop was starved is not proof of ownership. The timing constraint of the 023-acceptance
    consultation is checked at construction: `renew interval + renew timeout (the latency bound of one
    renewal) + safety margin < TTL`.

    Without Redis (`redis_client is None`) the lease is a local no-op: `distributed` is False, it is never
    `lost`, and it claims no exclusivity across processes (spec decision 7: without Redis the automatic runner
    does not claim distributed uniqueness). Money is protected by the 019 boundary either way.
    """

    def __init__(
        self,
        redis_client: Optional[Any],
        key: str,
        *,
        ttl_seconds: float = 30.0,
        renew_interval_seconds: float = 10.0,
        renew_timeout_seconds: float = 5.0,
        safety_margin_seconds: float = 5.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if min(ttl_seconds, renew_interval_seconds, renew_timeout_seconds) <= 0 or safety_margin_seconds < 0:
            raise ValueError("lease durations must be positive (the margin non-negative)")
        if renew_interval_seconds + renew_timeout_seconds + safety_margin_seconds >= ttl_seconds:
            raise ValueError(
                "renew interval + renew timeout + safety margin must stay below the lease TTL "
                f"({renew_interval_seconds} + {renew_timeout_seconds} + {safety_margin_seconds} >= {ttl_seconds})"
            )
        self._redis = redis_client
        self.key = key
        self.token = secrets.token_urlsafe(16)
        self.ttl_seconds = float(ttl_seconds)
        self.renew_interval_seconds = float(renew_interval_seconds)
        self.renew_timeout_seconds = float(renew_timeout_seconds)
        self.safety_margin_seconds = float(safety_margin_seconds)
        self._clock = clock
        self._acquired = False
        self._stolen = False
        self._valid_until = float("-inf")

    @property
    def distributed(self) -> bool:
        return self._redis is not None

    @property
    def lost(self) -> bool:
        if self._redis is None:
            return False
        return self._stolen or not self._acquired or self._clock() >= self._valid_until

    def _confirm(self, sent_at: float) -> None:
        self._valid_until = sent_at + self.ttl_seconds - self.safety_margin_seconds

    async def acquire(self, *, wait_timeout_seconds: float, poll_interval_seconds: float = 0.05) -> None:
        """Take the key or raise `ConflictException` after `wait_timeout_seconds` (0 = one try).

        Every `SET` is bounded (review P2-3): it may take at most what is left of the wait budget plus one round
        trip's latency bound (`renew_timeout_seconds`). A `SET` that timed out may still have landed with our token:
        that is not ownership (the lease stays not acquired); our token is removed by one bounded compare-and-delete
        (another `renew_timeout_seconds` at most), else its TTL expires it. So the worst case with an unresponsive
        Redis is about `wait_timeout_seconds + 2 * renew_timeout_seconds`: bounded, but not the SET allowance alone.
        """

        if self._redis is None:
            self._acquired = True
            return
        if wait_timeout_seconds < 0 or poll_interval_seconds <= 0:
            raise ValueError("wait_timeout_seconds must be non-negative, poll_interval_seconds positive")
        deadline = self._clock() + wait_timeout_seconds
        while True:
            sent_at = self._clock()
            try:
                async with asyncio.timeout(max(0.0, deadline - sent_at) + self.renew_timeout_seconds):
                    ok = await self._redis.set(self.key, self.token, nx=True, px=int(self.ttl_seconds * 1000))
            except TimeoutError:
                _lease_logger.warning("event=lease.acquire_timeout key=%s", self.key)
                await self._delete_own_token(event="acquire_uncertain")
                raise ConflictException(
                    "Resource is busy",
                    details={"lock_key": self.key, "wait_timeout_seconds": wait_timeout_seconds, "reason": "timeout"},
                )
            if ok:
                self._acquired = True
                self._confirm(sent_at)
                return
            if self._clock() >= deadline:
                raise ConflictException(
                    "Resource is busy",
                    details={"lock_key": self.key, "wait_timeout_seconds": wait_timeout_seconds},
                )
            await asyncio.sleep(poll_interval_seconds)

    async def renew(self) -> bool:
        """One renewal, bounded by `renew_timeout_seconds`. True only when Redis confirmed OUR token."""

        if self._redis is None:
            return True
        if self._stolen or not self._acquired:
            return False
        sent_at = self._clock()
        try:
            # `asyncio.timeout`, not `wait_for`: on 3.11 `wait_for` can swallow a cancellation that races the inner
            # call's completion, and the renewal task would then outlive its lease (measured: a hung test).
            async with asyncio.timeout(self.renew_timeout_seconds):
                renewed = await self._redis.eval(_RENEW_LUA, 1, self.key, self.token, int(self.ttl_seconds * 1000))
        except TimeoutError:
            _lease_logger.warning("event=lease.renew_timeout key=%s", self.key)
            return False
        except Exception as exc:  # noqa: BLE001 - a failed round trip proves nothing; the local expiry decides
            _lease_logger.warning("event=lease.renew_failed key=%s error=%s", self.key, type(exc).__name__)
            return False
        if renewed:
            self._confirm(sent_at)
            return True
        self._stolen = True
        _lease_logger.warning("event=lease.lost_to_another_owner key=%s", self.key)
        return False

    async def release(self) -> None:
        """Compare-and-delete our token, bounded by `renew_timeout_seconds` (review P2-3): an unresponsive Redis
        must not hold back a result already computed, or shutdown. An uncertain release relies on the TTL."""

        if self._redis is None or not self._acquired:
            return
        self._acquired = False
        await self._delete_own_token(event="release_uncertain")

    async def _delete_own_token(self, *, event: str) -> None:
        try:
            async with asyncio.timeout(self.renew_timeout_seconds):
                await self._redis.eval(_UNLOCK_LUA, 1, self.key, self.token)
        except Exception as exc:  # noqa: BLE001 - timeout or transport error alike: the key's TTL expires it
            _lease_logger.warning(
                "event=lease.%s key=%s error=%s relying_on_ttl_seconds=%s",
                event,
                self.key,
                type(exc).__name__,
                self.ttl_seconds,
            )

    async def _renew_until_lost(self) -> None:
        while not self._stolen and self._acquired:
            await asyncio.sleep(self.renew_interval_seconds)
            await self.renew()


@asynccontextmanager
async def renewable_lease(
    redis_client: Optional[Any],
    key: str,
    *,
    wait_timeout_seconds: float,
    poll_interval_seconds: float = 0.05,
    **lease_kwargs: Any,
) -> AsyncIterator[RenewableLease]:
    """Acquire a `RenewableLease`, renew it in the background while the block runs, release it on exit.

    The block checks `lease.lost` before starting new work; the helper does not cancel the block on a loss -
    work already in flight is finished by its owner (spec decision 7).
    """

    lease = RenewableLease(redis_client, key, **lease_kwargs)
    await lease.acquire(wait_timeout_seconds=wait_timeout_seconds, poll_interval_seconds=poll_interval_seconds)
    renewer: asyncio.Task | None = None
    if lease.distributed:
        renewer = asyncio.create_task(lease._renew_until_lost(), name=f"geo:lease:{key}")
    try:
        yield lease
    finally:
        if renewer is not None:
            renewer.cancel()
            try:
                await renewer
            except asyncio.CancelledError:
                pass
        await lease.release()
