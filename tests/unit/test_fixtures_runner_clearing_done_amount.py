from __future__ import annotations

import threading
import random
from datetime import datetime, timezone

from app.core.simulator.fixtures_runner import FixturesRunner
from app.core.simulator.models import RunRecord


class _DummySse:
    def __init__(self) -> None:
        self.events: list[dict] = []
        self._seq = 0

    def next_event_id(self, run: RunRecord) -> str:
        self._seq += 1
        return f"evt_{run.run_id}_{self._seq:06d}"

    def broadcast(self, run_id: str, payload: dict) -> None:
        self.events.append(payload)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def test_fixtures_runner_clearing_done_includes_cleared_amount() -> None:
    run = RunRecord(
        run_id="run_1",
        scenario_id="scenario_1",
        mode="fixtures",
        state="running",
    )

    run._rng = random.Random(0)
    run._edges_by_equivalent = {"UAH": [("alice", "bob")]}

    run.sim_time_ms = 1_000
    run._clearing_pending_done_at_ms = 0
    run._clearing_pending_plan_id_by_eq = {"UAH": "plan_123"}

    sse = _DummySse()
    lock = threading.RLock()

    runner = FixturesRunner(lock=lock, get_run=lambda _run_id: run, sse=sse, utc_now=_utc_now)

    runner.tick_fixtures_events("run_1")

    done_events = [e for e in sse.events if e.get("type") == "clearing.done"]
    assert len(done_events) == 1

    done = done_events[0]
    assert done.get("equivalent") == "UAH"
    assert done.get("plan_id") == "plan_123"

    # UI uses cleared_amount to show the flyout label.
    assert done.get("cleared_amount") == "10.00"
    assert done.get("cleared_cycles") == 1

    # Runner should clear pending state.
    assert run._clearing_pending_done_at_ms is None
    assert run._clearing_pending_plan_id_by_eq == {}
