"""The tick's static clearing: payment effects after the commit, and the cleared volume stays Decimal.

Until 2026-09-28 this module was `test_real_tick_clearing_coordinator_adaptive.py`. Programme 021 stage 3 (`T2104`)
removed the adaptive clearing mode; its five adaptive-only nodes went with it, and the static contracts the spec
names (Verification plan, item 3) stay here - two of them in static form. Programme 021 stage 4 (`T2105`) moved the
coordinator into `tick.py` (`RealTick.maybe_run_clearing`) and renamed this module from
`test_real_tick_clearing_coordinator_static.py`; the assertions are unchanged. The clearing driver is replaced at
its one call point (`RealTick._run_clearing`), which reports what it committed into the tick's accumulator - the
volume the tick returns comes from there (the stage 4 fix), not from a return value.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import pytest

import app.core.simulator.tick as tick_module
from app.core.simulator.models import RunRecord
from tests.simulator_tick_stand import unit_tick


class _AsyncSession:
    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


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
    return run


async def _return_zero_clearing(**_kwargs) -> None:
    return None


@pytest.mark.asyncio
async def test_static_clearing_applies_payment_effects_after_commit() -> None:
    coordinator = unit_tick(_clearing_every_n_ticks=1, _real_clearing_time_budget_ms=250)
    coordinator._run_clearing = _return_zero_clearing
    payments_result = _DeferredPaymentsResult()

    await coordinator.maybe_run_clearing(
        session=_AsyncSession(),
        run_id="run-1",
        run=_make_run(tick_index=1),
        equivalents=["USD"],
        planned_len=1,
        tick_t0=0.0,
        payments_result=payments_result,
    )

    assert payments_result.applied == 1


# --- p007_t715: the cleared volume is money and stays Decimal ----------------


# 19 significant digits: binary64 holds ~17, so any float stage changes it.
_TOO_PRECISE_FOR_FLOAT = Decimal("12345678901.12345678")


def test_volume_probe_is_beyond_float() -> None:
    """Anti-vacuum: the discriminator must actually discriminate."""

    assert Decimal(str(float(_TOO_PRECISE_FOR_FLOAT))) != _TOO_PRECISE_FOR_FLOAT


@pytest.mark.asyncio
async def test_static_keeps_the_volume_exact() -> None:
    """The static branch hands the caller the committed cleared volume as the same Decimal.

    Static form of `test_adaptive_keeps_the_volume_exact_and_hands_the_policy_a_float` (021 stage 3, `T2104`):
    the money half of that test - the volume leaves the coordinator exact and as `Decimal` - on the only
    branch that survives. The float half belonged to the removed backoff heuristic.
    """

    coordinator = unit_tick(_clearing_every_n_ticks=1, _real_clearing_time_budget_ms=250)

    async def run_clearing(*, committed, **_kwargs) -> None:
        committed["USD"] += _TOO_PRECISE_FOR_FLOAT

    coordinator._run_clearing = run_clearing

    volumes = await coordinator.maybe_run_clearing(
        session=_AsyncSession(),
        run_id="run-1",
        run=_make_run(tick_index=1),
        equivalents=["USD"],
        planned_len=0,
        tick_t0=0.0,
    )

    assert volumes["USD"] == _TOO_PRECISE_FOR_FLOAT
    assert isinstance(volumes["USD"], Decimal)


@pytest.mark.asyncio
async def test_every_early_return_hands_back_decimal_zeros(monkeypatch) -> None:
    """Every `clearing_volume_by_eq` seed is Decimal, not 0.0.

    Fixing only the assignment after a successful clearing would leave the type
    mixed: every branch that returns before (or instead of) that assignment
    would still hand the metrics producer a float zero.
    """

    async def run_clearing(**_kwargs):
        raise RuntimeError("clearing failed")

    # Seed 1 (maybe_run_clearing): clearing switched off entirely.
    static_coordinator = unit_tick(_clearing_every_n_ticks=1, _real_clearing_time_budget_ms=250)
    static_coordinator._run_clearing = run_clearing
    monkeypatch.setattr(tick_module.settings, "CLEARING_ENABLED", False)
    disabled = await static_coordinator.maybe_run_clearing(
        session=_AsyncSession(),
        run_id="run-1",
        run=_make_run(tick_index=1),
        equivalents=["USD", "EUR"],
        planned_len=0,
        tick_t0=0.0,
    )
    assert disabled == {"USD": Decimal("0"), "EUR": Decimal("0")}
    assert all(isinstance(value, Decimal) for value in disabled.values())

    # Seed 1 again (maybe_run_clearing): not a cadence tick, so the static branch returns before clearing.
    # Added 2026-09-28 (021 stage 3) in place of the removed adaptive seed (the policy deciding not to clear).
    monkeypatch.setattr(tick_module.settings, "CLEARING_ENABLED", True)
    cadence_coordinator = unit_tick(_clearing_every_n_ticks=3, _real_clearing_time_budget_ms=250)
    cadence_coordinator._run_clearing = run_clearing
    off_cadence = await cadence_coordinator.maybe_run_clearing(
        session=_AsyncSession(),
        run_id="run-1",
        run=_make_run(tick_index=1),
        equivalents=["USD"],
        planned_len=0,
        tick_t0=0.0,
    )
    assert off_cadence == {"USD": Decimal("0")}
    assert all(isinstance(value, Decimal) for value in off_cadence.values())

    # Seed 2 (_execute_clearing_with_timeout): the static runner fails before committing anything, so the
    # seeded dict is what the caller gets.
    failed = await static_coordinator.maybe_run_clearing(
        session=_AsyncSession(),
        run_id="run-1",
        run=_make_run(tick_index=1),
        equivalents=["USD"],
        planned_len=0,
        tick_t0=0.0,
    )
    assert failed == {"USD": Decimal("0")}
    assert all(isinstance(value, Decimal) for value in failed.values())
