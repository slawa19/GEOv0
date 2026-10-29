"""Lifecycle component controls: refusal, resume and another run keep their state.

The heartbeat stand deliberately replaces money work; real tick/DB coverage is
in test_p053_restart_tick_bound_state_postgres.py.
"""

from decimal import Decimal
import uuid

import pytest

from app.core.simulator.models import EdgeClearingHistory
from app.core.simulator.runtime import runtime
from app.core.simulator.viz_patch_helper import VizPatchHelper
from app.utils.exceptions import ConflictException
from tests.unit.test_p024_heartbeat_follows_every_entry_into_running import (
    _started,
    clock as clock,
)


def _cached_state(run):
    helpers = {
        eq: VizPatchHelper(
            eq, uuid.uuid4(), 2, _last_refresh_tick=91, _mags_sorted=[100, 200]
        )
        for eq in ("UAH", "USD")
    }
    run._real_viz_by_eq.update(helpers)
    history = EdgeClearingHistory(
        Decimal("123.45"), clearing_count=7, last_clearing_tick=1
    )
    run._edge_clearing_history["edge"] = history
    run._real_consec_money_no_progress_ticks = 2
    return helpers, history


async def test_restart_resets_only_this_runs_tick_bound_state_and_not_resume(
    clock, monkeypatch  # noqa: F811 - imported pytest fixture
):
    monkeypatch.setattr(runtime, "_max_active_runs", 2)
    run = await _started(clock, "anon:p053-reset")
    other = await _started(clock, "anon:p053-sibling")
    helpers, history = _cached_state(run)
    sibling_helpers, sibling_history = _cached_state(other)
    await runtime.pause(run.run_id)
    await runtime.resume(run.run_id)
    assert run._real_viz_by_eq == helpers and history.last_clearing_tick == 1
    assert (
        run._real_consec_money_no_progress_ticks == 2
    ), "resume continues the same launch"
    await runtime.stop(run.run_id)
    await runtime.restart(run.run_id)
    assert run._real_consec_money_no_progress_ticks == 0
    assert run._real_viz_by_eq == {}
    assert (
        history.original_limit,
        history.clearing_count,
        history.last_clearing_tick,
    ) == (Decimal("123.45"), 7, -1)
    assert run._edge_clearing_history["edge"] is history
    assert other._real_viz_by_eq == sibling_helpers
    assert (
        sibling_history.last_clearing_tick == 1
        and other._real_consec_money_no_progress_ticks == 2
    )
    # A new unresolved conflict now starts at one, rather than stopping at the
    # old launch's limit. This invokes the real no-progress policy, not a DB conflict.
    monkey_limit = runtime._real_runner._real_max_consec_money_no_progress_limit
    try:
        runtime._real_runner._real_max_consec_money_no_progress_limit = 3
        await runtime._real_runner._tick._record_money_conflict_tick(
            run.run_id,
            run=run,
            conflict="test-unresolved",
            error=RuntimeError("component policy control"),
        )
        assert run.state == "running" and run._real_consec_money_no_progress_ticks == 1
        assert (
            "Consecutive ticks without money progress: 1." in run.last_error["message"]
        )
    finally:
        runtime._real_runner._real_max_consec_money_no_progress_limit = monkey_limit


@pytest.mark.parametrize("limit", ["owner", "global"])
async def test_refused_restart_preserves_cache_markers_and_counter(
    clock, monkeypatch, limit  # noqa: F811 - imported pytest fixture
):
    run = await _started(clock, "anon:p053-refused")
    await runtime.stop(run.run_id)
    helpers, history = _cached_state(run)
    if limit == "global":
        monkeypatch.setattr(runtime, "_max_active_runs", 1)
    other = await _started(
        clock, run.owner_id if limit == "owner" else "anon:p053-other"
    )
    with pytest.raises(ConflictException):
        await runtime.restart(run.run_id)
    assert run.state == "stopped" and run._real_viz_by_eq == helpers
    assert history.last_clearing_tick == 1 and history.clearing_count == 7
    assert run._real_consec_money_no_progress_ticks == 2
    await runtime.stop(other.run_id)
    await runtime.restart(run.run_id)
    assert run._real_viz_by_eq == {} and history.last_clearing_tick == -1
    assert run._real_consec_money_no_progress_ticks == 0
