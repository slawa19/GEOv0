from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from decimal import Decimal

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


@dataclass
class _DeferredPaymentsResult:
    applied: int = 0

    def apply_deferred_effects(self) -> bool:
        if self.applied:
            return False
        self.applied += 1
        return True


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
async def test_static_clearing_applies_payment_effects_after_commit() -> None:
    coordinator = RealTickClearingCoordinator(
        lock=threading.Lock(),
        logger=logging.getLogger(__name__),
        clearing_every_n_ticks=1,
        real_clearing_time_budget_ms=250,
    )
    payments_result = _DeferredPaymentsResult()

    await coordinator.maybe_run_clearing(
        session=_AsyncSession(),
        run_id="run-1",
        run=_make_run(tick_index=1),
        equivalents=["USD"],
        planned_len=1,
        tick_t0=0.0,
        clearing_enabled=True,
        safe_int_env=lambda _key, default: default,
        run_clearing=lambda: _return_zero_clearing(),
        payments_result=payments_result,
    )

    assert payments_result.applied == 1


async def _return_zero_clearing() -> dict[str, float]:
    return {"USD": 0.0}


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

    assert calls == [sorted(equivalents)[0]]


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

    assert calls == [sorted(equivalents)[0]]



# --- p007_t715: the cleared volume is money and stays Decimal ----------------


# 19 significant digits: binary64 holds ~17, so any float stage changes it.
_TOO_PRECISE_FOR_FLOAT = Decimal("12345678901.12345678")


def test_volume_probe_is_beyond_float() -> None:
    """Anti-vacuum: the discriminator must actually discriminate."""

    assert Decimal(str(float(_TOO_PRECISE_FOR_FLOAT))) != _TOO_PRECISE_FOR_FLOAT


@pytest.mark.asyncio
async def test_adaptive_keeps_the_volume_exact_and_hands_the_policy_a_float() -> None:
    """The money value stays Decimal; only the backoff heuristic sees a float.

    `float(result[eq])` used to narrow the volume right after the engine
    returned it, so the `clearing_volume` metric lost precision on this branch
    even after the column became Numeric(20, 8).
    """

    cfg = AdaptiveClearingPolicyConfig(
        window_ticks=3,
        warmup_fallback_cadence=1,
        min_interval_ticks=1,
    )
    coordinator = RealTickClearingCoordinator(
        lock=threading.Lock(),
        logger=logging.getLogger(__name__),
        clearing_every_n_ticks=0,
        real_clearing_time_budget_ms=250,
        clearing_policy="adaptive",
        adaptive_config=cfg,
    )

    async def run_clearing_for_eq(eq: str, **_kwargs) -> dict[str, Decimal]:
        return {eq: _TOO_PRECISE_FOR_FLOAT}

    async def run_clearing():
        raise AssertionError("static run_clearing() must not be used in adaptive branch")

    volumes = await coordinator.maybe_run_clearing(
        session=_AsyncSession(),
        run_id="run-1",
        run=_make_run(tick_index=0),
        equivalents=["USD"],
        planned_len=0,
        tick_t0=0.0,
        clearing_enabled=True,
        safe_int_env=lambda k, d: d,
        run_clearing=run_clearing,
        run_clearing_for_eq=run_clearing_for_eq,
        payments_result=None,
    )

    assert volumes["USD"] == _TOO_PRECISE_FOR_FLOAT
    assert isinstance(volumes["USD"], Decimal)

    # The backoff policy is a heuristic and is deliberately fed a float; the
    # conversion happens at the call site, not by widening the money type.
    state = coordinator._adaptive_state
    assert state is not None
    recorded = state.get_per_eq_state("USD").last_clearing_volume
    assert isinstance(recorded, float)


@pytest.mark.asyncio
async def test_every_early_return_hands_back_decimal_zeros() -> None:
    """All three `clearing_volume_by_eq` seeds are Decimal, not 0.0.

    Fixing only the assignment after a successful clearing would leave the type
    mixed: every branch that returns before (or instead of) that assignment
    would still hand the metrics producer a float zero.
    """

    async def run_clearing():
        raise RuntimeError("clearing failed")

    async def run_clearing_for_eq(eq: str, **_kwargs):
        raise AssertionError("no equivalent should be cleared on this path")

    # Seed 1 (maybe_run_clearing): clearing switched off entirely.
    static_coordinator = RealTickClearingCoordinator(
        lock=threading.Lock(),
        logger=logging.getLogger(__name__),
        clearing_every_n_ticks=1,
        real_clearing_time_budget_ms=250,
    )
    disabled = await static_coordinator.maybe_run_clearing(
        session=_AsyncSession(),
        run_id="run-1",
        run=_make_run(tick_index=1),
        equivalents=["USD", "EUR"],
        planned_len=0,
        tick_t0=0.0,
        clearing_enabled=False,
        safe_int_env=lambda k, d: d,
        run_clearing=run_clearing,
    )
    assert disabled == {"USD": Decimal("0"), "EUR": Decimal("0")}
    assert all(isinstance(value, Decimal) for value in disabled.values())

    # Seed 2 (_maybe_run_adaptive): the policy decides not to clear anything.
    adaptive_coordinator = RealTickClearingCoordinator(
        lock=threading.Lock(),
        logger=logging.getLogger(__name__),
        clearing_every_n_ticks=0,
        real_clearing_time_budget_ms=250,
        clearing_policy="adaptive",
        adaptive_config=AdaptiveClearingPolicyConfig(
            window_ticks=5,
            warmup_fallback_cadence=0,  # warmup disabled -> no equivalent runs
        ),
    )
    skipped = await adaptive_coordinator.maybe_run_clearing(
        session=_AsyncSession(),
        run_id="run-1",
        run=_make_run(tick_index=0),
        equivalents=["USD"],
        planned_len=0,
        tick_t0=0.0,
        clearing_enabled=True,
        safe_int_env=lambda k, d: d,
        run_clearing=run_clearing,
        run_clearing_for_eq=run_clearing_for_eq,
        payments_result=None,
    )
    assert skipped == {"USD": Decimal("0")}
    assert all(isinstance(value, Decimal) for value in skipped.values())

    # Seed 3 (_execute_clearing_with_timeout): the static runner fails, so the
    # seeded dict is what the caller gets.
    failed = await static_coordinator.maybe_run_clearing(
        session=_AsyncSession(),
        run_id="run-1",
        run=_make_run(tick_index=1),
        equivalents=["USD"],
        planned_len=0,
        tick_t0=0.0,
        clearing_enabled=True,
        safe_int_env=lambda k, d: d,
        run_clearing=run_clearing,
    )
    assert failed == {"USD": Decimal("0")}
    assert all(isinstance(value, Decimal) for value in failed.values())
