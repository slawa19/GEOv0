"""Regression test for Bug X: a deadlock when clearing runs while the tick's parent session
still holds an open transaction.

The scenario:
  1. The parent (tick) session writes and does NOT commit - it keeps its transaction, its row locks
     and its transaction-level advisory locks.
  2. Clearing runs in a session of its own and has to write and commit the same rows.
  3. The second session waits for what the first one holds.
  4. If the parent awaits the second session, neither can proceed - a deadlock.

The fix (cd321e3+): tick_real_mode commits the parent session BEFORE spawning the
clearing session. This test verifies the invariant by running tick_real_mode on a real database
with debts that form a clearable triangle, and asserting that clearing completes within a
reasonable timeout (no hang) and really cleared the cycle.

If someone removes the early commit, clearing waits on the parent and the test fails - measured
2026-09-24 on PostgreSQL by removing the commit in `RealTickClearingCoordinator.maybe_run_clearing`:
no `clearing.done`, red. That holds only because the stand makes the parent hold the equivalent's
owner lock when clearing is reached (see the test body); without it the same mutation stayed green.

MOVED OFF SQLITE (017 stage 3, slice S2a), deliberately rather than deleted. It was written for
SQLite's single write lock; on PostgreSQL the locks are per row and per advisory key, and a parent
session holding them while it awaits a clearing session that needs the same rows is a real risk
there too - arguably a sharper one, because the application runs on PostgreSQL. The stand is a mode-B
clone of the migrated template (`committed_database`), because the tick and the clearing open and
commit sessions of their own. The asserts are unchanged. The SQLite engine below survives only for
`test_this_modules_engine_has_the_application_sqlite_pragmas`, a test of the SQLite mechanism that
leaves with it in the deletion slice.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import threading
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.db.base import Base
from app.db.sqlite_transaction_control import install_sqlite_transaction_control
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine
from app.core.simulator.models import RunRecord
from app.core.simulator.real_runner import RealRunner
from tests.scratch_db import install_test_sqlite_pragmas, scratch_db_path, scratch_db_url

from tests.debt_setup import debt_fixture_setup
from tests.simulator_tick_stand import pooled_sessionmaker_over


# ---------------------------------------------------------------------------
# Isolated SQLite DB for this test (avoids interfering with other tests)
# ---------------------------------------------------------------------------

# T1406: this module used to build its engine from a RELATIVE path, so the database landed
# in the repository root - against AGENTS.md §7/§12, and unnoticed because the test-database
# guard validates a URL and never looks at the filesystem. `tests/scratch_db` gives it a
# directory of its own under `.local-run/test-runs/`, which also keeps concurrent sessions in
# the shared working tree from colliding.
_TEST_DB_SLUG = "simulator-clearing-no-deadlock"
_TEST_DB_PATH = str(scratch_db_path(_TEST_DB_SLUG))
_TEST_DB_URL = scratch_db_url(_TEST_DB_SLUG)


@pytest_asyncio.fixture
async def deadlock_engine():
    """Create a fresh SQLite engine + schema for the deadlock test."""
    # Clean up any leftover DB
    for suffix in ("", "-journal", "-wal", "-shm"):
        try:
            Path(_TEST_DB_PATH + suffix).unlink(missing_ok=True)
        except Exception:
            pass

    eng = create_async_engine(
        _TEST_DB_URL,
        echo=False,
        poolclass=NullPool,
        connect_args={"timeout": 5},  # short timeout to detect deadlock fast
    )
    # T1525: the same SQLite transaction control AND the same connection pragmas as the application
    # engine - WAL, foreign keys, busy timeout. This module measures DEADLOCK behaviour, and the
    # rollback journal it used to run in has different locking from the application's WAL, so
    # without the pragmas its result did not transfer at all. Held by the pragma test below.
    install_test_sqlite_pragmas(eng.sync_engine, url=_TEST_DB_URL)
    install_sqlite_transaction_control(eng.sync_engine)

    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield eng

    await eng.dispose()
    for suffix in ("", "-journal", "-wal", "-shm"):
        try:
            Path(_TEST_DB_PATH + suffix).unlink(missing_ok=True)
        except Exception:
            pass


async def test_this_modules_engine_has_the_application_sqlite_pragmas(deadlock_engine) -> None:
    """T1525: WAL and enforced foreign keys, or this module's deadlock result does not transfer.

    This module is the sharpest case of the five: it MEASURES locking behaviour. Until 2026-09-12
    its engine carried only the transaction control, so it ran in the rollback journal - where a
    reader holds a SHARED lock and blocks writers - while the application runs in WAL, where a
    reader that then writes is refused outright instead. A "no deadlock" result under one says
    nothing about the other.
    """
    from sqlalchemy import text

    async with deadlock_engine.connect() as conn:
        journal_mode = (await conn.execute(text("PRAGMA journal_mode"))).scalar_one()
        foreign_keys = (await conn.execute(text("PRAGMA foreign_keys"))).scalar_one()
    assert str(journal_mode).lower() == "wal", journal_mode
    assert int(foreign_keys) == 1, foreign_keys


@pytest_asyncio.fixture
async def deadlock_session_factory(committed_database):
    """A mode-B PostgreSQL clone, pooled like the application: the tick and the clearing commit for real."""
    async with pooled_sessionmaker_over(committed_database.url) as factory:
        yield factory


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _make_pid(name: str) -> str:
    return f"p-{name}"


def _pubkey(name: str) -> str:
    return hashlib.sha256(name.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Seed: 3 participants, 3 trustlines forming a triangle, 3 debts = clearable cycle
# ---------------------------------------------------------------------------

async def _seed_triangle(session: AsyncSession) -> tuple[str, list[str]]:
    """Seed A→B→C→A triangle of debts.  Returns (equivalent_code, [pidA, pidB, pidC])."""
    eq = Equivalent(code="UAH", is_active=True, metadata_={})
    session.add(eq)

    names = ["alice", "bob", "carol"]
    parts: list[Participant] = []
    for n in names:
        p = Participant(
            pid=_make_pid(n),
            display_name=n.title(),
            public_key=_pubkey(n),
            type="person",
            status="active",
            profile={},
        )
        session.add(p)
        parts.append(p)

    await session.flush()  # assign IDs

    # Trustlines: each trusts the next (creditor→debtor direction)
    # A trusts B, B trusts C, C trusts A  →  debts A→B, B→C, C→A
    pairs = [(0, 1), (1, 2), (2, 0)]
    for i, j in pairs:
        session.add(
            TrustLine(
                from_participant_id=parts[i].id,
                to_participant_id=parts[j].id,
                equivalent_id=eq.id,
                limit=Decimal("1000.00"),
                status="active",
                policy={"auto_clearing": True, "can_be_intermediate": True},
            )
        )

    # Debts forming a cycle: A owes B 50, B owes C 50, C owes A 50
    for i, j in pairs:
        async with debt_fixture_setup(session, label="setup"):
            session.add(
                Debt(
                    debtor_id=parts[i].id,
                    creditor_id=parts[j].id,
                    equivalent_id=eq.id,
                    amount=Decimal("50.00"),
                )
            )

    await session.commit()
    return "UAH", [p.pid for p in parts]


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_the_tick_commits_its_parent_session_before_clearing(
    deadlock_session_factory,
    monkeypatch,
) -> None:
    """tick_real_mode must commit before clearing, or clearing waits on the parent's locks.

    This test seeds a clearable triangle into a real database and runs a
    full tick_real_mode.  If the parent session is NOT committed before clearing,
    the clearing session will block on the parent's locks → deadlock → timeout → FAIL.

    Renamed from `test_clearing_does_not_deadlock_on_sqlite` when it moved to PostgreSQL.
    """
    import app.db.session as app_db_session
    import app.core.simulator.storage as simulator_storage

    # Seed test data
    async with deadlock_session_factory() as seed_session:
        eq_code, pids = await _seed_triangle(seed_session)

    # Monkey-patch the app's session factory to use our test DB
    orig_factory = app_db_session.AsyncSessionLocal
    monkeypatch.setattr(app_db_session, "AsyncSessionLocal", deadlock_session_factory)

    # Stub out storage writes (they aren't relevant to deadlock testing)
    async def _noop_write_tick_metrics(**kw):
        pass

    async def _noop_write_tick_bottlenecks(**kw):
        pass

    async def _noop_sync_artifacts(run):
        pass

    async def _noop_upsert_run(run):
        pass

    monkeypatch.setattr(simulator_storage, "write_tick_metrics", _noop_write_tick_metrics)
    monkeypatch.setattr(simulator_storage, "write_tick_bottlenecks", _noop_write_tick_bottlenecks)
    monkeypatch.setattr(simulator_storage, "sync_artifacts", _noop_sync_artifacts)
    monkeypatch.setattr(simulator_storage, "upsert_run", _noop_upsert_run)

    # Build a minimal scenario that generates at least 1 payment (to create uncommitted writes)
    scenario = {
        "equivalents": [eq_code],
        "participants": [{"id": pid} for pid in pids],
        "trustlines": [
            {"from": pids[0], "to": pids[1], "equivalent": eq_code, "limit": "1000", "status": "active"},
            {"from": pids[1], "to": pids[2], "equivalent": eq_code, "limit": "1000", "status": "active"},
            {"from": pids[2], "to": pids[0], "equivalent": eq_code, "limit": "1000", "status": "active"},
        ],
        "behaviorProfiles": [],
    }

    run = RunRecord(run_id="deadlock-test", scenario_id="s1", mode="real", state="running")
    run.seed = 42
    run.tick_index = 25  # clearing tick
    run.sim_time_ms = 25000
    run.intensity_percent = 100
    run._real_seeded = True  # already seeded above

    # Pre-load participants so runner doesn't re-seed
    async with deadlock_session_factory() as tmp:
        from sqlalchemy import select
        rows = (await tmp.execute(
            select(Participant).where(Participant.pid.in_(pids))
        )).scalars().all()
        run._real_participants = [(p.id, p.pid) for p in rows]
        run._real_equivalents = [eq_code]

    clearing_done_events: list[dict] = []

    class _DummySse:
        """Minimal SSE stub that captures clearing.done events."""
        def next_event_id(self, run: RunRecord) -> str:
            run._event_seq += 1
            return f"e{run._event_seq}"

        def broadcast(self, run_id: str, payload: dict) -> None:
            if isinstance(payload, dict) and payload.get("type") == "clearing.done":
                clearing_done_events.append(payload)

    sse = _DummySse()

    class _DummyArtifacts:
        def write_real_tick_artifact(self, *a, **kw):
            pass
        def enqueue_event_artifact(self, *a, **kw):
            pass

    runner = RealRunner(
        lock=threading.RLock(),
        get_run=lambda _: run,
        get_scenario_raw=lambda _: scenario,
        sse=sse,
        artifacts=_DummyArtifacts(),
        utc_now=_utc_now,
        publish_run_status=lambda _: None,
        db_enabled=lambda: True,
        actions_per_tick_max=3,  # small number — just need uncommitted writes
        clearing_every_n_ticks=25,
        real_max_consec_tick_failures_default=3,
        real_max_timeouts_per_tick_default=10,
        real_max_errors_total_default=50,
        logger=logging.getLogger("test_deadlock"),
    )

    # Give clearing a generous budget — the point is it should NOT deadlock
    runner._real_clearing_time_budget_ms = 5000

    # THE PARENT HOLDS WHAT CLEARING NEEDS when clearing is reached (added 2026-09-24, 017 stage 3,
    # slice S2a). Since programme 015 / P1 the money commits at its own boundary, so by the time the
    # tick reaches clearing its parent session holds nothing - and this test stayed green with the
    # early commit REMOVED, on SQLite at 8e55455 and on PostgreSQL alike (measured by that very
    # mutation). The invariant is still the tick's: whatever the parent holds when clearing is due
    # must be released first. So the stand puts the parent in Bug X's shape - an open transaction
    # holding the equivalent's owner lock, which clearing takes too - and the early commit is what
    # must release it. Without that commit clearing waits on the parent and never clears.
    from sqlalchemy import select as _select

    from app.core.payments.engine import PaymentEngine

    async with deadlock_session_factory() as tmp:
        equivalent_id = (
            await tmp.execute(_select(Equivalent.id).where(Equivalent.code == eq_code))
        ).scalar_one()
    parent_held_the_owner_lock: list[int] = []
    coordinator = runner._real_tick_clearing_coordinator
    original_maybe_run_clearing = coordinator.maybe_run_clearing

    async def _parent_holds_the_owner_lock_then_clears(**kwargs):
        await PaymentEngine(kwargs["session"])._acquire_equivalent_owner_locks({equivalent_id})
        parent_held_the_owner_lock.append(1)
        return await original_maybe_run_clearing(**kwargs)

    monkeypatch.setattr(coordinator, "maybe_run_clearing", _parent_holds_the_owner_lock_then_clears)

    # Run tick_real_mode with a timeout — if it deadlocks, asyncio.wait_for raises TimeoutError
    try:
        await asyncio.wait_for(runner.tick_real_mode("deadlock-test"), timeout=10.0)
    except asyncio.TimeoutError:
        pytest.fail(
            "tick_real_mode deadlocked! The parent session likely holds an uncommitted "
            "write transaction while clearing tries to write on a separate session. "
            "Ensure session.commit() is called BEFORE tick_real_mode_clearing()."
        )

    # Non-vacuity: the parent really held the lock when clearing was reached.
    assert parent_held_the_owner_lock == [1], parent_held_the_owner_lock

    # Verify clearing actually ran and completed (not just skipped)
    assert len(clearing_done_events) >= 1, (
        "Expected at least one clearing.done SSE event — clearing may have been skipped"
    )
    # The triangle should have been cleared
    done = clearing_done_events[0]
    assert int(done.get("cleared_cycles", 0)) >= 1, (
        f"Expected cleared_cycles >= 1, got {done}"
    )

    # Ensure we didn't leave a pending background clearing task behind.
    assert run._real_clearing_task is None
