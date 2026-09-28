"""Programme 023, slice (c): the renewable owner-token lease (spec decision 7; Verification plan §2 "Аренда").

`app/utils/distributed_lock.py` gets an ADDITIVE helper next to `redis_distributed_lock`, which payments and the
integrity loop keep using unchanged. The lease is per equivalent, carries an owner token, is renewed while the
runner works, and answers one question for the runner before every new cycle: `lease.lost`.

What is checked here, on an in-memory Redis double that implements exactly the three commands the helper
sends (`SET NX PX`, the compare-and-`PEXPIRE` script, the compare-and-`DEL` script) and honours key expiry on a
controlled clock:

* acquire / renew / release with the owner token; a second owner is refused while the first holds the key;
* a renewal that finds another owner's token LOSES the lease (and does not extend the stranger's key); release
  never deletes a stranger's key;
* fail-closed local expiry: with no confirmed renewal the lease is lost at `sent + TTL - margin` even while
  Redis still holds our token; a transient renewal error is not a loss by itself, the expiry is;
* the timing constraint of the 023-acceptance consultation, `renew interval + renew latency bound + margin < TTL`,
  is refused at construction when violated;
* without Redis the lease claims no distributed exclusivity (`distributed` is False) and is never "lost";
* the context manager keeps a lease alive past its TTL by renewing, and stops renewing after a loss.

RED ON A TREE WITHOUT SLICE (c): the surface lookup (`tests/p023_support.py::slice_c_surface`) ends each test on
`TargetMismatch`.
"""

from __future__ import annotations

import asyncio

import pytest

from app.utils.exceptions import ConflictException
from tests.p023_support import slice_c_surface


KEY = "dlock:clearing:P023C"


class _Clock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class _FakeRedis:
    """The three commands the lease sends, with expiry on `clock`. Anything else is an AssertionError."""

    def __init__(self, clock: _Clock) -> None:
        self.clock = clock
        self.store: dict[str, tuple[str, float]] = {}
        self.fail_eval = 0
        self.hang_eval = False
        self.evals = 0

    def _live(self, key: str):
        entry = self.store.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if self.clock() >= expires_at:
            del self.store[key]
            return None
        return value

    def ttl_ms(self, key: str) -> float | None:
        if self._live(key) is None:
            return None
        return (self.store[key][1] - self.clock()) * 1000.0

    async def set(self, key, value, *, nx=False, px=None, ex=None):
        assert nx and px is not None and ex is None, "the lease sets with NX and a millisecond TTL"
        if self._live(key) is not None:
            return None
        self.store[key] = (value, self.clock() + px / 1000.0)
        return True

    async def eval(self, script, numkeys, *args):
        self.evals += 1
        if self.hang_eval:
            await asyncio.Event().wait()
        if self.fail_eval:
            self.fail_eval -= 1
            raise ConnectionError("redis went away")
        assert numkeys == 1
        key, token = args[0], args[1]
        current = self._live(key)
        if "pexpire" in script.lower():
            if current != token:
                return 0
            self.store[key] = (current, self.clock() + int(args[2]) / 1000.0)
            return 1
        if "del" in script.lower():
            if current != token:
                return 0
            del self.store[key]
            return 1
        raise AssertionError(f"unexpected script: {script!r}")

    def steal(self, key: str, token: str = "stranger") -> None:
        self.store[key] = (token, self.clock() + 60.0)


def _lease(api, redis, clock, **kwargs):
    params = dict(ttl_seconds=30.0, renew_interval_seconds=10.0, renew_timeout_seconds=5.0, safety_margin_seconds=5.0)
    params.update(kwargs)
    return api.RenewableLease(redis, KEY, clock=clock, **params)


@pytest.mark.asyncio
async def test_acquire_holds_the_key_with_the_owner_token_and_a_millisecond_ttl() -> None:
    api = slice_c_surface()
    clock = _Clock()
    redis = _FakeRedis(clock)
    lease = _lease(api, redis, clock)
    await lease.acquire(wait_timeout_seconds=0.0)
    assert lease.distributed is True and lease.lost is False
    assert redis.store[KEY][0] == lease.token
    assert redis.ttl_ms(KEY) == pytest.approx(30_000.0)


@pytest.mark.asyncio
async def test_a_second_owner_is_refused_while_the_first_holds_the_key() -> None:
    api = slice_c_surface()
    clock = _Clock()
    redis = _FakeRedis(clock)
    first = _lease(api, redis, clock)
    await first.acquire(wait_timeout_seconds=0.0)
    second = _lease(api, redis, clock)
    with pytest.raises(ConflictException):
        await second.acquire(wait_timeout_seconds=0.0)
    assert redis.store[KEY][0] == first.token and first.lost is False
    assert first.token != second.token


@pytest.mark.asyncio
async def test_renewal_with_the_owner_token_extends_the_ttl_and_the_local_validity() -> None:
    api = slice_c_surface()
    clock = _Clock()
    redis = _FakeRedis(clock)
    lease = _lease(api, redis, clock)
    await lease.acquire(wait_timeout_seconds=0.0)
    clock.now += 20.0  # 25 s is the local validity (TTL 30 - margin 5); 20 s in, still valid
    assert lease.lost is False
    assert await lease.renew() is True
    assert redis.ttl_ms(KEY) == pytest.approx(30_000.0)
    clock.now += 20.0  # 40 s after acquire: valid only because the renewal at 20 s was confirmed
    assert lease.lost is False


@pytest.mark.asyncio
async def test_a_renewal_that_finds_another_owner_loses_the_lease_and_leaves_the_stranger_alone() -> None:
    api = slice_c_surface()
    clock = _Clock()
    redis = _FakeRedis(clock)
    lease = _lease(api, redis, clock)
    await lease.acquire(wait_timeout_seconds=0.0)
    redis.steal(KEY)
    stranger_ttl = redis.ttl_ms(KEY)
    assert await lease.renew() is False
    assert lease.lost is True
    assert redis.store[KEY][0] == "stranger" and redis.ttl_ms(KEY) == stranger_ttl
    # A lost lease stays lost: the key disappearing afterwards does not give it back.
    del redis.store[KEY]
    assert lease.lost is True
    await lease.release()
    assert KEY not in redis.store


@pytest.mark.asyncio
async def test_release_deletes_only_the_owner_token() -> None:
    api = slice_c_surface()
    clock = _Clock()
    redis = _FakeRedis(clock)
    lease = _lease(api, redis, clock)
    await lease.acquire(wait_timeout_seconds=0.0)
    redis.steal(KEY)
    await lease.release()
    assert redis.store[KEY][0] == "stranger"

    own = _FakeRedis(clock)
    held = _lease(api, own, clock)
    await held.acquire(wait_timeout_seconds=0.0)
    await held.release()
    assert KEY not in own.store


@pytest.mark.asyncio
async def test_without_a_confirmed_renewal_the_lease_is_lost_at_ttl_minus_margin_even_if_redis_still_has_it() -> None:
    api = slice_c_surface()
    clock = _Clock()
    redis = _FakeRedis(clock)
    lease = _lease(api, redis, clock)
    await lease.acquire(wait_timeout_seconds=0.0)
    clock.now += 24.9
    assert lease.lost is False
    clock.now += 0.1  # 25.0 = TTL 30 - margin 5
    assert redis.store[KEY][0] == lease.token, "control: Redis still holds our token"
    assert lease.lost is True


@pytest.mark.asyncio
async def test_a_transient_renewal_error_is_not_a_loss_but_the_expiry_still_is() -> None:
    api = slice_c_surface()
    clock = _Clock()
    redis = _FakeRedis(clock)
    lease = _lease(api, redis, clock)
    await lease.acquire(wait_timeout_seconds=0.0)
    redis.fail_eval = 1
    clock.now += 10.0
    assert await lease.renew() is False
    assert lease.lost is False, "one failed round trip is not proof that someone else owns the key"
    clock.now += 15.0  # 25 s since the last CONFIRMED validity
    assert lease.lost is True


@pytest.mark.asyncio
async def test_a_hanging_renewal_is_bounded_by_the_renew_timeout() -> None:
    api = slice_c_surface()
    clock = _Clock()
    redis = _FakeRedis(clock)
    lease = _lease(api, redis, clock, ttl_seconds=3.0, renew_interval_seconds=1.0, renew_timeout_seconds=0.05, safety_margin_seconds=1.0)
    await lease.acquire(wait_timeout_seconds=0.0)
    redis.hang_eval = True
    assert await asyncio.wait_for(lease.renew(), timeout=2.0) is False
    assert lease.lost is False


@pytest.mark.parametrize(
    "interval, timeout, margin, ok",
    [(10.0, 5.0, 5.0, True), (10.0, 10.0, 10.0, False), (20.0, 5.0, 5.0, False), (10.0, 5.0, 15.0, False)],
)
def test_interval_plus_latency_plus_margin_must_stay_below_the_ttl(interval, timeout, margin, ok) -> None:
    api = slice_c_surface()
    clock = _Clock()
    make = lambda: _lease(  # noqa: E731
        api, _FakeRedis(clock), clock, renew_interval_seconds=interval, renew_timeout_seconds=timeout, safety_margin_seconds=margin
    )
    if ok:
        make()
    else:
        with pytest.raises(ValueError):
            make()


@pytest.mark.asyncio
async def test_without_redis_the_lease_claims_no_distributed_exclusivity() -> None:
    api = slice_c_surface()
    clock = _Clock()
    lease = api.RenewableLease(None, KEY, clock=clock)
    await lease.acquire(wait_timeout_seconds=0.0)
    assert lease.distributed is False
    clock.now += 10_000.0
    assert lease.lost is False
    assert await lease.renew() is True
    await lease.release()


@pytest.mark.asyncio
async def test_the_context_manager_renews_past_the_ttl_and_releases_on_exit() -> None:
    api = slice_c_surface()
    import time

    redis = _FakeRedis(time.monotonic)
    async with api.renewable_lease(
        redis,
        KEY,
        wait_timeout_seconds=0.0,
        ttl_seconds=0.4,
        renew_interval_seconds=0.05,
        renew_timeout_seconds=0.05,
        safety_margin_seconds=0.1,
    ) as lease:
        await asyncio.sleep(1.0)  # 2.5 TTLs
        assert lease.lost is False
        assert redis.store[KEY][0] == lease.token
        assert redis.evals >= 5, "control: the renewal task actually ran"
    assert KEY not in redis.store


@pytest.mark.asyncio
async def test_the_context_manager_stops_renewing_after_a_loss() -> None:
    api = slice_c_surface()
    import time

    redis = _FakeRedis(time.monotonic)
    async with api.renewable_lease(
        redis,
        KEY,
        wait_timeout_seconds=0.0,
        ttl_seconds=0.4,
        renew_interval_seconds=0.05,
        renew_timeout_seconds=0.05,
        safety_margin_seconds=0.1,
    ) as lease:
        redis.steal(KEY)
        for _ in range(100):
            if lease.lost:
                break
            await asyncio.sleep(0.01)
        assert lease.lost is True
        evals_at_loss = redis.evals
        await asyncio.sleep(0.3)
        assert redis.evals == evals_at_loss, "a lost lease is not renewed (and never re-acquired)"
    assert redis.store[KEY][0] == "stranger"
