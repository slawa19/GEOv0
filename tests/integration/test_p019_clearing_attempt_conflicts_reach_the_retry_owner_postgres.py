"""Programme 019 stage 5, `T1909` step 3 (5a review P2): a database conflict ANYWHERE in a clearing attempt
reaches the retry owner with its original SQLSTATE.

THE FINDING (fourth consultation, P2, class 2). `ClearingService._run_attempts` retries an attempt that met
40001/40P01 - but only where the attempt converted the error into `_ClearingAttemptConflict`: the stop/hold
read, the committed-occurrence read, the cycle `FOR UPDATE` and the money block. Four statement groups in
between did not: the auto-clearing policy read (`_cycle_respects_auto_clearing`), the metadata read (the
equivalent and the participants), the net positions before (`InvariantChecker._calculate_net_position`) went
through `_raise_unexpected_execution` (`E010`), and the checkpoint before
(`compute_integrity_checkpoint_for_equivalent`) SWALLOWED the error - the next statement then failed with
`25P02` (transaction aborted) and the original SQLSTATE was lost behind it. Intended: whole-execution retry
(spec, "Изоляция, писатели и клиринг", item 3).

THE SCHEDULE - a REAL deadlock, detected by PostgreSQL, at the named call site. A blocker session takes
`ACCESS EXCLUSIVE` on a table that the attempt reads for the first time AT that call site; the clearing
reaches it and waits (observed in `pg_locks`); the blocker then asks for a cycle debt row the clearing holds
`FOR UPDATE`. The clearing waited first, so ITS deadlock timer fires first and PostgreSQL aborts the
clearing's statement with `40P01`; the blocker then gets its row and rolls back, freeing the table. The
metadata site is reached naturally (`participants` is first read there). The policy site too
(`trust_lines`). The net-positions and checkpoint sites read only tables the attempt already holds
(`debts`, `trust_lines`), so there the call site is INSTRUMENTED: its function first reads a test-only
table `p019_t1909_barrier` - the statement is the test's, the deadlock and its SQLSTATE are PostgreSQL's
own (no error is injected). Every site is instrumented only on its first call, so the retry runs clean.

CONTROLS before the target: the clearing was really seen waiting on that table, the blocker really waited
on the clearing's row, and PostgreSQL's own `pg_stat_database.deadlocks` counted the deadlock (independent of
the code under test - on the old code the checkpoint site swallowed the 40P01, so no application hook would
have seen it). TARGET: the retry predicate saw `40P01`, and the execution is retried and clears the serial
result `A->B 70, C->A 10` (the cycle A->B 100, B->C 30, C->A 40 cleared by 30) with ONE committed occurrence,
its envelope and its audit row - instead of `E010`.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.clearing import service as clearing_module
from app.core.clearing.service import ClearingService
from app.core.invariants import InvariantChecker
from app.core.payments.router import PaymentRouter
from app.db.journal_tables import debt_operations
from app.db.models.audit_log import IntegrityAuditLog
from app.db.models.debt import Debt
from app.db.models.transaction import Transaction
from tests.integration.p019_interlock_support import _seed_interlock_case
from tests.p019_support import require_target, target_xfail

# MODE B: every commit lands in a clone dropped after the test (`tests/tier_on_a_clone.py`).
from tests.tier_on_a_clone import tier_sessions_on_a_clone  # noqa: E402,F401 - autouse fixture

BARRIER = "p019_t1909_barrier"
SITES = ["policy", "metadata", "net_positions", "checkpoint"]


@pytest_asyncio.fixture
async def stand(committed_database):
    engine = create_async_engine(
        committed_database.url, pool_size=6, max_overflow=0, pool_timeout=20, isolation_level="SERIALIZABLE"
    )
    try:
        factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False)
        async with factory() as s:
            await s.execute(text(f"CREATE TABLE IF NOT EXISTS {BARRIER} (id int)"))
            await s.commit()
        yield factory
    finally:
        await engine.dispose()


def _instrument(monkeypatch, site: str) -> None:
    """For the two sites that read only tables the attempt already holds: read the barrier first, once."""

    done: list[int] = []

    async def barrier_read(session) -> None:
        if not done:
            done.append(1)
            await session.execute(text(f"SELECT count(*) FROM {BARRIER}"))

    if site == "net_positions":
        original = InvariantChecker._calculate_net_position

        async def net_position(self, participant_id, equivalent_id):
            await barrier_read(self.session)
            return await original(self, participant_id, equivalent_id)

        monkeypatch.setattr(InvariantChecker, "_calculate_net_position", net_position)
    elif site == "checkpoint":
        original_checkpoint = clearing_module.compute_integrity_checkpoint_for_equivalent

        async def checkpoint(session, *, equivalent_id):
            await barrier_read(session)
            return await original_checkpoint(session, equivalent_id=equivalent_id)

        monkeypatch.setattr(clearing_module, "compute_integrity_checkpoint_for_equivalent", checkpoint)


_TABLE = {"policy": "trust_lines", "metadata": "participants", "net_positions": BARRIER, "checkpoint": BARRIER}


def _record_retried_codes(monkeypatch) -> list[list[str]]:
    """The SQLSTATEs of every error the clearing's retry predicate called retryable."""

    seen: list[list[str]] = []
    original = ClearingService._is_retryable_concurrency_error.__func__

    def recording(cls, exc):
        retryable = original(cls, exc)
        if retryable:
            seen.append(sorted(cls._postgres_error_codes(exc) & {"40001", "40P01"}))
        return retryable

    monkeypatch.setattr(ClearingService, "_is_retryable_concurrency_error", classmethod(recording))
    return seen


async def _deadlocks(stand) -> int:
    """PostgreSQL's own count of detected deadlocks in this database - evidence independent of the code."""

    async with stand() as s:
        value = await s.scalar(
            text("SELECT deadlocks FROM pg_stat_database WHERE datname = current_database()")
        )
        await s.rollback()
    return int(value or 0)


async def _waiting_on(observer, table: str) -> int | None:
    pid = await observer.scalar(
        text(
            "SELECT l.pid FROM pg_locks l WHERE l.locktype = 'relation' AND NOT l.granted "
            "AND l.relation = to_regclass(:t) LIMIT 1"
        ),
        {"t": table},
    )
    return None if pid is None else int(pid)


async def _blocked_by(observer, waiter: int, holder: int) -> bool:
    return bool(
        await observer.scalar(
            text("SELECT :holder = ANY(pg_blocking_pids(:waiter))"), {"holder": holder, "waiter": waiter}
        )
    )


async def _poll(check, timeout: float = 15.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        value = await check()
        if value:
            return value
        await asyncio.sleep(0.005)
    return None


@pytest.mark.asyncio
@pytest.mark.parametrize("site", SITES)
@target_xfail("019 stage 5 (T1909 step 3)", "a DB conflict outside the four converted sites ends the clearing as E010")
async def test_a_deadlock_anywhere_in_the_attempt_is_retried_by_the_owner(site, stand, monkeypatch) -> None:
    seed = await _seed_interlock_case()
    a_id, b_id, c_id = seed["participant_ids"]
    d_ab = seed["debt_ids"][0]
    _instrument(monkeypatch, site)
    retried = _record_retried_codes(monkeypatch)
    table = _TABLE[site]
    deadlocks_before = await _deadlocks(stand)

    blocker = stand()
    observer_engine = create_async_engine(stand.kw["bind"].url, pool_size=1, max_overflow=0)
    observer = await observer_engine.connect()
    await observer.execution_options(isolation_level="AUTOCOMMIT")
    clearing_task = blocker_task = None
    outcome: object = None
    try:
        blocker_pid = int(await blocker.scalar(text("SELECT pg_backend_pid()")))
        # WHO IS THE VICTIM. PostgreSQL checks for a deadlock once per lock wait, `deadlock_timeout` after
        # the wait began, in the waiting backend, and aborts the backend that finds it. The clearing waits
        # first (default 1 s); the blocker's check is pushed to 30 s so that it never detects first - the
        # stand needs the CLEARING to be the victim. (Needs a superuser, as the test role is here and in CI.)
        await blocker.execute(text("SET deadlock_timeout = '30s'"))
        await blocker.execute(text(f"LOCK TABLE {table} IN ACCESS EXCLUSIVE MODE"))

        async def clear():
            async with stand() as session:
                try:
                    return await ClearingService(session).execute_clearing_with_amount(seed["cycle"])
                except Exception as exc:  # noqa: BLE001 - compared below
                    return exc

        clearing_task = asyncio.create_task(clear())
        clearing_pid = await _poll(lambda: _waiting_on(observer, table))
        assert clearing_pid is not None, f"the clearing never waited on {table}"

        async def take_the_clearing_row():
            await blocker.execute(select(Debt.id).where(Debt.id == d_ab).with_for_update())

        # Immediately: the clearing's one deadlock check runs 1 s after it began to wait.
        blocker_task = asyncio.create_task(take_the_clearing_row())
        assert await _blocked_by(observer, clearing_pid, blocker_pid), "the clearing waits, but not on the blocker"
        blocker_waited = await _poll(lambda: _blocked_by(observer, blocker_pid, clearing_pid), timeout=5.0)
        # The deadlock resolves in the clearing's backend; the blocker then gets the row.
        await asyncio.wait_for(blocker_task, timeout=30)
        await blocker.rollback()
        outcome = await asyncio.wait_for(clearing_task, timeout=60)
    finally:
        try:
            await blocker.rollback()
        finally:
            await blocker.close()
            await observer.close()
            await observer_engine.dispose()
        for task in (clearing_task, blocker_task):
            if task is not None and not task.done():
                task.cancel()
        PaymentRouter.invalidate_cache(seed["equivalent_code"])

    # Controls: the blocker waited on the clearing, and PostgreSQL detected a deadlock (its own counter; the
    # statistics are flushed asynchronously, hence the poll).
    assert blocker_waited, "the blocker never queued on the clearing's row: no deadlock was formed"

    async def counted() -> bool:
        return await _deadlocks(stand) > deadlocks_before

    assert await _poll(counted, timeout=10.0), f"PostgreSQL detected no deadlock at {site}"

    async with stand() as s:
        debts = {
            (d.debtor_id, d.creditor_id): Decimal(str(d.amount))
            for d in (await s.scalars(select(Debt).where(Debt.equivalent_id == seed["equivalent_id"]))).all()
        }
        clearings = (
            await s.execute(
                select(Transaction.tx_id, Transaction.state).where(
                    Transaction.type == "CLEARING", Transaction.initiator_id.in_(seed["participant_ids"])
                )
            )
        ).all()
        tx_ids = [row.tx_id for row in clearings]
        envelopes = (
            await s.execute(select(debt_operations.c.state).where(debt_operations.c.tx_id.in_(tx_ids)))
        ).scalars().all()
        audits = int(
            await s.scalar(
                select(func.count()).select_from(IntegrityAuditLog).where(IntegrityAuditLog.tx_id.in_(tx_ids))
            )
        )

    require_target(
        ["40P01"] in retried,
        f"the {site} deadlock never reached the retry owner with its SQLSTATE (retried: {retried}; "
        f"outcome {outcome!r})",
    )
    require_target(
        outcome == Decimal("30.00000000"),
        f"the {site} conflict ended the clearing as {outcome!r} instead of a retry that clears 30",
    )
    require_target(
        debts == {(a_id, b_id): Decimal("70.00000000"), (c_id, a_id): Decimal("10.00000000")},
        f"final debts {debts}",
    )
    require_target(
        [row.state for row in clearings] == ["COMMITTED"] and list(envelopes) == ["COMPLETED"] and audits == 1,
        f"one committed occurrence with its envelope and audit row expected: {clearings}, {envelopes}, {audits}",
    )
