"""Integration tests for adaptive clearing policy (§7.2 of the spec).

Tests:
1) spy-based: simulate tick cycle with a spy RealClearingEngine, verify
   that the adaptive coordinator calls clearing when signals warrant it.
2) static mode: verify that static policy is unchanged by the presence of
   adaptive code paths.

Uses isolated SQLite DB (same pattern as test_simulator_clearing_no_deadlock.py).
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
# Isolated SQLite DB
# ---------------------------------------------------------------------------

_TEST_DB_PATH = ".pytest_adaptive_int_test.db"
_TEST_DB_URL = f"sqlite+aiosqlite:///{_TEST_DB_PATH}"


@pytest_asyncio.fixture
async def adaptive_engine():
    for suffix in ("", "-journal", "-wal", "-shm"):
        try:
            Path(_TEST_DB_PATH + suffix).unlink(missing_ok=True)
        except Exception:
            pass

    eng = create_async_engine(
        _TEST_DB_URL,
        echo=False,
        poolclass=NullPool,
        connect_args={"timeout": 5},
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
async def adaptive_session_factory(adaptive_engine):
    return async_sessionmaker(
        bind=adaptive_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


def _make_runner(
    run: RunRecord,
    scenario: dict,
    session_factory,
    monkeypatch,
    *,
    clearing_policy: str = "static",
) -> tuple[RealRunner, _DummySse]:
    import app.db.session as app_db_session
    import app.core.simulator.storage as simulator_storage

    monkeypatch.setattr(app_db_session, "AsyncSessionLocal", session_factory)
    monkeypatch.setattr(simulator_storage, "write_tick_metrics", _anoop)
    monkeypatch.setattr(simulator_storage, "write_tick_bottlenecks", _anoop)
    monkeypatch.setattr(simulator_storage, "sync_artifacts", _anoop)
    monkeypatch.setattr(simulator_storage, "upsert_run", _anoop)

    # Set env for clearing policy
    monkeypatch.setenv("SIMULATOR_CLEARING_POLICY", clearing_policy)
    # Tight thresholds for tests (activate easily)
    if clearing_policy == "adaptive":
        monkeypatch.setenv("SIMULATOR_CLEARING_ADAPTIVE_WINDOW_TICKS", "5")
        monkeypatch.setenv("SIMULATOR_CLEARING_ADAPTIVE_NO_CAPACITY_HIGH", "0.30")
        monkeypatch.setenv("SIMULATOR_CLEARING_ADAPTIVE_NO_CAPACITY_LOW", "0.10")
        monkeypatch.setenv("SIMULATOR_CLEARING_ADAPTIVE_MIN_INTERVAL_TICKS", "2")
        monkeypatch.setenv("SIMULATOR_CLEARING_ADAPTIVE_BACKOFF_MAX_INTERVAL_TICKS", "10")

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
        logger=logging.getLogger("test_adaptive_int"),
    )
    runner._real_clearing_time_budget_ms = 5000

    return runner, sse


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_adaptive_policy_triggers_clearing_on_high_rejection_rate(
    adaptive_session_factory,
    monkeypatch,
) -> None:
    """When no_capacity_rate exceeds HIGH threshold, adaptive policy triggers clearing.

    Uses a deterministic spy: monkeypatches the coordinator's run_clearing_for_eq
    callable to ensure clearing is actually invoked (not just "maybe").
    """
    from app.core.simulator.real_tick_clearing_coordinator import RealTickClearingCoordinator
    from app.core.simulator.adaptive_clearing_policy import (
        AdaptiveClearingPolicyConfig,
        TickSignals,
    )

    cfg = AdaptiveClearingPolicyConfig(
        window_ticks=5,
        no_capacity_high=0.30,
        no_capacity_low=0.10,
        min_interval_ticks=2,
        backoff_max_interval_ticks=10,
        warmup_fallback_cadence=0,  # disable fallback to test pure adaptive logic
    )

    coordinator = RealTickClearingCoordinator(
        lock=threading.RLock(),
        logger=logging.getLogger("test_trigger"),
        clearing_every_n_ticks=25,
        real_clearing_time_budget_ms=250,
        clearing_policy="adaptive",
        adaptive_config=cfg,
    )

    state = coordinator._adaptive_state

    # Pre-fill window with high rejection (50% > HIGH=30%)
    for _ in range(5):
        state.record_tick_signals("UAH", TickSignals(
            attempted_payments=10,
            rejected_no_capacity=5,
        ))

    # Build spy for run_clearing_for_eq
    clearing_calls: list[dict] = []

    async def spy_run_clearing_for_eq(eq, *, time_budget_ms_override=None, max_depth_override=None):
        clearing_calls.append({
            "eq": eq,
            "budget_ms": time_budget_ms_override,
            "max_depth": max_depth_override,
        })
        return {eq: 10.0}  # positive volume

    # Build minimal RunRecord
    run = RunRecord(run_id="trigger-test-1", scenario_id="s1", mode="real", state="running")
    run.tick_index = 10
    run.sim_time_ms = 10000
    run._real_in_flight = 0
    run.queue_depth = 0

    # Build a mock session
    class _MockSession:
        async def commit(self): pass
        async def rollback(self): pass

    session = _MockSession()

    # Build a fake payments_result with high rejection
    class _FakePaymentsResult:
        per_eq = {"UAH": {"committed": 5, "rejected": 5, "errors": 0, "timeouts": 0}}
        rejection_codes_by_eq = {"UAH": {"ROUTING_NO_CAPACITY": 5}}

    result = await coordinator.maybe_run_clearing(
        session=session,
        run_id="trigger-test-1",
        run=run,
        equivalents=["UAH"],
        planned_len=1,
        tick_t0=0.0,
        clearing_enabled=True,
        safe_int_env=lambda k, d: d,
        run_clearing=lambda: None,  # not used in adaptive
        run_clearing_for_eq=spy_run_clearing_for_eq,
        payments_result=_FakePaymentsResult(),
    )

    # Assert clearing was actually called
    assert len(clearing_calls) >= 1, f"Clearing was never called! Expected at least 1 call, got {clearing_calls}"
    assert clearing_calls[0]["eq"] == "UAH"
    assert clearing_calls[0]["budget_ms"] is not None
    assert result.get("UAH", 0.0) > 0.0


@pytest.mark.asyncio
async def test_static_policy_unchanged_with_adaptive_code_present(
    adaptive_session_factory,
    monkeypatch,
) -> None:
    """Static policy behavior is fully preserved even with adaptive code paths present.

    Clearing should fire at tick_index % clearing_every_n_ticks == 0 only.
    """
    async with adaptive_session_factory() as seed_session:
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

    async with adaptive_session_factory() as tmp:
        from sqlalchemy import select
        rows = (await tmp.execute(
            select(Participant).where(Participant.pid.in_(pids))
        )).scalars().all()
        run._real_participants = [(p.id, p.pid) for p in rows]
        run._real_equivalents = [eq_code]

    runner, sse = _make_runner(
        run, scenario, adaptive_session_factory, monkeypatch,
        clearing_policy="static",
    )

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


@pytest.mark.asyncio
async def test_adaptive_coordinator_respects_cooldown(
    adaptive_session_factory,
    monkeypatch,
) -> None:
    """Adaptive clearing coordinator must respect min_interval_ticks cooldown."""
    from app.core.simulator.real_tick_clearing_coordinator import RealTickClearingCoordinator
    from app.core.simulator.adaptive_clearing_policy import (
        AdaptiveClearingPolicyConfig,
    )

    cfg = AdaptiveClearingPolicyConfig(
        window_ticks=5,
        no_capacity_high=0.30,
        no_capacity_low=0.10,
        min_interval_ticks=3,
        backoff_max_interval_ticks=20,
    )

    coordinator = RealTickClearingCoordinator(
        lock=threading.RLock(),
        logger=logging.getLogger("test_cooldown"),
        clearing_every_n_ticks=25,
        real_clearing_time_budget_ms=250,
        clearing_policy="adaptive",
        adaptive_config=cfg,
    )

    # Verify internal state was created
    assert coordinator._adaptive_state is not None
    assert coordinator._adaptive_policy is not None
    assert coordinator._adaptive_config is not None

    # Feed high-rejection signals to the state directly
    from app.core.simulator.adaptive_clearing_policy import TickSignals
    state = coordinator._adaptive_state

    # Fill 5 ticks with 50% rejection (above HIGH=0.30)
    for _ in range(5):
        state.record_tick_signals("UAH", TickSignals(
            attempted_payments=10,
            rejected_no_capacity=5,
        ))

    policy = coordinator._adaptive_policy
    # First decision at tick 0: should activate
    d0 = policy.evaluate("UAH", state, tick_index=0)
    assert d0.should_run is True

    # Simulate that clearing ran at tick 0
    state.update_clearing_result("UAH", volume=10.0, cost_ms=50.0, tick=0)

    # Decision at tick 1: should be in cooldown (min_interval=3)
    d1 = policy.evaluate("UAH", state, tick_index=1)
    assert d1.should_run is False
    assert d1.reason == "SKIP_MIN_INTERVAL"

    # Decision at tick 2: still cooldown
    d2 = policy.evaluate("UAH", state, tick_index=2)
    assert d2.should_run is False
    assert d2.reason == "SKIP_MIN_INTERVAL"

    # Decision at tick 3: cooldown passed, should run again
    state.record_tick_signals("UAH", TickSignals(attempted_payments=10, rejected_no_capacity=5))
    d3 = policy.evaluate("UAH", state, tick_index=3)
    assert d3.should_run is True


@pytest.mark.asyncio
async def test_guardrail_inflight_blocks_clearing(
    adaptive_session_factory,
    monkeypatch,
) -> None:
    """When in_flight > inflight_threshold, coordinator must skip all clearing (§3.2)."""
    from app.core.simulator.real_tick_clearing_coordinator import RealTickClearingCoordinator
    from app.core.simulator.adaptive_clearing_policy import (
        AdaptiveClearingPolicyConfig,
        TickSignals,
    )

    cfg = AdaptiveClearingPolicyConfig(
        window_ticks=5,
        no_capacity_high=0.30,
        no_capacity_low=0.10,
        min_interval_ticks=2,
        backoff_max_interval_ticks=20,
        inflight_threshold=10,   # guardrail: skip if in_flight > 10
        queue_depth_threshold=0,  # disabled
        warmup_fallback_cadence=0,
    )

    coordinator = RealTickClearingCoordinator(
        lock=threading.RLock(),
        logger=logging.getLogger("test_guardrail"),
        clearing_every_n_ticks=25,
        real_clearing_time_budget_ms=250,
        clearing_policy="adaptive",
        adaptive_config=cfg,
    )

    state = coordinator._adaptive_state
    # Pre-fill with high rejection so policy would normally activate
    for _ in range(5):
        state.record_tick_signals("UAH", TickSignals(
            attempted_payments=10,
            rejected_no_capacity=5,
        ))

    clearing_calls: list[str] = []

    async def spy_clearing(eq, *, time_budget_ms_override=None, max_depth_override=None):
        clearing_calls.append(eq)
        return {eq: 10.0}

    run = RunRecord(run_id="guardrail-test", scenario_id="s1", mode="real", state="running")
    run.tick_index = 10
    run.sim_time_ms = 10000
    run._real_in_flight = 20  # ABOVE threshold of 10
    run.queue_depth = 0

    class _MockSession:
        async def commit(self): pass
        async def rollback(self): pass

    class _FakePaymentsResult:
        per_eq = {"UAH": {"committed": 5, "rejected": 5, "errors": 0, "timeouts": 0}}
        rejection_codes_by_eq = {"UAH": {"ROUTING_NO_CAPACITY": 5}}

    result = await coordinator.maybe_run_clearing(
        session=_MockSession(),
        run_id="guardrail-test",
        run=run,
        equivalents=["UAH"],
        planned_len=1,
        tick_t0=0.0,
        clearing_enabled=True,
        safe_int_env=lambda k, d: d,
        run_clearing=lambda: None,
        run_clearing_for_eq=spy_clearing,
        payments_result=_FakePaymentsResult(),
    )

    # Clearing should NOT have been called because in_flight > threshold
    assert len(clearing_calls) == 0, f"Clearing should be blocked by guardrail, but got calls: {clearing_calls}"
    assert result["UAH"] == 0.0


@pytest.mark.asyncio
async def test_guardrail_queue_depth_blocks_clearing(
    adaptive_session_factory,
    monkeypatch,
) -> None:
    """When queue_depth > queue_depth_threshold, coordinator must skip all clearing."""
    from app.core.simulator.real_tick_clearing_coordinator import RealTickClearingCoordinator
    from app.core.simulator.adaptive_clearing_policy import (
        AdaptiveClearingPolicyConfig,
        TickSignals,
    )

    cfg = AdaptiveClearingPolicyConfig(
        window_ticks=5,
        no_capacity_high=0.30,
        no_capacity_low=0.10,
        min_interval_ticks=2,
        backoff_max_interval_ticks=20,
        inflight_threshold=0,    # disabled
        queue_depth_threshold=5,  # guardrail: skip if queue_depth > 5
        warmup_fallback_cadence=0,
    )

    coordinator = RealTickClearingCoordinator(
        lock=threading.RLock(),
        logger=logging.getLogger("test_guardrail_qd"),
        clearing_every_n_ticks=25,
        real_clearing_time_budget_ms=250,
        clearing_policy="adaptive",
        adaptive_config=cfg,
    )

    state = coordinator._adaptive_state
    for _ in range(5):
        state.record_tick_signals("UAH", TickSignals(
            attempted_payments=10,
            rejected_no_capacity=5,
        ))

    clearing_calls: list[str] = []

    async def spy_clearing(eq, *, time_budget_ms_override=None, max_depth_override=None):
        clearing_calls.append(eq)
        return {eq: 10.0}

    run = RunRecord(run_id="guardrail-qd-test", scenario_id="s1", mode="real", state="running")
    run.tick_index = 10
    run.sim_time_ms = 10000
    run._real_in_flight = 0
    run.queue_depth = 15  # ABOVE threshold of 5

    class _MockSession:
        async def commit(self): pass
        async def rollback(self): pass

    class _FakePaymentsResult:
        per_eq = {"UAH": {"committed": 5, "rejected": 5, "errors": 0, "timeouts": 0}}
        rejection_codes_by_eq = {"UAH": {"ROUTING_NO_CAPACITY": 5}}

    result = await coordinator.maybe_run_clearing(
        session=_MockSession(),
        run_id="guardrail-qd-test",
        run=run,
        equivalents=["UAH"],
        planned_len=1,
        tick_t0=0.0,
        clearing_enabled=True,
        safe_int_env=lambda k, d: d,
        run_clearing=lambda: None,
        run_clearing_for_eq=spy_clearing,
        payments_result=_FakePaymentsResult(),
    )

    assert len(clearing_calls) == 0, f"Clearing should be blocked by queue_depth guardrail, but got: {clearing_calls}"
    assert result["UAH"] == 0.0


@pytest.mark.asyncio
async def test_adaptive_coordinator_processes_equivalents_in_sorted_order(
    adaptive_session_factory,
    monkeypatch,
) -> None:
    """Coordinator MUST iterate equivalents in deterministic order (sorted).

    This becomes observable when tick-level caps are enabled; "first K" equivalents
    must be stable and not depend on scenario list ordering.
    """
    from app.core.simulator.real_tick_clearing_coordinator import RealTickClearingCoordinator
    from app.core.simulator.adaptive_clearing_policy import AdaptiveClearingPolicyConfig

    # Caps: only allow clearing for 1 equivalent per tick.
    monkeypatch.setenv("SIMULATOR_CLEARING_ADAPTIVE_MAX_EQ_PER_TICK", "1")

    cfg = AdaptiveClearingPolicyConfig(
        window_ticks=1,
        no_capacity_high=0.0,
        no_capacity_low=0.0,
        min_interval_ticks=1,
        warmup_fallback_cadence=1,
    )

    coordinator = RealTickClearingCoordinator(
        lock=threading.RLock(),
        logger=logging.getLogger("test_sorted_eq"),
        clearing_every_n_ticks=25,
        real_clearing_time_budget_ms=250,
        clearing_policy="adaptive",
        adaptive_config=cfg,
    )

    # RunRecord with no guardrail pressure.
    run = RunRecord(run_id="sorted-eq-test", scenario_id="s1", mode="real", state="running")
    run.tick_index = 10
    run.sim_time_ms = 10000
    run._real_in_flight = 0
    run.queue_depth = 0

    class _MockSession:
        async def commit(self):
            return None
        async def rollback(self):
            return None

    # Capture actual clearing calls.
    clearing_calls: list[str] = []

    async def spy_clearing(eq, *, time_budget_ms_override=None, max_depth_override=None):
        clearing_calls.append(str(eq))
        return {str(eq): 1.0}

    # Provide 2 equivalents in reverse order. Sorted order should pick "AAA" first.
    equivalents = ["ZZZ", "AAA"]

    # Minimal fake payments result; values don't matter for warmup fallback.
    class _FakePaymentsResult:
        per_eq = {"AAA": {"committed": 0, "rejected": 0, "errors": 0, "timeouts": 0}, "ZZZ": {"committed": 0, "rejected": 0, "errors": 0, "timeouts": 0}}
        rejection_codes_by_eq = {"AAA": {"ROUTING_NO_CAPACITY": 0}, "ZZZ": {"ROUTING_NO_CAPACITY": 0}}

    await coordinator.maybe_run_clearing(
        session=_MockSession(),
        run_id="sorted-eq-test",
        run=run,
        equivalents=equivalents,
        planned_len=0,
        tick_t0=0.0,
        clearing_enabled=True,
        safe_int_env=lambda k, d: int(os.getenv(k, str(d))),
        run_clearing=lambda: None,
        run_clearing_for_eq=spy_clearing,
        payments_result=_FakePaymentsResult(),
    )

    assert clearing_calls, "Expected at least one per-eq clearing call"
    assert clearing_calls[0] == "AAA", f"Expected sorted order to pick 'AAA' first, got: {clearing_calls}"
