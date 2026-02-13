from __future__ import annotations

import asyncio
import logging
import threading

import pytest

from app.core.simulator.models import RunRecord
from app.core.simulator.real_tick_clearing_coordinator import RealTickClearingCoordinator
from app.core.simulator.real_tick_orchestrator import RealTickOrchestrator


class _DummyRunner:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._logger = logging.getLogger("test_pending_clearing")
        self._real_tick_clearing_coordinator = RealTickClearingCoordinator(
            lock=self._lock,
            logger=self._logger,
            clearing_every_n_ticks=1,
            real_clearing_time_budget_ms=1,
            clearing_policy="static",
        )


@pytest.mark.asyncio
async def test_await_pending_clearing_cancels_after_grace(monkeypatch) -> None:
    # Keep test fast: cap hard timeout to 1s => grace 0.5s
    monkeypatch.setenv("SIMULATOR_REAL_CLEARING_HARD_TIMEOUT_SEC", "1")

    runner = _DummyRunner()
    orch = RealTickOrchestrator(runner)  # type: ignore[arg-type]

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
