"""The tick's clearing step publishes durable progress before a failure or a cancellation propagates.

MOVED 2026-09-28, programme 021 `T2109` (was `tests/unit/test_real_clearing_engine_partial_failure.py`): the clearing
driver `RealClearingEngine` is gone and the tick calls the common runner itself (`tick.py::RealTick._run_clearing`,
one `run_clearing_pass` per equivalent). The stand is the same - a double of the runner entry, a session factory
that reads nothing, a viz helper at precision 2 - installed where the tick reads them at call time
(`tests/simulator_tick_stand.py::clearing_unit_tick`) instead of passed to the driver as `clearing_pass=` and
`async_session_local=`. Every assertion is kept; the driver's return value (`{"USD": 5.0}`) is now the tick's
committed volume (`committed`), which is what the tick reports as `clearing_volume`.

History (023 slice (d), 2026-09-28): the service doubles this module used (`find_cycles` then
`execute_clearing_with_amount`) were replaced by the runner seam; the same five shapes are held there, as the
runner's contract produces them (decision 10): the first occurrence handed off through `on_committed`, then

* `geo` - the pass stops on a GeoException (`ClearingPassError`): the progress is finalised (trust growth, one
  `clearing.done`), then the error takes its classification (a run error, sanitised);
* `cancelled_find` / `cancelled_execute` - the pass is cancelled (`ClearingPassCancelled`): progress is published
  without patches, no trust growth, the cancellation is preserved, no run error. In the runner both are one
  shape - cancellation between or during occurrences - and are kept as two ids for the history of this file;
* `cancelled_finalize` - the pass completes and the cancellation lands in trust growth;
* `committed_cancel` - the occurrence became durable while the caller was being cancelled (`after_cancellation`).

The tick against the REAL runner and PostgreSQL: `tests/integration/test_p023_d_tick_driver_through_runner_postgres.py`.
"""

from __future__ import annotations

import asyncio
import uuid
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
from app.utils.exceptions import GeoException
from tests.simulator_tick_stand import clearing_unit_tick

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

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


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


def _tick(monkeypatch, sse, runner_pass, apply_trust_growth, **collaborators):
    return clearing_unit_tick(
        monkeypatch,
        sse=sse,
        session_factory=lambda: _SessionContext(),
        runner_pass=runner_pass,
        apply_trust_growth=apply_trust_growth,
        edge_patch_builder=_EdgePatchBuilder(),
        max_fx_edges=8,
        budget_ms=10_000,
        **collaborators,
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
    failure_kind: str, monkeypatch
) -> None:
    sse = _SseCapture()
    run = _run("partial-clearing-run", 7)
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
        assert "cleared_amount_per_edge" not in kwargs  # 028 F-028-33: no float volume
        if failure_kind == "cancelled_finalize":
            raise asyncio.CancelledError
        return SimpleNamespace(updated_count=0)

    async def _unexpected_edge_patch(**_kwargs):
        raise AssertionError("trust-growth edge patch must not run")

    def _unexpected_broadcast(**_kwargs) -> None:
        raise AssertionError("trust-growth broadcast must not run")

    tick = _tick(
        monkeypatch,
        sse,
        _clearing_pass,
        _apply_trust_growth,
        build_edge_patch_for_equivalent=_unexpected_edge_patch,
        broadcast_topology_edge_patch=_unexpected_broadcast,
    )
    committed: dict[str, Decimal] = {}
    call = tick._run_clearing(session=None, run_id=run.run_id, run=run, equivalents=["USD"], committed=committed)
    if failure_kind.startswith("cancelled") or failure_kind == "committed_cancel":
        with pytest.raises(asyncio.CancelledError):
            await call
    else:
        await call
    # The committed occurrence is the tick's volume on every ending, the cancelled ones included.
    assert committed == {"USD": Decimal("5.00")}

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


# --- p007_t715: the cleared volume leaves the tick's clearing step as exact Decimal -------


# 19 significant digits: binary64 cannot hold it, so a single `float(...)` on the
# way out would change the value.
_TOO_PRECISE_FOR_FLOAT = Decimal("12345678901.12345678")


def test_exact_amount_probe_is_beyond_float() -> None:
    """Anti-vacuum: the probe must actually be unrepresentable as a float."""

    assert Decimal(str(float(_TOO_PRECISE_FOR_FLOAT))) != _TOO_PRECISE_FOR_FLOAT


@pytest.mark.asyncio
async def test_cleared_volume_is_returned_as_exact_decimal(monkeypatch) -> None:
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

    tick = _tick(
        monkeypatch,
        _SseCapture(),
        _clearing_pass,
        _apply_trust_growth,
        build_edge_patch_for_equivalent=_edge_patch,
        broadcast_topology_edge_patch=_broadcast,
    )
    # The whole clearing step of the tick (cadence, the commit before clearing, the hard timeout): the volume it
    # returns is what the `clearing_volume` metric receives.
    cleared = await tick.maybe_run_clearing(
        session=_Session(),
        run_id=run.run_id,
        run=run,
        equivalents=["USD", "EUR"],
        planned_len=0,
        tick_t0=0.0,
    )

    assert cleared["USD"] == _TOO_PRECISE_FOR_FLOAT
    assert isinstance(cleared["USD"], Decimal)
    # An equivalent that cleared nothing is a measured zero, still Decimal:
    # a float seed would re-narrow it before the metric writer ever sees it.
    assert isinstance(cleared["EUR"], Decimal)
    assert cleared["EUR"] == Decimal("0")


# --- `clearing.done.cycle_edges`: creditor -> debtor by PID, from the runner's debtor -> creditor by UUID ---------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("topology", "expected"),
    [
        # No topology cache: the converted edge is published as is - the only case where the conversion alone decides
        # the direction (with a cache, an edge held reversed is flipped to the cache's direction).
        (None, [{"from": "bob", "to": "alice"}]),
        ([("bob", "alice")], [{"from": "bob", "to": "alice"}]),
        # The cache holds only the reverse: the topology's direction wins (the snapshot links are drawn from it).
        ([("alice", "bob")], [{"from": "alice", "to": "bob"}]),
        # An edge the cache does not hold at all is not published.
        ([("carol", "alice")], None),
    ],
    ids=["no-topology", "topology-agrees", "topology-reversed", "not-in-topology"],
)
async def test_cycle_edges_are_creditor_to_debtor_pids(monkeypatch, topology, expected) -> None:
    sse = _SseCapture()
    run = _run("edge-direction-run", 5)
    run._edges_by_equivalent = {} if topology is None else {"USD": topology}

    async def _clearing_pass(_session_factory, equivalent, *, allowed_participant_pids, on_committed, deadline):
        occurrence = _occurrence(Decimal("1.00"))  # alice owes bob: debtor alice, creditor bob
        on_committed(occurrence)
        return _result([occurrence], status="complete", reason=None)

    growth: list[dict] = []

    async def _apply_trust_growth(**kwargs):
        growth.append(kwargs)
        return SimpleNamespace(updated_count=0)

    tick = _tick(monkeypatch, sse, _clearing_pass, _apply_trust_growth)
    await tick._run_clearing(session=None, run_id=run.run_id, run=run, equivalents=["USD"], committed={})

    [done] = [event for event in sse.events if event["type"] == "clearing.done"]
    assert done["cycle_edges"] == expected
    # Trust growth sees the trust-line direction (creditor, debtor) whatever the topology cache says.
    [call] = growth
    assert call["touched_edges"] == {("bob", "alice")}


# --- the runner's caller deadline is the tick's clearing budget, per equivalent --------------------------------


@pytest.mark.asyncio
async def test_each_pass_gets_the_tick_budget_as_its_deadline(monkeypatch) -> None:
    """023 decision 10: the budget is the runner's caller deadline, checked before every cycle start.

    Budget 300 ms: each equivalent's pass is handed `now + 0.3 s` of the event loop's clock, measured when the pass
    starts - not one deadline for the whole tick, not the hard timeout, and not missing (the runner has no budget of
    its own). The run perimeter reaches every pass.

    THE CLOCK IS THE STAND'S (031 `T3102`, closing review `T3008` finding 3). Measured on the real loop clock the
    stand read `deadline - loop.time()` and bounded it by `0.3`, and `(t + 0.3) - t` exceeds `0.3` by a rounding
    unit for some `t` (`assert 0.3000000000029104 <= 0.3`, full tier 2026-10-06). The loop's `time` is replaced by a
    clock that only the passes advance, so each pass knows the exact reading its deadline was taken from and the
    comparison is equality, with no rounding to tolerate.
    """

    loop = asyncio.get_running_loop()
    clock = {"now": 1000.0}  # any reading: the comparison below repeats the product's own float expression
    monkeypatch.setattr(loop, "time", lambda: clock["now"])
    run = _run("deadline-run", 2)
    # 034 S2b: the tick takes the equivalent's viz helper (its precision) BEFORE the pass; the stand's session reads
    # nothing, so the second equivalent of this test gets its helper here, as `_run` gives USD its own.
    run._real_viz_by_eq["EUR"] = _VizHelper()
    seen: list[tuple[str, float, float, object]] = []

    async def _clearing_pass(_session_factory, equivalent, *, allowed_participant_pids, on_committed, deadline):
        seen.append((equivalent, clock["now"], deadline, allowed_participant_pids))
        clock["now"] += 0.05  # the second equivalent starts later, so a shared deadline would show
        await asyncio.sleep(0)
        return _result([], status="complete", reason=None)

    async def _apply_trust_growth(**_kwargs):
        raise AssertionError("nothing was committed: no trust growth")

    tick = clearing_unit_tick(
        monkeypatch,
        sse=_SseCapture(),
        session_factory=lambda: _SessionContext(),
        runner_pass=_clearing_pass,
        apply_trust_growth=_apply_trust_growth,
        budget_ms=300,
    )
    await tick._run_clearing(session=None, run_id=run.run_id, run=run, equivalents=["USD", "EUR"], committed={})

    assert [eq for eq, _, _, _ in seen] == ["USD", "EUR"]
    [(_, usd_start, usd_deadline, _), (_, eur_start, eur_deadline, _)] = seen
    assert eur_start > usd_start, "premise: the second pass starts later on the stand's clock"
    assert eur_deadline > usd_deadline, "one deadline shared by the whole tick"
    for _, start, deadline, scope in seen:
        assert deadline == start + 0.3, (start, deadline)  # the budget, from the reading at this pass's start
        assert scope == {"alice", "bob"}
