"""047 fix-delta (2026-10-18, Codex §15 P1, class 1): a killed run's lock no longer silences the start-up run for a day.

THE DEFECT, on `1304374b`: with `INTEGRITY_CHECKPOINT_LOCK_TTL_SECONDS` unset the Redis lock lived `max(30, interval)`
- a day at the daily default. A process killed during its run left the lock; its replacement's start-up run found it
held (`startup_skipped_locked`), and `_integrity_loop` then waited a whole interval. Money kept moving meanwhile: the
money boundary reads the hold, not the age of the last reconciliation.

THE PATH: the real `_integrity_loop` and the real `_run_integrity_checkpoints_once` with its real lock
(`redis_distributed_lock`) over a fake Redis that holds the key for a number of attempts, as a killed run's key does
until its TTL. The work under the lock (checkpoints and reconciliation) is replaced by a recorder: its subject is
covered by the 015/030 tests. Time is not slept: `_stopped_within` is replaced by a recorder of the waits asked for.

WHAT THIS DOES NOT SEE: a real Redis and its expiry, two real processes, the supervisor around the loop.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import app.core.integrity as integrity_module
import app.core.maintenance_jobs as maintenance_jobs
import app.db.session as db_session_module
from app.config import settings

DAY = 86400


class _HeldKeyRedis:
    """A Redis whose key is already held for the first `held_for` SET NX attempts, as a killed run leaves it."""

    def __init__(self, held_for: int) -> None:
        self.held_for = held_for
        self.attempts = 0
        self.granted_ttls: list[int] = []

    async def set(self, key, token, *, nx, ex):
        assert nx is True
        self.attempts += 1
        if self.attempts <= self.held_for:
            return None
        self.granted_ttls.append(ex)
        return True

    async def eval(self, *_args):
        return 1


class _Session:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, *_exc) -> None:
        return None


def _stand(monkeypatch, *, redis, stop_after_waits: int | None = None, stop_on: float | None = None):
    """The app, the record of the work done under the lock and of the waits the loop asked for."""

    app = SimpleNamespace(state=SimpleNamespace(redis=redis, _bg_stop_event=asyncio.Event(), background_jobs={}))
    seen = SimpleNamespace(work=[], waits=[])

    async def checkpoints(_session):
        seen.work.append("checkpoints")
        return 1

    async def reconcile(_factory, *, reason):
        seen.work.append(reason)
        return True, None

    async def waited(stop_event, seconds):
        seen.waits.append(seconds)
        if (stop_on is not None and seconds == stop_on) or (
            stop_after_waits is not None and len(seen.waits) >= stop_after_waits
        ):
            stop_event.set()
            return True
        return False

    monkeypatch.setattr(db_session_module, "AsyncSessionLocal", _Session)
    monkeypatch.setattr(integrity_module, "compute_and_store_integrity_checkpoints", checkpoints)
    monkeypatch.setattr(maintenance_jobs, "_run_debt_reconciliation_once", reconcile)
    monkeypatch.setattr(maintenance_jobs, "_stopped_within", waited)
    monkeypatch.setattr(settings, "INTEGRITY_CHECKPOINT_INTERVAL_SECONDS", DAY)
    monkeypatch.setattr(settings, "INTEGRITY_CHECKPOINT_LOCK_TTL_SECONDS", 0)
    return app, seen


@pytest.mark.asyncio
async def test_a_start_up_run_that_finds_the_lock_held_is_retried_and_runs_when_it_is_free(monkeypatch) -> None:
    """The key is held for two attempts: the start-up run is retried after 30 s twice and then RUNS, long before the
    interval; the lock it takes lives 300 s, not a day. Then the loop waits the interval.
    RED BEFORE (`1304374b`): no retry - waits `[86400]`, no work at start-up, TTL 86400.
    MUTATIONS: drop the retry - red; the default TTL back to `max(30, interval)` - granted TTL 86400, red."""

    redis = _HeldKeyRedis(held_for=2)
    app, seen = _stand(monkeypatch, redis=redis, stop_on=DAY)

    await maintenance_jobs._integrity_loop(app)

    assert seen.work == ["checkpoints", "startup"], seen.work
    assert seen.waits == [30, 30, DAY], seen.waits
    assert redis.attempts == 3 and redis.granted_ttls == [300], (redis.attempts, redis.granted_ttls)
    assert app.state.background_jobs["integrity"]["event"] == "startup_success"


@pytest.mark.asyncio
async def test_the_stop_event_during_the_retry_wait_ends_the_loop(monkeypatch) -> None:
    """Stop requested while the loop waits to retry a locked start-up run: the loop returns, nothing more runs."""

    redis = _HeldKeyRedis(held_for=10**6)
    app, seen = _stand(monkeypatch, redis=redis, stop_after_waits=1)

    await asyncio.wait_for(maintenance_jobs._integrity_loop(app), timeout=5)

    assert seen.waits == [30] and seen.work == [] and redis.attempts == 1, (seen.waits, seen.work, redis.attempts)


@pytest.mark.asyncio
async def test_without_a_lock_the_start_up_runs_once_and_the_loop_waits_the_interval(monkeypatch) -> None:
    """Counter-check: no Redis (the lock is a no-op) - one start-up run, no retry, then the daily wait, then a periodic
    run after it."""

    app, seen = _stand(monkeypatch, redis=None, stop_after_waits=2)

    await maintenance_jobs._integrity_loop(app)

    assert seen.work == ["checkpoints", "startup", "checkpoints", "periodic"], seen.work
    assert seen.waits == [DAY, DAY], seen.waits


@pytest.mark.asyncio
async def test_a_lock_held_past_its_ttl_and_the_margin_gives_up_into_the_interval_with_an_event(monkeypatch) -> None:
    """Bounded: a key that stays held (another live holder) is retried ceil((300 + 60) / 30) = 12 times, then the loop
    records `startup_skipped_locked_gave_up` and waits the interval - it does not retry for ever."""

    redis = _HeldKeyRedis(held_for=10**6)
    app, seen = _stand(monkeypatch, redis=redis, stop_on=DAY)

    await maintenance_jobs._integrity_loop(app)

    assert seen.waits == [30] * 12 + [DAY], seen.waits
    assert seen.work == [] and redis.attempts == 13
    assert app.state.background_jobs["integrity"]["event"] == "startup_skipped_locked_gave_up"


@pytest.mark.asyncio
async def test_an_explicit_lock_ttl_stays_the_deployments_choice(monkeypatch) -> None:
    """`INTEGRITY_CHECKPOINT_LOCK_TTL_SECONDS` set explicitly is the TTL taken, whatever the default."""

    redis = _HeldKeyRedis(held_for=0)
    app, _seen = _stand(monkeypatch, redis=redis, stop_on=DAY)
    monkeypatch.setattr(settings, "INTEGRITY_CHECKPOINT_LOCK_TTL_SECONDS", 7200)

    await maintenance_jobs._integrity_loop(app)

    assert redis.granted_ttls == [7200]
