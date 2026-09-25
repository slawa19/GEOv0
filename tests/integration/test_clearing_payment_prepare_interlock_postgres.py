"""PostgreSQL schedules for the shared clearing/payment boundary.

019 STAGE 4 (`T1906`). There is no `PaymentEngine` and no durable `PREPARED` payment any more: a
payment is one transaction through `PaymentService`. The clearing-first schedule now races a whole
reverse payment against the clearing; the payment-first schedule of an uncommitted `prepare` holding a
reservation is gone with its contract (see the note where it stood). The seed and the session helpers
live in `tests/integration/p019_interlock_support.py`, which other race suites import too.

019 STAGE 5 (`T1909`, decision `KEEP-EQUIVALENT-LOCK`). The equivalent lock stays as ONE identity in two
modes: payments (and staged phases, the inject) take it SHARED, the clearing takes it EXCLUSIVE on its
pinned connection before its snapshot. There are no reservations, transaction or pair locks. The
schedules below therefore park the clearing at a point that still exists inside its money transaction
(`_cycle_respects_auto_clearing`, after the cycle rows are locked `FOR UPDATE` and before any mutation)
instead of the removed reservation scan, and the waits they assert are the shared/exclusive waits of the
one lock, read from `pg_locks` with their MODE and their blocker (`pg_blocking_pids`).
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from tests.integration.p019_interlock_support import (
    _no_advisory_lock_is_held,
    _seed_interlock_case,
    _use_serializable,
)

# A test that seeds (`_seed_interlock_case`) commits through several sessions and runs on a disposable
# clone of the migrated template: `@pytest.mark.usefixtures("tier_on_a_clone")`, and its rows go with
# the clone's drop (018 B0b; see `tests/tier_on_a_clone.py`). Its own one-connection engine is built
# over `committed_database.url`. Tests that commit nothing stay on the tier and pay for no clone.
from tests.tier_on_a_clone import tier_on_a_clone  # noqa: E402,F401 - opt-in fixture



def _require_postgres(db_session) -> None:
    dialect = db_session.get_bind().dialect.name
    if dialect not in {"postgresql", "postgres"}:
        pytest.skip("Postgres-only: clearing/payment advisory interlock")


async def _wait_for_advisory_waiter(
    observer,
    *,
    holder_pid: int,
    mode: str,
    waiter_pid: int | None = None,
) -> int | None:
    """The pid of a backend of THIS database queued on an advisory lock in `mode` behind `holder_pid`.

    `mode` is `pg_locks.mode`: `ShareLock` for `pg_advisory_xact_lock_shared` (a payment), `ExclusiveLock`
    for `pg_advisory_lock` (the clearing). The blocker is read from `pg_blocking_pids`, so "some advisory
    waiter exists" is not enough: the waiter must wait on the named holder, in the named mode.
    """

    try:
        async with asyncio.timeout(3.0):
            while True:
                rows = (
                    await observer.execute(
                        text(
                            "SELECT l.pid FROM pg_locks l "
                            "WHERE l.locktype = 'advisory' AND NOT l.granted AND l.mode = :mode "
                            "AND l.database = (SELECT oid FROM pg_database "
                            "WHERE datname = current_database()) "
                            "AND :holder = ANY(pg_blocking_pids(l.pid))"
                        ),
                        {"mode": mode, "holder": holder_pid},
                    )
                ).scalars().all()
                await observer.rollback()
                matching = [
                    int(pid) for pid in rows if waiter_pid is None or int(pid) == waiter_pid
                ]
                if matching:
                    return matching[0]
    except asyncio.TimeoutError:
        return None


async def _wait_for_exact_blocker(
    observer,
    *,
    waiter_pid: int,
    holder_pid: int,
) -> bool:
    try:
        async with asyncio.timeout(3.0):
            while True:
                blockers = await observer.scalar(
                    text("SELECT pg_blocking_pids(:waiter_pid)"),
                    {"waiter_pid": waiter_pid},
                )
                if holder_pid in (blockers or []):
                    return True
    except asyncio.TimeoutError:
        return False


#: The budget for the owner-lock probe at the end of each case. It used to be 2.0 seconds, which was
#: always covering two unrelated things and went over when the debt journal was armed (step 4 slice
#: C) and every unit of work grew an envelope INSERT: this suite runs on `NullPool`, so each probe
#: opens a BRAND NEW asyncpg connection and pays for its type introspection before it can ask for a
#: lock. Measured at the timeout: `pg_locks` held no advisory lock at all and the probe's own backend
#: was still `idle / ClientRead` inside that introspection. The property was never in doubt - the
#: budget was. `_no_advisory_lock_is_held` now asserts the property DIRECTLY, on a connection that is
#: already open, and the probe below keeps its place as the end-to-end form.
_PROBE_TIMEOUT = 20.0


@pytest.mark.asyncio
async def test_no_advisory_lock_check_ignores_other_databases_postgres(db_session, caplog):
    """T1537: `pg_locks` is the whole server; a lock held on another database is not this one's."""

    _require_postgres(db_session)

    from sqlalchemy.engine import make_url
    from sqlalchemy.pool import NullPool
    from tests.conftest import TEST_DATABASE_URL, TestingSessionLocal

    # `postgres` exists on every server this gate runs against, and a transaction-scoped lock on it
    # leaves nothing behind.
    foreign_engine = create_async_engine(
        make_url(TEST_DATABASE_URL).set(database="postgres"), poolclass=NullPool
    )
    try:
        async with foreign_engine.connect() as foreign:
            await foreign.execute(text("SELECT pg_advisory_xact_lock(1)"))
            foreign_pid = await foreign.scalar(text("SELECT pg_backend_pid()"))
            # PREMISE: the lock is visible from here and belongs to another database - otherwise
            # the check below passes because there was nothing to see.
            async with TestingSessionLocal() as observer:
                seen = (
                    await observer.execute(
                        text(
                            "SELECT database <> (SELECT oid FROM pg_database "
                            "WHERE datname = current_database()) FROM pg_locks "
                            "WHERE locktype = 'advisory' AND granted AND pid = :pid AND objid = 1"
                        ),
                        {"pid": foreign_pid},
                    )
                ).scalars().all()
            assert seen == [True], f"premise: foreign advisory lock not observed as such: {seen}"
            await _no_advisory_lock_is_held(caplog)
            await foreign.rollback()
    finally:
        await foreign_engine.dispose()


@pytest.mark.asyncio
@pytest.mark.usefixtures("tier_on_a_clone")
async def test_clearing_exclusive_lock_blocks_a_reverse_payment_postgres(
    db_session,
    monkeypatch,
):
    """Clearing-first: a reverse payment queues behind the clearing's EXCLUSIVE equivalent lock.

    019 STAGE 5 (`T1909`): renamed from `..._owner_blocks_a_reverse_payment_after_empty_snapshot_...`.
    The clearing is parked inside its money transaction (cycle rows `FOR UPDATE`, before any mutation);
    the payment's SHARED acquisition must be queued behind the clearing's pinned connection - `pg_locks`
    `advisory`, mode `ShareLock`, not granted, blocked by that backend. Then the clearing commits 30 and
    the payment commits after it; the money and audit assertions below are unchanged.

    The history of this schedule, kept for its reasoning:

    UNTIL 019 STAGE 4 this was `test_clearing_owner_blocks_new_reverse_prepare_after_empty_snapshot_postgres`
    and raced `PaymentEngine.prepare` (a durable `PREPARED` plus a reservation row) against the clearing.
    Since stage 4 a payment is one transaction (`PaymentService`), so the same schedule races a WHOLE
    reverse payment B -> A 5.00: clearing holds its owner lock after its empty reservation snapshot, the
    payment reaches its binding phase and waits on that lock (measured in `pg_locks`), then clearing
    commits 30 and the payment commits after it - on a fresh snapshot if its first attempt met the
    clearing's writes (a serialization failure `pay()` retries; not asserted, it is the product's to
    choose). Expected state: the clearing's cycle cleared, the payment's 5.00 netted on A -> B after it.

    RED if the payment took no shared lock, or the clearing held its lock in a mode that admits a shared
    holder, or released it before its commit: the payment would not queue behind the clearing.
    """

    _require_postgres(db_session)

    from app.config import settings
    from app.core.clearing.service import ClearingService
    from app.core.payments.service import PaymentService
    from app.db.models.audit_log import IntegrityAuditLog
    from app.db.models.debt import Debt
    from app.db.models.transaction import Transaction
    from app.db.models.trustline import TrustLine
    from tests.conftest import TestingSessionLocal

    # The payment waits on the clearing's owner lock inside its binding phase, which runs under the
    # `PREPARE_TIMEOUT_SECONDS` budget; the barrier holds the clearing for a moment, and the budgets
    # are not what this schedule is about.
    monkeypatch.setattr(settings, "PREPARE_TIMEOUT_SECONDS", 30)
    monkeypatch.setattr(settings, "COMMIT_TIMEOUT_SECONDS", 30)
    monkeypatch.setattr(settings, "PAYMENT_TOTAL_TIMEOUT_SECONDS", 60)

    seed = await _seed_interlock_case()
    _a_id, b_id, _c_id = seed["participant_ids"]
    a_pid = seed["participant_pids"][0]
    payment_tx_id = str(uuid.uuid4())
    clearing_session = None
    payment_session = None
    observer_session = None
    clearing_task = None
    payment_task = None
    clearing_parked = asyncio.Event()
    release_clearing = asyncio.Event()
    clearing_work_pid: int | None = None

    try:
        clearing_session = TestingSessionLocal()
        payment_session = TestingSessionLocal()
        observer_session = TestingSessionLocal()
        await _use_serializable(clearing_session)
        payment_pid = await _use_serializable(payment_session)

        clearing_service = ClearingService(clearing_session)
        original_policy = clearing_service._cycle_respects_auto_clearing

        async def _park_inside_the_money_transaction(debts):
            # Called by the execution with the cycle rows it holds `FOR UPDATE`; `self.session` is the
            # clearing's pinned work session at this point.
            nonlocal clearing_work_pid
            isolation = await clearing_service.session.scalar(
                text("SHOW transaction_isolation")
            )
            assert str(isolation).lower() == "serializable"
            clearing_work_pid = int(
                await clearing_service.session.scalar(text("SELECT pg_backend_pid()"))
            )
            allowed = await original_policy(debts)
            clearing_parked.set()
            await release_clearing.wait()
            return allowed

        monkeypatch.setattr(
            clearing_service,
            "_cycle_respects_auto_clearing",
            _park_inside_the_money_transaction,
        )
        clearing_task = asyncio.create_task(
            clearing_service.execute_clearing_with_amount(seed["cycle"]),
            name="clearing-first-exclusive",
        )
        await asyncio.wait_for(clearing_parked.wait(), timeout=5.0)
        assert clearing_work_pid is not None
        # PREMISE: the parked clearing holds the equivalent lock EXCLUSIVELY on its pinned connection.
        held_modes = (
            await observer_session.execute(
                text(
                    "SELECT mode FROM pg_locks "
                    "WHERE pid = :pid AND locktype = 'advisory' AND granted"
                ),
                {"pid": clearing_work_pid},
            )
        ).scalars().all()
        await observer_session.rollback()
        assert held_modes == ["ExclusiveLock"], held_modes

        payment_task = asyncio.create_task(
            PaymentService(payment_session).create_payment_internal(
                b_id,
                to_pid=a_pid,
                equivalent=seed["equivalent_code"],
                amount="5.00",
                idempotency_key=payment_tx_id,
            ),
            name="reverse-payment-waiter",
        )
        assert (
            await _wait_for_advisory_waiter(
                observer_session,
                holder_pid=clearing_work_pid,
                mode="ShareLock",
                waiter_pid=payment_pid,
            )
            == payment_pid
        ), "the reverse payment's shared lock did not queue behind the clearing's exclusive lock"
        assert not payment_task.done()
        assert not clearing_task.done()

        release_clearing.set()
        cleared_amount, payment_result = await asyncio.wait_for(
            asyncio.gather(clearing_task, payment_task),
            timeout=30.0,
        )
        assert cleared_amount == Decimal("30.00000000")
        assert payment_result.status == "COMMITTED", payment_result

        async with TestingSessionLocal() as verify:
            payment_tx = await verify.scalar(
                select(Transaction).where(Transaction.tx_id == payment_tx_id)
            )
            clearing_transactions = (
                await verify.scalars(
                    select(Transaction).where(
                        Transaction.type == "CLEARING",
                        Transaction.initiator_id.in_(seed["participant_ids"]),
                    )
                )
            ).all()
            audits = (
                await verify.scalars(
                    select(IntegrityAuditLog).where(
                        IntegrityAuditLog.equivalent_code
                        == seed["equivalent_code"]
                    )
                )
            ).all()
            debts = {
                debt.id: (debt.amount, debt.version)
                for debt in (
                    await verify.scalars(
                        select(Debt).where(
                            Debt.equivalent_id == seed["equivalent_id"]
                        )
                    )
                ).all()
            }
            trust_limits = (
                await verify.scalars(
                    select(TrustLine.limit).where(
                        TrustLine.equivalent_id == seed["equivalent_id"]
                    )
                )
            ).all()

        assert payment_tx is not None and payment_tx.state == "COMMITTED"
        assert payment_tx.initiator_id == b_id
        assert len(clearing_transactions) == 1
        clearing_tx = clearing_transactions[0]
        assert clearing_tx.state == "COMMITTED"
        assert Decimal(str(clearing_tx.payload["amount"])) == Decimal("30.00000000")
        assert set(clearing_tx.payload["cycle"]) == {
            str(debt_id) for debt_id in seed["debt_ids"]
        }
        assert {
            (audit.operation_type, audit.tx_id, audit.verification_passed)
            for audit in audits
        } == {("CLEARING", clearing_tx.tx_id, True), ("PAYMENT", payment_tx_id, True)}
        # A -> B: 100 - 30 (the clearing, version 2) - 5 (the reverse payment, version 3); the cleared
        # B -> C is gone; C -> A: 40 - 30.
        assert debts == {
            seed["debt_ids"][0]: (Decimal("65.00000000"), 3),
            seed["debt_ids"][2]: (Decimal("10.00000000"), 2),
        }
        assert trust_limits == [Decimal("200.00000000")] * 4
    finally:
        primary_error = sys.exc_info()[1]
        release_clearing.set()
        try:
            tasks = [
                task for task in (clearing_task, payment_task) if task is not None
            ]
            pending = [task for task in tasks if not task.done()]
            if pending:
                # A released task is let to FINISH first: cancelling one that holds locks leaves its
                # connection idle in transaction.
                await asyncio.wait(pending, timeout=15.0)
            pending = [task for task in tasks if not task.done()]
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.wait(pending, timeout=2.0)
            for task in tasks:
                if task.done() and not task.cancelled():
                    task.exception()
            for session in (clearing_session, payment_session, observer_session):
                if session is not None:
                    await session.rollback()
                    await session.close()
        except BaseException as teardown_error:
            if primary_error is None:
                raise
            primary_error.add_note(
                "Clearing-first interlock teardown also failed: "
                f"{type(teardown_error).__name__}: {teardown_error}"
            )


# `test_uncommitted_reverse_prepare_blocks_clearing_until_visible_postgres` (payment-first) was DROPPED by
# 019 stage 4 (manifest `t1901-manifest.md` 5.3, rows :520-521, :537-538, :596-606): it held an
# UNCOMMITTED `PaymentEngine.prepare(commit=False)` and asserted that clearing, after waiting, skipped
# the cycle because a committed reservation became visible, and that the payment stayed `PREPARED` with
# one reservation. Both contracts are removed - there is no `prepare` and no durable reservation a
# payment could leave. Its remaining premise - clearing waits on a payment's owner lock, `:529-533` - is
# a stage-5 contract. Since stage 5 (`T1909`) the payment-first order against clearing is held by
# `test_interlock_timeout_rolls_back_work_and_releases_owner_postgres` below: a SHARED holder makes the
# clearing's EXCLUSIVE acquisition wait (asserted in `pg_locks` with its mode and blocker) and time out;
# the payment-first order against the admin paths is raced by
# `tests/integration/test_p019_owner_before_row_races_postgres.py`, and payment and clearing on one
# trust line by `test_concurrent_clearing_payment_lost_update_postgres.py`.

@pytest.mark.asyncio
@pytest.mark.usefixtures("tier_on_a_clone")
async def test_clearing_interlock_completes_with_single_connection_pool_postgres(
    db_session,
    committed_database,
):
    """The shared boundary must not require two simultaneous pool connections."""

    _require_postgres(db_session)

    from app.core.clearing.service import ClearingService

    seed = await _seed_interlock_case()
    one_connection_engine = create_async_engine(
        committed_database.url,
        isolation_level="SERIALIZABLE",
        pool_size=1,
        max_overflow=0,
        pool_timeout=0.25,
    )
    sessions = async_sessionmaker(
        bind=one_connection_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )
    clearing_session = sessions()
    try:
        amount = await asyncio.wait_for(
            ClearingService(clearing_session).execute_clearing_with_amount(
                seed["cycle"]
            ),
            timeout=3.0,
        )
        assert amount == Decimal("30.00000000")
        assert not clearing_session.in_transaction()
    finally:
        await clearing_session.rollback()
        await clearing_session.close()
        await one_connection_engine.dispose()


@pytest.mark.asyncio
async def test_postgres_clearing_rejects_external_connection_bind_postgres(
    db_session,
):
    """The one-connection boundary accepts engine-bound sessions only."""

    _require_postgres(db_session)

    from app.core.clearing.service import ClearingService
    from app.utils.exceptions import GeoException
    from tests.conftest import TEST_DATABASE_URL

    one_connection_engine = create_async_engine(
        TEST_DATABASE_URL,
        isolation_level="SERIALIZABLE",
        pool_size=1,
        max_overflow=0,
        pool_timeout=0.25,
    )
    try:
        async with one_connection_engine.connect() as external_connection:
            async with AsyncSession(bind=external_connection) as external_session:
                with pytest.raises(GeoException):
                    await ClearingService(
                        external_session
                    ).execute_clearing_with_amount(
                        [{"debt_id": str(uuid.uuid4())}]
                    )
                assert not external_session.in_transaction()
                assert one_connection_engine.pool.checkedout() == 1

        assert one_connection_engine.pool.checkedout() == 0
        async with one_connection_engine.connect() as probe:
            assert await probe.scalar(text("SELECT 1")) == 1
    finally:
        await one_connection_engine.dispose()


@pytest.mark.asyncio
@pytest.mark.usefixtures("tier_on_a_clone")
async def test_cancellation_after_interlock_checkout_returns_connection_postgres(
    db_session,
    committed_database,
    monkeypatch,
):
    """Cancellation between checkout and isolation setup must not exhaust the pool."""

    _require_postgres(db_session)

    from app.core.clearing.service import ClearingService

    seed = await _seed_interlock_case()
    one_connection_engine = create_async_engine(
        committed_database.url,
        isolation_level="SERIALIZABLE",
        pool_size=1,
        max_overflow=0,
        pool_timeout=0.25,
    )
    sessions = async_sessionmaker(
        bind=one_connection_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )
    clearing_session = sessions()
    isolation_setup_entered = asyncio.Event()
    hold_isolation_setup = asyncio.Event()
    original_execution_options = AsyncConnection.execution_options

    async def _pause_after_checkout(connection, *args, **kwargs):
        if (
            connection.engine is one_connection_engine
            and kwargs.get("isolation_level") is not None
        ):
            isolation_setup_entered.set()
            await hold_isolation_setup.wait()
        return await original_execution_options(connection, *args, **kwargs)

    monkeypatch.setattr(
        AsyncConnection,
        "execution_options",
        _pause_after_checkout,
    )
    task = None
    try:
        task = asyncio.create_task(
            ClearingService(clearing_session).execute_clearing_with_amount(
                seed["cycle"]
            )
        )
        await asyncio.wait_for(isolation_setup_entered.wait(), timeout=3.0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=3.0)

        assert not clearing_session.in_transaction()
        assert one_connection_engine.pool.checkedout() == 0
        async with one_connection_engine.connect() as probe:
            assert await probe.scalar(text("SELECT 1")) == 1
    finally:
        hold_isolation_setup.set()
        if task is not None and not task.done():
            task.cancel()
            await asyncio.wait([task], timeout=2.0)
        await clearing_session.rollback()
        await clearing_session.close()
        await one_connection_engine.dispose()


@pytest.mark.asyncio
@pytest.mark.usefixtures("tier_on_a_clone")
async def test_cancellation_during_interlocked_work_rolls_back_before_unlock_postgres(
    db_session,
    monkeypatch,
    caplog,
):
    _require_postgres(db_session)

    from app.core.clearing.service import ClearingService
    from app.core.money_boundary import MoneyBoundary
    from app.db.models.audit_log import IntegrityAuditLog
    from app.db.models.debt import Debt
    from app.db.models.transaction import Transaction
    from tests.conftest import TestingSessionLocal

    seed = await _seed_interlock_case()
    clearing_session = TestingSessionLocal()
    probe_session = None
    task = None
    work_entered = asyncio.Event()
    never_release = asyncio.Event()
    service = ClearingService(clearing_session)
    original_policy = service._cycle_respects_auto_clearing

    # 019 stage 5 (`T1909`): the pause point moved from the removed reservation scan to the policy read
    # of the execution - still inside the money transaction, cycle rows `FOR UPDATE`, nothing mutated.
    async def _pause_inside_money_uow(debts):
        allowed = await original_policy(debts)
        work_entered.set()
        await never_release.wait()
        return allowed

    monkeypatch.setattr(
        service,
        "_cycle_respects_auto_clearing",
        _pause_inside_money_uow,
    )
    try:
        task = asyncio.create_task(service.execute_clearing_with_amount(seed["cycle"]))
        await asyncio.wait_for(work_entered.wait(), timeout=5.0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=5.0)

        assert not clearing_session.in_transaction()
        async with TestingSessionLocal() as verify:
            final_debts = {
                debt.id: (debt.amount, debt.version)
                for debt in (
                    await verify.scalars(
                        select(Debt).where(
                            Debt.equivalent_id == seed["equivalent_id"]
                        )
                    )
                ).all()
            }
            clearing_transactions = (
                await verify.scalars(
                    select(Transaction).where(
                        Transaction.type == "CLEARING",
                        Transaction.initiator_id.in_(seed["participant_ids"]),
                    )
                )
            ).all()
            audits = (
                await verify.scalars(
                    select(IntegrityAuditLog).where(
                        IntegrityAuditLog.equivalent_code
                        == seed["equivalent_code"]
                    )
                )
            ).all()
        assert final_debts == {
            seed["debt_ids"][0]: (Decimal("100.00000000"), 1),
            seed["debt_ids"][1]: (Decimal("30.00000000"), 1),
            seed["debt_ids"][2]: (Decimal("40.00000000"), 1),
        }
        assert clearing_transactions == []
        assert audits == []
        await _no_advisory_lock_is_held(caplog)
        probe_session = TestingSessionLocal()
        await asyncio.wait_for(
            MoneyBoundary(probe_session).acquire_shared_equivalent_locks(
                [seed["equivalent_id"]]
            ),
            timeout=_PROBE_TIMEOUT,
        )
    finally:
        never_release.set()
        if task is not None and not task.done():
            task.cancel()
            await asyncio.wait([task], timeout=2.0)
        for session in (clearing_session, probe_session):
            if session is not None:
                await session.rollback()
                await session.close()


@pytest.mark.asyncio
async def test_cancellation_during_preflight_select_rolls_back_caller_postgres(
    db_session,
):
    """Cancellation before pinned ownership must still end the caller UoW."""

    _require_postgres(db_session)

    from app.core.clearing.service import ClearingService
    from tests.conftest import TestingSessionLocal

    clearing_session = TestingSessionLocal()
    holder_session = TestingSessionLocal()
    observer_session = TestingSessionLocal()
    task = None
    try:
        clearing_pid = await _use_serializable(clearing_session)
        holder_pid = await _use_serializable(holder_session)
        await holder_session.execute(text("LOCK TABLE debts IN ACCESS EXCLUSIVE MODE"))

        task = asyncio.create_task(
            ClearingService(clearing_session).execute_clearing_with_amount(
                [{"debt_id": str(uuid.uuid4())}]
            )
        )
        assert await _wait_for_exact_blocker(
            observer_session,
            waiter_pid=clearing_pid,
            holder_pid=holder_pid,
        )
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=5.0)
        assert not clearing_session.in_transaction()
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.wait([task], timeout=2.0)
        for session in (clearing_session, holder_session, observer_session):
            await session.rollback()
            await session.close()


@pytest.mark.asyncio
@pytest.mark.usefixtures("tier_on_a_clone")
async def test_cancellation_during_interlock_release_preserves_durable_amount_postgres(
    db_session,
    monkeypatch,
    caplog,
):
    _require_postgres(db_session)

    from app.core.clearing.service import (
        ClearingCommittedAfterCancellation,
        ClearingService,
    )
    from app.core.money_boundary import MoneyBoundary
    from app.db.models.transaction import Transaction
    from tests.conftest import TestingSessionLocal

    seed = await _seed_interlock_case()
    clearing_session = TestingSessionLocal()
    probe_session = None
    task = None
    cleanup_entered = asyncio.Event()
    hold_cleanup = asyncio.Event()
    original_release = ClearingService._release_interlock_session

    async def _hold_after_durable_work(*args, **kwargs):
        cleanup_task = asyncio.create_task(original_release(*args, **kwargs))
        cleanup_entered.set()
        try:
            await hold_cleanup.wait()
        except asyncio.CancelledError:
            await asyncio.shield(cleanup_task)
            raise
        return await cleanup_task

    monkeypatch.setattr(
        ClearingService,
        "_release_interlock_session",
        staticmethod(_hold_after_durable_work),
    )
    try:
        task = asyncio.create_task(
            ClearingService(clearing_session).execute_clearing_with_amount(
                seed["cycle"]
            )
        )
        await asyncio.wait_for(cleanup_entered.wait(), timeout=5.0)
        task.cancel()
        with pytest.raises(ClearingCommittedAfterCancellation) as committed:
            await asyncio.wait_for(task, timeout=5.0)
        assert committed.value.cleared_amount == Decimal("30.00000000")

        async with TestingSessionLocal() as verify:
            transactions = (
                await verify.scalars(
                    select(Transaction).where(
                        Transaction.type == "CLEARING",
                        Transaction.initiator_id.in_(seed["participant_ids"]),
                    )
                )
            ).all()
        assert len(transactions) == 1
        assert transactions[0].state == "COMMITTED"
        await _no_advisory_lock_is_held(caplog)
        probe_session = TestingSessionLocal()
        await asyncio.wait_for(
            MoneyBoundary(probe_session).acquire_shared_equivalent_locks(
                [seed["equivalent_id"]]
            ),
            timeout=_PROBE_TIMEOUT,
        )
    finally:
        hold_cleanup.set()
        if task is not None and not task.done():
            task.cancel()
            await asyncio.wait([task], timeout=2.0)
        await clearing_session.rollback()
        await clearing_session.close()
        if probe_session is not None:
            await probe_session.rollback()
            await probe_session.close()


@pytest.mark.asyncio
@pytest.mark.usefixtures("tier_on_a_clone")
async def test_interlock_timeout_rolls_back_work_and_releases_owner_postgres(
    db_session,
    monkeypatch,
    caplog,
):
    """Payment-first: a SHARED holder makes the clearing's EXCLUSIVE acquisition wait and time out.

    019 stage 5 (`T1909`): the holder takes the lock the way a payment, a staged phase or an inject does
    (shared). The clearing's wait is asserted in `pg_locks` - `advisory`, mode `ExclusiveLock`, not
    granted, blocked by the holder - and the timeout must be THAT wait (SQLSTATE `55P03`, lock_timeout),
    not the connection checkout that shares its budget. Nothing is cleared; after the holder ends, no
    advisory lock is left and the same cycle clears 30.
    """

    _require_postgres(db_session)

    from app.config import settings
    from app.core.clearing.service import ClearingService
    from app.core.money_boundary import MoneyBoundary
    from app.db.models.debt import Debt
    from app.db.models.transaction import Transaction
    from app.utils.exceptions import TimeoutException
    from tests.conftest import TestingSessionLocal

    seed = await _seed_interlock_case()
    holder_session = TestingSessionLocal()
    clearing_session = TestingSessionLocal()
    observer_session = TestingSessionLocal()
    retry_session = None
    clearing_task = None
    original_commit_timeout = settings.COMMIT_TIMEOUT_SECONDS
    original_total_timeout = settings.PAYMENT_TOTAL_TIMEOUT_SECONDS
    # Long enough for a fresh NullPool checkout and for the observer to see the wait (the checkout
    # shares this budget, and at 0.05 s it could time out before the lock was ever requested); short
    # enough for a test. The lock budget is `min(COMMIT_TIMEOUT_SECONDS, PAYMENT_TOTAL_TIMEOUT_SECONDS)`.
    monkeypatch.setattr(settings, "COMMIT_TIMEOUT_SECONDS", 2.0)
    monkeypatch.setattr(settings, "PAYMENT_TOTAL_TIMEOUT_SECONDS", 2.0)
    try:
        holder_pid = await _use_serializable(holder_session)
        await MoneyBoundary(holder_session).acquire_shared_equivalent_locks(
            [seed["equivalent_id"]]
        )
        clearing_task = asyncio.create_task(
            ClearingService(clearing_session).execute_clearing_with_amount(
                seed["cycle"]
            )
        )
        assert (
            await _wait_for_advisory_waiter(
                observer_session, holder_pid=holder_pid, mode="ExclusiveLock"
            )
            is not None
        ), "the clearing's exclusive acquisition did not queue behind the shared holder"
        with pytest.raises(TimeoutException) as timed_out:
            await asyncio.wait_for(clearing_task, timeout=10.0)
        assert "55P03" in ClearingService._postgres_error_codes(
            timed_out.value.__cause__
        ), f"the timeout was not the lock wait: {timed_out.value.__cause__!r}"
        assert not clearing_session.in_transaction()
        async with TestingSessionLocal() as verify:
            cleared = (
                await verify.scalars(
                    select(Transaction).where(
                        Transaction.type == "CLEARING",
                        Transaction.initiator_id.in_(seed["participant_ids"]),
                    )
                )
            ).all()
            untouched = {
                debt.id: (debt.amount, debt.version)
                for debt in (
                    await verify.scalars(
                        select(Debt).where(Debt.equivalent_id == seed["equivalent_id"])
                    )
                ).all()
            }
        assert cleared == []
        assert untouched == {
            seed["debt_ids"][0]: (Decimal("100.00000000"), 1),
            seed["debt_ids"][1]: (Decimal("30.00000000"), 1),
            seed["debt_ids"][2]: (Decimal("40.00000000"), 1),
        }

        await holder_session.rollback()
        monkeypatch.setattr(
            settings,
            "COMMIT_TIMEOUT_SECONDS",
            original_commit_timeout,
        )
        monkeypatch.setattr(
            settings,
            "PAYMENT_TOTAL_TIMEOUT_SECONDS",
            original_total_timeout,
        )
        await _no_advisory_lock_is_held(caplog)
        retry_session = TestingSessionLocal()
        # Same budget story as `_PROBE_TIMEOUT`: a fresh `NullPool` connection plus a whole clearing,
        # now with an envelope of its own, does not fit in three seconds on this machine.
        amount = await asyncio.wait_for(
            ClearingService(retry_session).execute_clearing_with_amount(seed["cycle"]),
            timeout=_PROBE_TIMEOUT,
        )
        assert amount == Decimal("30.00000000")
    finally:
        if clearing_task is not None and not clearing_task.done():
            clearing_task.cancel()
            await asyncio.wait([clearing_task], timeout=2.0)
        for session in (holder_session, clearing_session, observer_session, retry_session):
            if session is not None:
                await session.rollback()
                await session.close()
