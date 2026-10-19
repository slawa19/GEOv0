"""047 class-1 fix (2026-10-18): a lock wait of the scheduled reconciliation ENDS, and costs one equivalent only.

THE DEFECT (Codex §15 static review on the 047 branch, inherited from `main`; confirmed by grep - no `lock_timeout` or
`statement_timeout` on the reconciliation's sessions, and the loop awaited the attempt without a deadline): another
open transaction holding the latest reconciliation result of one equivalent blocked `record_outcome` forever.
Equivalents are verified one after another, so a REAL divergence in a later equivalent was never reached and never
held, the job stayed `running`, `/health` healthy, and money moved.

THE DESIGN UNDER TEST - fail the equivalent and CONTINUE, so one held row hides no other equivalent's divergence:
`bound_the_waits` (`SET LOCAL lock_timeout`/`statement_timeout` on each transaction of the run) turns the wait into
SQLSTATE 55P03 - an error of that equivalent alone, no result row, the run not completed - and the next equivalent is
verified and, on a confirmed FAILED, held. Past everything, the attempt deadline ends what is left, with no half-state.

THE PATH IS THE REAL ONE: the scheduled host `_run_integrity_checkpoints_once`, the real verifier, the real reaction,
a genuine row lock held by a second session on a disposable clone. The bounds are shortened through `settings` only.

MUTATIONS, each red: remove `set_config('lock_timeout', ...)` from `bound_the_waits` - the first test ends at the
attempt deadline, `periodic_timed_out`, the later divergence never held; remove the per-equivalent `continue`
(let the error leave `run_scheduled_reconciliation`) - the later divergence is never verified; remove the attempt
deadline - the second test hits its own bound.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid

import pytest
from sqlalchemy import select, text

from app.config import settings
from app.core.ledger.reconciliation import FAILED, PASSED
from app.db.reconciliation_tables import debt_reconciliation_results
from tests.integration.test_p024_integrity_status_sees_hold_postgres import _run_the_host
from tests.integration.test_p030_s1_detection_postgres import _baselined, _latest
from tests.tier_on_a_clone import tier_on_a_clone  # noqa: F401 - opt-in fixture
from tests.unit.test_p015_step5c_reaction_and_hold import _faulty_triangle, _hold_of

#: The test's own bound: far above every bound under test, so a removed bound fails instead of hanging the tier.
_TEST_BOUND_SECONDS = 60.0


async def _two_equivalents(factory, monkeypatch):
    """`A...` - baselined, PASSED once by the real host - and, AFTER it in the run's order (by code), a `C6...`
    equivalent with one atom moved around the application: a real divergence the run must hold."""

    held_code = f"A47{uuid.uuid4().hex[:6].upper()}"
    blocked = await _baselined(factory, held_code)
    first = await _run_the_host(monkeypatch, factory)
    assert first.completed is True, f"stand: the clean first run did not complete: {first.state.background_jobs}"
    passed = await _latest(factory, blocked)
    assert passed is not None and passed.status == PASSED, passed
    faulty, _debt = await _faulty_triangle(factory)
    assert faulty.equivalent.code > held_code, "stand: the divergent equivalent must come AFTER the blocked one"
    return blocked, passed, faulty.equivalent.id


async def _hold_the_latest_result(factory, equivalent_id):
    """A second session's open transaction with the equivalent's latest result row `FOR UPDATE`."""

    blocker = factory()
    c = debt_reconciliation_results.c
    held = (await blocker.execute(
        select(c.id).where(c.equivalent_id == equivalent_id, c.is_latest.is_(True)).with_for_update()
    )).scalar_one()
    return blocker, held


async def _waiting_on_a_lock(factory, *, besides: int) -> list:
    """Backends of this database waiting on a lock, other than `besides` (the blocker's own pid)."""

    async with factory() as probe:
        return list((await probe.execute(text(
            "SELECT pid, left(query, 80) FROM pg_stat_activity WHERE datname = current_database() "
            "AND wait_event_type = 'Lock' AND pid <> :besides"
        ), {"besides": besides})).all())


async def _observe_a_waiter_blocked_by(factory, blocker_pid: int, *, until: asyncio.Task) -> list:
    """Poll `pg_stat_activity` while `until` runs for a backend waiting on a lock that `blocker_pid` holds.

    A bounded poll, not a sleep standing in for an event: the server offers no notification of a lock wait."""

    async with factory() as probe:
        while not until.done():
            rows = list((await probe.execute(text(
                "SELECT pid FROM pg_stat_activity WHERE datname = current_database() AND wait_event_type = 'Lock' "
                "AND :blocker = ANY(pg_blocking_pids(pid))"
            ), {"blocker": blocker_pid})).scalars())
            await probe.rollback()
            if rows:
                return rows
            await asyncio.sleep(0.02)
    return []


async def _nowait_lock_refusal(factory, row_id) -> str | None:
    """The SQLSTATE that refuses a third transaction's `FOR UPDATE NOWAIT` on the row, or None if it got the lock."""

    from sqlalchemy.exc import DBAPIError

    from app.db.sqlstate import sqlstate

    async with factory() as third:
        try:
            await third.execute(select(debt_reconciliation_results.c.id).where(
                debt_reconciliation_results.c.id == row_id).with_for_update(nowait=True))
        except DBAPIError as error:
            return sqlstate(error)
        finally:
            await third.rollback()
    return None


@pytest.mark.asyncio
@pytest.mark.usefixtures("tier_on_a_clone")
async def test_p047_a_held_result_row_costs_its_equivalent_only_and_a_later_divergence_is_still_held(
    monkeypatch, caplog
) -> None:
    from tests.conftest import TestingSessionLocal as factory

    blocked, passed, divergent = await _two_equivalents(factory, monkeypatch)
    monkeypatch.setattr(settings, "INTEGRITY_DB_LOCK_TIMEOUT_MS", 300)
    monkeypatch.setattr(settings, "INTEGRITY_ATTEMPT_TIMEOUT_SECONDS", 15.0)

    blocker, held_row = await _hold_the_latest_result(factory, blocked)
    try:
        started = time.perf_counter()
        with caplog.at_level(logging.ERROR):
            async with asyncio.timeout(_TEST_BOUND_SECONDS):
                app = await _run_the_host(monkeypatch, factory)
        elapsed = time.perf_counter() - started

        # The blocker is untouched: its transaction is alive and still holds the row.
        assert (await blocker.execute(select(debt_reconciliation_results.c.id).where(
            debt_reconciliation_results.c.id == held_row))).scalar_one() == held_row
    finally:
        await blocker.rollback()
        await blocker.close()

    # The LATER equivalent was verified in the same run, its divergence confirmed and held.
    later = await _latest(factory, divergent)
    hold = await _hold_of(factory, divergent)
    assert later is not None and later.status == FAILED, later
    assert hold is not None, "a held row of another equivalent kept the divergence from being held"

    state = app.state.background_jobs["integrity"]
    assert app.completed is False, "a run with an unverified equivalent must not read as completed"
    assert state == {
        "status": "failed", "event": "periodic_debt_reconciliation_error", "errored_equivalents": str(blocked)
    }, state
    assert 0.3 <= elapsed < 10.0, f"the wait was ended by the attempt deadline, not by lock_timeout ({elapsed:.1f}s)"
    assert any(
        f"debt_reconciliation.error equivalent_id={blocked} sqlstate=55P03" in message for message in caplog.messages
    ), caplog.messages

    # The blocked equivalent: no write landed, nothing held, its old verdict stays (and the run flags it).
    assert await _latest(factory, blocked) == passed
    assert await _hold_of(factory, blocked) is None


@pytest.mark.asyncio
@pytest.mark.usefixtures("tier_on_a_clone")
async def test_p047_the_attempt_deadline_ends_a_real_lock_wait_and_leaves_no_half_state(monkeypatch) -> None:
    """The lock wait outlives the deadline (lock_timeout set far above it) -> `periodic_timed_out`, False; the
    cancelled wait is gone from the server, nothing of the blocked equivalent was written, the blocker is untouched,
    and the pool serves the next session.

    ANTI-VACUUM (BACKLOG 047-4, fixed 2026-10-19): before the deadline a backend of the hub is OBSERVED waiting on a
    lock (`wait_event_type = 'Lock'`) whose blocker is exactly the holding session (`pg_blocking_pids`), so the
    deadline ended THAT wait and not something else; afterwards the blocker is the same transaction and still owns
    the row - a third transaction's `FOR UPDATE NOWAIT` is refused with 55P03. MUTATION: commit the blocker before the
    hub runs - no waiter is ever observed, red."""

    from tests.conftest import TestingSessionLocal as factory

    blocked, passed, divergent = await _two_equivalents(factory, monkeypatch)
    monkeypatch.setattr(settings, "INTEGRITY_DB_LOCK_TIMEOUT_MS", 600_000)
    monkeypatch.setattr(settings, "INTEGRITY_ATTEMPT_TIMEOUT_SECONDS", 1.5)

    blocker, held_row = await _hold_the_latest_result(factory, blocked)
    blocker_pid = (await blocker.execute(text("SELECT pg_backend_pid()"))).scalar_one()
    blocker_xact = (await blocker.execute(text("SELECT txid_current()"))).scalar_one()
    try:
        started = time.perf_counter()
        async with asyncio.timeout(_TEST_BOUND_SECONDS):
            host = asyncio.create_task(_run_the_host(monkeypatch, factory))
            waiters = await _observe_a_waiter_blocked_by(factory, blocker_pid, until=host)
            app = await host
        elapsed = time.perf_counter() - started

        assert waiters, "no backend of the hub was ever seen waiting on the blocker's lock: the deadline ended no wait"
        assert (await blocker.execute(text("SELECT txid_current()"))).scalar_one() == blocker_xact
        assert await _nowait_lock_refusal(factory, held_row) == "55P03", "the blocker no longer owns the row"

        assert app.completed is False
        assert app.state.background_jobs["integrity"] == {
            "status": "failed", "event": "periodic_timed_out", "error_type": "TimeoutError"
        }, app.state.background_jobs
        assert elapsed < 10.0, elapsed
        assert await _waiting_on_a_lock(factory, besides=blocker_pid) == [], "the cancelled wait is still on the server"
        assert (await blocker.execute(select(debt_reconciliation_results.c.id).where(
            debt_reconciliation_results.c.id == held_row))).scalar_one() == held_row
    finally:
        await blocker.rollback()
        await blocker.close()

    assert await _latest(factory, blocked) == passed, "a write of the cancelled attempt landed"
    assert await _hold_of(factory, blocked) is None
    # Not reached in the cancelled attempt: no verdict was invented for it either way.
    assert await _latest(factory, divergent) is None and await _hold_of(factory, divergent) is None
