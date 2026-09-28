"""The tick clearing engine publishes durable progress before a failure or a cancellation propagates.

MIGRATED 2026-09-28, programme 023 slice (d) (spec 023, "Срез (d)"): the engine no longer detects cycles and calls
the v1 executor itself - it runs ONE pass of the common runner per equivalent (`clearing_pass`, the runner entry
`run_clearing_pass` in production). The service doubles this module used (`find_cycles` then
`execute_clearing_with_amount`, flipping on the first call after the first execution) have no call site left; the
same five shapes are held at the runner seam, as the runner's contract produces them (decision 10): the first
occurrence handed off through `on_committed`, then

* `geo` - the pass stops on a GeoException (`ClearingPassError`): the progress is finalised (trust growth, one
  `clearing.done`), then the error takes its classification (a run error, sanitised);
* `cancelled_find` / `cancelled_execute` - the pass is cancelled (`ClearingPassCancelled`): progress is published
  without patches, no trust growth, the cancellation is preserved, no run error. In the runner both are one
  shape - cancellation between or during occurrences - and are kept as two ids for the history of this file;
* `cancelled_finalize` - the pass completes and the cancellation lands in trust growth;
* `committed_cancel` - the occurrence became durable while the caller was being cancelled (`after_cancellation`).

The assertions on accounting, the published amount, the direction of `cycle_edges` and the run-error bookkeeping
are unchanged. The engine against the REAL runner and PostgreSQL:
`tests/integration/test_p023_d_tick_driver_through_runner_postgres.py`.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.core.clearing.runner import (
    ClearingPassCancelled,
    ClearingPassError,
    ClearingPassResult,
    CommittedEdge,
    CommittedOccurrence,
    InterruptReason,
)
from app.core.simulator.models import RunRecord
from app.core.simulator.real_clearing_engine import RealClearingEngine
from app.utils.exceptions import GeoException

ALICE = uuid.uuid5(uuid.NAMESPACE_DNS, "alice.p023d")
BOB = uuid.uuid5(uuid.NAMESPACE_DNS, "bob.p023d")


class _ScalarResult:
    def scalars(self):
        return self

    def all(self) -> list:
        return []


class _Session:
    async def execute(self, _statement) -> _ScalarResult:
        return _ScalarResult()


class _SessionContext:
    async def __aenter__(self) -> _Session:
        return _Session()

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None


class _SseCapture:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def next_event_id(self, run: RunRecord) -> str:
        run._event_seq += 1
        return f"event-{run._event_seq}"

    def broadcast(self, _run_id: str, payload: dict) -> None:
        self.events.append(payload)


class _VizHelper:
    precision = 2

    async def maybe_refresh_quantiles(self, *_args, **_kwargs) -> None:
        return None

    async def compute_node_patches(self, *_args, **_kwargs) -> list:
        return []


class _EdgePatchBuilder:
    async def build_edge_patch_for_pairs(self, **_kwargs) -> list:
        return []


def _occurrence(amount: Decimal, *, after_cancellation: bool = False) -> CommittedOccurrence:
    """alice owes bob: the runner's progress edge, debtor -> creditor by participant UUID."""

    return CommittedOccurrence(
        occurrence_id=str(uuid.uuid4()),
        plan_id=uuid.uuid4(),
        ordinal=0,
        amount_atoms=int(amount.scaleb(8)),
        edges=(CommittedEdge(uuid.uuid4(), ALICE, BOB),),
        after_cancellation=after_cancellation,
    )


def _result(committed, *, status: str, reason) -> ClearingPassResult:
    return ClearingPassResult(
        equivalent="USD",
        status=status,
        reason=reason,
        committed=tuple(committed),
        remaining_cycles=0 if status == "complete" else 1,
        remaining_v_edge_atoms=0 if status == "complete" else 100,
        plans=1,
        distributed_exclusive=False,
    )


def _run(run_id: str, tick: int) -> RunRecord:
    run = RunRecord(run_id=run_id, scenario_id="scenario", mode="real", state="running")
    run.tick_index = tick
    run._real_viz_by_eq["USD"] = _VizHelper()
    run._edges_by_equivalent = {"USD": [("bob", "alice")]}
    run._real_participants = [(ALICE, "alice"), (BOB, "bob")]
    return run


def _engine(sse) -> RealClearingEngine:
    return RealClearingEngine(
        lock=threading.RLock(),
        sse=sse,
        utc_now=lambda: datetime(2026, 8, 8, tzinfo=timezone.utc),
        logger=logging.getLogger(__name__),
        edge_patch_builder=_EdgePatchBuilder(),
        clearing_max_fx_edges_limit=8,
        real_clearing_time_budget_ms=10_000,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure_kind",
    [
        "geo",
        "cancelled_find",
        "cancelled_execute",
        "cancelled_finalize",
        "committed_cancel",
    ],
)
async def test_partial_clearing_is_finalized_before_failure_propagates(
    failure_kind: str,
) -> None:
    sse = _SseCapture()
    run = _run("partial-clearing-run", 7)
    engine = _engine(sse)
    runner_calls = 0

    async def _clearing_pass(_session_factory, equivalent, *, allowed_participant_pids, on_committed, deadline):
        nonlocal runner_calls
        runner_calls += 1
        assert equivalent == "USD" and deadline is not None
        first = _occurrence(Decimal("5.00"), after_cancellation=failure_kind == "committed_cancel")
        on_committed(first)
        if failure_kind == "geo":
            failure = GeoException("private clearing failure detail")
            assert failure.code == "E010"
            raise ClearingPassError(_result([first], status="interrupted", reason=InterruptReason.ERROR), failure)
        if failure_kind in {"cancelled_find", "cancelled_execute", "committed_cancel"}:
            raise ClearingPassCancelled(_result([first], status="interrupted", reason=InterruptReason.CANCELLED))
        return _result([first], status="complete", reason=None)

    trust_growth_calls = 0

    async def _apply_trust_growth(**kwargs):
        nonlocal trust_growth_calls
        trust_growth_calls += 1
        assert kwargs["cleared_amount_per_edge"] == {("bob", "alice"): 5.0}
        if failure_kind == "cancelled_finalize":
            raise asyncio.CancelledError
        return SimpleNamespace(updated_count=0)

    async def _unexpected_edge_patch(**_kwargs):
        raise AssertionError("trust-growth edge patch must not run")

    def _unexpected_broadcast(**_kwargs) -> None:
        raise AssertionError("trust-growth broadcast must not run")

    call = engine.tick_real_mode_clearing(
        None,
        run_id=run.run_id,
        run=run,
        equivalents=["USD"],
        apply_trust_growth=_apply_trust_growth,
        build_edge_patch_for_equivalent=_unexpected_edge_patch,
        broadcast_topology_edge_patch=_unexpected_broadcast,
        async_session_local=lambda: _SessionContext(),
        clearing_pass=_clearing_pass,
    )
    if failure_kind.startswith("cancelled") or failure_kind == "committed_cancel":
        with pytest.raises(asyncio.CancelledError):
            await call
    else:
        cleared = await call
        assert cleared == {"USD": 5.0}

    assert runner_calls == 1
    expected_growth_calls = 0 if failure_kind in {
        "cancelled_find",
        "cancelled_execute",
        "committed_cancel",
    } else 1
    assert trust_growth_calls == expected_growth_calls
    if failure_kind.startswith("cancelled") or failure_kind == "committed_cancel":
        assert run.errors_total == 0
        assert run.last_error is None
    else:
        assert run.errors_total == 1
        assert run.last_error is not None
        assert run.last_error["code"] == "CLEARING_ERROR"
        assert run.last_error["message"] == "Internal server error"
        assert "private clearing failure detail" not in str(run.last_error)

    done_events = [event for event in sse.events if event["type"] == "clearing.done"]
    assert len(done_events) == 1
    assert done_events[0]["equivalent"] == "USD"
    assert done_events[0]["cleared_cycles"] == 1
    assert done_events[0]["cleared_amount"] == "5.00"
    assert done_events[0]["cycle_edges"] == [{"from": "bob", "to": "alice"}]


# --- p007_t715: the cleared volume leaves this engine as exact Decimal -------


# 19 significant digits: binary64 cannot hold it, so a single `float(...)` on the
# way out would change the value.
_TOO_PRECISE_FOR_FLOAT = Decimal("12345678901.12345678")


def test_exact_amount_probe_is_beyond_float() -> None:
    """Anti-vacuum: the probe must actually be unrepresentable as a float."""

    assert Decimal(str(float(_TOO_PRECISE_FOR_FLOAT))) != _TOO_PRECISE_FOR_FLOAT


@pytest.mark.asyncio
async def test_cleared_volume_is_returned_as_exact_decimal() -> None:
    """`float(cleared_amount_dec)` used to narrow the clearing volume here.

    The volume feeds the `clearing_volume` metric series, which the domain model
    declares as an amount, so it must stay Decimal all the way out (spec 007,
    T715 / finding B-D1-002).
    """

    run = _run("exact-clearing-run", 3)

    async def _clearing_pass(_session_factory, equivalent, *, allowed_participant_pids, on_committed, deadline):
        # Only USD has a cycle; EUR clears nothing this tick.
        if equivalent != "USD":
            return _result([], status="complete", reason=None)
        occurrence = _occurrence(_TOO_PRECISE_FOR_FLOAT)
        on_committed(occurrence)
        return _result([occurrence], status="complete", reason=None)

    async def _apply_trust_growth(**_kwargs):
        return SimpleNamespace(updated_count=0)

    async def _edge_patch(**_kwargs) -> list:
        return []

    def _broadcast(**_kwargs) -> None:
        return None

    cleared = await _engine(_SseCapture()).tick_real_mode_clearing(
        None,
        run_id=run.run_id,
        run=run,
        equivalents=["USD", "EUR"],
        apply_trust_growth=_apply_trust_growth,
        build_edge_patch_for_equivalent=_edge_patch,
        broadcast_topology_edge_patch=_broadcast,
        async_session_local=lambda: _SessionContext(),
        clearing_pass=_clearing_pass,
    )

    assert cleared["USD"] == _TOO_PRECISE_FOR_FLOAT
    assert isinstance(cleared["USD"], Decimal)
    # An equivalent that cleared nothing is a measured zero, still Decimal:
    # a float seed would re-narrow it before the metric writer ever sees it.
    assert isinstance(cleared["EUR"], Decimal)
    assert cleared["EUR"] == Decimal("0")
