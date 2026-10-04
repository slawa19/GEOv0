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
from app.core.simulator.run_lifecycle import RunLifecycle
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


async def _restart(run: RunRecord) -> None:
    """The real `RunLifecycle.restart` (024 Sh6 fix-delta, §15 P2): a new tick sequence starts at 0."""

    async def _no_heartbeat(_run_id: str) -> None:
        return None

    unused = dict.fromkeys(["new_run_id", "get_scenario_raw", "edges_by_equivalent"])
    artifacts = type("A", (), {"start_events_writer": lambda _s, _r: None})()  # restart restarts it (028 F-028-4)
    await RunLifecycle(
        lock=unit_tick()._runner._lock, runs={run.run_id: run}, set_active_run_id=lambda *_: None,
        utc_now=lambda: None, sse=type("S", (), {"prune_event_buffer_locked": lambda _s, _r: None})(),
        heartbeat_loop=_no_heartbeat, publish_run_status=lambda _: None, run_to_status=lambda _: None,
        get_run_status_payload_json=lambda _: {}, real_max_in_flight_default=1, get_max_active_runs=lambda: 0,
        get_max_run_records=lambda: 0, logger=None, artifacts=artifacts, **unused,
    ).restart(run.run_id)


@MODE_B
@pytest.mark.parametrize(
    "previous_sequence",
    [None, 7],
)
@pytest.mark.asyncio
async def test_a_failed_tick_commit_leaves_the_tail_to_the_final_flush(db_session, monkeypatch, previous_sequence) -> None:
    monkeypatch.setattr(simulator_storage, "db_enabled", lambda: True)
    factory = sessionmaker_of(db_session)
    monkeypatch.setattr(app_db_session, "AsyncSessionLocal", factory)
    run = RunRecord(run_id=f"t2416-{uuid.uuid4().hex[:8]}", scenario_id="s", mode="real", state="running", owner_id="t2416")
    run.tick_index, run.sim_time_ms, run._real_last_tick_storage_flushed_tick = 7, 7000, -1
    tick = unit_tick(_get_run=lambda _run_id: run)
    if previous_sequence is not None:  # tick 7 of the old sequence was flushed; restart; the new tick is 1
        run._real_last_tick_storage_flushed_tick = previous_sequence
        await _restart(run)
        run.tick_index = 1
    tick_no = run.tick_index

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
        all(restored) and mark == tick_no,
        f"after the failed commit the final flush wrote (metrics, bottlenecks)={restored} with the tick marked "
        f"{mark} before it: the tick was marked flushed although its commit never happened",
    )
