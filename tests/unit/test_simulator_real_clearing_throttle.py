import asyncio
import logging
import threading
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.core.simulator.models import RunRecord
from app.core.simulator.real_runner import RealRunner


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class _DummySession:
    async def rollback(self) -> None:  # pragma: no cover
        return


class _DummySessionCtx:
    async def __aenter__(self) -> _DummySession:
        return _DummySession()

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


class _DummySse:
    def next_event_id(self, run: RunRecord) -> str:
        # Minimal deterministic increment.
        run._event_seq += 1
        return f"e{run._event_seq}"

    def broadcast(self, run_id: str, payload: dict) -> None:
        return None


class _Edge:
    def __init__(self, amount: Decimal) -> None:
        self.amount = amount


class _DummyClearingService:
    def __init__(self, session) -> None:
        self._n = 0

    async def find_cycles(self, equivalent_code: str, max_depth: int = 6):
        # Return 12 executable cycles, then stop.
        if self._n >= 12:
            return []
        self._n += 1
        return [[_Edge(Decimal("1"))]]

    async def execute_clearing(self, cycle) -> bool:
        return True


@pytest.mark.asyncio
async def test_tick_real_mode_clearing_yields_to_event_loop(monkeypatch) -> None:
    """Long clearing bursts should not block the event loop (soft-yield via sleep(0))."""

    import app.core.simulator.real_runner as real_runner_mod

    run = RunRecord(run_id="r1", scenario_id="s1", mode="real", state="running")
    run.tick_index = 1

    runner = RealRunner(
        lock=threading.RLock(),
        get_run=lambda _run_id: run,
        get_scenario_raw=lambda _scenario_id: {},
        sse=_DummySse(),
        artifacts=None,
        utc_now=_utc_now,
        publish_run_status=lambda _run_id: None,
        db_enabled=lambda: True,
        actions_per_tick_max=20,
        clearing_every_n_ticks=25,
        real_max_consec_tick_failures_default=3,
        real_max_timeouts_per_tick_default=3,
        real_max_errors_total_default=10,
        logger=logging.getLogger(__name__),
    )

    # Avoid time-budget early exit to ensure we hit the yield points.
    runner._real_clearing_time_budget_ms = 0

    monkeypatch.setattr(
        real_runner_mod.db_session, "AsyncSessionLocal", lambda: _DummySessionCtx()
    )
    monkeypatch.setattr(real_runner_mod, "ClearingService", _DummyClearingService)

    sleep_calls: list[float] = []

    async def _sleep(dt: float):
        sleep_calls.append(float(dt))
        return None

    monkeypatch.setattr(asyncio, "sleep", _sleep)

    out = await runner.tick_real_mode_clearing(
        None,
        run_id="r1",
        run=run,
        equivalents=["UAH"],
    )

    assert out["UAH"] > 0.0
    # With 12 cycles: yields should happen at least at cycles 5 and 10.
    assert sleep_calls.count(0.0) >= 2
