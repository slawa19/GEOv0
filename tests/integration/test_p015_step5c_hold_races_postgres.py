"""Step 5c on PostgreSQL: the integrity hold binds at the T1544 boundary, measured at SERIALIZABLE.

Only two outcomes are allowed for money racing the reaction that sets a hold: the money commits BEFORE the
hold's transaction commits, or the hold commits first and the money is refused. Since 019 stage 5
(`T1909`) the reaction takes NO advisory lock: the hold is one `UPDATE` of the equivalent row, and every
money writer - payment, tick, inject, the clearing in every attempt - reads that row `FOR SHARE` (the hold
in the same statement as `is_active`) and holds it through its commit. So, for payment and clearing alike:

* the writer has read the row: the hold's `UPDATE` queues on the WRITER'S transaction (measured in
  `pg_locks`, `transactionid`/`tuple`, blocker named by `pg_blocking_pids`) and commits after the money;
* the hold's `UPDATE` holds the row uncommitted: the writer's `FOR SHARE` queues on the REACTION'S
  transaction, meets 40001 when it commits, and the writer's retry owner refuses on a fresh snapshot;
* the writer's snapshot predates the hold but it has not read the row yet: it holds nothing the reaction
  needs, the hold commits first, and the writer's `FOR SHARE` meets 40001 and its retry refuses.

Until `T1909` the reaction held the equivalent owner lock through its commit and these races asserted "some
backend waits on an advisory lock"; those probes are replaced by the probe of the ACTUAL wait (spec,
"Изоляция, писатели и клиринг", item 6) plus the mechanism of the one lock order: the waiting WRITER
already holds its equivalent advisory lock (shared for a payment, exclusive for the clearing) while it
waits on the row, and the reaction / admin clear hold none.

Plus the reaction's confirmation in its OWN snapshot (the re-run is the confirmation, not the scheduled
verdict), the admin clear against a holder of the row, and both schema construction paths with the
migration's downgrade refusal. (The placement of the hold below the payment TTL branch was a contract of
`PaymentEngine.commit` over a durable `PREPARED` payment; 019 stage 4 removed both - see the note where
that test stood.)

THE STAND. The P1 stand's SERIALIZABLE engine (`factory`) - the shared test engine runs READ COMMITTED,
where the stale-snapshot races cannot be seen. Barriers are `asyncio.Event`s; waits are observed in
`pg_locks`, never slept. Every control asserts its premise, so none passes by never having raced.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, insert, select, text, update
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.core.clearing.service import ClearingService
from app.core.ledger import reconciliation
from app.core.ledger.reconciliation import (
    FAILED,
    HOLD_NOT_CONFIRMED,
    HOLD_SET,
    PASSED,
    run_scheduled_reconciliation,
    take_baseline,
)
from app.core.money_boundary import MoneyBoundary
from app.core.payments.service import PaymentService
from app.db.base import Base
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.transaction import Transaction
from app.db.reconciliation_tables import debt_reconciliation_results
from app.utils.exceptions import ConflictException, RetryablePaymentConflictException
from tests.integration.p019_interlock_support import (
    _no_advisory_lock_is_held,
    _seed_interlock_case,
    _use_serializable,
)
from tests.integration.test_p015_p1_money_replay_postgres import (  # noqa: F401 - `factory` is a fixture
    _OPENING,
    _forget_the_route_cache,
    _debts,
    _seed,
    _transactions,
    factory,
)
from tests.integration.test_p015_t1544_operator_stop_races_postgres import (  # noqa: F401 - fixture
    ADMIN,
    _advisory_modes,
    _assert_row_wait,
    _clearing_transactions,
    _retries_on_40001,
    _waiters_behind,
    admin_api,
)
from tests.ledger_corruption import corrupt
from tests.migrated_schema import REPO_ROOT, run_alembic_upgrade_head, scratch_databases
from tests.unit.test_p015_step5c_reaction_and_hold import hold_directly

# MODE B (017 stage 2c, T1702): every commit of this module lands in a clone dropped after the test,
# not in the tier database it shares with mode-A tests - see `tests/tier_on_a_clone.py`.
from tests.tier_on_a_clone import tier_sessions_on_a_clone  # noqa: E402,F401 - autouse fixture

HOLD = MoneyBoundary.EQUIVALENT_INTEGRITY_HOLD_REASON
_ATOM = Decimal("0.00000001")


@pytest.fixture(autouse=True)
def _barrier_budgets(monkeypatch):
    # The barriers hold locks for a moment; the default advisory-lock budgets are seconds and are not
    # what these controls are about.
    monkeypatch.setattr(settings, "COMMIT_TIMEOUT_SECONDS", 30)
    monkeypatch.setattr(settings, "PAYMENT_TOTAL_TIMEOUT_SECONDS", 60)


async def _around_the_application(session_factory, statement: str) -> None:
    """Commit `statement` with the journal's triggers OFF, through the named corruption helper.

    018 stage B1: the application's connection gets `GE001` for a write to `debts` outside an
    operation, so the one-atom fault (and its repair) is modelled the way it can still arise - an
    operator or a restore with the triggers off (`tests/ledger_corruption.py`). The helper takes no
    advisory lock, which the ordering test below requires of its repair.
    """

    await corrupt(session_factory.kw["bind"].url.render_as_string(hide_password=False), [statement])


async def _baseline_and_one_atom(factory, equivalent_id, *, debt_id=None) -> None:
    async with factory() as session:
        await take_baseline(session, equivalent_id)
        await session.commit()
    async with factory() as session:
        if debt_id is None:
            debt_id = (
                await session.execute(select(Debt.id).where(Debt.equivalent_id == equivalent_id))
            ).scalar_one()
    await _around_the_application(
        factory,
        f"UPDATE debts SET amount = amount + 0.00000001 WHERE id = '{uuid.UUID(str(debt_id))}'",
    )


async def _hold_of(factory, equivalent_id):
    async with factory() as session:
        return (
            await session.execute(
                select(Equivalent.integrity_hold_result_id).where(Equivalent.id == equivalent_id)
            )
        ).scalar_one()


def _pause_after_the_hold_is_written(monkeypatch) -> tuple[asyncio.Event, asyncio.Event, list[int]]:
    """The reaction stops with the hold UPDATE executed (the row lock held) and nothing committed; the
    list receives the reaction's backend pid, the blocker a waiting writer must name."""

    reached, release = asyncio.Event(), asyncio.Event()
    pid: list[int] = []
    original = reconciliation._set_integrity_hold

    async def _set_then_wait(session, equivalent_id, result_id):
        await original(session, equivalent_id, result_id)
        pid.append(int(await session.scalar(text("SELECT pg_backend_pid()"))))
        reached.set()
        await release.wait()

    monkeypatch.setattr(reconciliation, "_set_integrity_hold", _set_then_wait)
    return reached, release, pid


def _observe_commits_when_the_hold_is_written(monkeypatch, probe) -> list:
    """After the reaction's hold `UPDATE` returns (i.e. once it got the row), record `await probe()` - what
    another session sees committed at that moment. The order of COMMITS, which task completion is not."""

    seen: list = []
    original = reconciliation._set_integrity_hold

    async def _set_then_look(session, equivalent_id, result_id):
        await original(session, equivalent_id, result_id)
        seen.append(await probe())

    monkeypatch.setattr(reconciliation, "_set_integrity_hold", _set_then_look)
    return seen


def _assert_hold_refusal(exc: BaseException, code: str) -> None:
    assert isinstance(exc, ConflictException), repr(exc)
    assert not isinstance(exc, RetryablePaymentConflictException)
    assert exc.details.get("reason") == HOLD, exc.details
    assert exc.details.get("equivalents") == [code], exc.details


async def _finish(*tasks) -> None:
    for task in tasks:
        # A released task is let to FINISH first; cancelling one mid-commit leaves a connection idle in
        # transaction holding rows, and the cleanup then waits on it (T1544 stand, 2026-09-14).
        if task is not None and not task.done():
            await asyncio.wait([task], timeout=15)
        if task is not None and not task.done():
            task.cancel()
            await asyncio.wait([task], timeout=5)


# ── payment commit <-> hold ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_step5c_p_a_reaction_arriving_between_the_binding_and_the_money_phase_holds_first_and_the_payment_is_refused(
    factory, monkeypatch, caplog
) -> None:
    """The payment has bound (holds the equivalent lock SHARED) but not yet read the hold; the reaction arrives.

    HISTORY OF THIS SCHEDULE. Until 019 stage 3 it was `test_step5c_p_a_payment_commit_waiting_behind_the_
    reaction_is_refused_by_the_hold` ("HOLD FIRST"). Stages 3-4 held the owner lock exclusively from the
    binding phase to the commit and the reaction took it too, so the reaction waited and held only after
    the payment committed (`..._waits_for_the_payment`). Since stage 5 (`T1909`) the payment holds the
    equivalent lock SHARED and the reaction takes none: until its `FOR SHARE` read of the row the payment
    holds nothing the reaction needs, so the reaction HOLDS FIRST (while the payment is parked) and the
    payment is REFUSED by the hold - the other outcome T1546 allows. The payment's snapshot predates the
    hold, its `FOR SHARE` meets 40001, `pay()` retries on a fresh snapshot, and the hold refuses the
    admitted payment (stored `ABORTED`). The sibling below is the payment-first outcome.

    RED if the money phase read the hold without `FOR SHARE` (its stale snapshot says "not held" and it
    commits after the hold), or if the reaction waited on the payment's shared lock.
    """
    world = await _seed(factory)
    code = world.equivalent.code
    payment = reconcile = None
    release_commit = asyncio.Event()
    try:
        await _baseline_and_one_atom(factory, world.equivalent.id)
        prepared = asyncio.Event()
        payment_pid: list[int] = []
        original_commit = PaymentService._apply_payment

        # The barrier stands at the entry of the payment's money phase: the binding phase has taken the
        # equivalent lock (shared); the hold has not been read yet. The retry passes through.
        async def _commit_after_barrier(self, declaration, **kwargs):
            if not prepared.is_set():
                payment_pid.append(int(await self.session.scalar(text("SELECT pg_backend_pid()"))))
                prepared.set()
                await release_commit.wait()
            return await original_commit(self, declaration, **kwargs)

        monkeypatch.setattr(PaymentService, "_apply_payment", _commit_after_barrier)

        async def _pay(tx_id: str):
            async with factory() as session:
                return await PaymentService(session).create_payment_internal(
                    world.sender.id, to_pid=world.receiver.pid, equivalent=code, amount="10.00",
                    idempotency_key=tx_id,
                )

        tx_id = str(uuid.uuid4())
        completed: list[str] = []
        with caplog.at_level(logging.WARNING):
            payment = asyncio.create_task(_pay(tx_id))
            payment.add_done_callback(lambda _t: completed.append("payment"))
            await asyncio.wait_for(prepared.wait(), timeout=20)
            assert await _transactions(factory, world) == {}, "premise: the payment is durable before its commit"
            assert await _advisory_modes(payment_pid[0], world.equivalent.id) == ["ShareLock"], (
                "premise: the parked payment does not hold the equivalent lock shared"
            )

            reconcile = asyncio.create_task(
                run_scheduled_reconciliation(factory, equivalent_ids=[world.equivalent.id])
            )
            reconcile.add_done_callback(lambda _t: completed.append("reaction"))
            counts = await asyncio.wait_for(reconcile, timeout=30)
            assert (counts[FAILED], counts[f"hold_{HOLD_SET}"]) == (1, 1), counts
            assert not payment.done(), "premise: the payment was not parked while the reaction held"
            assert completed == ["reaction"], completed
            assert await _hold_of(factory, world.equivalent.id) is not None

            release_commit.set()
            with pytest.raises(ConflictException) as refused_first:
                await asyncio.wait_for(payment, timeout=30)

        _assert_hold_refusal(refused_first.value, code)
        retries = _retries_on_40001(caplog, "payment.attempt_retry")
        assert len(retries) == 1, (
            f"premise: the refusal did not come through the payment's FOR SHARE 40001 and one retry: {retries}"
        )
        assert await _debts(factory, world) == {(world.sender.pid, world.receiver.pid): _OPENING + _ATOM}
        assert await _transactions(factory, world) == {tx_id: "ABORTED"}
        assert await _hold_of(factory, world.equivalent.id) is not None
        monkeypatch.setattr(PaymentService, "_apply_payment", original_commit)
        with pytest.raises(ConflictException) as refused:
            await _pay(str(uuid.uuid4()))
        _assert_hold_refusal(refused.value, code)
    finally:
        release_commit.set()
        await _finish(payment, reconcile)
        _forget_the_route_cache(world)


@pytest.mark.asyncio
async def test_step5c_p_a_reaction_arriving_while_a_payment_holds_its_check_waits_and_holds_after(
    factory, monkeypatch
) -> None:
    """PAYMENT FIRST. The payment has passed its commit check `FOR SHARE` and holds it; the reaction's hold
    `UPDATE` must queue on the PAYMENT'S transaction - measured: `pg_locks` `transactionid`/`tuple`, blocker
    = the payment's backend, the reaction holding no advisory lock - and hold only after the payment
    committed (read when the hold `UPDATE` gets the row: the payment is already committed); the next
    payment is refused.

    RED if the payment's check read without `FOR SHARE` or released the row before its commit: the hold
    then commits while the payment is still about to commit.
    """
    world = await _seed(factory)
    code = world.equivalent.code
    payment = reconcile = None
    try:
        await _baseline_and_one_atom(factory, world.equivalent.id)
        checked, release_payment = asyncio.Event(), asyncio.Event()
        payment_pid: list[int] = []
        original_check = MoneyBoundary.refuse_inactive_equivalents

        async def _check_then_wait(self, equivalent_ids, *, row_lock):
            await original_check(self, equivalent_ids, row_lock=row_lock)
            if row_lock and not checked.is_set():
                payment_pid.append(int(await self.session.scalar(text("SELECT pg_backend_pid()"))))
                checked.set()
                await release_payment.wait()

        monkeypatch.setattr(MoneyBoundary, "refuse_inactive_equivalents", _check_then_wait)

        async def _pay(tx_id: str):
            async with factory() as session:
                return await PaymentService(session).create_payment_internal(
                    world.sender.id, to_pid=world.receiver.pid, equivalent=code, amount="10.00",
                    idempotency_key=tx_id,
                )

        tx_id = str(uuid.uuid4())
        seen_at_hold = _observe_commits_when_the_hold_is_written(
            monkeypatch, lambda: _transactions(factory, world)
        )
        completed: list[str] = []
        payment = asyncio.create_task(_pay(tx_id))
        payment.add_done_callback(lambda _t: completed.append("payment"))
        await asyncio.wait_for(checked.wait(), timeout=20)
        assert await _advisory_modes(payment_pid[0], world.equivalent.id) == ["ShareLock"], (
            "premise: the parked payment does not hold the equivalent lock shared"
        )

        reconcile = asyncio.create_task(run_scheduled_reconciliation(factory, equivalent_ids=[world.equivalent.id]))
        reconcile.add_done_callback(lambda _t: completed.append("reaction"))
        reaction_pid = _assert_row_wait(
            await _waiters_behind(payment_pid[0], timeout=30.0),
            what="the reaction's hold",
            behind="the payment that passed its check",
        )
        assert await _advisory_modes(reaction_pid, world.equivalent.id) == [], (
            "the reaction holds the equivalent advisory lock: since T1909 it takes none"
        )
        assert not reconcile.done()
        assert await _hold_of(factory, world.equivalent.id) is None

        release_payment.set()
        result = await asyncio.wait_for(payment, timeout=30)
        counts = await asyncio.wait_for(reconcile, timeout=30)

        assert result.status == "COMMITTED", result
        assert (counts[FAILED], counts[f"hold_{HOLD_SET}"]) == (1, 1), counts
        assert seen_at_hold == [{tx_id: "COMMITTED"}], (
            f"the hold got the row before the payment's commit was visible: {seen_at_hold}"
        )
        assert completed == ["payment", "reaction"], completed
        assert await _debts(factory, world) == {
            (world.sender.pid, world.receiver.pid): _OPENING + _ATOM + Decimal("10.00")
        }
        with pytest.raises(ConflictException) as refused:
            await _pay(str(uuid.uuid4()))
        _assert_hold_refusal(refused.value, code)
    finally:
        release_payment.set()
        await _finish(payment, reconcile)
        _forget_the_route_cache(world)


@pytest.mark.asyncio
async def test_step5c_p_the_reaction_confirms_in_its_own_snapshot_taken_after_the_verdict(
    factory, monkeypatch
) -> None:
    """The scheduled verdict is FAILED and published; the fault is repaired and committed before the
    reaction's transaction has run its first statement; the reaction's re-run must see the repair and NOT
    hold (`HOLD_NOT_CONFIRMED`).

    UNTIL 019 STAGE 5 this was `test_step5c_p_the_owner_lock_comes_before_the_authoritative_snapshot`: the
    reaction waited on the equivalent owner lock on a session of its own, and its snapshot had to be
    taken AFTER that wait. `T1909` removed the reaction's lock, and with it the wait this schedule parked
    in; what survives of the contract is its point - the confirmation is a FULL RE-RUN in the reaction's
    own snapshot (`react_to_failed` step 3), never the scheduled verdict re-used. The stand parks the
    reaction at the opening of its transaction, before any statement of it (`_open_reaction_transaction`),
    commits the repair there, and then lets it run.

    RED if the reaction held on the scheduled verdict without re-running, or took its snapshot before the
    park (it would re-verify the stale state and hold an equivalent whose ledger is already consistent).
    """
    world = await _seed(factory)
    reconcile = None
    opened, release_open = asyncio.Event(), asyncio.Event()
    in_transaction_at_park: list[bool] = []
    original_open = reconciliation._open_reaction_transaction

    async def _park_then_open(session):
        in_transaction_at_park.append(bool(session.in_transaction()))
        opened.set()
        await release_open.wait()
        await original_open(session)

    monkeypatch.setattr(reconciliation, "_open_reaction_transaction", _park_then_open)
    try:
        await _baseline_and_one_atom(factory, world.equivalent.id)

        reconcile = asyncio.create_task(run_scheduled_reconciliation(factory, equivalent_ids=[world.equivalent.id]))
        await asyncio.wait_for(opened.wait(), timeout=30)
        assert in_transaction_at_park == [False], (
            "premise: the reaction's work session had already begun (and so taken a snapshot) at the park"
        )
        assert not reconcile.done()
        async with factory() as observer:
            statuses = (
                await observer.execute(
                    select(debt_reconciliation_results.c.status).where(
                        debt_reconciliation_results.c.equivalent_id == world.equivalent.id
                    )
                )
            ).scalars().all()
        assert statuses == [FAILED], f"premise: the scheduled verdict was not recorded FAILED first: {statuses}"

        await _around_the_application(
            factory,
            f"UPDATE debts SET amount = amount - 0.00000001 WHERE equivalent_id = '{world.equivalent.id}'",
        )
        assert not reconcile.done(), "premise: the repair did not land while the reaction was parked"

        release_open.set()
        counts = await asyncio.wait_for(reconcile, timeout=30)
        assert counts[f"hold_{HOLD_NOT_CONFIRMED}"] == 1, counts
        assert await _hold_of(factory, world.equivalent.id) is None
    finally:
        release_open.set()
        await _finish(reconcile)
        _forget_the_route_cache(world)


# ── clearing <-> hold ──────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_step5c_p_a_clearing_that_waited_behind_the_reaction_refuses_in_its_fresh_snapshot(
    factory, monkeypatch, caplog
) -> None:
    """HOLD FIRST. The reaction holds the row with the hold written, uncommitted; the clearing takes its
    exclusive equivalent lock and queues with its `FOR SHARE` on the REACTION'S transaction (measured:
    `pg_locks` `transactionid`/`tuple`, blocker = the reaction's backend, the waiter holding the exclusive
    lock, the reaction none); the hold commits; the clearing's read meets 40001, its retry owner runs a
    fresh attempt, which reads the hold and refuses.

    RED if the clearing read the hold without `FOR SHARE` (it reads "not held" from its stale snapshot and
    clears after the hold committed).
    """
    from tests.conftest import TestingSessionLocal

    seed = await _seed_interlock_case()
    clearing_session = TestingSessionLocal()
    clearing = reconcile = None
    try:
        await _baseline_and_one_atom(factory, seed["equivalent_id"], debt_id=seed["debt_ids"][0])
        hold_written, release_hold, reaction_pid = _pause_after_the_hold_is_written(monkeypatch)
        with caplog.at_level(logging.WARNING):
            reconcile = asyncio.create_task(
                run_scheduled_reconciliation(factory, equivalent_ids=[seed["equivalent_id"]])
            )
            await asyncio.wait_for(hold_written.wait(), timeout=30)

            await _use_serializable(clearing_session)
            clearing = asyncio.create_task(
                ClearingService(clearing_session).execute_clearing_with_amount(seed["cycle"])
            )
            clearing_pid = _assert_row_wait(
                await _waiters_behind(reaction_pid[0]), what="the clearing", behind="the reaction's hold"
            )
            assert await _advisory_modes(clearing_pid, seed["equivalent_id"]) == ["ExclusiveLock"], (
                "the backend queued on the hold's row does not hold the clearing's exclusive lock"
            )
            assert await _advisory_modes(reaction_pid[0], seed["equivalent_id"]) == [], (
                "the reaction holds the equivalent advisory lock: since T1909 it takes none"
            )
            assert not clearing.done()

            release_hold.set()
            counts = await asyncio.wait_for(reconcile, timeout=30)
            assert counts[f"hold_{HOLD_SET}"] == 1, counts
            with pytest.raises(ConflictException) as refused:
                await asyncio.wait_for(clearing, timeout=30)
        _assert_hold_refusal(refused.value, seed["equivalent_code"])
        assert _retries_on_40001(caplog, "clearing.attempt_retry"), (
            "premise: the refusal did not come through the clearing's FOR SHARE 40001 and a fresh attempt"
        )

        async with factory() as verify:
            debts = {
                d.id: d.amount
                for d in (await verify.scalars(select(Debt).where(Debt.equivalent_id == seed["equivalent_id"]))).all()
            }
            clearings = await verify.scalar(
                select(func.count(Transaction.id)).where(
                    Transaction.type == "CLEARING", Transaction.initiator_id.in_(seed["participant_ids"])
                )
            )
        assert debts == {
            seed["debt_ids"][0]: Decimal("100.00000001"),
            seed["debt_ids"][1]: Decimal("30.00000000"),
            seed["debt_ids"][2]: Decimal("40.00000000"),
        }, debts
        assert clearings == 0
        assert not clearing_session.in_transaction()
        # Not vacuous: the clearing held its exclusive session lock while it waited (asserted above); this
        # is its release by the cleanup, without invalidating the connection.
        await _no_advisory_lock_is_held(caplog)
    finally:
        await _finish(clearing, reconcile)
        await clearing_session.rollback()
        await clearing_session.close()


@pytest.mark.asyncio
async def test_step5c_p_a_reaction_waits_for_a_clearing_that_already_read_the_hold(factory, monkeypatch) -> None:
    """CLEARING FIRST. The clearing holds its exclusive equivalent lock, has read "not held" `FOR SHARE`,
    locked its cycle rows, and pauses before mutating (at its auto-clearing policy check, after the cycle's
    `FOR UPDATE`); the reaction's hold `UPDATE` must queue on the CLEARING'S transaction - measured:
    `pg_locks` `transactionid`/`tuple`, blocker = the clearing's backend, the reaction holding no advisory
    lock - and hold only after the clearing committed.

    THE ORDER `["clearing", "reaction"]` is the order of COMMITS, read when the hold `UPDATE` gets the row:
    the clearing's transaction is already visible then. (Until `T1909` it was read from task completion;
    the clearing now lets the reaction go at its commit and still releases its pinned connection after,
    so task order no longer means commit order.)

    RED if the clearing read the hold without `FOR SHARE` or released the row before its commit: the hold
    then commits while the clearing is still about to commit.
    """
    from tests.conftest import TestingSessionLocal

    seed = await _seed_interlock_case()
    clearing_session = TestingSessionLocal()
    paused, release_clearing = asyncio.Event(), asyncio.Event()
    clearing_pid: list[int] = []
    clearing = reconcile = None
    try:
        await _baseline_and_one_atom(factory, seed["equivalent_id"], debt_id=seed["debt_ids"][0])
        await _use_serializable(clearing_session)
        service = ClearingService(clearing_session)
        original_policy = service._cycle_respects_auto_clearing

        # Execution only: this stand calls `execute_clearing_with_amount` directly, so detection (the
        # other caller of the policy check) never runs here.
        async def _pause_before_mutation(debts):
            respects = await original_policy(debts)
            if not paused.is_set():
                clearing_pid.append(int(await service.session.scalar(text("SELECT pg_backend_pid()"))))
                paused.set()
                await release_clearing.wait()
            return respects

        monkeypatch.setattr(service, "_cycle_respects_auto_clearing", _pause_before_mutation)
        seen_at_hold = _observe_commits_when_the_hold_is_written(
            monkeypatch, lambda: _clearing_transactions(factory, seed)
        )
        clearing = asyncio.create_task(service.execute_clearing_with_amount(seed["cycle"]))
        await asyncio.wait_for(paused.wait(), timeout=20)
        assert await _advisory_modes(clearing_pid[0], seed["equivalent_id"]) == ["ExclusiveLock"], (
            "premise: the parked clearing does not hold its exclusive equivalent lock"
        )

        reconcile = asyncio.create_task(run_scheduled_reconciliation(factory, equivalent_ids=[seed["equivalent_id"]]))
        reaction_pid = _assert_row_wait(
            await _waiters_behind(clearing_pid[0], timeout=30.0),
            what="the reaction's hold",
            behind="the clearing that read the hold",
        )
        assert await _advisory_modes(reaction_pid, seed["equivalent_id"]) == [], (
            "the reaction holds the equivalent advisory lock: since T1909 it takes none"
        )
        assert not reconcile.done()

        release_clearing.set()
        amount = await asyncio.wait_for(clearing, timeout=30)
        counts = await asyncio.wait_for(reconcile, timeout=30)

        assert amount == Decimal("30.00000000"), "premise: the clearing did not run to its commit"
        assert (counts[FAILED], counts[f"hold_{HOLD_SET}"]) == (1, 1), counts
        order = ["clearing", "reaction"] if seen_at_hold == [1] else ["reaction", "clearing"]
        assert order == ["clearing", "reaction"], (
            f"the hold got the row before the clearing's commit was visible: {seen_at_hold}"
        )
        assert await _hold_of(factory, seed["equivalent_id"]) is not None
    finally:
        release_clearing.set()
        await _finish(clearing, reconcile)
        await clearing_session.rollback()
        await clearing_session.close()


# ── placement: below the TTL branch - DROPPED by 019 stage 4 ─────────────────────────────────────────
#
# `test_step5c_p_an_expired_payment_in_a_held_equivalent_is_aborted_as_expired` seeded a durable `PREPARED`
# payment with an expired `PrepareLock` in a held equivalent and called `PaymentEngine.commit`, asserting that
# the engine's TTL branch refused it as "expired before commit", above the hold check (manifest
# `t1901-manifest.md` 5.4, rows :490-494). The contract is removed, not moved: there is no durable
# `PREPARED` (CHECK `030` refuses the seed itself), no reservation TTL and no `PaymentEngine.commit`; a
# payment's hold check is the only refusal between its admission and its commit. What stays is the hold
# refusal itself: `test_step5c_p_a_reaction_arriving_between_the_binding_and_the_money_phase_holds_first_and_
# the_payment_is_refused` and
# its sibling (next payment refused by the hold, `_assert_hold_refusal`), and
# `tests/integration/test_p015_t1523_replay_after_a_hold_or_an_abort.py` (the stored hold refusal replays).


# ── the admin clear, against a holder of the row ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_step5c_p_the_admin_clear_waits_for_a_holder_of_the_row(factory, admin_api) -> None:
    """A held equivalent with a later PASSED; a holder of the equivalent row `FOR SHARE` - the lock every
    money writer holds through its commit; the clear must wait - measured: its `FOR UPDATE` queues on the
    holder's transaction (`pg_locks` `transactionid`/`tuple`), and it holds no advisory lock - and succeed
    only after the holder is gone.

    UNTIL 019 STAGE 5 this was `test_step5c_p_the_admin_clear_waits_for_the_owner_lock`, with a holder of
    the equivalent owner lock. `T1909` removed the clear's advisory lock (manifest `t1901-manifest.md` 5.4,
    rows :538-:544: the owner-lock wait DROPPED, "clear returns 200, hold cleared" kept); the row is now the
    whole protocol between the clear and money, so the holder here holds the row. Against real money the
    clear is raced in `tests/integration/test_p019_owner_before_row_races_postgres.py` (`hold_clear`).

    RED if the clear read the hold without `FOR UPDATE`: it returns while the row holder is still inside.
    """
    client, _gate = admin_api
    world = await _seed(factory)
    holder = factory()
    clear = None
    try:
        hold_id = await hold_directly(factory, world.equivalent.id)
        now = datetime.now(timezone.utc)
        async with factory() as session:
            await session.execute(
                update(debt_reconciliation_results)
                .where(debt_reconciliation_results.c.id == hold_id)
                .values(is_latest=False)
            )
            await session.execute(
                insert(debt_reconciliation_results).values(
                    id=uuid.uuid4(), equivalent_id=world.equivalent.id, status=PASSED, fingerprint="p" * 64,
                    detail={"stand": "later PASSED"}, checked_at=now, last_checked_at=now, is_latest=True,
                )
            )
            await session.commit()

        await holder.execute(
            select(Equivalent.id).where(Equivalent.id == world.equivalent.id).with_for_update(read=True)
        )
        holder_pid = int(await holder.scalar(text("SELECT pg_backend_pid()")))
        clear = asyncio.create_task(
            client.post(
                f"/api/v1/admin/equivalents/{world.equivalent.code}/integrity-hold/clear",
                json={"reason": "s5c clear against a row holder"},
                headers=ADMIN,
            )
        )
        clear_pid = _assert_row_wait(
            await _waiters_behind(holder_pid), what="the clear", behind="the holder of the row"
        )
        assert await _advisory_modes(clear_pid, world.equivalent.id) == [], (
            "the clear holds the equivalent advisory lock: since T1909 the row is its whole protocol"
        )
        assert not clear.done()

        await holder.rollback()
        resp = await asyncio.wait_for(clear, timeout=30)
        assert resp.status_code == 200, resp.text
        assert await _hold_of(factory, world.equivalent.id) is None
    finally:
        await holder.rollback()
        await holder.close()
        await _finish(clear)
        _forget_the_route_cache(world)


# ── the evidence of a hold is RESTRICT ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_step5c_p_the_evidence_of_a_hold_cannot_be_deleted_while_held(factory) -> None:
    """On the migrated schema: deleting the FAILED row a hold points at fails with 23503; the hold remains.

    MUTATION: `ondelete="SET NULL"` in migration 028 - the delete succeeds and releases the hold, red.
    """
    from sqlalchemy.exc import IntegrityError

    world = await _seed(factory)
    try:
        hold_id = await hold_directly(factory, world.equivalent.id)
        with pytest.raises(IntegrityError) as refused:
            async with factory() as session:
                connection = await session.connection()
                await connection.exec_driver_sql(
                    f"DELETE FROM debt_reconciliation_results WHERE id = '{uuid.UUID(str(hold_id))}'"
                )
                await session.commit()
        orig = refused.value.orig
        assert (getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)) == "23503", repr(orig)
        assert await _hold_of(factory, world.equivalent.id) == hold_id
    finally:
        _forget_the_route_cache(world)


# ── both construction paths, and the downgrade refusal ───────────────────────────────────────────


_HOLD_CATALOGUE = (
    "SELECT data_type, is_nullable, column_default FROM information_schema.columns "
    "WHERE table_name = 'equivalents' AND column_name = 'integrity_hold_result_id'"
)
_HOLD_FK = (
    "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
    "WHERE conrelid = CAST('equivalents' AS regclass) AND conname = 'fk_equivalents_integrity_hold_result'"
)


async def _describe_hold(url: str) -> tuple:
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            column = (await connection.execute(text(_HOLD_CATALOGUE))).all()
            fk = (await connection.execute(text(_HOLD_FK))).scalars().all()
        return [tuple(row) for row in column], [" ".join(d.split()) for d in fk]
    finally:
        await engine.dispose()


def _alembic(url: str, *argv: str) -> subprocess.CompletedProcess:
    import os

    return subprocess.run(  # noqa: S603 - fixed argv, no shell
        [sys.executable, "-m", "alembic", "-c", "migrations/alembic.ini", *argv],
        capture_output=True, text=True, env=dict(os.environ, DATABASE_URL=url), cwd=str(REPO_ROOT), timeout=600,
    )


@pytest.mark.asyncio
async def test_step5c_p_both_construction_paths_build_the_same_hold_column_and_the_downgrade_refuses_a_hold() -> None:
    """`create_all` and `alembic upgrade head` build the same nullable column and the same
    `ON DELETE RESTRICT` foreign key; migration 028's downgrade refuses while an equivalent is held and
    succeeds once none is.

    MUTATIONS: (1) `ondelete="SET NULL"` in migration 028 - the two paths disagree, red; (2) make the
    downgrade drop the column without its check - the held downgrade succeeds, red.
    """
    from tests.conftest import TEST_DATABASE_URL

    if "postgresql" not in TEST_DATABASE_URL:
        pytest.skip(f"this module needs a PostgreSQL TEST_DATABASE_URL, got {TEST_DATABASE_URL!r}")

    # NOT a skip when the role cannot create databases (T1701): `scratch_databases` raises. Until
    # 2026-09-21 this said "an ABSENT measurement, not a pass" and then reported a pass anyway.
    async with scratch_databases(TEST_DATABASE_URL, "s5cmig", "s5cmeta") as (
        migrated_url,
        metadata_url,
    ):
        engine = create_async_engine(metadata_url, poolclass=NullPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
        finally:
            await engine.dispose()
        # No preconditioning here: `migrations/env.py` owns the `alembic_version` widening and
        # establishes it inside this run (T1701).
        run_alembic_upgrade_head(migrated_url)

        from_metadata = await _describe_hold(metadata_url)
        migrated = await _describe_hold(migrated_url)
        assert migrated == from_metadata, f"alembic: {migrated}\nmetadata: {from_metadata}"
        assert migrated[0] == [("uuid", "YES", None)], migrated
        assert migrated[1] == [
            "FOREIGN KEY (integrity_hold_result_id) REFERENCES debt_reconciliation_results(id) ON DELETE RESTRICT"
        ], migrated

        equivalent_id, result_id = uuid.uuid4(), uuid.uuid4()
        now = datetime.now(timezone.utc)
        engine = create_async_engine(migrated_url, poolclass=NullPool)
        try:
            async with engine.begin() as connection:
                await connection.execute(
                    Equivalent.__table__.insert(),
                    [{"id": equivalent_id, "code": "S5CDOWN", "precision": 2, "is_active": True, "metadata_": {}}],
                )
                await connection.execute(
                    insert(debt_reconciliation_results).values(
                        id=result_id, equivalent_id=equivalent_id, status=FAILED, fingerprint="d" * 64,
                        detail={}, checked_at=now, last_checked_at=now, is_latest=True,
                    )
                )
                await connection.execute(
                    update(Equivalent.__table__)
                    .where(Equivalent.__table__.c.id == equivalent_id)
                    .values(integrity_hold_result_id=result_id)
                )
        finally:
            await engine.dispose()

        refused = _alembic(migrated_url, "downgrade", "027_payment_intent_version_2")
        assert refused.returncode != 0 and "refusing to drop" in (refused.stdout + refused.stderr), refused
        assert (await _describe_hold(migrated_url))[0], "the refused downgrade dropped the column anyway"

        engine = create_async_engine(migrated_url, poolclass=NullPool)
        try:
            async with engine.begin() as connection:
                await connection.execute(
                    update(Equivalent.__table__).values(integrity_hold_result_id=None)
                )
        finally:
            await engine.dispose()
        allowed = _alembic(migrated_url, "downgrade", "027_payment_intent_version_2")
        assert allowed.returncode == 0, allowed
        assert (await _describe_hold(migrated_url)) == ([], []), "the downgrade left the column behind"
