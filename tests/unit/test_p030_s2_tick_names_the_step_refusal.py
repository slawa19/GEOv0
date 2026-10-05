"""030 S2, §15 review `T3092` finding 1: the tick keeps the reason of the clearing executor's step refusal.

A pass stopped by `ClearingOccurrenceRefused("occurrence_amount_not_in_step")` (F-030-1) is a run error - the database
holds debts finer than the step and is to be reseeded - and its record names the refusal instead of the generic
`CLEARING_ERROR` / "Internal server error". It is not a money stop: the run's error counters move.

Counter-check: any other failure of the pass keeps the sanitised `CLEARING_ERROR` with no private detail
(`tests/unit/test_tick_clearing_publishes_progress.py`, the `geo` cell).
"""

from __future__ import annotations

import pytest

from app.core.clearing.runner import ClearingPassError, ClearingPassResult, InterruptReason
from app.core.clearing.service import OCCURRENCE_AMOUNT_NOT_IN_STEP, ClearingOccurrenceRefused
from tests.unit.test_tick_clearing_publishes_progress import _run, _SseCapture, _tick


@pytest.mark.asyncio
async def test_the_tick_records_the_step_refusal_by_name(monkeypatch) -> None:
    run = _run("step-refusal-run", 4)

    async def _clearing_pass(_session_factory, equivalent, *, allowed_participant_pids, on_committed, deadline):
        refusal = ClearingOccurrenceRefused(OCCURRENCE_AMOUNT_NOT_IN_STEP)
        result = ClearingPassResult(
            equivalent=equivalent, status="interrupted", reason=InterruptReason.ERROR, committed=(),
            remaining_cycles=1, remaining_v_edge_atoms=1_500_000, plans=1, distributed_exclusive=False,
        )
        raise ClearingPassError(result, refusal)

    async def _no_growth(**_kwargs):
        raise AssertionError("nothing was committed, so nothing grows")

    tick = _tick(monkeypatch, _SseCapture(), _clearing_pass, _no_growth)
    committed: dict = {}
    await tick._run_clearing(session=None, run_id=run.run_id, run=run, equivalents=["USD"], committed=committed)

    assert committed == {}
    assert run.errors_total == 1, "a step refusal is a run error, not a skipped money stop"
    assert run.last_error is not None
    assert run.last_error["code"] == "CLEARING_REFUSED", run.last_error
    assert OCCURRENCE_AMOUNT_NOT_IN_STEP in run.last_error["message"], run.last_error
    assert set(run.last_error) == {"code", "message", "at"}, "the record keeps the SimulatorLastError shape"
