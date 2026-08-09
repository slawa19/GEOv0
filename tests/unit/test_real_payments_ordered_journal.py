import asyncio
import logging
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

import pytest

from app.core.payments.service import PaymentService, StagedPaymentResult
from app.core.simulator.edge_patch_builder import EdgePatchBuilder
from app.core.simulator.models import RunRecord
from app.core.simulator.real_payments_executor import (
    DeferredRealPaymentEffects,
    RealPaymentsExecutor,
    RealPaymentsResult,
    _PaymentObservation,
)
from app.core.simulator.real_tick_payments_coordinator import (
    RealTickPaymentsCoordinator,
)
from app.core.simulator.sse_broadcast import SseEventEmitter
from app.schemas.payment import PaymentResult
from app.utils.exceptions import BadRequestException, TimeoutException


@dataclass(frozen=True)
class _Action:
    seq: int
    equivalent: str
    sender_pid: str
    receiver_pid: str
    amount: str


class _Nested:
    def __init__(self, session: "_Session") -> None:
        self.session = session

    async def __aenter__(self):
        self.session.nested_begins += 1
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.session.nested_ends += 1
        return False


class _Session:
    def __init__(self) -> None:
        self.nested_begins = 0
        self.nested_ends = 0
        self.commits = 0
        self.rollbacks = 0
        self.staged: list[str] = []
        self.persisted: list[str] = []

    def begin_nested(self):
        return _Nested(self)

    async def execute(self, *args, **kwargs):
        raise RuntimeError("viz queries disabled")

    async def commit(self) -> None:
        self.commits += 1
        self.persisted.extend(self.staged)
        self.staged.clear()

    async def rollback(self) -> None:
        self.rollbacks += 1
        self.staged.clear()


class _ControlledRollbackSession(_Session):
    def __init__(self, *, rollback_error: Exception | None = None) -> None:
        super().__init__()
        self.rollback_started = asyncio.Event()
        self.release_rollback = asyncio.Event()
        self.rollback_error = rollback_error

    async def rollback(self) -> None:
        self.rollback_started.set()
        await self.release_rollback.wait()
        self.rollbacks += 1
        if self.rollback_error is not None:
            raise self.rollback_error
        self.staged.clear()


class _Sse:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def next_event_id(self, run: RunRecord) -> str:
        run._event_seq += 1
        return f"evt_{run.run_id}_{run._event_seq:06d}"

    def broadcast(self, _run_id: str, payload: dict) -> None:
        self.events.append(payload)


class _ExplodingPaymentEffect:
    def __init__(self) -> None:
        self.calls = 0
        self.cache_invalidations = 0

    def invalidate_routing_cache_once(self) -> bool:
        self.cache_invalidations += 1
        return True

    def apply_once(self) -> bool:
        self.calls += 1
        raise RuntimeError("receiver effect failed")


class _CountingPaymentEffect:
    def __init__(self) -> None:
        self.calls = 0
        self.cache_invalidations = 0

    def invalidate_routing_cache_once(self) -> bool:
        self.cache_invalidations += 1
        return True

    def apply_once(self) -> bool:
        self.calls += 1
        return True


class _StaticPaymentsExecutor:
    def __init__(self, result: RealPaymentsResult) -> None:
        self.result = result

    async def execute_planned_payments(self, **_kwargs) -> RealPaymentsResult:
        return self.result


def _payment_result(*, to_pid: str, amount: str) -> PaymentResult:
    return PaymentResult(
        tx_id=f"tx-{to_pid}",
        status="COMMITTED",
        **{"from": "A"},
        to=to_pid,
        equivalent="UAH",
        amount=amount,
        routes=None,
        error=None,
        created_at=datetime.now(timezone.utc).isoformat(),
        committed_at=None,
    )


def _executor(sse: _Sse) -> RealPaymentsExecutor:
    logger = logging.getLogger(__name__)
    return RealPaymentsExecutor(
        lock=threading.RLock(),
        sse=sse,  # type: ignore[arg-type]
        utc_now=lambda: datetime.now(timezone.utc),
        logger=logger,
        edge_patch_builder=EdgePatchBuilder(logger=logger),
        should_warn_this_tick=lambda *_args, **_kwargs: False,
        sim_idempotency_key=lambda **kwargs: f"idem-{kwargs['seq']}",
    )


def _run() -> RunRecord:
    run = RunRecord(
        run_id="ordered-journal",
        scenario_id="scenario",
        mode="real",
        state="running",
        started_at=datetime.now(timezone.utc),
    )
    run.tick_index = 1
    run.sim_time_ms = 1000
    return run


@pytest.mark.asyncio
async def test_timeout_stops_tick_but_harvests_and_rolls_back_successful_sibling(
    monkeypatch,
):
    session = _Session()
    sse = _Sse()
    run = _run()
    injected: list[str] = []

    async def _staged(self, _sender_id, *, to_pid, amount, **_kwargs):
        injected.append(to_pid)
        if to_pid == "B":
            raise TimeoutException("timeout")
        session.staged.append("tx-success")
        return StagedPaymentResult(
            result=_payment_result(to_pid=to_pid, amount=amount),
            post_commit_effects=_ExplodingPaymentEffect(),  # type: ignore[arg-type]
        )

    monkeypatch.setattr(
        PaymentService,
        "create_payment_internal_staged",
        _staged,
        raising=True,
    )

    async def _fail_run(_run_id: str, _code: str, _message: str) -> None:
        run.state = "error"

    coordinator = RealTickPaymentsCoordinator(
        lock=threading.RLock(),
        logger=logging.getLogger(__name__),
    )
    actions = [
        _Action(0, "UAH", "A", "B", "1.00"),
        _Action(1, "UAH", "A", "C", "2.00"),
    ]
    phase, should_stop = await coordinator.run_payments_phase(
        session=session,
        run_id=run.run_id,
        run=run,
        scenario={},
        participants=[(uuid.uuid4(), "A")],
        equivalents=["UAH"],
        load_debt_snapshot_by_pid=lambda *_args: _empty_snapshot(),
        plan_payments=lambda *_args: actions,
        payments_executor=_executor(sse),
        max_in_flight=2,
        max_timeouts_per_tick=1,
        max_errors_total=100,
        fail_run=_fail_run,
    )

    assert should_stop is True
    assert phase.committed == 1
    assert phase.timeouts == 1
    assert injected == ["B", "C"]
    assert session.nested_begins == session.nested_ends == 2
    assert session.rollbacks == 1
    assert session.staged == []
    assert session.persisted == []
    assert [event.get("type") for event in sse.events] == ["tx.failed"]
    assert run.committed_total == 0


async def _empty_snapshot() -> dict:
    return {}


def _stop_requested_result(
    *,
    sse: _Sse,
    run: RunRecord,
) -> tuple[RealPaymentsResult, DeferredRealPaymentEffects, _CountingPaymentEffect]:
    logger = logging.getLogger(__name__)
    committed_effect = _CountingPaymentEffect()
    deferred = DeferredRealPaymentEffects(
        lock=threading.RLock(),
        emitter=SseEventEmitter(
            sse=sse,  # type: ignore[arg-type]
            utc_now=lambda: datetime.now(timezone.utc),
            logger=logger,
        ),
        logger=logger,
        utc_now=lambda: datetime.now(timezone.utc),
        run_id=run.run_id,
        run=run,
        items=[
            _PaymentObservation(
                seq=0,
                outcome="committed",
                equivalent="UAH",
                sender_pid="A",
                receiver_pid="B",
                amount="1.00",
                edges=[{"from": "A", "to": "B"}],
                payment_effects=committed_effect,  # type: ignore[arg-type]
            ),
            _PaymentObservation(
                seq=1,
                outcome="rejected",
                equivalent="UAH",
                sender_pid="A",
                receiver_pid="C",
                amount="2.00",
                edges=[{"from": "A", "to": "C"}],
                error_code="PAYMENT_REJECTED",
                error_details={"message": "rejected"},
            ),
        ],
    )
    result = RealPaymentsResult(
        committed=1,
        rejected=1,
        errors=0,
        timeouts=0,
        stall_ticks=0,
        per_eq={},
        per_eq_route={},
        per_eq_edge_stats={},
        deferred_effects=deferred,
        stop_requested=True,
    )
    return result, deferred, committed_effect


def _run_stop_requested_phase(
    *,
    session: _ControlledRollbackSession,
    run: RunRecord,
    result: RealPaymentsResult,
):
    coordinator = RealTickPaymentsCoordinator(
        lock=threading.RLock(),
        logger=logging.getLogger(__name__),
    )
    return coordinator.run_payments_phase(
        session=session,
        run_id=run.run_id,
        run=run,
        scenario={},
        participants=[(uuid.uuid4(), "A")],
        equivalents=["UAH"],
        load_debt_snapshot_by_pid=lambda *_args: _empty_snapshot(),
        plan_payments=lambda *_args: [],
        payments_executor=_StaticPaymentsExecutor(result),  # type: ignore[arg-type]
        max_in_flight=1,
        max_timeouts_per_tick=0,
        max_errors_total=100,
        fail_run=lambda *_args: None,
    )


@pytest.mark.asyncio
async def test_stop_requested_rollback_failure_resolves_unknown_once_and_propagates():
    session = _ControlledRollbackSession(
        rollback_error=RuntimeError("stop rollback failed")
    )
    session.release_rollback.set()
    sse = _Sse()
    run = _run()
    result, deferred, committed_effect = _stop_requested_result(sse=sse, run=run)

    with pytest.raises(RuntimeError, match="stop rollback failed"):
        await _run_stop_requested_phase(session=session, run=run, result=result)

    assert session.rollbacks == 1
    assert committed_effect.calls == 0
    assert committed_effect.cache_invalidations == 1
    assert [event.get("type") for event in sse.events] == ["tx.failed"]
    assert run.committed_total == 0
    assert run.rejected_total == 1
    assert deferred.apply_after_unknown_transaction_outcome() is False
    assert deferred.apply_after_rollback() is False


@pytest.mark.asyncio
async def test_stop_requested_double_cancellation_bounds_rollback_wait():
    session = _ControlledRollbackSession()
    sse = _Sse()
    run = _run()
    result, deferred, committed_effect = _stop_requested_result(sse=sse, run=run)
    task = asyncio.create_task(
        _run_stop_requested_phase(session=session, run=run, result=result)
    )

    await session.rollback_started.wait()
    task.cancel("first cancellation")
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    task.cancel("second cancellation")
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    with pytest.raises(asyncio.CancelledError):
        await task

    assert session.rollbacks == 0
    assert committed_effect.calls == 0
    assert committed_effect.cache_invalidations == 1
    assert [event.get("type") for event in sse.events] == ["tx.failed"]
    assert run.committed_total == 0
    assert run.rejected_total == 1
    assert deferred.apply_after_rollback() is False
    assert deferred.apply_after_unknown_transaction_outcome() is False

    session.release_rollback.set()
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_commit_publishes_mixed_outcomes_in_seq_order_and_isolates_errors(
    monkeypatch,
):
    session = _Session()
    sse = _Sse()
    run = _run()
    exploding_effect = _ExplodingPaymentEffect()
    injected: list[str] = []

    async def _staged(self, _sender_id, *, to_pid, amount, **_kwargs):
        injected.append(to_pid)
        if to_pid == "C":
            raise BadRequestException("rejected")
        session.staged.append("tx-success")
        return StagedPaymentResult(
            result=_payment_result(to_pid=to_pid, amount=amount),
            post_commit_effects=exploding_effect,  # type: ignore[arg-type]
        )

    monkeypatch.setattr(
        PaymentService,
        "create_payment_internal_staged",
        _staged,
        raising=True,
    )
    actions = [
        _Action(0, "UAH", "A", "B", "1.00"),
        _Action(1, "UAH", "A", "C", "2.00"),
    ]
    result = await _executor(sse).execute_planned_payments(
        session=session,
        run_id=run.run_id,
        run=run,
        planned=actions,
        equivalents=["UAH"],
        sender_id_by_pid={"A": uuid.uuid4()},
        max_in_flight=2,
        max_timeouts_per_tick=0,
        fail_run=lambda *_args: None,
    )

    assert injected == ["B", "C"]
    assert sse.events == []
    await session.commit()
    assert result.deferred_effects is not None
    assert result.deferred_effects.apply_after_commit() is True

    assert exploding_effect.calls == 1
    assert [event.get("type") for event in sse.events] == [
        "tx.updated",
        "tx.failed",
    ]
    assert session.persisted == ["tx-success"]
    assert run.committed_total == 1
    assert run.rejected_total == 1
    assert run.attempts_total == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("resolution", ["commit", "rollback"])
async def test_idempotent_committed_replay_observation_follows_outer_outcome(
    monkeypatch,
    resolution: str,
):
    session = _Session()
    sse = _Sse()
    run = _run()

    async def _staged(self, _sender_id, *, to_pid, amount, **_kwargs):
        return StagedPaymentResult(
            result=_payment_result(to_pid=to_pid, amount=amount),
            post_commit_effects=None,
        )

    monkeypatch.setattr(
        PaymentService,
        "create_payment_internal_staged",
        _staged,
        raising=True,
    )

    result = await _executor(sse).execute_planned_payments(
        session=session,
        run_id=run.run_id,
        run=run,
        planned=[_Action(0, "UAH", "A", "B", "1.00")],
        equivalents=["UAH"],
        sender_id_by_pid={"A": uuid.uuid4()},
        max_in_flight=1,
        max_timeouts_per_tick=0,
        fail_run=lambda *_args: None,
    )

    assert result.committed == 1
    assert result.deferred_effects is not None
    assert len(result.deferred_effects.items) == 1
    assert sse.events == []
    assert run.attempts_total == 0
    assert run.committed_total == 0

    if resolution == "commit":
        await session.commit()
        assert result.deferred_effects.apply_after_commit() is True
        assert [event.get("type") for event in sse.events] == ["tx.updated"]
        assert run.attempts_total == 1
        assert run.committed_total == 1
        assert result.deferred_effects.apply_after_rollback() is False
    else:
        await session.rollback()
        assert result.deferred_effects.apply_after_rollback() is True
        assert sse.events == []
        assert run.attempts_total == 0
        assert run.committed_total == 0
        assert result.deferred_effects.apply_after_commit() is False
