"""Programme 015, T1525: an aborted payment and a rolled-back tick leave no money behind, on PostgreSQL.

WHAT IS UNDER TEST. Two money paths apply their effects inside a SAVEPOINT and rely on the root
transaction's rollback to undo them when something fails afterwards:

* `PaymentEngine.commit` applies each flow inside `_apply_flow`'s `begin_nested()`. When an invariant
  check after the flows fails, the engine rolls back and aborts: an ABORTED payment must leave every
  debt exactly as it was.
* The simulator tick's payments phase executes each staged payment inside `RealPaymentsExecutor`'s
  per-action `begin_nested()`. When the tick rolls back, no staged payment may remain in the database.

These were first written (2026-09-12) against SQLite, where a savepoint opened before the first write
was its own transaction and both properties were false until the T1525 transaction control. That
module is gone with SQLite (programme 017 stage 3, slice S3); its scenarios and assertions live here,
unchanged, and this module has always been their PostgreSQL run. A red result here is a money defect,
not a dialect quirk.

WHY THE STAND IS BUILT THIS WAY:

* Its own engine with the application's isolation level (`app/db/session.py`, SERIALIZABLE by
  default), not the savepoint-wrapped `db_session`.
* A real pool, not NullPool and not the savepoint-wrapped `db_session`. Under `db_session` an outer
  transaction survives every commit and rollback of the code under test, so "nothing was stored"
  would be true by construction of the fixture rather than by the application's rollback.
* Every verdict is read through a new session on a pooled connection after the working session is
  closed, and each scenario asserts its mechanism first (the flow wrote the debt inside the
  committing transaction; the staged payments were COMMITTED; the tick really took its rollback
  branch) - an absence is only evidence when the presence was observed first.
"""


from __future__ import annotations

import asyncio
import hashlib
import logging
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.payments.engine import PaymentEngine
from app.core.payments.router import PaymentRouter
from app.core.payments.service import PaymentService
from app.core.simulator.commit_resolution import resolve_rollback_under_cancellation
from app.core.simulator.edge_patch_builder import EdgePatchBuilder
from app.core.simulator.models import RunRecord
from app.core.simulator.real_debt_snapshot_loader import RealDebtSnapshotLoader
from app.core.simulator.real_payments_executor import RealPaymentsExecutor
from app.core.simulator.real_runner import RealRunner
from app.core.simulator.real_tick_payments_coordinator import RealTickPaymentsCoordinator
from app.db.models.audit_log import IntegrityAuditLog
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.prepare_lock import PrepareLock
from app.db.models.transaction import Transaction
from app.db.models.trustline import TrustLine
from app.utils.exceptions import IntegrityViolationException
from tests.debt_setup import purge_test_ledger

_PAYMENT = Decimal("7.00")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------------------------
# World: two participants, one equivalent, one trust line allowing sender -> receiver payments.
# --------------------------------------------------------------------------------------------


@dataclass
class _World:
    equivalent: Equivalent
    sender: Participant
    receiver: Participant
    tx_ids: set[str] = field(default_factory=set)


async def _seed_world(factory) -> _World:
    n = uuid.uuid4().hex[:8].upper()
    async with factory() as s:
        eq = Equivalent(code=f"T1525{n}", precision=2, is_active=True, metadata_={})
        sender = Participant(
            pid=f"T1525_S_{n}", display_name="Sender", public_key=f"pk_t1525_s_{n}",
            type="person", status="active", profile={},
        )
        receiver = Participant(
            pid=f"T1525_R_{n}", display_name="Receiver", public_key=f"pk_t1525_r_{n}",
            type="person", status="active", profile={},
        )
        s.add_all([eq, sender, receiver])
        await s.flush()
        # Trust line direction is creditor -> debtor: the receiver extends credit to the sender,
        # which is what lets the sender pay the receiver.
        s.add(
            TrustLine(
                from_participant_id=receiver.id,
                to_participant_id=sender.id,
                equivalent_id=eq.id,
                limit=Decimal("1000.00"),
                status="active",
            )
        )
        await s.commit()
    return _World(eq, sender, receiver)


async def _cleanup(factory, world: _World) -> None:
    ids = [world.sender.id, world.receiver.id]
    async with factory() as s:
        # The debts AND the journal rows that describe them, through the driver and BEFORE the
        # deletes below: `session.execute(delete(Debt))` is Core DML the write guard refuses
        # (that is `C2`), and `debt_operations.tx_id` RESTRICTs `transactions.tx_id`, so an
        # envelope still standing would block the transaction delete above it.
        await purge_test_ledger(s, equivalent_ids=[world.equivalent.id])
        tx_ids = set(world.tx_ids) | set(
            (
                await s.execute(select(Transaction.tx_id).where(Transaction.initiator_id.in_(ids)))
            ).scalars()
        )
        if tx_ids:
            await s.execute(delete(IntegrityAuditLog).where(IntegrityAuditLog.tx_id.in_(tx_ids)))
            await s.execute(delete(PrepareLock).where(PrepareLock.tx_id.in_(tx_ids)))
            await s.execute(delete(Transaction).where(Transaction.tx_id.in_(tx_ids)))
        await s.execute(delete(TrustLine).where(TrustLine.equivalent_id == world.equivalent.id))
        await s.execute(delete(Participant).where(Participant.id.in_(ids)))
        await s.execute(delete(Equivalent).where(Equivalent.id == world.equivalent.id))
        await s.commit()
    PaymentRouter.invalidate_cache(world.equivalent.code)


async def _stored_debts(factory, world: _World) -> dict[tuple[str, str], Decimal]:
    """Debts of the world's equivalent as the database holds them, read on a fresh connection."""
    pid_by_id = {world.sender.id: world.sender.pid, world.receiver.id: world.receiver.pid}
    async with factory() as fresh:
        rows = (
            await fresh.execute(
                select(Debt.debtor_id, Debt.creditor_id, Debt.amount).where(
                    Debt.equivalent_id == world.equivalent.id
                )
            )
        ).all()
    return {
        (pid_by_id.get(d, str(d)), pid_by_id.get(c, str(c))): Decimal(str(a)) for d, c, a in rows
    }


async def _stored_transactions(factory, world: _World) -> dict[str, str]:
    """tx_id -> state of every transaction the world's participants initiated, read fresh."""
    async with factory() as fresh:
        rows = (
            await fresh.execute(
                select(Transaction.tx_id, Transaction.state).where(
                    Transaction.initiator_id.in_([world.sender.id, world.receiver.id])
                )
            )
        ).all()
    return {tx_id: state for tx_id, state in rows}


def _patch_delta_check_to_report_drift(monkeypatch, world: _World) -> list[Decimal | None]:
    """Make the engine's own delta barrier fail, and record what the flows had written by then.

    A real drift cannot be produced without a real bug, so the barrier is replaced - but with the
    engine's own exception and its own details shape, raised from the engine's own call site, which
    runs after every `_apply_flow` of the payment. The replacement first reads, in the committing
    session, the debt the flow should have written: that is the stand's proof that the violation
    comes AFTER the money moved, not before.
    """

    observed: list[Decimal | None] = []

    async def _delta_check_reports_drift(self, *, equivalent_id, flows, net_positions_before):
        debt = await self._get_debt(world.sender.id, world.receiver.id, equivalent_id)
        observed.append(None if debt is None else Decimal(str(debt.amount)))
        raise IntegrityViolationException(
            "Per-participant delta check failed",
            details={
                "invariant": "PAYMENT_DELTA_DRIFT",
                "source": "delta_check",
                "equivalent": world.equivalent.code,
                "equivalent_id": str(equivalent_id),
                "total_drift": "0.01",
                "drifts": [],
            },
        )

    monkeypatch.setattr(PaymentEngine, "check_payment_delta", _delta_check_reports_drift)
    return observed


# --------------------------------------------------------------------------------------------
# (b) PaymentEngine.commit with an invariant violation after the flows.
# --------------------------------------------------------------------------------------------


@dataclass
class _CommitOutcome:
    raised: BaseException | None
    observed_in_commit: list[Decimal | None]
    debts_before: dict[tuple[str, str], Decimal]
    debts_after: dict[tuple[str, str], Decimal]
    transactions: dict[str, str]
    prepare_locks_left: int


async def _scenario_service_payment_violates_after_flows(factory, world, monkeypatch) -> _CommitOutcome:
    """The public payment path: `PaymentService` creates, prepares and commits in one session."""
    debts_before = await _stored_debts(factory, world)
    observed = _patch_delta_check_to_report_drift(monkeypatch, world)
    tx_id = f"t1525-svc-{uuid.uuid4().hex[:12]}"
    world.tx_ids.add(tx_id)
    raised: BaseException | None = None
    async with factory() as s:
        try:
            await PaymentService(s).create_payment_internal(
                world.sender.id,
                to_pid=world.receiver.pid,
                equivalent=world.equivalent.code,
                amount=str(_PAYMENT),
                idempotency_key=tx_id,
            )
        except IntegrityViolationException as exc:
            raised = exc
    return await _commit_outcome(factory, world, raised, observed, debts_before, tx_id)


async def _scenario_engine_commit_violates_after_flows(factory, world, monkeypatch) -> _CommitOutcome:
    """`PaymentEngine.commit` called on a fresh session for a payment the engine itself prepared.

    The NEW transaction row is built exactly as `PaymentService._create_payment_impl` builds it
    (step 3) and `PaymentEngine.prepare` writes the locks and the PREPARED state, so the commit sees
    state the application produces. The commit runs on its own session, as a separate request does.
    """
    debts_before = await _stored_debts(factory, world)
    tx_id = f"t1525-eng-{uuid.uuid4().hex[:12]}"
    world.tx_ids.add(tx_id)
    async with factory() as s:
        s.add(
            Transaction(
                id=uuid.uuid4(),
                tx_id=tx_id,
                idempotency_key=None,
                type="PAYMENT",
                initiator_id=world.sender.id,
                payload={
                    "from": world.sender.pid,
                    "to": world.receiver.pid,
                    "amount": str(_PAYMENT),
                    "equivalent": world.equivalent.code,
                    "routes": [
                        {"path": [world.sender.pid, world.receiver.pid], "amount": str(_PAYMENT)}
                    ],
                    "idempotency": {"key": tx_id, "fingerprint": "t1525"},
                },
                state="NEW",
            )
        )
        await s.commit()
        await PaymentEngine(s).prepare(
            tx_id, [world.sender.pid, world.receiver.pid], _PAYMENT, world.equivalent.id
        )
    async with factory() as s:
        prepared = await s.scalar(select(Transaction.state).where(Transaction.tx_id == tx_id))
        assert prepared == "PREPARED", f"stand: the engine did not prepare the payment ({prepared})"

    observed = _patch_delta_check_to_report_drift(monkeypatch, world)
    raised: BaseException | None = None
    async with factory() as s:
        try:
            await PaymentEngine(s).commit(tx_id)
        except IntegrityViolationException as exc:
            raised = exc
    return await _commit_outcome(factory, world, raised, observed, debts_before, tx_id)


async def _commit_outcome(factory, world, raised, observed, debts_before, tx_id) -> _CommitOutcome:
    async with factory() as fresh:
        locks_left = int(
            await fresh.scalar(
                select(func.count()).select_from(PrepareLock).where(PrepareLock.tx_id == tx_id)
            )
        )
    return _CommitOutcome(
        raised=raised,
        observed_in_commit=observed,
        debts_before=debts_before,
        debts_after=await _stored_debts(factory, world),
        transactions=await _stored_transactions(factory, world),
        prepare_locks_left=locks_left,
    )


def _assert_aborted_payment_left_no_debt(outcome: _CommitOutcome, world: _World) -> None:
    # Mechanism first: the barrier really failed, and it failed after the flow had written.
    assert isinstance(outcome.raised, IntegrityViolationException), outcome.raised
    assert outcome.observed_in_commit == [_PAYMENT], (
        "stand: the violation must be raised after `_apply_flow` wrote the debt, observed in the "
        f"committing session: {outcome.observed_in_commit}"
    )
    assert list(outcome.transactions.values()) == ["ABORTED"], outcome.transactions
    assert outcome.prepare_locks_left == 0
    # Verdict: an ABORTED payment must not have moved money.
    assert outcome.debts_after == outcome.debts_before, (
        f"the payment is ABORTED but its debt is stored: before={outcome.debts_before} "
        f"after={outcome.debts_after} - the flow's savepoint was committed by its own RELEASE"
    )


# --------------------------------------------------------------------------------------------
# (c) The simulator tick: staged payments in per-action savepoints, then the tick's rollback.
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _PlannedAction:
    seq: int
    equivalent: str
    sender_pid: str
    receiver_pid: str
    amount: str


class _Sse:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def next_event_id(self, run: RunRecord) -> str:
        run._event_seq += 1
        return f"e{run._event_seq}"

    def broadcast(self, _run_id: str, payload: dict[str, Any]) -> None:
        if isinstance(payload, dict):
            self.events.append(payload)


class _Artifacts:
    def write_real_tick_artifact(self, *a, **kw) -> None:
        return None

    def enqueue_event_artifact(self, *a, **kw) -> None:
        return None


def _run_for(world: _World, run_id: str) -> RunRecord:
    run = RunRecord(run_id=run_id, scenario_id="p015-t1525", mode="real", state="running")
    run.seed = 7
    run.tick_index = 1  # not a clearing tick
    run.sim_time_ms = 1_000
    run.intensity_percent = 100
    run._real_seeded = True
    run._real_participants = [(world.sender.id, world.sender.pid), (world.receiver.id, world.receiver.pid)]
    run._real_equivalents = [world.equivalent.code]
    run._real_viz_by_eq = {}
    return run


def _record_staged_payments(monkeypatch, world: _World) -> list[tuple[str, str]]:
    """Record (tx_id, status) of every staged payment the real executor makes. Pass-through."""
    staged: list[tuple[str, str]] = []
    original = PaymentService.create_payment_internal_staged

    async def _recording(self, *args, **kwargs):
        result = await original(self, *args, **kwargs)
        staged.append((str(result.result.tx_id), str(result.result.status)))
        world.tx_ids.add(str(result.result.tx_id))
        return result

    monkeypatch.setattr(PaymentService, "create_payment_internal_staged", _recording)
    return staged


@dataclass
class _TickOutcome:
    staged: list[tuple[str, str]]
    rollback_resolution: str | None
    published_types: list[str]
    last_error: dict[str, Any] | None
    debts_before: dict[tuple[str, str], Decimal]
    debts_after: dict[tuple[str, str], Decimal]
    transactions: dict[str, str]


async def _scenario_executor_then_tick_rollback(factory, world, monkeypatch) -> _TickOutcome:
    """The narrowest real shape: the tick's reads, the real executor, the tick's rollback call.

    The session only reads before the executor, as the payments phase does on SQLite (the debt
    snapshot; the owner-lock call returns early). The rollback is the orchestrator's own resolver
    with the phase's own observation callbacks, as at the tick's `except` branch.
    """
    debts_before = await _stored_debts(factory, world)
    staged = _record_staged_payments(monkeypatch, world)
    run = _run_for(world, f"t1525-exec-{uuid.uuid4().hex[:8]}")
    sse = _Sse()
    logger = logging.getLogger("tests.p015.t1525")
    executor = RealPaymentsExecutor(
        lock=threading.RLock(),
        sse=sse,  # type: ignore[arg-type]
        utc_now=_utc_now,
        logger=logger,
        edge_patch_builder=EdgePatchBuilder(logger=logger),
        should_warn_this_tick=lambda _run, key=None: False,
        sim_idempotency_key=lambda **kw: "t1525-sim-"
        + hashlib.sha256("|".join(f"{k}={v}" for k, v in sorted(kw.items())).encode()).hexdigest()[:40],
    )
    async with factory() as tick_session:
        await RealDebtSnapshotLoader().load_debt_snapshot_by_pid(
            session=tick_session,
            participants=run._real_participants,
            equivalents=[world.equivalent.code],
        )
        result = await asyncio.wait_for(
            executor.execute_planned_payments(
                session=tick_session,
                run_id=run.run_id,
                run=run,
                planned=[
                    _PlannedAction(0, world.equivalent.code, world.sender.pid, world.receiver.pid, str(_PAYMENT)),
                ],
                equivalents=[world.equivalent.code],
                sender_id_by_pid={world.sender.pid: world.sender.id},
                max_in_flight=1,
                max_timeouts_per_tick=0,
                fail_run=lambda *_a, **_kw: None,
            ),
            timeout=20.0,
        )
        assert result.deferred_effects is not None
        await resolve_rollback_under_cancellation(
            rollback=tick_session.rollback,
            on_rollback=result.deferred_effects.apply_after_rollback,
            on_unknown=result.deferred_effects.apply_after_unknown_transaction_outcome,
        )
        resolution = result.deferred_effects._resolution
    return _TickOutcome(
        staged=staged,
        rollback_resolution=resolution,
        published_types=[str(e.get("type")) for e in sse.events],
        last_error=None,
        debts_before=debts_before,
        debts_after=await _stored_debts(factory, world),
        transactions=await _stored_transactions(factory, world),
    )


_INJECTED_TICK_FAILURE = "T1525 stand: a failure right after the payments phase"


async def _scenario_real_tick_fails_after_payments(factory, world, monkeypatch) -> _TickOutcome:
    """The whole real tick of `RealRunner`, failing right after its payments have been staged.

    Nothing is stubbed on the money path: seeding is skipped because the world is already in the
    database, the planner plans, the coordinator reads the snapshot and runs the real executor.
    The failure is raised by the money boundary's own commit, once the payments have been staged,
    so the rollback under test is the one that discards the transaction those payments were staged
    in (programme 015 / P1 owns that boundary; see the comment at the injection point below for
    why the failure is no longer raised from clearing).
    """
    import app.db.session as app_db_session

    debts_before = await _stored_debts(factory, world)
    staged = _record_staged_payments(monkeypatch, world)
    monkeypatch.setattr(app_db_session, "AsyncSessionLocal", factory)

    scenario = {
        "equivalents": [world.equivalent.code],
        "participants": [{"id": world.sender.pid}, {"id": world.receiver.pid}],
        "trustlines": [
            {
                "from": world.receiver.pid,
                "to": world.sender.pid,
                "equivalent": world.equivalent.code,
                "limit": "1000.00",
                "status": "active",
            }
        ],
        "behaviorProfiles": [],
    }
    run = _run_for(world, f"t1525-tick-{uuid.uuid4().hex[:8]}")
    sse = _Sse()
    runner = RealRunner(
        lock=threading.RLock(),
        get_run=lambda _rid: run,
        get_scenario_raw=lambda _sid: scenario,
        sse=sse,
        artifacts=_Artifacts(),
        utc_now=_utc_now,
        publish_run_status=lambda _rid: None,
        db_enabled=lambda: True,
        actions_per_tick_max=3,
        clearing_every_n_ticks=10_000,
        real_max_consec_tick_failures_default=3,
        real_max_timeouts_per_tick_default=10,
        real_max_errors_total_default=50,
        logger=logging.getLogger("tests.p015.t1525.tick"),
    )

    phases: list[Any] = []
    original_phase = RealTickPaymentsCoordinator.run_payments_phase

    async def _capture_phase(self, **kwargs):
        res, should_stop = await original_phase(self, **kwargs)
        phases.append(res)
        return res, should_stop

    monkeypatch.setattr(RealTickPaymentsCoordinator, "run_payments_phase", _capture_phase)

    # WHERE THIS FAILURE IS INJECTED MOVED WITH PROGRAMME 015 / P1, 2026-09-12. It used to be
    # raised from `maybe_run_clearing`, that is, from the tick's TAIL. The tail now runs after the
    # money boundary's explicit commit, so a failure there can no longer roll payments back - that
    # is P1's hard right edge, and a test that still expected a rollback there would be asserting
    # the opposite of the money contract.
    #
    # The property this module owns is the T1525 one and it lives INSIDE the boundary: a payment
    # staged in a per-action savepoint must not survive the rollback of the transaction it was
    # staged in. The MONEY COMMIT is the last moment at which the tick can still roll them back,
    # so that is where the failure goes. Identifying it needs no knowledge of the boundary's
    # internals: the first commit that happens once a payment has been staged IS the money commit.
    #
    # Injecting it one step earlier - by raising from `run_payments_phase` itself - does not work,
    # and the reason is worth recording: the phase result never reaches the boundary, so there is
    # no observation buffer for it to resolve, and `rollback_resolution` stays None. The payments
    # are still rolled back correctly, but the half of this test that checks the tick REPORTED
    # them as rolled back would silently stop testing anything.
    failed_commits: list[int] = []
    original_commit = AsyncSession.commit

    async def _fail_the_money_commit(self):
        if staged and not failed_commits:
            failed_commits.append(1)
            raise RuntimeError(_INJECTED_TICK_FAILURE)
        return await original_commit(self)

    monkeypatch.setattr(AsyncSession, "commit", _fail_the_money_commit)

    await asyncio.wait_for(runner.tick_real_mode(run.run_id), timeout=30.0)

    assert len(phases) == 1, "stand: the payments phase did not run"
    assert failed_commits == [1], (
        "stand: the money commit was never reached, so no failure was injected and this proves "
        "nothing"
    )
    deferred = phases[0].deferred_effects
    return _TickOutcome(
        staged=staged,
        rollback_resolution=None if deferred is None else deferred._resolution,
        published_types=[str(e.get("type")) for e in sse.events],
        last_error=run.last_error,
        debts_before=debts_before,
        debts_after=await _stored_debts(factory, world),
        transactions=await _stored_transactions(factory, world),
    )


def _assert_rolled_back_tick_left_no_payment(outcome: _TickOutcome, *, via_tick: bool) -> None:
    # Mechanism first: payments were really committed inside the tick, and the tick really rolled
    # back and told its observers so.
    committed = [tx_id for tx_id, status in outcome.staged if status == "COMMITTED"]
    assert committed, f"stand: no staged payment was committed, nothing to roll back: {outcome.staged}"
    if via_tick:
        assert outcome.last_error is not None
        assert outcome.last_error.get("code") == "REAL_MODE_TICK_FAILED", outcome.last_error
        assert _INJECTED_TICK_FAILURE in str(outcome.last_error.get("message")), outcome.last_error
    assert outcome.rollback_resolution == "rollback", outcome.rollback_resolution
    assert "tx.updated" not in outcome.published_types, outcome.published_types
    # Verdict: what the tick reported as rolled back must not be in the database.
    assert (outcome.transactions, outcome.debts_after) == ({}, outcome.debts_before), (
        f"the tick rolled back but its payments are stored: transactions={outcome.transactions} "
        f"debts before={outcome.debts_before} after={outcome.debts_after} - each payment's "
        f"savepoint was committed by its own RELEASE"
    )



@pytest_asyncio.fixture
async def serializable_factory():
    from tests.conftest import TEST_DATABASE_URL, _ensure_schema_initialized

    await _ensure_schema_initialized()
    eng = create_async_engine(
        TEST_DATABASE_URL,
        pool_size=2,
        max_overflow=0,
        pool_timeout=10,
        isolation_level="SERIALIZABLE",
    )
    assert eng.dialect.name == "postgresql", eng.dialect.name
    factory = async_sessionmaker(
        bind=eng,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )
    try:
        yield factory
    finally:
        await eng.dispose()


@pytest.mark.asyncio
async def test_postgres_an_aborted_payment_commit_leaves_debts_unchanged(
    serializable_factory, monkeypatch
) -> None:
    world = await _seed_world(serializable_factory)
    try:
        outcome = await _scenario_engine_commit_violates_after_flows(
            serializable_factory, world, monkeypatch
        )
        _assert_aborted_payment_left_no_debt(outcome, world)
    finally:
        await _cleanup(serializable_factory, world)


@pytest.mark.asyncio
async def test_postgres_an_aborted_service_payment_leaves_debts_unchanged(
    serializable_factory, monkeypatch
) -> None:
    world = await _seed_world(serializable_factory)
    try:
        outcome = await _scenario_service_payment_violates_after_flows(
            serializable_factory, world, monkeypatch
        )
        _assert_aborted_payment_left_no_debt(outcome, world)
    finally:
        await _cleanup(serializable_factory, world)


@pytest.mark.asyncio
async def test_postgres_a_rolled_back_tick_leaves_no_payment_from_the_executor(
    serializable_factory, monkeypatch
) -> None:
    world = await _seed_world(serializable_factory)
    try:
        outcome = await _scenario_executor_then_tick_rollback(
            serializable_factory, world, monkeypatch
        )
        _assert_rolled_back_tick_left_no_payment(outcome, via_tick=False)
    finally:
        await _cleanup(serializable_factory, world)


@pytest.mark.asyncio
async def test_postgres_a_real_tick_failing_after_payments_leaves_no_payment(
    serializable_factory, monkeypatch
) -> None:
    world = await _seed_world(serializable_factory)
    try:
        outcome = await _scenario_real_tick_fails_after_payments(
            serializable_factory, world, monkeypatch
        )
        _assert_rolled_back_tick_left_no_payment(outcome, via_tick=True)
    finally:
        await _cleanup(serializable_factory, world)
