import logging
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

import pytest

from app.core.payments.service import PaymentService, StagedPaymentResult
from app.core.simulator.edge_patch_builder import EdgePatchBuilder
from app.core.simulator.models import RunRecord
from app.core.simulator.real_payments_executor import RealPaymentsExecutor
from app.core.simulator.real_tick_payments_coordinator import (
    RealTickPaymentsCoordinator,
)
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

    def apply_once(self) -> bool:
        self.calls += 1
        raise RuntimeError("receiver effect failed")


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
