"""Step 5c on PostgreSQL: the integrity hold binds at the T1544 boundary, measured at SERIALIZABLE.

Only two outcomes are allowed for money racing the reaction that sets a hold: the money commits BEFORE the
hold's transaction commits, or the hold commits first and the money is refused. The reaction holds the
equivalent owner lock through its commit, like the deactivating PATCH, so:

| race                    | what binds it                                                                   |
|-------------------------|---------------------------------------------------------------------------------|
| payment commit <-> hold | `FOR SHARE` on the equivalent row at payment commit (reads the hold in the same |
|                         | statement as `is_active`), after the owner lock, against the hold's UPDATE      |
| clearing <-> hold       | the reaction's owner lock through its commit; clearing reads the hold in its    |
|                         | fresh post-lock snapshot                                                        |

Plus the reaction's own ordering - the owner lock BEFORE the authoritative snapshot - and the admin clear
under the same lock; the placement of the hold below the payment TTL branch; and both schema construction
paths with the migration's downgrade refusal.

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
from app.core.payments.engine import PaymentEngine
from app.core.payments.service import PaymentService
from app.db.base import Base
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.prepare_lock import PrepareLock
from app.db.models.transaction import Transaction
from app.db.reconciliation_tables import debt_reconciliation_results
from app.utils.exceptions import ConflictException, RetryablePaymentConflictException
from tests.integration.test_clearing_payment_prepare_interlock_postgres import (
    _cleanup_interlock_case,
    _no_advisory_lock_is_held,
    _seed_interlock_case,
    _use_serializable,
)
from tests.integration.test_p015_p1_money_replay_postgres import (  # noqa: F401 - `factory` is a fixture
    _OPENING,
    _cleanup,
    _debts,
    _prepare_locks,
    _seed,
    _transactions,
    factory,
)
from tests.integration.test_p015_t1544_operator_stop_races_postgres import (  # noqa: F401 - fixture
    ADMIN,
    _advisory_waiter_exists,
    admin_api,
)
from tests.migrated_schema import REPO_ROOT, run_alembic_upgrade_head, scratch_databases
from tests.unit.test_p015_step5c_reaction_and_hold import hold_directly

# MODE B (017 stage 2c, T1702): every commit of this module lands in a clone dropped after the test,
# not in the tier database it shares with mode-A tests - see `tests/tier_on_a_clone.py`.
from tests.tier_on_a_clone import tier_sessions_on_a_clone  # noqa: E402,F401 - autouse fixture

HOLD = PaymentEngine.EQUIVALENT_INTEGRITY_HOLD_REASON
_ATOM = Decimal("0.00000001")


@pytest.fixture(autouse=True)
def _barrier_budgets(monkeypatch):
    # The barriers hold locks for a moment; the default advisory-lock budgets are seconds and are not
    # what these controls are about.
    monkeypatch.setattr(settings, "COMMIT_TIMEOUT_SECONDS", 30)
    monkeypatch.setattr(settings, "PAYMENT_TOTAL_TIMEOUT_SECONDS", 60)


async def _baseline_and_one_atom(factory, equivalent_id, *, debt_id=None) -> None:
    async with factory() as session:
        await take_baseline(session, equivalent_id)
        await session.commit()
    async with factory() as session:
        if debt_id is None:
            debt_id = (
                await session.execute(select(Debt.id).where(Debt.equivalent_id == equivalent_id))
            ).scalar_one()
        connection = await session.connection()
        await connection.exec_driver_sql(
            f"UPDATE debts SET amount = amount + 0.00000001 WHERE id = '{uuid.UUID(str(debt_id))}'"
        )
        await session.commit()


async def _hold_of(factory, equivalent_id):
    async with factory() as session:
        return (
            await session.execute(
                select(Equivalent.integrity_hold_result_id).where(Equivalent.id == equivalent_id)
            )
        ).scalar_one()


def _pause_after_the_hold_is_written(monkeypatch) -> tuple[asyncio.Event, asyncio.Event]:
    """The reaction stops with the hold UPDATE executed, its owner lock held, and nothing committed."""

    reached, release = asyncio.Event(), asyncio.Event()
    original = reconciliation._set_integrity_hold

    async def _set_then_wait(session, equivalent_id, result_id):
        await original(session, equivalent_id, result_id)
        reached.set()
        await release.wait()

    monkeypatch.setattr(reconciliation, "_set_integrity_hold", _set_then_wait)
    return reached, release


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
async def test_step5c_p_a_payment_commit_waiting_behind_the_reaction_is_refused_by_the_hold(
    factory, monkeypatch, caplog
) -> None:
    """HOLD FIRST. The payment is prepared; the reaction holds the owner lock with the hold written; the
    commit takes its snapshot and waits on that lock; the hold commits.

    RED if the reaction releases its owner lock before its commit, or if the commit reads the hold without
    `FOR SHARE` (plainly, or from the `Equivalent` the service loaded): the stale snapshot still says "not
    held" and 10.00 commits after the hold did.
    """
    world = await _seed(factory)
    code = world.equivalent.code
    tx_id = str(uuid.uuid4())
    payment = reconcile = None
    try:
        await _baseline_and_one_atom(factory, world.equivalent.id)
        prepared, release_commit = asyncio.Event(), asyncio.Event()
        original_commit = PaymentEngine.commit

        async def _commit_after_barrier(self, tx_id_arg, *, commit=True):
            prepared.set()
            await release_commit.wait()
            return await original_commit(self, tx_id_arg, commit=commit)

        monkeypatch.setattr(PaymentEngine, "commit", _commit_after_barrier)
        hold_written, release_hold = _pause_after_the_hold_is_written(monkeypatch)

        async def _pay():
            async with factory() as session:
                return await PaymentService(session).create_payment_internal(
                    world.sender.id, to_pid=world.receiver.pid, equivalent=code, amount="10.00",
                    idempotency_key=tx_id,
                )

        with caplog.at_level(logging.WARNING):
            payment = asyncio.create_task(_pay())
            await asyncio.wait_for(prepared.wait(), timeout=20)
            assert await _transactions(factory, world) == {tx_id: "PREPARED"}, "premise: not prepared"

            reconcile = asyncio.create_task(
                run_scheduled_reconciliation(factory, equivalent_ids=[world.equivalent.id])
            )
            await asyncio.wait_for(hold_written.wait(), timeout=30)

            release_commit.set()
            assert await _advisory_waiter_exists(), "premise: the commit did not wait on the reaction's lock"
            assert not payment.done()

            release_hold.set()
            counts = await asyncio.wait_for(reconcile, timeout=30)
            assert counts[f"hold_{HOLD_SET}"] == 1, counts
            with pytest.raises(ConflictException) as refused:
                await asyncio.wait_for(payment, timeout=30)

        _assert_hold_refusal(refused.value, code)
        retries = [r.getMessage() for r in caplog.records if "event=payment.uow_retry op=commit" in r.getMessage()]
        assert any("pgcode=40001" in m for m in retries), (
            f"premise: the refusal did not come through the FOR SHARE serialization failure: {retries}"
        )
        assert await _debts(factory, world) == {(world.sender.pid, world.receiver.pid): _OPENING + _ATOM}
        assert await _transactions(factory, world) == {tx_id: "ABORTED"}
        assert await _prepare_locks(factory, world) == 0
        assert await _hold_of(factory, world.equivalent.id) is not None
    finally:
        await _finish(payment, reconcile)
        await _cleanup(factory, world)


@pytest.mark.asyncio
async def test_step5c_p_a_reaction_arriving_while_a_payment_holds_its_check_waits_and_holds_after(
    factory, monkeypatch
) -> None:
    """PAYMENT FIRST. The payment has passed its commit check and holds its locks; the reaction must wait
    on the owner lock - measured - and hold only after the payment committed; the next payment is refused.

    RED if the reaction takes no owner lock: it holds while the payment is still about to commit.
    """
    world = await _seed(factory)
    code = world.equivalent.code
    payment = reconcile = None
    try:
        await _baseline_and_one_atom(factory, world.equivalent.id)
        checked, release_payment = asyncio.Event(), asyncio.Event()
        original_check = PaymentEngine.refuse_inactive_equivalents

        async def _check_then_wait(self, equivalent_ids, *, row_lock):
            await original_check(self, equivalent_ids, row_lock=row_lock)
            if row_lock and not checked.is_set():
                checked.set()
                await release_payment.wait()

        monkeypatch.setattr(PaymentEngine, "refuse_inactive_equivalents", _check_then_wait)

        async def _pay(tx_id: str):
            async with factory() as session:
                return await PaymentService(session).create_payment_internal(
                    world.sender.id, to_pid=world.receiver.pid, equivalent=code, amount="10.00",
                    idempotency_key=tx_id,
                )

        completed: list[str] = []
        payment = asyncio.create_task(_pay(str(uuid.uuid4())))
        payment.add_done_callback(lambda _t: completed.append("payment"))
        await asyncio.wait_for(checked.wait(), timeout=20)

        reconcile = asyncio.create_task(run_scheduled_reconciliation(factory, equivalent_ids=[world.equivalent.id]))
        reconcile.add_done_callback(lambda _t: completed.append("reaction"))
        assert await _advisory_waiter_exists(), "the reaction did not wait for the payment that passed its check"
        assert not reconcile.done()
        assert await _hold_of(factory, world.equivalent.id) is None

        release_payment.set()
        result = await asyncio.wait_for(payment, timeout=30)
        counts = await asyncio.wait_for(reconcile, timeout=30)

        assert result.status == "COMMITTED", result
        assert (counts[FAILED], counts[f"hold_{HOLD_SET}"]) == (1, 1), counts
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
        await _cleanup(factory, world)


@pytest.mark.asyncio
async def test_step5c_p_the_owner_lock_comes_before_the_authoritative_snapshot(factory) -> None:
    """The scheduled verdict is FAILED; the reaction waits on the owner lock; the fault is repaired and
    committed while it waits; the reaction's snapshot must be taken AFTER the lock and see the repair.

    RED if the reaction takes the owner lock in its work transaction (the snapshot is then taken at that
    transaction's first statement, before the wait) or reads anything before the lock: it re-verifies the
    stale state and holds an equivalent whose ledger is already consistent.
    """
    world = await _seed(factory)
    holder = factory()
    reconcile = None
    try:
        await _baseline_and_one_atom(factory, world.equivalent.id)
        await PaymentEngine(holder).acquire_staged_equivalent_owner_locks([world.equivalent.id])

        reconcile = asyncio.create_task(run_scheduled_reconciliation(factory, equivalent_ids=[world.equivalent.id]))
        assert await _advisory_waiter_exists(), "premise: the reaction did not wait on the owner lock"
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

        async with factory() as repair:
            connection = await repair.connection()
            await connection.exec_driver_sql(
                f"UPDATE debts SET amount = amount - 0.00000001 WHERE equivalent_id = '{world.equivalent.id}'"
            )
            await repair.commit()
        assert not reconcile.done(), "premise: the repair did not land while the reaction waited"

        await holder.rollback()
        counts = await asyncio.wait_for(reconcile, timeout=30)
        assert counts[f"hold_{HOLD_NOT_CONFIRMED}"] == 1, counts
        assert await _hold_of(factory, world.equivalent.id) is None
    finally:
        await holder.rollback()
        await holder.close()
        await _finish(reconcile)
        await _cleanup(factory, world)


# ── clearing <-> hold ──────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_step5c_p_a_clearing_that_waited_behind_the_reaction_refuses_in_its_fresh_snapshot(
    factory, monkeypatch, caplog
) -> None:
    """HOLD FIRST. The reaction holds the owner lock with the hold written; clearing waits on it; the hold
    commits; clearing, rolled back to a fresh snapshot after its lock, reads the hold and refuses.

    RED if the reaction releases its lock before its commit: clearing then reads "not held" and clears.
    """
    from tests.conftest import TestingSessionLocal

    seed = await _seed_interlock_case()
    clearing_session = TestingSessionLocal()
    clearing = reconcile = None
    try:
        await _baseline_and_one_atom(factory, seed["equivalent_id"], debt_id=seed["debt_ids"][0])
        hold_written, release_hold = _pause_after_the_hold_is_written(monkeypatch)
        reconcile = asyncio.create_task(run_scheduled_reconciliation(factory, equivalent_ids=[seed["equivalent_id"]]))
        await asyncio.wait_for(hold_written.wait(), timeout=30)

        await _use_serializable(clearing_session)
        clearing = asyncio.create_task(ClearingService(clearing_session).execute_clearing_with_amount(seed["cycle"]))
        assert await _advisory_waiter_exists(), "premise: the clearing did not wait on the reaction's lock"
        assert not clearing.done()

        release_hold.set()
        counts = await asyncio.wait_for(reconcile, timeout=30)
        assert counts[f"hold_{HOLD_SET}"] == 1, counts
        with pytest.raises(ConflictException) as refused:
            await asyncio.wait_for(clearing, timeout=30)
        _assert_hold_refusal(refused.value, seed["equivalent_code"])

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
        await _no_advisory_lock_is_held(caplog)
    finally:
        await _finish(clearing, reconcile)
        await clearing_session.rollback()
        await clearing_session.close()
        await _cleanup_interlock_case(seed)


@pytest.mark.asyncio
async def test_step5c_p_a_reaction_waits_for_a_clearing_that_already_read_the_hold(factory, monkeypatch) -> None:
    """CLEARING FIRST. Clearing holds its owner lock, has read "not held", and pauses before mutating; the
    reaction must wait - measured - and hold only after the clearing committed.

    RED if the reaction takes no owner lock: the hold commits while the clearing is still about to commit.
    """
    from tests.conftest import TestingSessionLocal

    seed = await _seed_interlock_case()
    clearing_session = TestingSessionLocal()
    completed: list[str] = []
    paused, release_clearing = asyncio.Event(), asyncio.Event()
    clearing = reconcile = None
    try:
        await _baseline_and_one_atom(factory, seed["equivalent_id"], debt_id=seed["debt_ids"][0])
        await _use_serializable(clearing_session)
        service = ClearingService(clearing_session)
        original_locked_pairs = service._locked_pairs_for_equivalent

        async def _pause_before_mutation(equivalent_id):
            pairs = await original_locked_pairs(equivalent_id)
            paused.set()
            await release_clearing.wait()
            return pairs

        monkeypatch.setattr(service, "_locked_pairs_for_equivalent", _pause_before_mutation)
        clearing = asyncio.create_task(service.execute_clearing_with_amount(seed["cycle"]))
        clearing.add_done_callback(lambda _t: completed.append("clearing"))
        await asyncio.wait_for(paused.wait(), timeout=20)

        reconcile = asyncio.create_task(run_scheduled_reconciliation(factory, equivalent_ids=[seed["equivalent_id"]]))
        reconcile.add_done_callback(lambda _t: completed.append("reaction"))
        assert await _advisory_waiter_exists(), "the reaction did not wait on the clearing's owner lock"
        assert not reconcile.done()

        release_clearing.set()
        amount = await asyncio.wait_for(clearing, timeout=30)
        counts = await asyncio.wait_for(reconcile, timeout=30)

        assert amount == Decimal("30.00000000"), "premise: the clearing did not run to its commit"
        assert (counts[FAILED], counts[f"hold_{HOLD_SET}"]) == (1, 1), counts
        assert completed == ["clearing", "reaction"], completed
        assert await _hold_of(factory, seed["equivalent_id"]) is not None
    finally:
        release_clearing.set()
        await _finish(clearing, reconcile)
        await clearing_session.rollback()
        await clearing_session.close()
        await _cleanup_interlock_case(seed)


# ── placement: below the TTL branch ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_step5c_p_an_expired_payment_in_a_held_equivalent_is_aborted_as_expired(factory) -> None:
    """Precedence. RED if a hold check is placed above the TTL branch of the payment commit."""
    from datetime import timedelta

    world = await _seed(factory)
    tx_id = str(uuid.uuid4())
    try:
        async with factory() as setup:
            setup.add(
                Transaction(id=uuid.uuid4(), tx_id=tx_id, type="PAYMENT", initiator_id=world.sender.id,
                            payload={"from": world.sender.pid, "to": world.receiver.pid}, state="PREPARED")
            )
            await setup.flush()
            setup.add(
                PrepareLock(
                    tx_id=tx_id,
                    participant_id=world.sender.id,
                    effects={"flows": [{"from": str(world.sender.id), "to": str(world.receiver.id),
                                        "amount": "7.00", "equivalent": str(world.equivalent.id)}]},
                    expires_at=datetime.now(timezone.utc) - timedelta(minutes=5),
                )
            )
            await setup.commit()
        await hold_directly(factory, world.equivalent.id)
        assert await _hold_of(factory, world.equivalent.id) is not None, "premise: not held"

        async with factory() as session:
            with pytest.raises(ConflictException) as refused:
                await PaymentEngine(session).commit(tx_id)

        assert "expired before commit" in refused.value.message, refused.value.message
        assert (refused.value.details or {}).get("reason") != HOLD, (
            "an expired payment was refused as held: the hold check sits above the TTL branch"
        )
        assert await _transactions(factory, world) == {tx_id: "ABORTED"}
    finally:
        await _cleanup(factory, world)


# ── the admin clear, under the owner lock ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_step5c_p_the_admin_clear_waits_for_the_owner_lock(factory, admin_api) -> None:
    """A held equivalent with a later PASSED; a holder of the owner lock; the clear must wait - measured -
    and succeed only after the holder is gone.

    RED if the clear takes no owner lock: it returns while the lock holder is still inside.
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

        await PaymentEngine(holder).acquire_staged_equivalent_owner_locks([world.equivalent.id])
        clear = asyncio.create_task(
            client.post(
                f"/api/v1/admin/equivalents/{world.equivalent.code}/integrity-hold/clear",
                json={"reason": "s5c clear under the owner lock"},
                headers=ADMIN,
            )
        )
        assert await _advisory_waiter_exists(), "the clear did not wait on the owner lock"
        assert not clear.done()

        await holder.rollback()
        resp = await asyncio.wait_for(clear, timeout=30)
        assert resp.status_code == 200, resp.text
        assert await _hold_of(factory, world.equivalent.id) is None
    finally:
        await holder.rollback()
        await holder.close()
        await _finish(clear)
        await _cleanup(factory, world)


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
        await _cleanup(factory, world)


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
