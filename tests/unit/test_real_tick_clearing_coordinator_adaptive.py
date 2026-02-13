from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

import pytest

from app.core.simulator.adaptive_clearing_policy import AdaptiveClearingPolicyConfig
from app.core.simulator.models import RunRecord
from app.core.simulator.real_tick_clearing_coordinator import RealTickClearingCoordinator


class _AsyncSession:
    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


@dataclass
class _PaymentsResult:
    per_eq: dict[str, dict[str, int]]
    rejection_codes_by_eq: dict[str, dict[str, int]]


def _make_run(*, tick_index: int = 0) -> RunRecord:
    run = RunRecord(
        run_id="run-1",
        scenario_id="scenario-1",
        mode="real",
        state="running",
        tick_index=int(tick_index),
    )
    # Optional fields used by coordinator guardrails
    setattr(run, "_real_in_flight", 0)
    return run


@pytest.mark.asyncio
async def test_adaptive_records_zero_signals_each_tick_so_warmup_finishes() -> None:
    cfg = AdaptiveClearingPolicyConfig(
        window_ticks=3,
        warmup_fallback_cadence=1,
        min_interval_ticks=1,
        no_capacity_low=0.30,
        no_capacity_high=0.60,
    )
    coordinator = RealTickClearingCoordinator(
        lock=threading.Lock(),
        logger=logging.getLogger(__name__),
        clearing_every_n_ticks=0,
        real_clearing_time_budget_ms=250,
        clearing_policy="adaptive",
        adaptive_config=cfg,
    )

    session = _AsyncSession()
    run = _make_run(tick_index=0)
    equivalents = ["USD"]

    calls: list[int] = []

    async def run_clearing_for_eq(eq: str, *, time_budget_ms_override=None, max_depth_override=None):
        calls.append(int(run.tick_index))
        # Positive volume to avoid zero-yield backoff interfering with the warmup test.
        return {eq: 10.0}

    async def run_clearing():
        raise AssertionError("static run_clearing() must not be used in adaptive branch")

    # payments_result=None for several ticks.
    for tick in range(5):
        run.tick_index = tick
        await coordinator.maybe_run_clearing(
            session=session,
            run_id=str(run.run_id),
            run=run,
            equivalents=equivalents,
            planned_len=0,
            tick_t0=0.0,
            clearing_enabled=True,
            safe_int_env=lambda k, d: d,
            run_clearing=run_clearing,
            run_clearing_for_eq=run_clearing_for_eq,
            payments_result=None,
        )

    # Warmup fallback runs while window_fill < window_ticks.
    # With zero signals recorded on each tick: tick 0 (fill=1), tick 1 (fill=2) run;
    # tick 2+ (fill>=3) warmup is over and no_capacity_rate=0 => skip.
    assert calls == [0, 1]


@pytest.mark.asyncio
async def test_adaptive_exception_is_treated_as_zero_yield_and_triggers_cooldown() -> None:
    cfg = AdaptiveClearingPolicyConfig(
        window_ticks=1,
        min_interval_ticks=3,
        no_capacity_low=0.30,
        no_capacity_high=0.60,
    )
    coordinator = RealTickClearingCoordinator(
        lock=threading.Lock(),
        logger=logging.getLogger(__name__),
        clearing_every_n_ticks=0,
        real_clearing_time_budget_ms=250,
        clearing_policy="adaptive",
        adaptive_config=cfg,
    )

    session = _AsyncSession()
    run = _make_run(tick_index=0)
    equivalents = ["USD"]

    calls: list[int] = []

    async def run_clearing_for_eq(eq: str, *, time_budget_ms_override=None, max_depth_override=None):
        calls.append(int(run.tick_index))
        raise RuntimeError("boom")

    async def run_clearing():
        raise AssertionError("static run_clearing() must not be used in adaptive branch")

    payments_result = _PaymentsResult(
        per_eq={"USD": {"committed": 0, "rejected": 10, "errors": 0, "timeouts": 0}},
        rejection_codes_by_eq={"USD": {"ROUTING_NO_CAPACITY": 10}},
    )

    # tick 0: high pressure -> should attempt clearing and fail.
    run.tick_index = 0
    await coordinator.maybe_run_clearing(
        session=session,
        run_id=str(run.run_id),
        run=run,
        equivalents=equivalents,
        planned_len=0,
        tick_t0=0.0,
        clearing_enabled=True,
        safe_int_env=lambda k, d: d,
        run_clearing=run_clearing,
        run_clearing_for_eq=run_clearing_for_eq,
        payments_result=payments_result,
    )

    # tick 1: would attempt again under high pressure, but must be blocked by cooldown/backoff.
    run.tick_index = 1
    await coordinator.maybe_run_clearing(
        session=session,
        run_id=str(run.run_id),
        run=run,
        equivalents=equivalents,
        planned_len=0,
        tick_t0=0.0,
        clearing_enabled=True,
        safe_int_env=lambda k, d: d,
        run_clearing=run_clearing,
        run_clearing_for_eq=run_clearing_for_eq,
        payments_result=payments_result,
    )

    assert calls == [0]


@pytest.mark.asyncio
async def test_adaptive_tick_budget_caps_multi_equivalent_clearing(monkeypatch) -> None:
    # Fake monotonic clock so the tick budget is deterministic.
    class _FakeClock:
        def __init__(self) -> None:
            self.t = 0.0

        def monotonic(self) -> float:
            return float(self.t)

        def advance(self, seconds: float) -> None:
            self.t += float(seconds)

    clock = _FakeClock()
    monkeypatch.setattr(
        "app.core.simulator.real_tick_clearing_coordinator.time.monotonic",
        clock.monotonic,
    )

    cfg = AdaptiveClearingPolicyConfig(
        window_ticks=1,
        min_interval_ticks=1,
        no_capacity_low=0.30,
        no_capacity_high=0.60,
    )
    coordinator = RealTickClearingCoordinator(
        lock=threading.Lock(),
        logger=logging.getLogger(__name__),
        clearing_every_n_ticks=0,
        real_clearing_time_budget_ms=250,
        clearing_policy="adaptive",
        adaptive_config=cfg,
    )

    session = _AsyncSession()
    run = _make_run(tick_index=0)
    equivalents = ["USD", "EUR"]

    calls: list[str] = []

    async def run_clearing_for_eq(eq: str, *, time_budget_ms_override=None, max_depth_override=None):
        calls.append(eq)
        # Simulate per-eq wall time cost.
        clock.advance(0.010)  # 10ms
        return {eq: 10.0}

    async def run_clearing():
        raise AssertionError("static run_clearing() must not be used in adaptive branch")

    payments_result = _PaymentsResult(
        per_eq={
            "USD": {"committed": 0, "rejected": 10, "errors": 0, "timeouts": 0},
            "EUR": {"committed": 0, "rejected": 10, "errors": 0, "timeouts": 0},
        },
        rejection_codes_by_eq={
            "USD": {"ROUTING_NO_CAPACITY": 10},
            "EUR": {"ROUTING_NO_CAPACITY": 10},
        },
    )

    env = {
        # 5ms tick budget should allow only one eq (our fake clearing costs 10ms).
        "SIMULATOR_CLEARING_ADAPTIVE_TICK_BUDGET_MS": 5,
        "SIMULATOR_CLEARING_ADAPTIVE_MAX_EQ_PER_TICK": 0,
    }

    await coordinator.maybe_run_clearing(
        session=session,
        run_id=str(run.run_id),
        run=run,
        equivalents=equivalents,
        planned_len=0,
        tick_t0=0.0,
        clearing_enabled=True,
        safe_int_env=lambda k, d: int(env.get(k, d)),
        run_clearing=run_clearing,
        run_clearing_for_eq=run_clearing_for_eq,
        payments_result=payments_result,
    )

    assert calls == ["USD"]


@pytest.mark.asyncio
async def test_adaptive_max_eq_per_tick_caps_multi_equivalent_clearing() -> None:
    cfg = AdaptiveClearingPolicyConfig(
        window_ticks=1,
        min_interval_ticks=1,
        no_capacity_low=0.30,
        no_capacity_high=0.60,
    )
    coordinator = RealTickClearingCoordinator(
        lock=threading.Lock(),
        logger=logging.getLogger(__name__),
        clearing_every_n_ticks=0,
        real_clearing_time_budget_ms=250,
        clearing_policy="adaptive",
        adaptive_config=cfg,
    )

    session = _AsyncSession()
    run = _make_run(tick_index=0)
    equivalents = ["USD", "EUR"]

    calls: list[str] = []

    async def run_clearing_for_eq(eq: str, *, time_budget_ms_override=None, max_depth_override=None):
        calls.append(eq)
        return {eq: 10.0}

    async def run_clearing():
        raise AssertionError("static run_clearing() must not be used in adaptive branch")

    payments_result = _PaymentsResult(
        per_eq={
            "USD": {"committed": 0, "rejected": 10, "errors": 0, "timeouts": 0},
            "EUR": {"committed": 0, "rejected": 10, "errors": 0, "timeouts": 0},
        },
        rejection_codes_by_eq={
            "USD": {"ROUTING_NO_CAPACITY": 10},
            "EUR": {"ROUTING_NO_CAPACITY": 10},
        },
    )

    env = {
        "SIMULATOR_CLEARING_ADAPTIVE_TICK_BUDGET_MS": 0,
        "SIMULATOR_CLEARING_ADAPTIVE_MAX_EQ_PER_TICK": 1,
    }

    await coordinator.maybe_run_clearing(
        session=session,
        run_id=str(run.run_id),
        run=run,
        equivalents=equivalents,
        planned_len=0,
        tick_t0=0.0,
        clearing_enabled=True,
        safe_int_env=lambda k, d: int(env.get(k, d)),
        run_clearing=run_clearing,
        run_clearing_for_eq=run_clearing_for_eq,
        payments_result=payments_result,
    )

    assert calls == ["USD"]

