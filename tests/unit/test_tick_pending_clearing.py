"""The tick's bounded grace for a clearing task left running by the previous tick.

Programme 021 stage 4 (`T2105`): renamed from `test_real_tick_orchestrator_pending_clearing.py` and moved from
`RealTickOrchestrator` (with a `RealTickClearingCoordinator` for the hard timeout) onto `RealTick`, which owns both;
the assertions are unchanged.
"""

from __future__ import annotations

import asyncio

import pytest

from app.core.simulator.models import RunRecord
from tests.simulator_tick_stand import unit_tick


@pytest.mark.asyncio
async def test_await_pending_clearing_cancels_after_grace(monkeypatch) -> None:
    # Keep test fast: cap hard timeout to 1s => grace 0.5s
    monkeypatch.setattr("app.config.settings.SIMULATOR_REAL_CLEARING_HARD_TIMEOUT_SEC", 1)

    orch = unit_tick(_clearing_every_n_ticks=1, _real_clearing_time_budget_ms=1)

    run = RunRecord(run_id="r1", scenario_id="s1", mode="real", state="running")
    run.tick_index = 123

    async def _slow_clearing():
        await asyncio.sleep(10)
        return {"UAH": 1.0}

    task = asyncio.create_task(_slow_clearing())
    run._real_clearing_task = task

    await orch._await_pending_clearing(run.run_id, run=run)

    assert run._real_clearing_task is None
    assert task.done() or task.cancelled()
