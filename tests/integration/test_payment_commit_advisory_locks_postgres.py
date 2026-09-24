import asyncio
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text

from app.core.payments.engine import PaymentEngine
from app.db.journal_tables import debt_operations
from app.db.models.audit_log import IntegrityAuditLog
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.prepare_lock import PrepareLock
from app.db.models.transaction import Transaction
from app.db.models.trustline import TrustLine
from app.utils.exceptions import RoutingException

# Every test here commits through several sessions and runs on a disposable clone of the migrated
# template; its rows go with the clone's drop and nothing is deleted row by row (018 B0b; see
# `tests/tier_on_a_clone.py`).
from tests.tier_on_a_clone import tier_sessions_on_a_clone  # noqa: E402,F401 - autouse fixture



def _require_postgres(db_session) -> None:
    dialect = None
    try:
        dialect = db_session.get_bind().dialect.name
    except Exception:
        dialect = None
    if dialect not in {"postgresql", "postgres"}:
        pytest.skip("Postgres-only: validates payment commit advisory-lock serialization")


async def _use_read_committed(session) -> None:
    await session.connection(execution_options={"isolation_level": "READ COMMITTED"})
    isolation = (await session.execute(text("SHOW transaction_isolation"))).scalar_one()
    assert str(isolation).lower() == "read committed"


async def _use_serializable(session) -> None:
    # tests.conftest owns a deliberately generic test engine; opt this
    # regression into the production PostgreSQL isolation contract explicitly.
    await session.connection(execution_options={"isolation_level": "SERIALIZABLE"})
    isolation = (await session.execute(text("SHOW transaction_isolation"))).scalar_one()
    assert str(isolation).lower() == "serializable"


async def _wait_for_advisory_wait(observer, *, backend_pid: int) -> bool:
    for _ in range(200):
        waiting = await observer.scalar(
            text(
                "SELECT EXISTS ("
                "SELECT 1 FROM pg_locks "
                "WHERE pid = :pid AND locktype = 'advisory' AND NOT granted"
                ")"
            ),
            {"pid": backend_pid},
        )
        if waiting:
            return True
        await asyncio.sleep(0.01)
    return False


async def _seed_prepared_payment(
    *,
    include_waiter: bool,
    prepare_holder: bool = True,
):
    from tests.conftest import TestingSessionLocal

    nonce = uuid.uuid4().hex[:8]
    equivalent = Equivalent(
        code=f"PC{nonce}".upper(),
        description="Payment commit advisory-lock test",
        precision=2,
    )
    sender_pid = f"C_PC_{nonce}"
    receiver_pid = f"D_PC_{nonce}"
    sender = Participant(
        pid=sender_pid,
        display_name=sender_pid,
        public_key=f"pk_{sender_pid}",
        type="person",
        status="active",
    )
    receiver = Participant(
        pid=receiver_pid,
        display_name=receiver_pid,
        public_key=f"pk_{receiver_pid}",
        type="person",
        status="active",
    )

    async with TestingSessionLocal() as setup:
        setup.add_all([equivalent, sender, receiver])
        await setup.commit()
        await setup.refresh(equivalent)
        await setup.refresh(sender)
        await setup.refresh(receiver)

        setup.add(
            TrustLine(
                from_participant_id=receiver.id,
                to_participant_id=sender.id,
                equivalent_id=equivalent.id,
                limit=Decimal("10.00"),
                status="active",
            )
        )
        holder_tx = Transaction(
            id=uuid.uuid4(),
            tx_id=str(uuid.uuid4()),
            type="PAYMENT",
            initiator_id=sender.id,
            payload={},
            state="NEW",
        )
        setup.add(holder_tx)
        waiter_tx = None
        if include_waiter:
            waiter_tx = Transaction(
                id=uuid.uuid4(),
                tx_id=str(uuid.uuid4()),
                type="PAYMENT",
                initiator_id=sender.id,
                payload={},
                state="NEW",
            )
            setup.add(waiter_tx)
        await setup.commit()

    if prepare_holder:
        async with TestingSessionLocal() as prepare_session:
            await _use_read_committed(prepare_session)
            await PaymentEngine(prepare_session).prepare(
                holder_tx.tx_id,
                [sender_pid, receiver_pid],
                Decimal("8.00"),
                equivalent.id,
            )

    return {
        "equivalent_id": equivalent.id,
        "participant_ids": [sender.id, receiver.id],
        "sender_id": sender.id,
        "receiver_id": receiver.id,
        "sender_pid": sender_pid,
        "receiver_pid": receiver_pid,
        "holder_tx_id": holder_tx.tx_id,
        "waiter_tx_id": waiter_tx.tx_id if waiter_tx is not None else None,
    }


#: The identity prefix of the finished operations `_give_the_journal_a_history` writes.
#:
#: They used to be deleted again, with a `VACUUM` to give the table its pages back, because the
#: planner statistics they leave were SHARED with every later test of the run. Since 018 B0b the test
#: runs on its own disposable clone, and the rows and the statistics go with the clone's drop.
_JOURNAL_HISTORY_IDENTITY = "t1529-history-"

#: HOW MANY, AND WHY A NUMBER AT ALL (T1529, measured on PostgreSQL 16.9, 2026-09-13).
#:
#: A duplicate INSERT against an already-committed row is `23505` at READ COMMITTED, REPEATABLE READ
#: and SERIALIZABLE alike - the isolation level does not convert it into `40001`, which is what this
#: was first assumed to do. What converts it is SSI seeing the collision, and whether SSI sees it
#: was measured in `pg_locks` rather than reasoned about. The holder's completion statement
#: `UPDATE debt_operations SET state = 'COMPLETED' ... WHERE id = ... AND state = 'OPEN'` leaves
#: exactly one `SIReadLock`, and which one depends on how that statement is PLANNED:
#:
#: * all but empty table -> `Seq Scan` -> `locktype = relation` on `debt_operations`. The waiter's
#:   heap insert meets it before any index is touched, so it is cancelled as a pivot: `40001`, 20/20.
#: * a thousand analysed rows -> `Index Scan` -> `locktype = page` on `ix_debt_operations_open`
#:   only. The duplicate is then reported by `uq_debt_operations_kind_identity`, which is checked
#:   earlier and which nobody holds a predicate lock on: `23505`, 20/20 at 1000, 50000 and 200000.
#:
#: Two thousand is that threshold with margin, and it is also the shape a deployed journal is
#: permanently in - which is the point of this test: the `40001` rescue is the special case, not the
#: normal one, and the property under test must not depend on it.
_JOURNAL_HISTORY_ROWS = 2000

#: How many times the duplicate-commit schedule is repeated while PostgreSQL keeps rescuing it.
#:
#: History makes the duplicate the normal outcome, not the certain one. Measured over 18 runs of the
#: schedule with `_JOURNAL_HISTORY_ROWS` in place: `23505` fifteen times, `40001` three times. The
#: residual variance is SSI predicate-lock granularity, which depends on the holder transaction's
#: whole read set and is not a lever this stand has, so the schedule is simply repeated. At the
#: measured 5/6 per attempt, six attempts leave about one chance in forty thousand of a premise
#: failure - and none at all of a pass on an unexercised path, because the premise is asserted.
_RACE_ATTEMPTS = 6


async def _completion_update_plan() -> str:
    """How PostgreSQL currently plans the holder's envelope completion UPDATE.

    This is the precondition of the `23505` test below, and measuring it is the difference between
    a test that says "the stand is not set up" and one that reports a mysterious `40001`. The id is
    a fresh uuid that matches nothing, so the statement plans like the real one and touches no row.
    """

    from tests.conftest import TestingSessionLocal

    async with TestingSessionLocal() as session:
        rows = (
            await session.execute(
                text(
                    "EXPLAIN UPDATE debt_operations SET state = 'COMPLETED' "
                    "WHERE id = :probe AND state = 'OPEN'"
                ),
                {"probe": str(uuid.uuid4())},
            )
        ).scalars().all()
    return "\n".join(str(row) for row in rows)


async def _give_the_journal_a_history(rows: int = _JOURNAL_HISTORY_ROWS) -> int:
    """Put finished operations in `debt_operations` and analyse it, as a live journal would be.

    THROUGH THE DRIVER, like every other teardown and fixture in this suite since the journal was
    armed: Core DML naming a journal table is refused by the write guard, correctly, because it is
    indistinguishable from a writer recording work nobody verified (design v2 §8 R6). These rows are
    not a record of work - they are the table's physical size - so they go round the guard the one
    way that module documents, `exec_driver_sql`, which fires no `before_execute`.

    `TEST_FIXTURE` and a NULL `tx_id`, because `chk_debt_operations_tx_id_iff_kind` allows a
    transaction id exactly for `PAYMENT` and `CLEARING`; a filler row owns no transaction.

    THE `ANALYZE` IS LOAD-BEARING. Without fresh statistics the planner works from a default
    estimate for a table it believes has no pages, and the choice between a sequential and an index
    scan - which is what decides the SQLSTATE - becomes whatever autovacuum last did. That is
    exactly the intermittency this test exists to remove.
    """

    from tests.conftest import TestingSessionLocal

    async with TestingSessionLocal() as session:
        connection = await session.connection()
        await connection.exec_driver_sql(
            "INSERT INTO debt_operations "
            "(id, kind, identity, tx_id, intent, intent_digest, schema_version, "
            " money_encoding_version, intent_encoding_version, opened_at, state, "
            " completed_at, flush_count, effect_count, effect_digest) "
            "SELECT gen_random_uuid(), 'TEST_FIXTURE', "
            f"'{_JOURNAL_HISTORY_IDENTITY}' || g, NULL, '{{}}', repeat('0', 64), 1, 1, 1, "
            "now(), 'COMPLETED', now(), 1, 1, repeat('0', 64) "
            f"FROM generate_series(1, {int(rows)}) AS g"
        )
        await connection.exec_driver_sql("ANALYZE debt_operations")
        await session.commit()

    async with TestingSessionLocal() as verify:
        return int(
            await verify.scalar(
                select(func.count())
                .select_from(debt_operations)
                .where(debt_operations.c.identity.like(f"{_JOURNAL_HISTORY_IDENTITY}%"))
            )
        )


@pytest.mark.asyncio
async def test_prepare_reservation_blocks_concurrent_commit_on_same_segment_postgres(
    db_session,
    monkeypatch,
):
    _require_postgres(db_session)

    from tests.conftest import TestingSessionLocal

    seed = await _seed_prepared_payment(include_waiter=True)
    waiter_segment_acquired = asyncio.Event()
    release_waiter_segment = asyncio.Event()
    commit_owner_attempted = asyncio.Event()
    commit_owner_acquired = asyncio.Event()
    commit_segment_attempted = asyncio.Event()
    commit_segment_acquired = asyncio.Event()
    waiter_keys: set[int] = set()
    commit_keys: set[int] = set()
    waiter_task = None
    commit_task = None
    exercise_completed = False

    async with TestingSessionLocal() as waiter_session, TestingSessionLocal() as commit_session:
        await _use_read_committed(waiter_session)
        await _use_read_committed(commit_session)
        waiter_engine = PaymentEngine(waiter_session)
        commit_engine = PaymentEngine(commit_session)
        waiter_segment_acquire = waiter_engine._acquire_segment_advisory_lock_keys
        commit_owner_acquire = commit_engine._acquire_equivalent_owner_locks
        commit_segment_acquire = commit_engine._acquire_segment_advisory_lock_keys

        async def _hold_waiter_segment(keys):
            waiter_keys.update(keys)
            await waiter_segment_acquire(keys)
            waiter_segment_acquired.set()
            await release_waiter_segment.wait()

        async def _observe_commit_segment(keys):
            commit_keys.update(keys)
            commit_segment_attempted.set()
            await commit_segment_acquire(keys)
            commit_segment_acquired.set()

        async def _observe_commit_owner(equivalent_ids):
            commit_owner_attempted.set()
            await commit_owner_acquire(equivalent_ids)
            commit_owner_acquired.set()

        monkeypatch.setattr(
            waiter_engine,
            "_acquire_segment_advisory_lock_keys",
            _hold_waiter_segment,
        )
        monkeypatch.setattr(
            commit_engine,
            "_acquire_equivalent_owner_locks",
            _observe_commit_owner,
        )
        monkeypatch.setattr(
            commit_engine,
            "_acquire_segment_advisory_lock_keys",
            _observe_commit_segment,
        )

        async def _prepare_waiter():
            try:
                await waiter_engine.prepare(
                    seed["waiter_tx_id"],
                    [seed["sender_pid"], seed["receiver_pid"]],
                    Decimal("8.00"),
                    seed["equivalent_id"],
                )
                return "ok"
            except Exception as exc:
                await waiter_session.rollback()
                return exc

        try:
            waiter_task = asyncio.create_task(_prepare_waiter())
            await asyncio.wait_for(waiter_segment_acquired.wait(), timeout=5.0)

            commit_task = asyncio.create_task(
                commit_engine.commit(seed["holder_tx_id"])
            )
            await asyncio.wait_for(commit_owner_attempted.wait(), timeout=5.0)
            assert waiter_keys
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(commit_owner_acquired.wait(), timeout=0.25)
            assert not commit_task.done()

            release_waiter_segment.set()
            waiter_result, commit_result = await asyncio.wait_for(
                asyncio.gather(waiter_task, commit_task),
                timeout=10.0,
            )

            assert isinstance(waiter_result, RoutingException)
            assert waiter_result.code == "E002"
            assert commit_result is True
            assert commit_owner_acquired.is_set()
            assert commit_segment_attempted.is_set()
            assert commit_segment_acquired.is_set()
            assert waiter_keys == commit_keys
            exercise_completed = True
        finally:
            release_waiter_segment.set()
            tasks = [task for task in (waiter_task, commit_task) if task is not None]
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            if not exercise_completed:
                await waiter_session.rollback()
                await commit_session.rollback()

    async with TestingSessionLocal() as verify:
        holder_state = await verify.scalar(
            select(Transaction.state).where(
                Transaction.tx_id == seed["holder_tx_id"]
            )
        )
        waiter_state = await verify.scalar(
            select(Transaction.state).where(
                Transaction.tx_id == seed["waiter_tx_id"]
            )
        )
        debt_amount = await verify.scalar(
            select(Debt.amount).where(
                Debt.debtor_id == seed["sender_id"],
                Debt.creditor_id == seed["receiver_id"],
                Debt.equivalent_id == seed["equivalent_id"],
            )
        )
        lock_count = len(
            (
                await verify.execute(
                    select(PrepareLock).where(
                        PrepareLock.tx_id.in_(
                            [seed["holder_tx_id"], seed["waiter_tx_id"]]
                        )
                    )
                )
            )
            .scalars()
            .all()
        )
        assert holder_state == "COMMITTED"
        assert waiter_state == "NEW"
        assert debt_amount == Decimal("8.00000000")
        assert lock_count == 0


@pytest.mark.asyncio
async def test_concurrent_same_transaction_commit_applies_effects_once_postgres(
    db_session,
    monkeypatch,
):
    _require_postgres(db_session)

    from tests.conftest import TestingSessionLocal

    seed = await _seed_prepared_payment(include_waiter=False)
    holder_at_segment = asyncio.Event()
    release_holder = asyncio.Event()
    waiter_owner_attempted = asyncio.Event()
    waiter_owner_acquired = asyncio.Event()
    observed_retry_errors: list[tuple[str, str | None, str, str | None, bool]] = []
    waiter_preflight_calls = 0
    waiter_rollback_calls = 0
    holder_task = None
    waiter_task = None
    exercise_completed = False

    async with TestingSessionLocal() as holder_session, TestingSessionLocal() as waiter_session:
        await _use_serializable(holder_session)
        await _use_serializable(waiter_session)
        holder_engine = PaymentEngine(holder_session)
        waiter_engine = PaymentEngine(waiter_session)
        holder_engine._retry_base_delay_s = 0.0
        holder_engine._retry_max_delay_s = 0.0
        waiter_engine._retry_base_delay_s = 0.0
        waiter_engine._retry_max_delay_s = 0.0
        holder_segment_acquire = holder_engine._acquire_segment_advisory_lock_keys
        waiter_owner_acquire = waiter_engine._acquire_equivalent_owner_locks
        waiter_preflight = waiter_engine._preacquire_equivalent_owner_locks_for_tx
        waiter_retryable = waiter_engine._is_retryable_db_error
        waiter_rollback = waiter_session.rollback
        waiter_pid = int(
            (await waiter_session.execute(text("SELECT pg_backend_pid()"))).scalar_one()
        )

        async def _hold_before_segment(keys):
            holder_at_segment.set()
            await release_holder.wait()
            await holder_segment_acquire(keys)

        async def _observe_waiter_owner(equivalent_ids):
            waiter_owner_attempted.set()
            await waiter_owner_acquire(equivalent_ids)
            waiter_owner_acquired.set()

        async def _count_waiter_preflight(*args, **kwargs):
            nonlocal waiter_preflight_calls
            waiter_preflight_calls += 1
            return await waiter_preflight(*args, **kwargs)

        async def _count_waiter_rollback():
            nonlocal waiter_rollback_calls
            waiter_rollback_calls += 1
            return await waiter_rollback()

        def _record_retryable(exc, *, op):
            # THE VERDICT IS RECORDED, NOT ONLY THE ERROR (T1529, 2026-09-13). This wrapper runs
            # for every DBAPIError the engine CONSIDERS, whatever it decides, so a recorded
            # SQLSTATE on its own never meant the error was retried. The assertions below used to
            # admit `{"40001", "23505"}` from this list while `23505` on the envelope's identity
            # was classified fail-closed - an admitted code under which the test's own later
            # assertions cannot hold, because the commit raises and never reaches them.
            verdict = waiter_retryable(exc, op=op)
            observed_retry_errors.append(
                (
                    op,
                    waiter_engine._get_pgcode(exc),
                    str(exc.statement or ""),
                    waiter_engine._get_db_constraint_name(exc),
                    verdict,
                )
            )
            return verdict

        monkeypatch.setattr(
            holder_engine,
            "_acquire_segment_advisory_lock_keys",
            _hold_before_segment,
        )
        monkeypatch.setattr(
            waiter_engine,
            "_acquire_equivalent_owner_locks",
            _observe_waiter_owner,
        )
        monkeypatch.setattr(
            waiter_engine,
            "_preacquire_equivalent_owner_locks_for_tx",
            _count_waiter_preflight,
        )
        monkeypatch.setattr(
            waiter_engine,
            "_is_retryable_db_error",
            _record_retryable,
        )
        monkeypatch.setattr(waiter_session, "rollback", _count_waiter_rollback)

        try:
            holder_task = asyncio.create_task(
                holder_engine.commit(seed["holder_tx_id"])
            )
            await asyncio.wait_for(holder_at_segment.wait(), timeout=5.0)

            waiter_task = asyncio.create_task(
                waiter_engine.commit(seed["holder_tx_id"])
            )
            await asyncio.wait_for(waiter_owner_attempted.wait(), timeout=5.0)
            async with TestingSessionLocal() as observer:
                assert await _wait_for_advisory_wait(
                    observer,
                    backend_pid=waiter_pid,
                )
            assert not waiter_task.done()

            release_holder.set()
            holder_result, waiter_result = await asyncio.wait_for(
                # `return_exceptions=True` so that a waiter which RAISED is reported by the
                # assertions below, with the SQLSTATE and constraint this stand recorded, instead
                # of escaping from `gather` as a bare `IntegrityError` traceback (T1529).
                asyncio.gather(holder_task, waiter_task, return_exceptions=True),
                timeout=15.0,
            )
            assert holder_result is True, holder_result
            assert waiter_result is True, (
                f"the concurrent duplicate commit was not idempotent: {waiter_result!r}; "
                f"the engine considered {observed_retry_errors}"
            )
            assert waiter_owner_acquired.is_set()
            assert waiter_rollback_calls == 1
            assert waiter_preflight_calls == 2
            assert len(observed_retry_errors) == 1
            (
                retry_op,
                retry_code,
                retry_statement,
                retry_constraint,
                retry_retryable,
            ) = observed_retry_errors[0]
            assert retry_op == "commit"
            # WHICH CODE, AND WHY TWO ARE ADMITTED (T1529, measured 2026-09-13). PostgreSQL reports
            # this collision as `40001` when SSI sees it and as `23505` when it does not, and which
            # one it is depends on the PLAN of the holder's envelope completion UPDATE - see the
            # measurement on `_JOURNAL_HISTORY_ROWS` above. WHICH ONE THIS STAND MEETS IS NOT ITS
            # OWN BUSINESS: the planner works from statistics that any earlier test in the run can
            # move, which is why the gate saw `23505` here once and a repeat of the same suite saw
            # `40001`. So neither code is the subject - the IDEMPOTENT OUTCOME is - both are
            # admitted, and the VERDICT is asserted, which is what the admitted set alone could not
            # do: until T1529 this list could record `23505` while the engine was re-raising it, and
            # the assertions below were never reached to notice. The `23505` branch is pinned
            # deterministically by
            # `test_concurrent_duplicate_commit_is_idempotent_with_journal_history_postgres` below.
            assert retry_code in {"40001", "23505"}, retry_code
            assert retry_retryable is True, (
                f"the engine classified {retry_code} on {retry_constraint} as fail-closed, so the "
                f"assertions above held only because PostgreSQL chose the other code this time"
            )
            # See the note in `test_payment_engine_uow_retry_postgres.py`: the envelope INSERT is
            # the unit of work's first write since step 4 slice C, so it is the statement that
            # meets the conflict.
            assert "INSERT INTO debt_operations" in retry_statement, retry_statement
            exercise_completed = True
        finally:
            release_holder.set()
            tasks = [task for task in (holder_task, waiter_task) if task is not None]
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            if not exercise_completed:
                await holder_session.rollback()
                await waiter_session.rollback()

    async with TestingSessionLocal() as verify:
        transaction = await verify.scalar(
            select(Transaction).where(
                Transaction.tx_id == seed["holder_tx_id"]
            )
        )
        debt_amount = await verify.scalar(
            select(Debt.amount).where(
                Debt.debtor_id == seed["sender_id"],
                Debt.creditor_id == seed["receiver_id"],
                Debt.equivalent_id == seed["equivalent_id"],
            )
        )
        remaining_locks = (
            await verify.execute(
                select(PrepareLock).where(
                    PrepareLock.tx_id == seed["holder_tx_id"]
                )
            )
        ).scalars().all()
        reverse_debt = await verify.scalar(
            select(Debt.amount).where(
                Debt.debtor_id == seed["receiver_id"],
                Debt.creditor_id == seed["sender_id"],
                Debt.equivalent_id == seed["equivalent_id"],
            )
        )
        audit_count = await verify.scalar(
            select(func.count()).select_from(IntegrityAuditLog).where(
                IntegrityAuditLog.tx_id == seed["holder_tx_id"],
                IntegrityAuditLog.operation_type == "PAYMENT",
            )
        )
        persisted_limit = await verify.scalar(
            select(TrustLine.limit).where(
                TrustLine.equivalent_id == seed["equivalent_id"]
            )
        )
        assert transaction is not None
        assert transaction.state == "COMMITTED"
        assert transaction.error is None
        assert debt_amount == Decimal("8.00000000")
        assert reverse_debt is None
        assert remaining_locks == []
        assert audit_count == 1
        assert persisted_limit == Decimal("10.00000000")


@pytest.mark.asyncio
async def test_concurrent_duplicate_commit_is_idempotent_with_journal_history_postgres(
    db_session,
    monkeypatch,
):
    """T1529: the same race as the test above, on a journal that already has history.

    THE DEFECT. `debt_operation` INSERTs and flushes the operation envelope as the unit of work's
    FIRST write, and `_uow`'s idempotency check reads `transactions` from the unit of work's own
    snapshot. The application runs PostgreSQL at SERIALIZABLE (`DB_POSTGRES_ISOLATION_LEVEL`), so
    the second commit of one tx_id resumes from the advisory wait on a snapshot taken before the
    holder committed: it still reads `PREPARED`, walks past the `COMMITTED` short-circuit, and meets
    the holder's committed envelope on the envelope's identity. That `23505` was classified
    fail-closed, so a duplicate concurrent commit RAISED instead of returning idempotently.

    WHY A SECOND TEST AND NOT A STRONGER ASSERTION IN THE FIRST. The test above is green on this
    same code path because PostgreSQL can rescue the race with `40001`, and on an all but empty
    `debt_operations` it always does - see the measurement on `_JOURNAL_HISTORY_ROWS`. An empty
    journal is the state a reset test database is in and a deployed one never is, so this stand
    gives the table history first.

    WHY THE SCHEDULE IS RUN IN A LOOP, and this is the honest part. History makes the duplicate the
    normal outcome but not the certain one: measured over 18 runs of this schedule with history in
    place, PostgreSQL reported the duplicate 15 times and still rescued the race 3 times. The
    remaining variance is SSI predicate-lock granularity, which depends on the whole holder
    transaction's read set and is not a lever this stand has. So the schedule is repeated until the
    duplicate happens, at most `_RACE_ATTEMPTS` times - which leaves a residual one-in-forty-thousand
    chance of a premise failure and no chance at all of a vacuous pass.

    THE PREMISE IS ASSERTED, so this cannot pass by not exercising the scenario: the waiter must have
    parked on the segment advisory lock, the holder must have committed first, and the engine must
    have met `23505` on an envelope identity constraint at the envelope INSERT. If every attempt was
    rescued, this test FAILS and says so rather than reporting a green run on a path it never
    reached.

    MUTATION that must redden it: drop the `is_envelope_insert` branch from
    `PaymentEngine._is_retryable_db_error` (the code as it stood at `7ba41d4`).
    """

    _require_postgres(db_session)

    history_rows = await _give_the_journal_a_history()
    completion_plan = await _completion_update_plan()

    assert history_rows == _JOURNAL_HISTORY_ROWS, (
        f"the stand did not give the journal a history ({history_rows} rows), so the holder's "
        f"completion UPDATE is still planned as a sequential scan and this test measures the "
        f"`40001` path the test above already covers"
    )
    # THE PRECONDITION, MEASURED AND NOT ASSUMED. The `23505` branch is reachable only while the
    # holder's completion UPDATE is planned as an index scan: a sequential scan takes a
    # relation-wide `SIReadLock` on `debt_operations` (measured in `pg_locks`) and PostgreSQL
    # then cancels the waiter as a pivot every single time. Reading the plan makes a stand that
    # is not set up say so, instead of reporting a `40001` whose cause the next reader has to
    # rediscover.
    assert "Seq Scan on debt_operations" not in completion_plan, (
        f"the holder's envelope completion UPDATE is still a sequential scan despite "
        f"{history_rows} history rows, so this race cannot reach the duplicate at all:\n"
        f"{completion_plan}"
    )

    codes: list[str | None] = []
    race = None
    for _ in range(_RACE_ATTEMPTS):
        race = await _one_duplicate_commit_race(monkeypatch)
        codes.append(race["code"])
        if race["code"] == "23505":
            break

    assert race is not None
    assert race["waiter_parked"], (
        "the waiter never waited on the segment advisory lock, so it never resumed on a "
        "snapshot older than the holder's commit"
    )
    assert race["holder_result"] is True, (
        f"the holder did not commit first ({race['holder_result']!r}); without that there is "
        f"no committed envelope for the waiter to collide with"
    )
    assert race["op"] == "commit", race["op"]
    assert "INSERT INTO debt_operations" in race["statement"], race["statement"]
    assert race["code"] == "23505", (
        f"PostgreSQL rescued every one of {len(codes)} attempts with {codes} instead of "
        f"reporting the duplicate, so the fail-closed path was NOT exercised. The rescue is SSI "
        f"seeing the collision; it depends on the predicate-lock granularity of the holder's "
        f"envelope completion UPDATE, whose plan this stand checked above. A green run here "
        f"would mean nothing."
    )
    assert race["constraint"] in {
        "uq_debt_operations_kind_identity",
        "uq_debt_operations_tx_id",
    }, (
        f"the duplicate was not the envelope's identity but {race['constraint']}; this is a "
        f"different collision and must not be retried"
    )

    # --- and only now the property: the duplicate commit is idempotent ----------------------
    assert race["retryable"] is True, (
        f"{race['code']} on {race['constraint']} is classified fail-closed, so a concurrent "
        f"duplicate commit raises instead of returning idempotently"
    )
    assert race["waiter_result"] is True, (
        f"the concurrent duplicate commit was not idempotent: {race['waiter_result']!r}"
    )
    assert race["waiter_rollback_calls"] == 1, race["waiter_rollback_calls"]
    assert race["waiter_preflight_calls"] == 2, race["waiter_preflight_calls"]

    durable = race["durable"]
    assert durable["transaction_state"] == "COMMITTED", durable
    assert durable["debt_amount"] == Decimal("8.00000000"), durable
    assert durable["reverse_debt"] is None, durable
    assert durable["remaining_locks"] == 0, durable
    assert durable["audit_count"] == 1, durable
    # ONE envelope, not two: the duplicate declaration must have gone back with the attempt that
    # made it, and the retry must not have opened a second one.
    assert durable["envelopes"] == ["COMPLETED"], durable


async def _one_duplicate_commit_race(monkeypatch) -> dict:
    """Run the two-session duplicate commit once and report everything observed.

    Returns rather than asserts, because the caller repeats this until PostgreSQL reports the
    duplicate instead of rescuing the race, and a rescued attempt is not a failure - it is a draw.
    Each attempt seeds its own payment under fresh ids and reads back only those ids, so attempts
    cannot borrow each other's state; the rows stay in the test's clone until its drop (018 B0b).
    """

    from tests.conftest import TestingSessionLocal

    seed = await _seed_prepared_payment(include_waiter=False)
    holder_at_segment = asyncio.Event()
    release_holder = asyncio.Event()
    waiter_owner_attempted = asyncio.Event()
    observed: list[tuple[str, str | None, str, str | None, bool]] = []
    waiter_preflight_calls = 0
    waiter_rollback_calls = 0
    holder_task = None
    waiter_task = None
    holder_result: object = None
    waiter_result: object = None
    waiter_parked = False

    async with TestingSessionLocal() as holder_session, TestingSessionLocal() as waiter_session:
        await _use_serializable(holder_session)
        await _use_serializable(waiter_session)
        holder_engine = PaymentEngine(holder_session)
        waiter_engine = PaymentEngine(waiter_session)
        holder_engine._retry_base_delay_s = 0.0
        holder_engine._retry_max_delay_s = 0.0
        waiter_engine._retry_base_delay_s = 0.0
        waiter_engine._retry_max_delay_s = 0.0
        holder_segment_acquire = holder_engine._acquire_segment_advisory_lock_keys
        waiter_preflight = waiter_engine._preacquire_equivalent_owner_locks_for_tx
        waiter_retryable = waiter_engine._is_retryable_db_error
        waiter_rollback = waiter_session.rollback
        waiter_pid = int(
            (await waiter_session.execute(text("SELECT pg_backend_pid()"))).scalar_one()
        )

        async def _hold_before_segment(keys):
            holder_at_segment.set()
            await release_holder.wait()
            await holder_segment_acquire(keys)

        async def _count_waiter_preflight(*args, **kwargs):
            nonlocal waiter_preflight_calls
            waiter_preflight_calls += 1
            waiter_owner_attempted.set()
            return await waiter_preflight(*args, **kwargs)

        async def _count_waiter_rollback():
            nonlocal waiter_rollback_calls
            waiter_rollback_calls += 1
            return await waiter_rollback()

        def _record_retryable(exc, *, op):
            verdict = waiter_retryable(exc, op=op)
            observed.append(
                (
                    op,
                    waiter_engine._get_pgcode(exc),
                    str(exc.statement or ""),
                    waiter_engine._get_db_constraint_name(exc),
                    verdict,
                )
            )
            return verdict

        monkeypatch.setattr(
            holder_engine, "_acquire_segment_advisory_lock_keys", _hold_before_segment
        )
        monkeypatch.setattr(
            waiter_engine,
            "_preacquire_equivalent_owner_locks_for_tx",
            _count_waiter_preflight,
        )
        monkeypatch.setattr(waiter_engine, "_is_retryable_db_error", _record_retryable)
        monkeypatch.setattr(waiter_session, "rollback", _count_waiter_rollback)

        try:
            holder_task = asyncio.create_task(holder_engine.commit(seed["holder_tx_id"]))
            await asyncio.wait_for(holder_at_segment.wait(), timeout=5.0)

            waiter_task = asyncio.create_task(waiter_engine.commit(seed["holder_tx_id"]))
            await asyncio.wait_for(waiter_owner_attempted.wait(), timeout=5.0)
            async with TestingSessionLocal() as observer:
                waiter_parked = await _wait_for_advisory_wait(
                    observer, backend_pid=waiter_pid
                )

            release_holder.set()
            holder_result, waiter_result = await asyncio.wait_for(
                asyncio.gather(holder_task, waiter_task, return_exceptions=True),
                timeout=15.0,
            )
        finally:
            release_holder.set()
            tasks = [task for task in (holder_task, waiter_task) if task is not None]
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            # THE ORIGINAL `rollback`, NOT THE COUNTING WRAPPER. A clean-up rollback after a
            # raising waiter would otherwise be counted as one the retry wrapper took, and the
            # assertion that the retry rolled back EXACTLY once would be measuring this line.
            if not isinstance(waiter_result, bool):
                await waiter_rollback()
            if not isinstance(holder_result, bool):
                await holder_session.rollback()

    async with TestingSessionLocal() as verify:
        durable = {
            "transaction_state": await verify.scalar(
                select(Transaction.state).where(
                    Transaction.tx_id == seed["holder_tx_id"]
                )
            ),
            "debt_amount": await verify.scalar(
                select(Debt.amount).where(
                    Debt.debtor_id == seed["sender_id"],
                    Debt.creditor_id == seed["receiver_id"],
                    Debt.equivalent_id == seed["equivalent_id"],
                )
            ),
            "reverse_debt": await verify.scalar(
                select(Debt.amount).where(
                    Debt.debtor_id == seed["receiver_id"],
                    Debt.creditor_id == seed["sender_id"],
                    Debt.equivalent_id == seed["equivalent_id"],
                )
            ),
            "remaining_locks": int(
                await verify.scalar(
                    select(func.count())
                    .select_from(PrepareLock)
                    .where(PrepareLock.tx_id == seed["holder_tx_id"])
                )
            ),
            "audit_count": int(
                await verify.scalar(
                    select(func.count())
                    .select_from(IntegrityAuditLog)
                    .where(
                        IntegrityAuditLog.tx_id == seed["holder_tx_id"],
                        IntegrityAuditLog.operation_type == "PAYMENT",
                    )
                )
            ),
            "envelopes": list(
                (
                    await verify.execute(
                        select(debt_operations.c.state).where(
                            debt_operations.c.kind == "PAYMENT",
                            debt_operations.c.identity == seed["holder_tx_id"],
                        )
                    )
                )
                .scalars()
                .all()
            ),
        }

    op, code, statement, constraint, retryable = (
        observed[0] if len(observed) == 1 else (None, None, "", None, False)
    )
    return {
        "op": op,
        "code": code,
        "statement": statement,
        "constraint": constraint,
        "retryable": retryable,
        "observed": observed,
        "holder_result": holder_result,
        "waiter_result": waiter_result,
        "waiter_parked": waiter_parked,
        "waiter_preflight_calls": waiter_preflight_calls,
        "waiter_rollback_calls": waiter_rollback_calls,
        "durable": durable,
    }


@pytest.mark.asyncio
async def test_concurrent_commit_and_abort_share_segment_lock_protocol_postgres(
    db_session,
    monkeypatch,
):
    _require_postgres(db_session)

    from tests.conftest import TestingSessionLocal

    seed = await _seed_prepared_payment(include_waiter=False)
    holder_at_segment = asyncio.Event()
    release_holder = asyncio.Event()
    abort_tx_lock_attempted = asyncio.Event()
    abort_tx_lock_acquired = asyncio.Event()
    commit_task = None
    abort_task = None
    exercise_completed = False

    async with TestingSessionLocal() as commit_session, TestingSessionLocal() as abort_session:
        await _use_read_committed(commit_session)
        await _use_read_committed(abort_session)
        commit_engine = PaymentEngine(commit_session)
        abort_engine = PaymentEngine(abort_session)
        commit_segment_acquire = commit_engine._acquire_segment_advisory_lock_keys
        abort_execute = abort_session.execute

        async def _hold_before_segment(keys):
            holder_at_segment.set()
            await release_holder.wait()
            await commit_segment_acquire(keys)

        async def _observe_abort_tx_lock(statement, *args, **kwargs):
            sql = str(statement).lower()
            is_tx_lock = "pg_advisory_xact_lock" in sql and "namespace" in sql
            if is_tx_lock:
                abort_tx_lock_attempted.set()
            result = await abort_execute(statement, *args, **kwargs)
            if is_tx_lock:
                abort_tx_lock_acquired.set()
            return result

        monkeypatch.setattr(
            commit_engine,
            "_acquire_segment_advisory_lock_keys",
            _hold_before_segment,
        )
        monkeypatch.setattr(abort_session, "execute", _observe_abort_tx_lock)

        try:
            commit_task = asyncio.create_task(
                commit_engine.commit(seed["holder_tx_id"])
            )
            await asyncio.wait_for(holder_at_segment.wait(), timeout=5.0)

            abort_task = asyncio.create_task(
                abort_engine.abort(
                    seed["holder_tx_id"],
                    reason="Concurrent abort lost to commit",
                )
            )
            await asyncio.wait_for(abort_tx_lock_attempted.wait(), timeout=5.0)
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(abort_tx_lock_acquired.wait(), timeout=0.25)
            assert not abort_task.done()

            release_holder.set()
            commit_result, abort_result = await asyncio.wait_for(
                asyncio.gather(commit_task, abort_task),
                timeout=15.0,
            )
            assert commit_result is True
            assert abort_result is True
            assert abort_tx_lock_acquired.is_set()
            exercise_completed = True
        finally:
            release_holder.set()
            tasks = [task for task in (commit_task, abort_task) if task is not None]
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            if not exercise_completed:
                await commit_session.rollback()
                await abort_session.rollback()

    async with TestingSessionLocal() as verify:
        tx = (
            await verify.execute(
                select(Transaction).where(
                    Transaction.tx_id == seed["holder_tx_id"]
                )
            )
        ).scalar_one()
        debt_amount = await verify.scalar(
            select(Debt.amount).where(
                Debt.debtor_id == seed["sender_id"],
                Debt.creditor_id == seed["receiver_id"],
                Debt.equivalent_id == seed["equivalent_id"],
            )
        )
        remaining_locks = (
            await verify.execute(
                select(PrepareLock).where(
                    PrepareLock.tx_id == seed["holder_tx_id"]
                )
            )
        ).scalars().all()

        assert tx.state == "COMMITTED"
        assert debt_amount == Decimal("8.00000000")
        assert tx.error is None
        assert remaining_locks == []


@pytest.mark.asyncio
async def test_duplicate_prepare_cannot_resurrect_transaction_during_commit_postgres(
    db_session,
    monkeypatch,
):
    _require_postgres(db_session)

    from tests.conftest import TestingSessionLocal

    seed = await _seed_prepared_payment(include_waiter=False)
    duplicate_tx_lock_acquired = asyncio.Event()
    release_duplicate_tx_lock = asyncio.Event()
    commit_owner_attempted = asyncio.Event()
    commit_owner_acquired = asyncio.Event()
    commit_tx_lock_attempted = asyncio.Event()
    commit_tx_lock_acquired = asyncio.Event()
    duplicate_task = None
    commit_task = None
    exercise_completed = False

    async with TestingSessionLocal() as duplicate_session, TestingSessionLocal() as commit_session:
        await _use_read_committed(duplicate_session)
        await _use_read_committed(commit_session)
        duplicate_engine = PaymentEngine(duplicate_session)
        commit_engine = PaymentEngine(commit_session)
        duplicate_tx_lock = duplicate_engine._acquire_tx_advisory_lock
        commit_owner_lock = commit_engine._acquire_equivalent_owner_locks
        commit_tx_lock = commit_engine._acquire_tx_advisory_lock

        async def _hold_duplicate_tx_lock(tx_id):
            await duplicate_tx_lock(tx_id)
            duplicate_tx_lock_acquired.set()
            await release_duplicate_tx_lock.wait()

        async def _observe_commit_tx_lock(tx_id):
            commit_tx_lock_attempted.set()
            await commit_tx_lock(tx_id)
            commit_tx_lock_acquired.set()

        async def _observe_commit_owner_lock(equivalent_ids):
            commit_owner_attempted.set()
            await commit_owner_lock(equivalent_ids)
            commit_owner_acquired.set()

        monkeypatch.setattr(
            duplicate_engine,
            "_acquire_tx_advisory_lock",
            _hold_duplicate_tx_lock,
        )
        monkeypatch.setattr(
            commit_engine,
            "_acquire_equivalent_owner_locks",
            _observe_commit_owner_lock,
        )
        monkeypatch.setattr(
            commit_engine,
            "_acquire_tx_advisory_lock",
            _observe_commit_tx_lock,
        )

        try:
            duplicate_task = asyncio.create_task(
                duplicate_engine.prepare(
                    seed["holder_tx_id"],
                    [seed["sender_pid"], seed["receiver_pid"]],
                    Decimal("8.00"),
                    seed["equivalent_id"],
                )
            )
            await asyncio.wait_for(duplicate_tx_lock_acquired.wait(), timeout=5.0)

            commit_task = asyncio.create_task(
                commit_engine.commit(seed["holder_tx_id"])
            )
            await asyncio.wait_for(commit_owner_attempted.wait(), timeout=5.0)
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(commit_owner_acquired.wait(), timeout=0.25)
            assert not commit_task.done()

            release_duplicate_tx_lock.set()
            duplicate_result, commit_result = await asyncio.wait_for(
                asyncio.gather(duplicate_task, commit_task),
                timeout=15.0,
            )
            assert duplicate_result is True
            assert commit_result is True
            assert commit_owner_acquired.is_set()
            assert commit_tx_lock_attempted.is_set()
            assert commit_tx_lock_acquired.is_set()
            exercise_completed = True
        finally:
            release_duplicate_tx_lock.set()
            tasks = [task for task in (duplicate_task, commit_task) if task is not None]
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            if not exercise_completed:
                await duplicate_session.rollback()
                await commit_session.rollback()

    async with TestingSessionLocal() as verify:
        tx = await verify.scalar(
            select(Transaction).where(
                Transaction.tx_id == seed["holder_tx_id"]
            )
        )
        debt_amount = await verify.scalar(
            select(Debt.amount).where(
                Debt.debtor_id == seed["sender_id"],
                Debt.creditor_id == seed["receiver_id"],
                Debt.equivalent_id == seed["equivalent_id"],
            )
        )
        remaining_locks = await verify.scalars(
            select(PrepareLock).where(
                PrepareLock.tx_id == seed["holder_tx_id"]
            )
        )
        assert tx is not None and tx.state == "COMMITTED"
        assert debt_amount == Decimal("8.00000000")
        assert remaining_locks.all() == []


@pytest.mark.asyncio
async def test_new_prepare_cannot_resurrect_transaction_during_abort_postgres(
    db_session,
    monkeypatch,
):
    _require_postgres(db_session)

    from tests.conftest import TestingSessionLocal

    seed = await _seed_prepared_payment(
        include_waiter=False,
        prepare_holder=False,
    )
    participant_read_ready = asyncio.Event()
    release_participant_read = asyncio.Event()
    abort_tx_lock_attempted = asyncio.Event()
    prepare_task = None
    abort_task = None
    exercise_completed = False

    async with TestingSessionLocal() as prepare_session, TestingSessionLocal() as abort_session:
        await _use_read_committed(prepare_session)
        await _use_read_committed(abort_session)
        prepare_execute = prepare_session.execute
        abort_execute = abort_session.execute

        async def _prepare_execute(statement, *args, **kwargs):
            sql = str(statement).lower()
            if "from participants" in sql and "participants.pid" in sql:
                participant_read_ready.set()
                await release_participant_read.wait()
            return await prepare_execute(statement, *args, **kwargs)

        async def _abort_execute(statement, *args, **kwargs):
            sql = str(statement).lower()
            if "pg_advisory_xact_lock" in sql and "namespace" in sql:
                abort_tx_lock_attempted.set()
            return await abort_execute(statement, *args, **kwargs)

        monkeypatch.setattr(prepare_session, "execute", _prepare_execute)
        monkeypatch.setattr(abort_session, "execute", _abort_execute)

        try:
            prepare_task = asyncio.create_task(
                PaymentEngine(prepare_session).prepare(
                    seed["holder_tx_id"],
                    [seed["sender_pid"], seed["receiver_pid"]],
                    Decimal("8.00"),
                    seed["equivalent_id"],
                )
            )
            await asyncio.wait_for(participant_read_ready.wait(), timeout=5.0)

            abort_task = asyncio.create_task(
                PaymentEngine(abort_session).abort(
                    seed["holder_tx_id"],
                    reason="Concurrent abort after prepare",
                )
            )
            await asyncio.wait_for(abort_tx_lock_attempted.wait(), timeout=5.0)
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(asyncio.shield(abort_task), timeout=0.25)

            release_participant_read.set()
            prepare_result, abort_result = await asyncio.wait_for(
                asyncio.gather(prepare_task, abort_task),
                timeout=15.0,
            )
            assert prepare_result is True
            assert abort_result is True
            exercise_completed = True
        finally:
            release_participant_read.set()
            tasks = [task for task in (prepare_task, abort_task) if task is not None]
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            if not exercise_completed:
                await prepare_session.rollback()
                await abort_session.rollback()

    async with TestingSessionLocal() as verify:
        tx = await verify.scalar(
            select(Transaction).where(
                Transaction.tx_id == seed["holder_tx_id"]
            )
        )
        debt_count = await verify.scalar(
            select(func.count()).select_from(Debt).where(
                Debt.equivalent_id == seed["equivalent_id"]
            )
        )
        remaining_locks = await verify.scalars(
            select(PrepareLock).where(
                PrepareLock.tx_id == seed["holder_tx_id"]
            )
        )
        assert tx is not None and tx.state == "ABORTED"
        assert (tx.error or {}).get("message") == "Concurrent abort after prepare"
        assert debt_count == 0
        assert remaining_locks.all() == []
