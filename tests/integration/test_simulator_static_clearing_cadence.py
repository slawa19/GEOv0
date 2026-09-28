"""The static clearing cadence of a real tick: clearing fires on `tick_index % clearing_every_n_ticks == 0` only.

TRANSFERRED 2026-09-28 (programme 021, stage 3, `T2104`) from
`tests/integration/test_simulator_adaptive_clearing_integration.py::test_static_policy_unchanged_with_adaptive_code_present`,
which the removal of the adaptive clearing mode deletes. The stand, the seed and both asserts are unchanged; what
went is the environment switch `SIMULATOR_CLEARING_POLICY=static` the old helper set (there is no other policy to
choose any more - `tests/unit/test_p021_adaptive_clearing_is_gone.py`) and the adaptive half of the old module.

Mode B (`committed_database`, a PostgreSQL clone dropped after the test): the tick opens and commits sessions of
its own and its clearing refuses a connection-bound session, so it needs commits that are real.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import threading
from datetime import datetime, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.simulator.models import RunRecord
from app.core.simulator.real_runner import RealRunner
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine
from tests.debt_setup import debt_fixture_setup
from tests.simulator_tick_stand import pooled_sessionmaker_over


@pytest_asyncio.fixture
async def cadence_session_factory(committed_database):
    """A mode-B clone, pooled like the application: the tick's sessions must see what the seed committed."""
    async with pooled_sessionmaker_over(committed_database.url) as factory:
        yield factory


def _utc_now():
    return datetime.now(timezone.utc)


def _make_pid(name: str) -> str:
    return f"p-{name}"


def _pubkey(name: str) -> str:
    return hashlib.sha256(name.encode()).hexdigest()


async def _seed_triangle(session: AsyncSession) -> tuple[str, list[str]]:
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

    await session.flush()

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


class _DummySse:
    def __init__(self):
        self.events: list[dict] = []

    def next_event_id(self, run: RunRecord) -> str:
        run._event_seq += 1
        return f"e{run._event_seq}"

    def broadcast(self, run_id: str, payload: dict) -> None:
        self.events.append(payload)


class _DummyArtifacts:
    def write_real_tick_artifact(self, *a, **kw):
        pass

    def enqueue_event_artifact(self, *a, **kw):
        pass


def _noop(*a, **kw):
    pass


async def _anoop(*a, **kw):
    pass


def _make_runner(run: RunRecord, scenario: dict, session_factory, monkeypatch) -> tuple[RealRunner, _DummySse]:
    import app.core.simulator.storage as simulator_storage
    import app.db.session as app_db_session

    monkeypatch.setattr(app_db_session, "AsyncSessionLocal", session_factory)
    monkeypatch.setattr(simulator_storage, "write_tick_metrics", _anoop)
    monkeypatch.setattr(simulator_storage, "write_tick_bottlenecks", _anoop)
    monkeypatch.setattr(simulator_storage, "sync_artifacts", _anoop)
    monkeypatch.setattr(simulator_storage, "upsert_run", _anoop)

    sse = _DummySse()

    runner = RealRunner(
        lock=threading.RLock(),
        get_run=lambda _: run,
        get_scenario_raw=lambda _: scenario,
        sse=sse,
        artifacts=_DummyArtifacts(),
        utc_now=_utc_now,
        publish_run_status=_noop,
        db_enabled=lambda: True,
        actions_per_tick_max=3,
        clearing_every_n_ticks=25,
        real_max_consec_tick_failures_default=3,
        real_max_timeouts_per_tick_default=10,
        real_max_errors_total_default=50,
        logger=logging.getLogger("test_static_cadence"),
    )
    runner._real_clearing_time_budget_ms = 5000

    return runner, sse


@pytest.mark.asyncio
async def test_static_clearing_fires_only_on_the_cadence_tick(cadence_session_factory, monkeypatch) -> None:
    """Clearing fires at tick_index % clearing_every_n_ticks == 0 only (was
    `test_static_policy_unchanged_with_adaptive_code_present`)."""
    async with cadence_session_factory() as seed_session:
        eq_code, pids = await _seed_triangle(seed_session)

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

    run = RunRecord(run_id="static-test-1", scenario_id="s1", mode="real", state="running")
    run.seed = 42
    run.tick_index = 24  # one before clearing tick (clearing_every_n_ticks=25)
    run.sim_time_ms = 24000
    run.intensity_percent = 100
    run._real_seeded = True

    async with cadence_session_factory() as tmp:
        rows = (await tmp.execute(select(Participant).where(Participant.pid.in_(pids)))).scalars().all()
        run._real_participants = [(p.id, p.pid) for p in rows]
        run._real_equivalents = [eq_code]

    runner, sse = _make_runner(run, scenario, cadence_session_factory, monkeypatch)

    # Tick 24 — clearing should NOT fire (25 % 25 == 0, not 24)
    await asyncio.wait_for(runner.tick_real_mode("static-test-1"), timeout=8.0)
    clearing_events_24 = [e for e in sse.events if isinstance(e, dict) and e.get("type") == "clearing.done"]

    # Tick 25 — clearing SHOULD fire
    run.tick_index = 25
    run.sim_time_ms = 25000
    await asyncio.wait_for(runner.tick_real_mode("static-test-1"), timeout=8.0)
    clearing_events_25 = [e for e in sse.events if isinstance(e, dict) and e.get("type") == "clearing.done"]

    # On tick 24 no clearing; on tick 25, clearing should have happened
    assert len(clearing_events_24) == 0, "Clearing should not fire at tick 24"
    assert len(clearing_events_25) >= 1, "Clearing should fire at tick 25"
