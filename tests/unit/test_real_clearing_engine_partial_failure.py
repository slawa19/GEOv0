from __future__ import annotations

import asyncio
import logging
import threading
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.core.clearing.service import ClearingCommittedAfterCancellation
from app.core.simulator.models import RunRecord
from app.core.simulator.real_clearing_engine import RealClearingEngine
from app.utils.exceptions import GeoException


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


class _SuccessThenE010Service:
    instance: "_SuccessThenE010Service | None" = None
    failure_kind = "geo"

    def __init__(self, _session) -> None:
        self.execute_calls = 0
        self.find_calls = 0
        self.cycle = [
            {
                "debtor": "alice",
                "creditor": "bob",
                # Deliberately stale candidate: the service result is authoritative.
                "amount": "11.00",
            }
        ]
        type(self).instance = self

    async def find_cycles(
        self, _equivalent: str, *, max_depth: int, allowed_participant_pids=None
    ) -> list:
        # 2026-08-22 / p010: the tick must pass the run perimeter; a double that swallowed
        # the keyword would keep passing if it stopped.
        assert allowed_participant_pids is None or isinstance(allowed_participant_pids, set)
        assert max_depth >= 1
        self.find_calls += 1
        if self.failure_kind == "cancelled_find" and self.find_calls > 2:
            raise asyncio.CancelledError
        if self.failure_kind == "cancelled_finalize" and self.find_calls > 2:
            return []
        return [self.cycle]

    async def _execute(self):
        self.execute_calls += 1
        if self.failure_kind == "committed_cancel" and self.execute_calls == 1:
            raise ClearingCommittedAfterCancellation(
                tx_id="clearing-committed",
                cleared_amount=Decimal("5.00"),
            )
        if self.execute_calls == 1:
            return Decimal("5.00")
        if self.failure_kind == "cancelled_execute":
            raise asyncio.CancelledError
        failure = GeoException("private clearing failure detail")
        assert failure.code == "E010"
        raise failure

    async def execute_clearing(self, _cycle) -> bool:
        return (await self._execute()) is not None

    async def execute_clearing_with_amount(
        self, _cycle, *, allowed_participant_pids=None
    ) -> Decimal | None:
        return await self._execute()


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
    run = RunRecord(
        run_id="partial-clearing-run",
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
        utc_now=lambda: datetime(2026, 8, 8, tzinfo=timezone.utc),
        logger=logging.getLogger(__name__),
        edge_patch_builder=_EdgePatchBuilder(),
        clearing_max_depth_limit=6,
        clearing_max_fx_edges_limit=8,
        real_clearing_time_budget_ms=10_000,
    )
    _SuccessThenE010Service.failure_kind = failure_kind

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
        clearing_service_cls=_SuccessThenE010Service,
    )
    if failure_kind.startswith("cancelled") or failure_kind == "committed_cancel":
        with pytest.raises(asyncio.CancelledError):
            await call
    else:
        cleared = await call
        assert cleared == {"USD": 5.0}

    assert _SuccessThenE010Service.instance is not None
    expected_execute_calls = 1 if failure_kind in {
        "cancelled_find",
        "cancelled_finalize",
        "committed_cancel",
    } else 2
    assert _SuccessThenE010Service.instance.execute_calls == expected_execute_calls
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


class _ExactAmountService:
    """Clears one cycle for an amount no float can represent, then stops."""

    def __init__(self, _session) -> None:
        self.calls = 0

    async def find_cycles(
        self, equivalent: str, *, max_depth: int, allowed_participant_pids=None
    ) -> list:
        # Only USD has a cycle; EUR clears nothing this tick.
        if self.calls or str(equivalent) != "USD":
            return []
        return [[{"debtor": "alice", "creditor": "bob", "amount": "1.00"}]]

    async def execute_clearing_with_amount(
        self, _cycle, *, allowed_participant_pids=None
    ) -> Decimal | None:
        self.calls += 1
        return _TOO_PRECISE_FOR_FLOAT


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

    run = RunRecord(
        run_id="exact-clearing-run",
        scenario_id="scenario",
        mode="real",
        state="running",
    )
    run.tick_index = 3
    run._real_viz_by_eq["USD"] = _VizHelper()
    run._edges_by_equivalent = {"USD": [("bob", "alice")]}

    engine = RealClearingEngine(
        lock=threading.RLock(),
        sse=_SseCapture(),
        utc_now=lambda: datetime(2026, 8, 20, tzinfo=timezone.utc),
        logger=logging.getLogger(__name__),
        edge_patch_builder=_EdgePatchBuilder(),
        clearing_max_depth_limit=6,
        clearing_max_fx_edges_limit=8,
        real_clearing_time_budget_ms=10_000,
    )

    async def _apply_trust_growth(**_kwargs):
        return SimpleNamespace(updated_count=0)

    async def _edge_patch(**_kwargs) -> list:
        return []

    def _broadcast(**_kwargs) -> None:
        return None

    cleared = await engine.tick_real_mode_clearing(
        None,
        run_id=run.run_id,
        run=run,
        equivalents=["USD", "EUR"],
        apply_trust_growth=_apply_trust_growth,
        build_edge_patch_for_equivalent=_edge_patch,
        broadcast_topology_edge_patch=_broadcast,
        async_session_local=lambda: _SessionContext(),
        clearing_service_cls=_ExactAmountService,
    )

    assert cleared["USD"] == _TOO_PRECISE_FOR_FLOAT
    assert isinstance(cleared["USD"], Decimal)
    # An equivalent that cleared nothing is a measured zero, still Decimal:
    # a float seed would re-narrow it before the metric writer ever sees it.
    assert isinstance(cleared["EUR"], Decimal)
    assert cleared["EUR"] == Decimal("0")
