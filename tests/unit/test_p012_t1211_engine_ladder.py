"""T1211 fix round: the engine's depth ladder must survive to the execution loop.

Both re-review slices found the same defect in the first edition of the engine ladder: it
laddered the PREFLIGHT call only, while the execution `while` re-found at full depth on
every iteration - the reviewer instrumented `find_depths=[4, 6, 6]` on the existing
partial-failure scenario.  The preflight answer never survived to execution, and
long-cycle-only graphs paid one extra detector query on top of the old cost.

This pin asserts the DEPTH SEQUENCE, which - as the reviewer noted - no prior engine test
did: the perimeter census counts calls and `partial_failure` counts outcomes, so both
stayed green while the ladder decorated a call whose result was thrown away.  The sample
was implementations, not values: the ladder's only behavioral pin sat on `auto_clear`, the
other of the two executors.

Expected sequence for one clearable triangle at `max_depth=6`:

    [4, 4, 6]

preflight short rung (finds the triangle) -> first loop iteration consumes the preflight
answer (NO call) -> after clearing, refresh: short rung (now empty) -> widen (empty) ->
loop ends.

MUTATIONS THIS CATCHES, each by breaking the exact sequence:
- "first iteration also re-finds" (the reviewed defect): [4, 4, 4, 6];
- "later iterations do not refresh": the loop would re-execute the stale answer, changing
  both the sequence and `execute_calls`;
- "loop re-finds at full depth, no ladder": [4, 6, ...].

The harness is the one `test_real_clearing_engine_partial_failure.py` established; the
service double records depths instead of failures.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.core.simulator.models import RunRecord
from app.core.simulator.real_clearing_engine import RealClearingEngine
from tests.unit.test_real_clearing_engine_partial_failure import (
    _EdgePatchBuilder,
    _SessionContext,
    _SseCapture,
    _VizHelper,
)


class _DepthRecordingService:
    instance: "_DepthRecordingService | None" = None

    def __init__(self, _session) -> None:
        self.find_depths: list[int] = []
        self.execute_calls = 0
        self.cycle = [{"debtor": "alice", "creditor": "bob", "amount": "11.00"}]
        type(self).instance = self

    async def find_cycles(
        self, _equivalent: str, *, max_depth: int, allowed_participant_pids=None
    ) -> list:
        self.find_depths.append(int(max_depth))
        # One triangle, available until it has been cleared once.
        if self.execute_calls == 0:
            return [self.cycle]
        return []

    async def execute_clearing(self, _cycle) -> bool:
        return (await self.execute_clearing_with_amount(_cycle)) is not None

    async def execute_clearing_with_amount(
        self, _cycle, *, allowed_participant_pids=None
    ) -> Decimal | None:
        self.execute_calls += 1
        return Decimal("5.00")


@pytest.mark.asyncio
async def test_the_ladder_survives_to_the_execution_loop() -> None:
    sse = _SseCapture()
    run = RunRecord(
        run_id="ladder-depths-run",
        scenario_id="scenario",
        mode="real",
        state="running",
    )
    run.tick_index = 7
    run._real_viz_by_eq["USD"] = _VizHelper()
    run._edges_by_equivalent = {"USD": [("bob", "alice")]}

    engine = RealClearingEngine(
        lock=threading.RLock(),
        sse=sse,
        utc_now=lambda: datetime(2026, 8, 25, tzinfo=timezone.utc),
        logger=logging.getLogger(__name__),
        edge_patch_builder=_EdgePatchBuilder(),
        clearing_max_depth_limit=6,
        clearing_max_fx_edges_limit=8,
        real_clearing_time_budget_ms=10_000,
    )

    async def _apply_trust_growth(**kwargs):
        return SimpleNamespace(updated_count=0)

    async def _edge_patch(**_kwargs):
        return []

    def _broadcast(**_kwargs) -> None:
        return None

    cleared = await engine.tick_real_mode_clearing(
        None,
        run_id=run.run_id,
        run=run,
        equivalents=["USD"],
        apply_trust_growth=_apply_trust_growth,
        build_edge_patch_for_equivalent=_edge_patch,
        broadcast_topology_edge_patch=_broadcast,
        async_session_local=lambda: _SessionContext(),
        clearing_service_cls=_DepthRecordingService,
    )

    service = _DepthRecordingService.instance
    assert service is not None
    assert cleared == {"USD": 5.0}
    assert service.execute_calls == 1, (
        f"one triangle, one execution; got {service.execute_calls} - more means the loop "
        f"re-executed a stale answer instead of refreshing"
    )
    assert service.find_depths == [4, 4, 6], (
        f"depth sequence must be [4 (preflight, finds), 4 (refresh after clearing, empty), "
        f"6 (widen, empty)]; got {service.find_depths!r}. [4, 4, 4, 6] means the first loop "
        f"iteration re-found instead of consuming the preflight; a leading [4, 6] pair or "
        f"bare [6]s mean the ladder is decorative again - the reviewed defect measured "
        f"find_depths=[4, 6, 6]."
    )
