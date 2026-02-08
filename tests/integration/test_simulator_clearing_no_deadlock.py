"""Regression test for Bug X: SQLite deadlock when clearing runs concurrently
with an uncommitted parent session.

The scenario:
  1. Parent session writes data (INSERT) but does NOT commit — holds the SQLite write lock.
  2. A second (clearing) session tries to write + commit.
  3. On SQLite (single-writer), session 2 blocks waiting for the write lock.
  4. If the parent awaits session 2 → classic deadlock.

The fix (cd321e3+): tick_real_mode commits the parent session BEFORE spawning the
clearing session.  This test verifies the invariant by running tick_real_mode on a
real SQLite database with debts that form a clearable triangle, and asserting that
clearing completes within a reasonable timeout (no hang).

If someone removes the early commit, this test will hang and be killed by the 12s timeout.
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
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.db.base import Base
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine
from app.core.simulator.models import RunRecord
from app.core.simulator.real_runner import RealRunner


# ---------------------------------------------------------------------------
# Isolated SQLite DB for this test (avoids interfering with other tests)
# ---------------------------------------------------------------------------

_TEST_DB_PATH = ".pytest_deadlock_test.db"
_TEST_DB_URL = f"sqlite+aiosqlite:///{_TEST_DB_PATH}"


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

    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield eng

    await eng.dispose()
    for suffix in ("", "-journal", "-wal", "-shm"):
        try:
            Path(_TEST_DB_PATH + suffix).unlink(missing_ok=True)
        except Exception:
            pass


@pytest_asyncio.fixture
async def deadlock_session_factory(deadlock_engine):
    factory = async_sessionmaker(
        bind=deadlock_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )
    return factory


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
async def test_clearing_does_not_deadlock_on_sqlite(
    deadlock_session_factory,
    monkeypatch,
) -> None:
    """tick_real_mode must commit before clearing to avoid SQLite single-writer deadlock.

    This test seeds a clearable triangle into a real SQLite DB and runs a
    full tick_real_mode.  If the parent session is NOT committed before clearing,
    the clearing session will block on the write lock → deadlock → timeout → FAIL.
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

    # Run tick_real_mode with a timeout — if it deadlocks, asyncio.wait_for raises TimeoutError
    try:
        await asyncio.wait_for(runner.tick_real_mode("deadlock-test"), timeout=10.0)
    except asyncio.TimeoutError:
        pytest.fail(
            "tick_real_mode deadlocked! The parent session likely holds an uncommitted "
            "write transaction while clearing tries to write on a separate session. "
            "Ensure session.commit() is called BEFORE tick_real_mode_clearing()."
        )

    # Verify clearing actually ran and completed (not just skipped)
    assert len(clearing_done_events) >= 1, (
        "Expected at least one clearing.done SSE event — clearing may have been skipped"
    )
    # The triangle should have been cleared
    done = clearing_done_events[0]
    assert int(done.get("cleared_cycles", 0)) >= 1, (
        f"Expected cleared_cycles >= 1, got {done}"
    )
