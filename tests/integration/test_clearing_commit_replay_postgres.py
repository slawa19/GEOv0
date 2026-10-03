"""PostgreSQL clearing commit-confirmation and deterministic replay coverage."""

from __future__ import annotations

import asyncio
import dataclasses
import sys
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, async_sessionmaker

from tests.debt_setup import debt_fixture_setup
from tests.p019_support import require_target
from tests.p023_support import LATER_PLAN_ID, TEST_PLAN_ID, occurrence_of

# Every test here commits through several sessions, so each runs on a disposable clone of the migrated
# template and its rows go with the clone's drop - nothing is deleted row by row (018 B0b; see
# `tests/tier_on_a_clone.py`). The SERIALIZABLE engines are built over `committed_database.engine`.
from tests.tier_on_a_clone import tier_sessions_on_a_clone  # noqa: E402,F401 - autouse fixture



async def _wait_until_blocked(observer, *, holder_pid: int, waiter_pid: int) -> bool:
    """027 stage 2: `waiter_pid` queues on a lock `holder_pid` holds (row locks now; an advisory lock until then)."""
    for _ in range(250):
        blocked = await observer.scalar(text("SELECT CAST(:h AS int) = ANY(pg_blocking_pids(:w))"),
                                        {"h": holder_pid, "w": waiter_pid})
        await observer.rollback()
        if blocked:
            return True
        await asyncio.sleep(0.02)
    return False


@pytest.mark.asyncio
async def test_concurrent_same_cycle_serializable_resolves_one_durable_occurrence_postgres(
    db_session,
    committed_database,
    monkeypatch,
):
    """Equivalent ownership serializes one occurrence and its durable replay."""

    dialect = db_session.get_bind().dialect.name
    if dialect not in {"postgresql", "postgres"}:
        pytest.skip("Postgres-only: SERIALIZABLE clearing reconciliation")

    from app.core.clearing.service import ClearingService
    from app.core.money_boundary import MoneyBoundary
    from app.db.models.audit_log import IntegrityAuditLog
    from app.db.models.debt import Debt
    from app.db.models.equivalent import Equivalent
    from app.db.models.participant import Participant
    from app.db.models.transaction import Transaction
    from app.db.models.trustline import TrustLine
    from tests.conftest import TestingSessionLocal

    test_engine = committed_database.engine

    nonce = uuid.uuid4().hex[:10]
    equivalent_id = uuid.uuid4()
    equivalent_code = f"CC{nonce}".upper()
    participant_ids = [uuid.uuid4() for _ in range(3)]
    a_id, b_id, c_id = participant_ids
    debt_ids = [uuid.uuid4() for _ in range(3)]
    # 025 `T2508.1`: BOTH workers execute ONE plan occurrence of the cycle (declared 30, the minimum); the second
    # is the durable replay of the first. (Under v1 the second worker passed the cycle reversed - one set-hash.)
    occurrence = occurrence_of(
        debt_ids, equivalent_id=equivalent_id, amount="30.00", plan_id=TEST_PLAN_ID, ordinal=0
    )
    sessions = []
    workers = []
    observer = None

    try:
        async with TestingSessionLocal() as setup:
            setup.add(
                Equivalent(
                    id=equivalent_id,
                    code=equivalent_code,
                    description="Concurrent clearing replay test",
                    precision=2,
                )
            )
            setup.add_all(
                [
                    Participant(
                        id=participant_id,
                        pid=f"{label}_CC_{nonce}",
                        display_name=label,
                        public_key=f"pk_{label}_{nonce}",
                        type="person",
                        status="active",
                    )
                    for participant_id, label in zip(
                        participant_ids,
                        ("A", "B", "C"),
                        strict=True,
                    )
                ]
            )
            setup.add_all(
                [
                    TrustLine(
                        from_participant_id=creditor_id,
                        to_participant_id=debtor_id,
                        equivalent_id=equivalent_id,
                        limit=Decimal("200.00"),
                        policy={"auto_clearing": True},
                        status="active",
                    )
                    for debtor_id, creditor_id in (
                        (a_id, b_id),
                        (b_id, c_id),
                        (c_id, a_id),
                    )
                ]
            )
            async with debt_fixture_setup(setup, label="setup"):
                setup.add_all(
                    [
                        Debt(
                            id=debt_id,
                            debtor_id=debtor_id,
                            creditor_id=creditor_id,
                            equivalent_id=equivalent_id,
                            amount=Decimal(amount),
                        )
                        for debt_id, debtor_id, creditor_id, amount in (
                            (debt_ids[0], a_id, b_id, "100.00"),
                            (debt_ids[1], b_id, c_id, "30.00"),
                            (debt_ids[2], c_id, a_id, "40.00"),
                        )
                    ]
                )
            await setup.commit()

        first_owner_acquired = asyncio.Event()
        second_owner_attempted = asyncio.Event()
        release_first_owner = asyncio.Event()
        acquisition_count = 0
        acquisition_pids: dict[int, int] = {}
        original_acquire = MoneyBoundary.lock_pair_lines  # 027 stage 2: the cycle's line locks

        async def _coordinate_owner_acquisition(engine, equivalent_id):
            nonlocal acquisition_count
            acquisition_count += 1
            call_number = acquisition_count
            acquisition_pids[call_number] = int(
                await engine.session.scalar(text("SELECT pg_backend_pid()"))
            )
            if call_number == 2:
                second_owner_attempted.set()
            result = await original_acquire(engine, equivalent_id)
            if call_number == 1:
                first_owner_acquired.set()
                await release_first_owner.wait()
            return result

        monkeypatch.setattr(
            MoneyBoundary,
            "lock_pair_lines",
            _coordinate_owner_acquisition,
        )

        serializable_sessions = async_sessionmaker(
            bind=test_engine.execution_options(isolation_level="READ COMMITTED"),
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
        for _ in range(2):
            session = serializable_sessions()
            sessions.append(session)
            await session.connection(
                execution_options={"isolation_level": "READ COMMITTED"}
            )
            isolation = (await session.execute(text("SHOW transaction_isolation"))).scalar_one()
            assert str(isolation).lower() == "read committed"
        observer = TestingSessionLocal()

        workers = [
            asyncio.create_task(
                ClearingService(session).execute_occurrence(
                    replayed
                )
            )
            for session, replayed in zip(
                sessions,
                (occurrence, dataclasses.replace(occurrence)),
                strict=True,
            )
        ]
        await asyncio.wait_for(first_owner_acquired.wait(), timeout=5.0)
        await asyncio.wait_for(second_owner_attempted.wait(), timeout=5.0)
        assert await _wait_until_blocked(
            observer,
            holder_pid=acquisition_pids[1],
            waiter_pid=acquisition_pids[2],
        )
        release_first_owner.set()
        results = await asyncio.wait_for(
            asyncio.gather(*workers, return_exceptions=True),
            timeout=15.0,
        )

        assert results == [Decimal("30.00000000"), Decimal("30.00000000")]
        assert acquisition_count == 2
        assert all(not session.in_transaction() for session in sessions)

        async with TestingSessionLocal() as verify:
            clearing_transactions = (
                await verify.scalars(
                    select(Transaction).where(
                        Transaction.type == "CLEARING",
                        Transaction.initiator_id.in_(participant_ids),
                    )
                )
            ).all()
            clearing_audits = (
                await verify.scalars(
                    select(IntegrityAuditLog).where(
                        IntegrityAuditLog.operation_type == "CLEARING",
                        IntegrityAuditLog.equivalent_code == equivalent_code,
                    )
                )
            ).all()
            remaining_debts = {
                debt.id: debt.amount
                for debt in (
                    await verify.scalars(
                        select(Debt).where(Debt.equivalent_id == equivalent_id)
                    )
                ).all()
            }

        assert len(clearing_transactions) == 1
        assert clearing_transactions[0].state == "COMMITTED"
        assert len(clearing_audits) == 1
        assert remaining_debts == {
            debt_ids[0]: Decimal("70.00000000"),
            debt_ids[2]: Decimal("10.00000000"),
        }
    finally:
        primary_error = sys.exc_info()[1]
        try:
            pending = [worker for worker in workers if not worker.done()]
            for worker in pending:
                worker.cancel()
            if pending:
                await asyncio.wait(pending, timeout=2.0)
            for worker in workers:
                if worker.done() and not worker.cancelled():
                    worker.exception()

            async with asyncio.timeout(5.0):
                for session in sessions:
                    await session.rollback()
                    await session.close()
                if observer is not None:
                    await observer.rollback()
                    await observer.close()
        except BaseException as teardown_error:
            if primary_error is None:
                raise
            primary_error.add_note(
                "Concurrent clearing replay teardown also failed: "
                f"{type(teardown_error).__name__}: {teardown_error}"
            )


async def _seed_conflict_cycle(prefix: str):
    """A fresh equivalent with the cycle A->B 100, B->C 30, C->A 40 (a clearing clears 30)."""

    from app.db.models.debt import Debt
    from app.db.models.equivalent import Equivalent
    from app.db.models.participant import Participant
    from app.db.models.trustline import TrustLine
    from tests.conftest import TestingSessionLocal

    nonce = uuid.uuid4().hex[:10]
    equivalent_id = uuid.uuid4()
    equivalent_code = f"{prefix}{nonce}".upper()
    participant_ids = [uuid.uuid4() for _ in range(3)]
    a_id, b_id, c_id = participant_ids
    debt_ids = [uuid.uuid4() for _ in range(3)]
    async with TestingSessionLocal() as setup:
        setup.add(Equivalent(id=equivalent_id, code=equivalent_code, description="p019 clearing retry", precision=2))
        setup.add_all(
            [
                Participant(
                    id=participant_id, pid=f"{label}_{prefix}_{nonce}", display_name=label,
                    public_key=f"pk_{label}_{prefix}_{nonce}", type="person", status="active",
                )
                for participant_id, label in zip(participant_ids, ("A", "B", "C"), strict=True)
            ]
        )
        setup.add_all(
            [
                TrustLine(
                    from_participant_id=creditor_id, to_participant_id=debtor_id, equivalent_id=equivalent_id,
                    limit=Decimal("200.00"), policy={"auto_clearing": True}, status="active",
                )
                for debtor_id, creditor_id in ((a_id, b_id), (b_id, c_id), (c_id, a_id))
            ]
        )
        async with debt_fixture_setup(setup, label="setup"):
            setup.add_all(
                [
                    Debt(id=debt_id, debtor_id=debtor_id, creditor_id=creditor_id,
                         equivalent_id=equivalent_id, amount=Decimal(amount))
                    for debt_id, debtor_id, creditor_id, amount in (
                        (debt_ids[0], a_id, b_id, "100.00"),
                        (debt_ids[1], b_id, c_id, "30.00"),
                        (debt_ids[2], c_id, a_id, "40.00"),
                    )
                ]
            )
        await setup.commit()
    return equivalent_id, equivalent_code, participant_ids, debt_ids


def _conflicting_clearing_service(debt_id, writes: list[Decimal], observed_sqlstates: list[str]):
    """A `ClearingService` whose first `len(writes)` attempts each meet a concurrent committed write.

    027 STAGE 2 (`T2704`): a real deadlock (`40P01`, `deadlock_after_the_wait`); the text below is history.

    THE SCHEDULE IS REAL. Each attempt's FIRST statement is the committed-occurrence read
    (`_committed_execution_amount`), which fixes the attempt's SERIALIZABLE snapshot; right after it, a
    writer on its own connection commits the next amount from `writes` to the debt, so the attempt's
    `FOR UPDATE` re-read of the cycle meets a row changed after its snapshot - PostgreSQL's own 40001,
    nothing injected. Attempts beyond `len(writes)` run undisturbed. `attempts` counts the attempts;
    `observed_sqlstates` records every retryable SQLSTATE the service classified (the control that the
    conflict was the one claimed).
    """

    from app.core.clearing.service import ClearingService
    from app.db.models.debt import Debt
    from tests.conftest import TestingSessionLocal

    from app.db.models.trustline import TrustLine
    from tests.p019_support import deadlock_after_the_wait

    writers: list[asyncio.Task] = []

    async def write(attempt_no: int, holding: asyncio.Event) -> None:
        async with TestingSessionLocal() as writer:
            debt = await writer.get(Debt, debt_id, with_for_update=True)
            await deadlock_after_the_wait(writer, holding, select(TrustLine.id).where(
                TrustLine.equivalent_id == debt.equivalent_id).with_for_update())
            # Declared: the journal asks every movement of money to name its operation.
            async with debt_fixture_setup(writer, label=f"concurrent-writer-{attempt_no}"):
                debt.amount = writes[attempt_no - 1]
            await writer.commit()

    class _Service(ClearingService):
        attempts = 0

        async def _committed_execution_amount(self, tx_id: str, *, allowed_participant_pids=None):
            # The perimeter is forwarded rather than dropped (p010), so this observer cannot mask a
            # call that lost it.
            amount = await super()._committed_execution_amount(
                tx_id, allowed_participant_pids=allowed_participant_pids
            )
            type(self).attempts += 1
            assert amount is None, "premise: no occurrence is committed when an attempt starts"
            for previous in writers:
                await previous
            if type(self).attempts <= len(writes):
                holding = asyncio.Event()
                writers.append(asyncio.create_task(write(type(self).attempts, holding)))
                await holding.wait()
            return amount

        @classmethod
        def _is_retryable_concurrency_error(cls, exc: BaseException) -> bool:
            is_retryable = super()._is_retryable_concurrency_error(exc)
            if is_retryable:
                observed_sqlstates.extend(
                    sorted(cls._postgres_error_codes(exc) & {"40001", "40P01"})
                )
            return is_retryable

    _Service.attempts = 0
    return _Service


async def _clearing_evidence(equivalent_code: str, execution_tx_id: str, equivalent_id):
    from sqlalchemy import func

    from app.db.journal_tables import debt_journal_entries, debt_operations
    from app.db.models.audit_log import IntegrityAuditLog
    from app.db.models.debt import Debt
    from app.db.models.transaction import Transaction
    from tests.conftest import TestingSessionLocal

    async with TestingSessionLocal() as verify:
        transactions = (
            await verify.scalars(
                select(Transaction).where(Transaction.tx_id == execution_tx_id, Transaction.type == "CLEARING")
            )
        ).all()
        audits = (
            await verify.scalars(
                select(IntegrityAuditLog).where(
                    IntegrityAuditLog.operation_type == "CLEARING",
                    IntegrityAuditLog.equivalent_code == equivalent_code,
                )
            )
        ).all()
        operations = (
            await verify.execute(
                select(debt_operations.c.id, debt_operations.c.state).where(
                    debt_operations.c.kind == "CLEARING", debt_operations.c.tx_id == execution_tx_id
                )
            )
        ).all()
        entries = int(
            await verify.scalar(
                select(func.count())
                .select_from(debt_journal_entries)
                .where(debt_journal_entries.c.operation_id.in_([row.id for row in operations] or [uuid.uuid4()]))
            )
        )
        debts = {
            debt.id: debt.amount
            for debt in (await verify.scalars(select(Debt).where(Debt.equivalent_id == equivalent_id))).all()
        }
    return transactions, audits, operations, entries, debts


async def _run_owner(service_cls, occurrence):
    from tests.conftest import TestingSessionLocal

    owner_session = TestingSessionLocal()
    try:
        await owner_session.connection(execution_options={"isolation_level": "READ COMMITTED"})
        isolation = (await owner_session.execute(text("SHOW transaction_isolation"))).scalar_one()
        assert str(isolation).lower() == "read committed"
        try:
            return await asyncio.wait_for(service_cls(owner_session).execute_occurrence(occurrence), 30)
        except Exception as exc:  # noqa: BLE001 - compared by the caller
            return exc
    finally:
        await owner_session.rollback()
        await owner_session.close()


@pytest.mark.asyncio
async def test_a_serializable_conflict_retries_the_whole_clearing_on_a_fresh_snapshot_postgres(
    db_session,
    committed_database,
):
    """019 stage 5 (`T1907`, `FORK-4`): the clearing owns its retries - a real 40001 is not an error.

    Replaces `test_serializable_conflict_without_committed_occurrence_stays_failure_postgres`, which
    fixed the pre-stage-5 contract (`E010`, debts `101/30/40`): the committed-occurrence resolver was the
    only answer to a conflict, so a clearing that lost a race to a plain debt writer failed with an
    internal error (observed on SERIALIZABLE as `T1549`-находка 2). The contract now: the attempt is
    discarded and the WHOLE execution runs again on a fresh session, re-reading the cycle rows and their
    amounts - here it sees `101`, clears `30` and leaves `71/0/10`, with ONE committed occurrence and its
    envelope, journal and audit.
    """

    dialect = db_session.get_bind().dialect.name
    if dialect not in {"postgresql", "postgres"}:
        pytest.skip("Postgres-only: SERIALIZABLE clearing retry")

    equivalent_id, equivalent_code, _participants, debt_ids = await _seed_conflict_cycle("CN")
    occurrence = occurrence_of(debt_ids, equivalent_id=equivalent_id, amount="30.00", plan_id=TEST_PLAN_ID, ordinal=0)
    execution_tx_id = occurrence.occurrence_id
    observed: list[str] = []
    service_cls = _conflicting_clearing_service(debt_ids[0], [Decimal("101.00")], observed)

    outcome = await _run_owner(service_cls, occurrence)

    # Controls: the conflict was real and it is PostgreSQL's 40001.
    assert observed and set(observed) == {"40P01"}, observed

    require_target(
        outcome == Decimal("30.00000000"),
        f"a clearing that met one real 40001 ended with {outcome!r} instead of retrying",
    )
    assert service_cls.attempts == 2, service_cls.attempts
    assert observed == ["40P01"], observed
    transactions, audits, operations, entries, debts = await _clearing_evidence(
        equivalent_code, execution_tx_id, equivalent_id
    )
    assert [(t.state, Decimal(str(t.payload["amount"]))) for t in transactions] == [
        ("COMMITTED", Decimal("30.00"))
    ]
    assert len(audits) == 1 and audits[0].tx_id == execution_tx_id
    assert [row.state for row in operations] == ["COMPLETED"], operations
    assert entries == 3, entries  # two reductions and the deletion of the cleared edge
    assert debts == {
        debt_ids[0]: Decimal("71.00000000"),
        debt_ids[2]: Decimal("10.00000000"),
    }


@pytest.mark.asyncio
async def test_a_persistent_conflict_exhausts_the_clearing_budget_with_a_retryable_refusal_postgres(
    db_session,
    committed_database,
    monkeypatch,
):
    """019 stage 5 (`T1907`, `FORK-4`), the other half: the retry budget is bounded.

    Every attempt meets a fresh concurrent write, so every attempt is refused by SSI. When the budget
    (`COMMIT_RETRY_ATTEMPTS`, here 3) is spent the clearing ends with a TYPED RETRYABLE refusal
    (`409/E008`, `details.retryable`), with no committed occurrence and no partial effect: no
    transaction row, no envelope, no journal, no audit, and the debts are exactly what the concurrent
    writer left.
    """

    dialect = db_session.get_bind().dialect.name
    if dialect not in {"postgresql", "postgres"}:
        pytest.skip("Postgres-only: SERIALIZABLE clearing retry")

    from app.config import settings

    monkeypatch.setattr(settings, "COMMIT_RETRY_ATTEMPTS", 3, raising=False)
    monkeypatch.setattr(settings, "PAYMENT_TOTAL_TIMEOUT_SECONDS", 60, raising=False)
    equivalent_id, equivalent_code, _participants, debt_ids = await _seed_conflict_cycle("CX")
    occurrence = occurrence_of(debt_ids, equivalent_id=equivalent_id, amount="30.00", plan_id=TEST_PLAN_ID, ordinal=0)
    execution_tx_id = occurrence.occurrence_id
    observed: list[str] = []
    writes = [Decimal("101.00"), Decimal("102.00"), Decimal("103.00"), Decimal("104.00")]
    service_cls = _conflicting_clearing_service(debt_ids[0], writes, observed)

    outcome = await _run_owner(service_cls, occurrence)

    # Control: the conflicts were real 40001s.
    assert observed and set(observed) == {"40P01"}, observed

    details = getattr(outcome, "details", None) or {}
    require_target(
        getattr(outcome, "code", None) == "E008" and details.get("retryable") is True,
        f"an exhausted clearing ended with {outcome!r}, not a typed retryable refusal",
    )
    assert getattr(outcome, "status_code", None) == 409, outcome
    assert details.get("conflict_kind") == "database_concurrency", details
    assert service_cls.attempts == 3, service_cls.attempts
    assert observed == ["40P01"] * 3, observed
    transactions, audits, operations, entries, debts = await _clearing_evidence(
        equivalent_code, execution_tx_id, equivalent_id
    )
    assert (transactions, audits, operations, entries) == ([], [], [], 0)
    assert debts == {
        debt_ids[0]: Decimal("103.00000000"),
        debt_ids[1]: Decimal("30.00000000"),
        debt_ids[2]: Decimal("40.00000000"),
    }



@pytest.mark.asyncio
@pytest.mark.parametrize(
    "boundary_kind",
    [
        "cancellation",
        "ack_loss",
        "connection_loss",
        "connection_loss_reconcile_cancellation",
    ],
)
async def test_post_commit_boundary_reconciles_and_new_cycle_still_executes_postgres(
    db_session,
    monkeypatch,
    boundary_kind,
):
    dialect = db_session.get_bind().dialect.name
    if dialect not in {"postgresql", "postgres"}:
        pytest.skip("Postgres-only: durable clearing commit confirmation")

    from app.core.clearing.service import (
        ClearingCommittedAfterCancellation,
        ClearingService,
    )
    from app.db.models.audit_log import IntegrityAuditLog
    from app.db.models.debt import Debt
    from app.db.models.equivalent import Equivalent
    from app.db.models.participant import Participant
    from app.db.models.transaction import Transaction
    from app.db.models.trustline import TrustLine
    from tests.conftest import TestingSessionLocal

    nonce = uuid.uuid4().hex[:10]
    equivalent_id = uuid.uuid4()
    equivalent_code = f"CR{nonce}".upper()
    participant_ids = [uuid.uuid4() for _ in range(3)]
    a_id, b_id, c_id = participant_ids
    debt_ids = [uuid.uuid4() for _ in range(3)]
    # 025 `T2508.1`: the occurrence, declared (30, the minimum); its replay is the same descriptor again; the
    # replacement - a later plan over a new debt set - is a distinct occurrence (declared 5).
    occurrence = occurrence_of(debt_ids, equivalent_id=equivalent_id, amount="30.00", plan_id=TEST_PLAN_ID, ordinal=0)
    replacement_debt_id = uuid.uuid4()
    replacement = occurrence_of(
        [debt_ids[0], replacement_debt_id, debt_ids[2]],
        equivalent_id=equivalent_id,
        amount="5.00",
        plan_id=LATER_PLAN_ID,
        ordinal=0,
    )

    service_session = None
    replay_session = None
    replacement_session = None
    service_task = None
    release_commit_ack = asyncio.Event()
    commit_completed = asyncio.Event()
    reconciliation_observed = asyncio.Event()
    release_reconciliation = asyncio.Event()

    try:
        async with TestingSessionLocal() as setup:
            setup.add(
                Equivalent(
                    id=equivalent_id,
                    code=equivalent_code,
                    description="Clearing commit replay test",
                    precision=2,
                )
            )
            setup.add_all(
                [
                    Participant(
                        id=participant_id,
                        pid=f"{label}_CR_{nonce}",
                        display_name=label,
                        public_key=f"pk_{label}_{nonce}",
                        type="person",
                        status="active",
                    )
                    for participant_id, label in zip(
                        participant_ids,
                        ("A", "B", "C"),
                        strict=True,
                    )
                ]
            )
            setup.add_all(
                [
                    TrustLine(
                        from_participant_id=creditor_id,
                        to_participant_id=debtor_id,
                        equivalent_id=equivalent_id,
                        limit=Decimal("200.00"),
                        policy={"auto_clearing": True},
                        status="active",
                    )
                    for debtor_id, creditor_id in (
                        (a_id, b_id),
                        (b_id, c_id),
                        (c_id, a_id),
                    )
                ]
            )
            async with debt_fixture_setup(setup, label="setup-1"):
                setup.add_all(
                    [
                        Debt(
                            id=debt_id,
                            debtor_id=debtor_id,
                            creditor_id=creditor_id,
                            equivalent_id=equivalent_id,
                            amount=Decimal(amount),
                        )
                        for debt_id, debtor_id, creditor_id, amount in (
                            (debt_ids[0], a_id, b_id, "100.00"),
                            (debt_ids[1], b_id, c_id, "30.00"),
                            (debt_ids[2], c_id, a_id, "40.00"),
                        )
                    ]
                )
            await setup.commit()

        service_session = TestingSessionLocal()
        await service_session.connection(
            execution_options={"isolation_level": "READ COMMITTED"}
        )
        service = ClearingService(service_session)
        real_reconcile = service._reconcile_committed_execution

        async def _reconcile_then_delay_result(tx_id, *, allowed_participant_pids=None):
            # 2026-08-22 / p010: forwarded, not dropped, so this double cannot hide a
            # reconcile call that lost the run perimeter on the way.
            amount = await real_reconcile(
                tx_id, allowed_participant_pids=allowed_participant_pids
            )
            reconciliation_observed.set()
            await release_reconciliation.wait()
            return amount

        if boundary_kind == "connection_loss_reconcile_cancellation":
            monkeypatch.setattr(
                service,
                "_reconcile_committed_execution",
                _reconcile_then_delay_result,
            )
        real_commit = AsyncSession.commit
        boundary_commit_seen = False

        async def _commit_then_delay_ack(session):
            nonlocal boundary_commit_seen
            await real_commit(session)
            if boundary_commit_seen:
                return
            boundary_commit_seen = True
            commit_completed.set()
            if boundary_kind == "ack_loss":
                raise RuntimeError("commit acknowledgement lost")
            if boundary_kind in {
                "connection_loss",
                "connection_loss_reconcile_cancellation",
            }:
                bind = session.bind
                assert isinstance(bind, AsyncConnection)
                await bind.invalidate()
                raise ConnectionError("connection lost after commit")
            await release_commit_ack.wait()

        monkeypatch.setattr(AsyncSession, "commit", _commit_then_delay_ack)

        service_task = asyncio.create_task(
            service.execute_occurrence(occurrence),
            name="clearing-post-commit-cancellation",
        )
        await asyncio.wait_for(commit_completed.wait(), timeout=5.0)
        if boundary_kind == "cancellation":
            service_task.cancel()
            await asyncio.sleep(0)
            release_commit_ack.set()
            with pytest.raises(ClearingCommittedAfterCancellation) as cancellation:
                await asyncio.wait_for(service_task, timeout=5.0)
            boundary_amount = cancellation.value.cleared_amount
        elif boundary_kind == "connection_loss_reconcile_cancellation":
            await asyncio.wait_for(reconciliation_observed.wait(), timeout=5.0)
            service_task.cancel()
            await asyncio.sleep(0)
            release_reconciliation.set()
            with pytest.raises(ClearingCommittedAfterCancellation) as cancellation:
                await asyncio.wait_for(service_task, timeout=5.0)
            boundary_amount = cancellation.value.cleared_amount
        else:
            boundary_amount = await asyncio.wait_for(service_task, timeout=5.0)

        replay_session = TestingSessionLocal()
        await replay_session.connection(
            execution_options={"isolation_level": "READ COMMITTED"}
        )
        replay_amount = await ClearingService(
            replay_session
        ).execute_occurrence(dataclasses.replace(occurrence))

        assert replay_amount == Decimal("30.00000000")
        assert boundary_amount == replay_amount
        assert not replay_session.in_transaction()

        # Anti-vacuum: a genuinely new occurrence (another plan, a new debt set) must
        # not be mistaken for replay of the committed one.
        async with TestingSessionLocal() as add_replacement:
            async with debt_fixture_setup(add_replacement, label="setup-2"):
                add_replacement.add(
                    Debt(
                        id=replacement_debt_id,
                        debtor_id=b_id,
                        creditor_id=c_id,
                        equivalent_id=equivalent_id,
                        amount=Decimal("5.00"),
                    )
                )
            await add_replacement.commit()

        replacement_session = TestingSessionLocal()
        replacement_amount = await ClearingService(
            replacement_session
        ).execute_occurrence(replacement)
        assert replacement_amount == Decimal("5.00000000")

        async with TestingSessionLocal() as verify:
            clearing_transactions = (
                await verify.scalars(
                    select(Transaction).where(
                        Transaction.type == "CLEARING",
                        Transaction.initiator_id.in_(participant_ids),
                    )
                )
            ).all()
            clearing_audits = (
                await verify.scalars(
                    select(IntegrityAuditLog).where(
                        IntegrityAuditLog.operation_type == "CLEARING",
                        IntegrityAuditLog.equivalent_code == equivalent_code,
                    )
                )
            ).all()
            remaining_debts = {
                debt.id: debt.amount
                for debt in (
                    await verify.scalars(
                        select(Debt).where(Debt.equivalent_id == equivalent_id)
                    )
                ).all()
            }

        assert len(clearing_transactions) == 2
        assert all(tx.state == "COMMITTED" for tx in clearing_transactions)
        assert len(clearing_audits) == 2
        assert remaining_debts == {
            debt_ids[0]: Decimal("65.00000000"),
            debt_ids[2]: Decimal("5.00000000"),
        }
    finally:
        primary_error = sys.exc_info()[1]
        release_commit_ack.set()
        release_reconciliation.set()
        try:
            if service_task is not None and not service_task.done():
                service_task.cancel()
                try:
                    await asyncio.wait_for(service_task, timeout=2.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass
            elif service_task is not None and not service_task.cancelled():
                service_task.exception()

            async with asyncio.timeout(5.0):
                for session in (
                    service_session,
                    replay_session,
                    replacement_session,
                ):
                    if session is not None:
                        await session.rollback()
                        await session.close()

        except BaseException as teardown_error:
            if primary_error is None:
                raise
            primary_error.add_note(
                "Clearing commit-replay teardown also failed: "
                f"{type(teardown_error).__name__}: {teardown_error}"
            )
