"""024 Sh6 `T2416.3`: the tick tail is marked flushed only by a CONFIRMED commit (§19.5 class 2, diagnostics).

The mark was set before the tick's commit; a failed commit then made the final flush of `runtime.stop`
(`RealRunnerImpl.flush_pending_storage` -> `RealTick.flush_pending_storage`) skip the lost tick.
PostgreSQL, `MODE_B`: the tick's session, the independent reader and the final flush share one clone.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

import app.core.simulator.storage as simulator_storage
import app.db.session as app_db_session
from app.core.simulator.models import RunRecord
from app.db.models.simulator_storage import SimulatorRunBottleneck, SimulatorRunMetric
from tests.conftest import MODE_B, sessionmaker_of
from tests.p019_support import require_target
from tests.simulator_tick_stand import unit_tick


async def _rows(session, run_id: str) -> tuple[int, int]:
    counts = [
        int(await session.scalar(select(func.count()).select_from(model).where(model.run_id == run_id)))
        for model in (SimulatorRunMetric, SimulatorRunBottleneck)
    ]
    return counts[0], counts[1]


@MODE_B
@pytest.mark.asyncio
async def test_a_failed_tick_commit_leaves_the_tail_to_the_final_flush(db_session, monkeypatch) -> None:
    monkeypatch.setattr(simulator_storage, "db_enabled", lambda: True)
    factory = sessionmaker_of(db_session)
    monkeypatch.setattr(app_db_session, "AsyncSessionLocal", factory)
    run = RunRecord(run_id=f"t2416-{uuid.uuid4().hex[:8]}", scenario_id="s", mode="real", state="running")
    run.tick_index, run.sim_time_ms, run._real_last_tick_storage_flushed_tick = 7, 7000, -1
    tick = unit_tick(_get_run=lambda _run_id: run)

    staged: list[tuple[int, int]] = []

    async def _commit_fails_before_committing() -> None:
        staged.append(await _rows(db_session, run.run_id))
        raise RuntimeError("tick commit failed before it committed")

    monkeypatch.setattr(db_session, "commit", _commit_fails_before_committing)
    with pytest.raises(RuntimeError, match="before it committed"):
        await tick.persist_tick_tail(
            session=db_session, run=run, equivalents=["UAH"], tick_t0=0.0, planned_len=1, committed=1,
            rejected=0, errors=1, timeouts=0, per_eq={"UAH": {"committed": 1, "rejected": 0, "errors": 1}},
            per_eq_metric_values={"UAH": {"avg_route_length": 1.0}},
            per_eq_edge_stats={"UAH": {("a", "b"): {"attempts": 2, "errors": 1}}},
        )
    # Controls: the real writers staged non-empty rows in the tick's session, the rollback succeeded (the
    # session is usable and empty), and an independent session sees nothing committed.
    assert staged and all(staged[0]), f"the writers staged nothing: {staged!r}"
    assert await _rows(db_session, run.run_id) == (0, 0)
    async with factory() as independent:
        assert await _rows(independent, run.run_id) == (0, 0)

    await tick.flush_pending_storage(run.run_id)  # the final flush `runtime.stop` makes

    async with factory() as independent:
        restored = await _rows(independent, run.run_id)
    mark = run._real_last_tick_storage_flushed_tick
    require_target(
        all(restored) and mark == 7,
        f"after the failed commit the final flush wrote (metrics, bottlenecks)={restored} with the tick marked "
        f"{mark} before it: the tick was marked flushed although its commit never happened",
    )
