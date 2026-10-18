"""044 stage 2 (decision 3; Verification plan (a), (в)): N runs of N visitors live side by side; the N+1-th visitor gets
409 `global_active_limit` whose message names the number of places and whose `details` keep their shape.

The production runtime and lifecycle (`create_run`); storage writes are stubbed and the runs are stopped before any
tick, so nothing touches the database. WHAT IT DOES NOT SEE: the perimeters of the runs (044 `T4405`, its own tests),
the HTTP envelope (the route maps `ConflictException` as for every 409), the client text (Simulator UI v2 unit tests).
"""

from __future__ import annotations

import pytest

import app.core.simulator.storage as simulator_storage
from app.core.simulator.runtime import runtime
from app.utils.exceptions import ConflictException


@pytest.mark.asyncio
async def test_n_visitors_run_side_by_side_and_the_next_one_is_told_the_number(monkeypatch) -> None:
    async def no_io(*_a, **_kw) -> None:
        return None

    monkeypatch.setattr(simulator_storage, "upsert_run", no_io)
    monkeypatch.setattr(simulator_storage, "sync_artifacts", no_io)
    monkeypatch.setattr(runtime._real_runner, "flush_pending_storage", no_io)
    monkeypatch.setattr(runtime, "_max_active_runs", 3)
    monkeypatch.setattr(runtime, "_max_active_runs_per_owner", 1)
    created: list[str] = []
    try:
        for i in range(3):
            created.append(await runtime.create_run(scenario_id="clearing-demo-10", mode="real", owner_id=f"anon:p044-s2-{i}"))
        assert [runtime.get_run(r).state for r in created] == ["running"] * 3, "three visitors, three live runs"

        with pytest.raises(ConflictException) as refused:
            await runtime.create_run(scenario_id="clearing-demo-10", mode="real", owner_id="anon:p044-s2-late")
        assert refused.value.details == {"conflict_kind": "global_active_limit", "max_active_runs": 3, "active_runs": 3}
        assert refused.value.message == "Global active runs limit reached: all 3 places are taken"

        await runtime.stop(created[0])  # a place frees up: the late visitor gets a run
        created.append(await runtime.create_run(scenario_id="clearing-demo-10", mode="real", owner_id="anon:p044-s2-late"))
        assert runtime.get_run(created[-1]).state == "running"
    finally:
        for run_id in created:
            await runtime.stop(run_id)
        with runtime._lock:
            for run_id in created:
                runtime._runs.pop(run_id, None)
            for i in [*range(3), "late"]:
                runtime._active_run_id_by_owner.pop(f"anon:p044-s2-{i}", None)
