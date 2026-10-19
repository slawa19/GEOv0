"""047 fix-delta 2 (2026-10-18, Codex §15 P1 twice, class 1): the daily interval starts only after a COMPLETED run.

THE DEFECTS. On `1304374b` a process killed during its run left the Redis lock for a day (`max(30, interval)`) and its
replacement skipped the start-up run and slept a day. On `34c5c8c4` the TTL was 300 s and a skipped start-up run was
retried, but only 12 times: a second orphan (Codex's schedule below) exhausted the retries and the process slept a day
with no completed check, `running` and healthy; and a run that ERRED (`False`) was not retried at all. Money kept
moving meanwhile: the money boundary reads the hold, not the age of the last reconciliation.

THE RULE UNDER TEST (`_integrity_loop`): only `_run_integrity_checkpoints_once(...) is True` - it ran, the checkpoints
committed, the reconciliation had no verifier or hold error - starts the daily interval. A skip (`None`), an error or
an incomplete run (`False`) is retried every 30 s while less than TTL + 60 s has been waited, then every TTL, for ever;
past that fast phase the job reads `failed` (`/health` degraded). Every wait honours the stop event.

THE STAND. The real loop, the real `_run_integrity_checkpoints_once` and the real `redis_distributed_lock` over a
TIME-AWARE fake Redis: `SET NX EX` creates a key that expires `ex` seconds later on a controllable clock, and the
release (`eval` of the unlock script) deletes the key only when it is unexpired AND holds the caller's token - so a
lock that never expired, or a release that ignored the token, would show in these results (the second one is
exercised by `test_a_release_after_the_lock_expired_under_a_long_run_leaves_the_new_holders_key`). The clock advances only
in the replaced `_stopped_within` (no real sleep), which also fires scheduled events (another process taking the
lock) and stops the loop at a horizon. The work under the lock is a recorder, or the real
`_run_debt_reconciliation_once` over a scripted `run_scheduled_reconciliation`; its subject is covered by 015/030.

WHAT THIS DOES NOT SEE: a real Redis, real processes, the supervisor around the loop, the time the attempts take.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.core.integrity as integrity_module
import app.core.ledger.reconciliation as reconciliation_module
import app.core.maintenance_jobs as maintenance_jobs
import app.db.session as db_session_module
from app.config import settings
from app.utils.background_jobs import background_jobs_degraded

DAY = 86400
TTL = 300
KEY = "geo:integrity:checkpoints"
#: Fast phase with the default TTL: 30 s waits while under 300 + 60 s were waited -> 12 of them.
FAST = [30] * 12


class _Clock:
    def __init__(self) -> None:
        self.now = 0


class _ExpiringRedis:
    """SET NX EX and token-checked release, on `clock`. `expires=False` is the broken fake the tests must notice."""

    def __init__(self, clock: _Clock, *, expires: bool = True) -> None:
        self.clock, self.expires = clock, expires
        self.keys: dict[str, tuple[str, int]] = {}
        self.granted: list[tuple[int, int]] = []  # (time, ex) of every SET NX that succeeded
        self.released: list[int] = []

    def _live(self, key: str):
        entry = self.keys.get(key)
        if entry is not None and (not self.expires or entry[1] > self.clock.now):
            return entry
        self.keys.pop(key, None)
        return None

    def take(self, key: str, token: str, ex: int) -> bool:
        if self._live(key) is not None:
            return False
        self.keys[key] = (token, self.clock.now + ex)
        return True

    async def set(self, key, token, *, nx, ex):
        assert nx is True
        if not self.take(key, token, ex):
            return None
        self.granted.append((self.clock.now, ex))
        return True

    async def eval(self, _script, _numkeys, key, token):
        entry = self._live(key)
        if entry is not None and entry[0] == token:
            del self.keys[key]
            self.released.append(self.clock.now)
            return 1
        return 0


class _Session:
    async def __aenter__(self):
        return SimpleNamespace(execute=AsyncMock(return_value=None))  # `bound_the_waits` on the checkpoint session

    async def __aexit__(self, *_exc) -> None:
        return None


def _stand(monkeypatch, *, redis_expires: bool = True, use_redis: bool = True, horizon: int = 3 * DAY,
           stop_at_wait: int | None = None, checkpoint_errors: int = 0, reconcile=None, during_run=None,
           checkpoint_hangs: int = 0):
    clock = _Clock()
    redis = _ExpiringRedis(clock, expires=redis_expires) if use_redis else None
    app = SimpleNamespace(state=SimpleNamespace(redis=redis, _bg_stop_event=asyncio.Event(), background_jobs={}))
    seen = SimpleNamespace(clock=clock, redis=redis, app=app, work=[], waits=[], degraded=[], events=[])
    failures = {"checkpoints": checkpoint_errors, "hangs": checkpoint_hangs}

    async def checkpoints(_session):
        seen.work.append((clock.now, "checkpoints"))
        if during_run is not None and len(seen.work) == 1:
            during_run(seen)
        if failures["hangs"] > 0:  # a blocked attempt: only the attempt deadline ends it
            failures["hangs"] -= 1
            await asyncio.Event().wait()
        if failures["checkpoints"] > 0:
            failures["checkpoints"] -= 1
            raise RuntimeError("p047: the checkpoints fail on this attempt")
        return 1

    async def reconciled(_factory, *, reason):
        seen.work.append((clock.now, reason))
        return True, None

    async def waited(stop_event, seconds):
        seen.waits.append(seconds)
        seen.degraded.append((clock.now, background_jobs_degraded(app)))
        if stop_at_wait is not None and len(seen.waits) >= stop_at_wait:
            stop_event.set()
            return True
        target = clock.now + seconds
        for at, action in sorted(seen.events, key=lambda e: e[0]):
            if clock.now < at <= target:
                clock.now = at
                action()
        clock.now = target
        if clock.now >= horizon:
            stop_event.set()
            return True
        return False

    monkeypatch.setattr(db_session_module, "AsyncSessionLocal", _Session)
    monkeypatch.setattr(integrity_module, "compute_and_store_integrity_checkpoints", checkpoints)
    if reconcile is None:
        monkeypatch.setattr(maintenance_jobs, "_run_debt_reconciliation_once", reconciled)
    else:
        monkeypatch.setattr(reconciliation_module, "run_scheduled_reconciliation", reconcile)
    monkeypatch.setattr(maintenance_jobs, "_stopped_within", waited)
    monkeypatch.setattr(settings, "INTEGRITY_CHECKPOINT_INTERVAL_SECONDS", DAY)
    monkeypatch.setattr(settings, "INTEGRITY_CHECKPOINT_LOCK_TTL_SECONDS", 0)
    return seen


def _runs(seen) -> list[int]:
    """When the work under the lock started (the checkpoints)."""

    return [at for at, what in seen.work if what == "checkpoints"]


@pytest.mark.asyncio
async def test_codex_three_process_schedule_the_survivor_checks_within_a_ttl_of_the_last_orphan(monkeypatch) -> None:
    """A took the lock at -20 and dies; B (this loop) starts at 0 and retries; A's replacement C takes the expired lock
    at 281 and dies too. C's orphan expires at 581. B must run within a TTL of that, and only then wait a day; its lock
    lives 300 s and B's release removes its own key. Health is degraded once the fast phase is over, until B ran.
    RED BEFORE (`34c5c8c4`): B gives up at 360 and first runs at 86760.
    MUTATIONS: the daily interval after a skip - red; the default TTL back to `max(30, interval)` - red; the fake made
    never to expire - B never runs, red."""

    seen = _stand(monkeypatch)
    seen.redis.keys[KEY] = ("A", 280)
    seen.events.append((281, lambda: seen.redis.take(KEY, "C", TTL)))

    await maintenance_jobs._integrity_loop(seen.app)

    assert _runs(seen), f"B never ran (waits {seen.waits[:3]}...): the lock never became free"
    first = _runs(seen)[0]
    assert 581 < first <= 581 + TTL, (_runs(seen), seen.waits)
    assert seen.redis.granted[0] == (first, TTL) and seen.redis.released[0] == first, (seen.redis.granted,
                                                                                       seen.redis.released)
    after = seen.waits[[i for i, (at, _d) in enumerate(seen.degraded) if at == first][0]]
    assert after == DAY, seen.waits
    assert [d for at, d in seen.degraded if at < 360] == [False] * 12
    assert all(d for at, d in seen.degraded if 360 <= at < first)
    assert [d for at, d in seen.degraded if at >= first] and not any(d for at, d in seen.degraded if at >= first)


@pytest.mark.asyncio
async def test_an_attempt_that_errs_is_retried_and_not_followed_by_a_day(monkeypatch) -> None:
    """The checkpoints raise on the first attempt (`False`): retried 30 s later, then the day. RED BEFORE: `[86400]`."""

    seen = _stand(monkeypatch, checkpoint_errors=1, horizon=DAY)

    await maintenance_jobs._integrity_loop(seen.app)

    assert _runs(seen) == [0, 30] and seen.waits == [30, DAY], (_runs(seen), seen.waits)
    assert seen.degraded == [(0, True), (30, False)]


@pytest.mark.asyncio
async def test_an_incomplete_reconciliation_counts_as_not_checked_retried_then_every_ttl(monkeypatch) -> None:
    """The real `_run_debt_reconciliation_once` over a reconciliation reporting a verifier error 20 times: each run is
    incomplete, so the loop retries - 12 times in the fast phase, then every TTL - and waits the day only after the
    clean run. RED BEFORE: `[86400]` after the first incomplete run."""

    outcomes = [{"error": 1, "hold_errors": 0, "errored_equivalents": ["e"]}] * 20 + [{"error": 0, "hold_errors": 0}]

    async def reconcile(_factory, **_kwargs):
        return outcomes.pop(0)

    seen = _stand(monkeypatch, reconcile=reconcile, horizon=DAY)

    await maintenance_jobs._integrity_loop(seen.app)

    assert seen.waits[:21] == FAST + [TTL] * 8 + [DAY], seen.waits
    assert seen.app.state.background_jobs["integrity"]["event"] == "startup_success"
    assert all(d for _at, d in seen.degraded[:20]) and not seen.degraded[20][1]


@pytest.mark.asyncio
async def test_a_completed_run_is_followed_by_the_daily_interval(monkeypatch) -> None:
    """Counter-check: nothing held, nothing failing - one start-up run, then one run a day."""

    seen = _stand(monkeypatch, horizon=2 * DAY)

    await maintenance_jobs._integrity_loop(seen.app)

    assert _runs(seen) == [0, DAY] and seen.waits == [DAY, DAY], (_runs(seen), seen.waits)
    assert [what for _at, what in seen.work] == ["checkpoints", "startup", "checkpoints", "periodic"]


@pytest.mark.asyncio
async def test_a_periodic_run_that_finds_the_lock_held_is_retried_too(monkeypatch) -> None:
    """At the daily run an orphan (until DAY + 100) holds the lock: the periodic run is retried every 30 s and runs at
    DAY + 120, not a day later."""

    seen = _stand(monkeypatch, horizon=2 * DAY)
    seen.events.append((DAY - 1, lambda: seen.redis.take(KEY, "orphan", 101)))

    await maintenance_jobs._integrity_loop(seen.app)

    assert _runs(seen)[:2] == [0, DAY + 120], _runs(seen)
    assert seen.waits[:6] == [DAY, 30, 30, 30, 30, DAY], seen.waits


@pytest.mark.asyncio
@pytest.mark.parametrize(("orphan_until", "stop_at_wait", "expected"), [
    (10 * DAY, 1, [30]),  # during a fast retry
    (10 * DAY, 13, FAST + [TTL]),  # during a retry every TTL
    (None, 1, [DAY]),  # during the daily interval
])
async def test_the_stop_event_during_any_wait_ends_the_loop(monkeypatch, orphan_until, stop_at_wait, expected) -> None:
    seen = _stand(monkeypatch, stop_at_wait=stop_at_wait)
    if orphan_until is not None:
        seen.redis.keys[KEY] = ("orphan", orphan_until)

    await asyncio.wait_for(maintenance_jobs._integrity_loop(seen.app), timeout=5)

    assert seen.waits == expected, seen.waits


@pytest.mark.asyncio
async def test_health_is_degraded_past_the_fast_phase_and_cleared_by_a_completed_run(monkeypatch) -> None:
    """A long orphan (a lock of a day, as `1304374b` would have left): `running` through the fast phase, then `failed`
    with `startup_pending_not_checked` (degraded; the integrity view names a pending check), retried every TTL, and
    healthy again once the run completes. RED BEFORE: `startup_skipped_locked_gave_up`, `running`, never degraded."""

    seen = _stand(monkeypatch, horizon=DAY + 2000)
    seen.redis.keys[KEY] = ("orphan", DAY - 20)

    await maintenance_jobs._integrity_loop(seen.app)

    assert [d for at, d in seen.degraded if at < 360] == [False] * 12
    pending = [(at, d) for at, d in seen.degraded if 360 <= at < _runs(seen)[0]]
    assert pending and all(d for _at, d in pending), pending
    assert _runs(seen)[0] == 360 + TTL * -(-(DAY - 20 - 360) // TTL), _runs(seen)
    assert seen.app.state.background_jobs["integrity"]["event"] == "startup_success"
    assert not background_jobs_degraded(seen.app)


@pytest.mark.asyncio
async def test_the_pending_state_names_itself_in_the_integrity_view(monkeypatch) -> None:
    seen = _stand(monkeypatch, stop_at_wait=14)
    seen.redis.keys[KEY] = ("orphan", 10 * DAY)

    await maintenance_jobs._integrity_loop(seen.app)

    assert seen.app.state.background_jobs["integrity"] == {"status": "failed", "event": "startup_pending_not_checked"}
    assert "pending" in maintenance_jobs.debt_reconciliation_run_problem(seen.app, "any")


@pytest.mark.asyncio
async def test_an_explicit_lock_ttl_stays_the_deployments_choice(monkeypatch) -> None:
    seen = _stand(monkeypatch, horizon=DAY)
    monkeypatch.setattr(settings, "INTEGRITY_CHECKPOINT_LOCK_TTL_SECONDS", 7200)

    await maintenance_jobs._integrity_loop(seen.app)

    assert seen.redis.granted == [(0, 7200)]


@pytest.mark.asyncio
async def test_a_release_after_the_lock_expired_under_a_long_run_leaves_the_new_holders_key(monkeypatch) -> None:
    """Our run takes 400 s; at 350 another process D takes the lock, expired at 300. Our release must not delete D's
    key: the unlock compares the token. MUTATION: drop the token comparison from the fake's release - D's key is
    deleted, red (the stand would then hide a release that ignores ownership)."""

    def long_run(seen) -> None:
        seen.clock.now = 350
        assert seen.redis.take(KEY, "D", TTL), "stand: our lock must have expired at 300"
        seen.clock.now = 400

    seen = _stand(monkeypatch, horizon=DAY, during_run=long_run)

    await maintenance_jobs._integrity_loop(seen.app)

    assert seen.redis.granted[0] == (0, TTL) and seen.redis.released == [], (seen.redis.granted, seen.redis.released)
    assert seen.redis.keys.get(KEY) == ("D", 350 + TTL), seen.redis.keys


@pytest.mark.parametrize("configured", [-5, 0])
def test_a_non_positive_interval_reads_as_300_s(configured) -> None:
    """Codex §15 P3: a negative interval was the loop's wait as is. Not positive -> 300 s, as an explicit 0 already
    meant. RED BEFORE (`fb05cd6d`): -5 stayed -5. Counter-check: a positive value is kept."""

    from app.config import Settings

    assert Settings(INTEGRITY_CHECKPOINT_INTERVAL_SECONDS=configured).INTEGRITY_CHECKPOINT_INTERVAL_SECONDS == 300
    assert Settings(INTEGRITY_CHECKPOINT_INTERVAL_SECONDS=86400).INTEGRITY_CHECKPOINT_INTERVAL_SECONDS == 86400


@pytest.mark.asyncio
@pytest.mark.parametrize("seconds", [0, -5])
async def test_the_stop_is_heard_by_a_wait_of_zero_or_less(seconds) -> None:
    """The real `_stopped_within`: with the stop set, a wait of zero or less reports the stop. RED BEFORE
    (`fb05cd6d`): `wait_for(timeout<=0)` cancelled the wait first and reported a timeout (False). Counter-check: an
    unset stop with a zero wait is a timeout."""

    stop = asyncio.Event()
    assert await maintenance_jobs._stopped_within(stop, seconds) is False
    stop.set()
    assert await maintenance_jobs._stopped_within(stop, seconds) is True


@pytest.mark.asyncio
async def test_an_attempt_that_times_out_is_retried_fast_then_every_ttl_and_degrades_health(monkeypatch) -> None:
    """The combination of the attempt deadline (`INTEGRITY_ATTEMPT_TIMEOUT_SECONDS`, PR #309) and the cadence rule:
    the checkpoints block 14 times, each attempt ends at the (real, shortened) deadline as `startup_timed_out` - a
    failure, never a completed check - and is retried 30 s later in the fast phase, then every TTL, the job `failed`
    (degraded) throughout; the 15th attempt completes and only then does the loop wait a day.
    MUTATION: the deadline's handler returns True - the loop waits a day after the first timed-out attempt, red."""

    seen = _stand(monkeypatch, checkpoint_hangs=14, horizon=DAY)
    monkeypatch.setattr(settings, "INTEGRITY_ATTEMPT_TIMEOUT_SECONDS", 0.01)

    async with asyncio.timeout(30):
        await maintenance_jobs._integrity_loop(seen.app)

    assert seen.waits[:15] == FAST + [TTL] * 2 + [DAY], seen.waits
    assert all(d for _at, d in seen.degraded[:14]) and not seen.degraded[14][1], seen.degraded[:15]
    assert seen.app.state.background_jobs["integrity"]["event"] == "startup_success"
