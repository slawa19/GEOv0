"""047 class-1 fix (2026-10-18): an integrity attempt that blocks ENDS, as a failed attempt; the loop goes on.

THE DEFECT (Codex §15 static review on the 047 branch, inherited from `main`): `_integrity_loop` awaited each attempt
without a deadline, and the Redis lock's `SET NX EX` and release `EVAL` had no bound. An attempt that never returned
left the job `running` - `/health` healthy - while detection had stopped and money moved.

WHAT IS UNDER TEST: the real `_run_integrity_checkpoints_once` and `_integrity_loop` of `app/core/maintenance_jobs.py`
and the real `redis_distributed_lock`. Replaced are only the stand's edges: the checkpoint session (a stand-in, no
database here - the database side is `tests/integration/test_p047_integrity_waits_are_bounded_postgres.py`), the
checkpoints, and the one call whose hang is the subject. Each test asserts its hang was actually reached.

EVERY TEST BOUNDS ITSELF (`asyncio.timeout`) well above the bound under test, so a removed bound fails the test
instead of hanging the tier.

MUTATIONS, each red: remove the attempt deadline (`asyncio.timeout(deadline)` in `_run_integrity_checkpoints_once`) -
the first test hits its own bound; remove the Redis call bound (`call_timeout_seconds` of the integrity lock) - the
loop test and the release test hit their own bounds / the attempt deadline.
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.core.integrity as integrity_module
import app.core.ledger.reconciliation as reconciliation_module
import app.core.maintenance_jobs as maintenance_jobs
import app.db.session as db_session_module
from app.config import settings

#: The test's own bound: far above every bound under test, far below a hung tier.
_TEST_BOUND_SECONDS = 10.0


def _app(redis=None) -> SimpleNamespace:
    return SimpleNamespace(
        state=SimpleNamespace(redis=redis, _bg_stop_event=asyncio.Event(), _bg_tasks=[], background_jobs={})
    )


class _Session:
    async def __aenter__(self):
        return SimpleNamespace(execute=AsyncMock(return_value=None))  # `bound_the_waits` on the checkpoint session

    async def __aexit__(self, *_exc) -> None:
        return None


def _stand(monkeypatch, *, reconciliation) -> None:
    monkeypatch.setattr(db_session_module, "AsyncSessionLocal", _Session)
    monkeypatch.setattr(integrity_module, "compute_and_store_integrity_checkpoints", AsyncMock(return_value=0))
    monkeypatch.setattr(reconciliation_module, "run_scheduled_reconciliation", reconciliation)


_CLEAN = {"error": 0, "hold_errors": 0}


class _SilentRedis:
    """A Redis whose calls are recorded and, where named, never answered."""

    def __init__(self, *, set_hangs: bool, eval_hangs: bool) -> None:
        self.set_hangs, self.eval_hangs = set_hangs, eval_hangs
        self.sets: list[str] = []
        self.evals: list[tuple] = []
        self.on_set = None

    async def set(self, key, token, *, nx, ex):
        self.sets.append(token)
        if self.on_set is not None:
            self.on_set(len(self.sets))
        if self.set_hangs:
            await asyncio.Event().wait()
        return True

    async def eval(self, script, numkeys, key, token):
        self.evals.append((key, token))
        if self.eval_hangs:
            await asyncio.Event().wait()
        return 1


@pytest.mark.asyncio
async def test_p047_an_attempt_past_its_deadline_ends_failed_timed_out_and_flags_every_verdict(monkeypatch) -> None:
    """A reconciliation that never returns -> the attempt ends at `INTEGRITY_ATTEMPT_TIMEOUT_SECONDS`, False,
    `periodic_timed_out`; the hung call was cancelled; every equivalent's stored verdict reads unvouched.

    MUTATION: remove the deadline - the attempt never returns, the test's own bound fails it.
    """

    reached, cancelled = [], []

    async def hangs(_factory):
        reached.append(1)
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.append(1)
            raise

    _stand(monkeypatch, reconciliation=hangs)
    monkeypatch.setattr(settings, "INTEGRITY_ATTEMPT_TIMEOUT_SECONDS", 0.2)
    app = _app()

    started = time.perf_counter()
    async with asyncio.timeout(_TEST_BOUND_SECONDS):
        completed = await maintenance_jobs._run_integrity_checkpoints_once(app, reason="periodic")
    elapsed = time.perf_counter() - started

    assert reached == [1] and cancelled == [1], "stand: the hung reconciliation was not reached, or not cancelled"
    assert completed is False, "a timed-out attempt must never read as a completed check"
    assert app.state.background_jobs["integrity"] == {
        "status": "failed", "event": "periodic_timed_out", "error_type": "TimeoutError"
    }, app.state.background_jobs
    assert elapsed < 5.0, f"the attempt ended at {elapsed:.2f}s, not at its 0.2s deadline"
    assert maintenance_jobs.debt_reconciliation_run_problem(app, "any-equivalent") is not None, (
        "a timed-out run must leave every equivalent's stored verdict unvouched"
    )


@pytest.mark.asyncio
async def test_p047_an_unanswered_redis_set_fails_the_attempt_and_the_loop_goes_on(monkeypatch) -> None:
    """`SET NX EX` never answers -> each attempt ends at the Redis call bound as `<reason>_timed_out`, our possibly
    landed token is deleted by a bounded compare-and-delete, and `_integrity_loop` runs its next attempt.

    Since the merge with the 047 cadence rule (2026-10-19): a timed-out attempt is not a completed check, so the loop
    retries it as the same `startup` attempt after `RETRY_FAST_SECONDS` (shortened here), not after the interval.

    The attempt deadline is set far above the test's own bound, so only the Redis bound can end the attempts.
    MUTATION: remove `call_timeout_seconds` from the integrity lock - the first `SET` hangs, the bound fails the test.
    """

    redis = _SilentRedis(set_hangs=True, eval_hangs=True)
    reconciled = AsyncMock(return_value=_CLEAN)
    _stand(monkeypatch, reconciliation=reconciled)
    monkeypatch.setattr(settings, "INTEGRITY_ATTEMPT_TIMEOUT_SECONDS", 600.0)
    monkeypatch.setattr(settings, "INTEGRITY_REDIS_CALL_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(maintenance_jobs, "RETRY_FAST_SECONDS", 0.01)  # the loop's own wait before the retry
    app = _app(redis)
    events: list[str] = []
    original = maintenance_jobs._record_background_job_event

    def record(app_, **kwargs):
        events.append(kwargs["event"])
        original(app_, **kwargs)

    monkeypatch.setattr(maintenance_jobs, "_record_background_job_event", record)
    redis.on_set = lambda calls: app.state._bg_stop_event.set() if calls == 2 else None

    async with asyncio.timeout(_TEST_BOUND_SECONDS):
        await maintenance_jobs._integrity_loop(app)

    assert len(redis.sets) == 2, f"the loop did not go on to a second attempt: {redis.sets}"
    assert events == ["startup_timed_out", "startup_timed_out"], events
    assert [token for _key, token in redis.evals] == redis.sets, "a SET past its bound was not followed by its delete"
    assert app.state.background_jobs["integrity"]["status"] == "failed"
    assert reconciled.await_count == 0, "no check ran without the lock, yet nothing may read as checked"


@pytest.mark.asyncio
async def test_p047_an_unanswered_release_does_not_hold_back_a_completed_check(monkeypatch, caplog) -> None:
    """The check ran and was published; the release `EVAL` never answers -> it is given up at the Redis bound (the
    key's TTL expires it) and the attempt is the completed check it was.

    MUTATION: remove `call_timeout_seconds` - the release hangs until the attempt deadline, which records the completed
    check as `periodic_timed_out`, red.
    """

    redis = _SilentRedis(set_hangs=False, eval_hangs=True)
    reconciled = AsyncMock(return_value=_CLEAN)
    _stand(monkeypatch, reconciliation=reconciled)
    monkeypatch.setattr(settings, "INTEGRITY_ATTEMPT_TIMEOUT_SECONDS", 2.0)
    monkeypatch.setattr(settings, "INTEGRITY_REDIS_CALL_TIMEOUT_SECONDS", 0.05)
    app = _app(redis)

    with caplog.at_level("WARNING"):
        async with asyncio.timeout(_TEST_BOUND_SECONDS):
            completed = await maintenance_jobs._run_integrity_checkpoints_once(app, reason="periodic")

    assert reconciled.await_count == 1 and len(redis.evals) == 1, "stand: the check or the release was not reached"
    assert completed is True and app.state.background_jobs["integrity"]["event"] == "periodic_success", (
        app.state.background_jobs
    )
    assert any("lock.release_uncertain" in message for message in caplog.messages), caplog.messages
