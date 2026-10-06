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

THE SCHEDULE - a REAL deadlock, detected by PostgreSQL, at the named call site, with the clearing as its victim
BY CONSTRUCTION (031 `T3191` finding 4, `tests/p019_support.queue_behind_the_victim`). At the call site, before
the clearing's own statement, a blocker session takes `ACCESS EXCLUSIVE` on the table that the attempt reads for
the first time there, then asks for a cycle debt row the clearing holds `FOR UPDATE` and is CONFIRMED waiting on
the clearing's backend. Only then does the clearing read the table: its wait closes the cycle, its deadlock check
(short, set for its transaction) finds the cycle closed, and PostgreSQL aborts the clearing's statement with
`40P01`; the blocker's check is set far beyond the stand. The blocker then gets its row and rolls back, freeing
the table. Before, the clearing began to wait first under an ordinary timer and the blocker closed the cycle
afterwards - if the clearing's one check ran before the blocker's request, the victim was left to timing. The
metadata site is reached naturally (`participants` is first read there). The policy site too
(`trust_lines`). The net-positions site (and the checkpoint site, until 024 `T2413.2` removed the checkpoint
from the clearing) reads only tables the attempt already holds (`debts`), so there the call site is INSTRUMENTED: its function first reads a test-only
table `p019_t1909_barrier` - the statement is the test's, the deadlock and its SQLSTATE are PostgreSQL's
own (no error is injected). Every site is instrumented only on its first call, so the retry runs clean.

CONTROLS before the target: the blocker was confirmed waiting on the clearing's row before the clearing asked
for the table, the server's DETAIL names the clearing's backend as the one that detected the cycle against the
blocker, and PostgreSQL's own `pg_stat_database.deadlocks` counted the deadlock (independent of
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

from app.core.clearing.service import ClearingService
from app.core.invariants import InvariantChecker
from app.core.payments.router import PaymentRouter
from app.db.journal_tables import debt_operations
from app.db.models.audit_log import IntegrityAuditLog
from app.db.models.debt import Debt
from app.db.models.transaction import Transaction
from tests.integration.p019_interlock_support import _seed_interlock_case
from tests.p019_support import QueuedCompetitor, assert_victim_of, queue_behind_the_victim, require_target

# MODE B: every commit lands in a clone dropped after the test (`tests/tier_on_a_clone.py`).
from tests.tier_on_a_clone import tier_sessions_on_a_clone  # noqa: E402,F401 - autouse fixture
from tests.debt_setup import transactions_of

BARRIER = "p019_t1909_barrier"
#: The checkpoint site left with the checkpoint (024 `T2413.2`: the clearing computes none in its transaction).
# 027: `trust_lines` is now first read by the line lock; 028 `F-028-28`: `participants` by the participant lock, the
# attempt's FIRST lock - neither table is first read after the cycle rows any more, so neither site can deadlock there.
SITES = ["net_positions"]


@pytest_asyncio.fixture
async def stand(committed_database):
    engine = create_async_engine(
        committed_database.url, pool_size=6, max_overflow=0, pool_timeout=20, isolation_level="READ COMMITTED"
    )
    try:
        factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False)
        async with factory() as s:
            await s.execute(text(f"CREATE TABLE IF NOT EXISTS {BARRIER} (id int)"))
            await s.commit()
        yield factory
    finally:
        await engine.dispose()


class _Schedule:
    """What the instrumented call site did on its one armed call: the queued blocker and the clearing's error."""

    def __init__(self) -> None:
        self.queued: QueuedCompetitor | None = None
        self.victim_error: BaseException | None = None
        self.reached = asyncio.Event()


def _instrument(monkeypatch, site: str, *, blocker, cycle_row, schedule: _Schedule) -> None:
    """For the site that reads only tables the attempt already holds (net positions): read the barrier first, once
    - after the blocker took the barrier and was confirmed waiting on the clearing's `cycle_row`."""

    from sqlalchemy.exc import DBAPIError

    done: list[int] = []

    async def barrier_read(session) -> None:
        if not done:
            done.append(1)
            try:
                schedule.queued = await queue_behind_the_victim(
                    session,
                    blocker,
                    hold=text(f"LOCK TABLE {_TABLE[site]} IN ACCESS EXCLUSIVE MODE"),
                    wait_on_victim=select(Debt.id).where(Debt.id == cycle_row).with_for_update(),
                )
            finally:
                schedule.reached.set()
            try:
                await session.execute(text(f"SELECT count(*) FROM {BARRIER}"))
            except DBAPIError as exc:
                schedule.victim_error = exc
                raise

    if site == "net_positions":
        original = InvariantChecker._calculate_net_position

        async def net_position(self, participant_id, equivalent_id, pairs=None):
            await barrier_read(self.session)
            return await original(self, participant_id, equivalent_id, pairs)

        monkeypatch.setattr(InvariantChecker, "_calculate_net_position", net_position)


_TABLE = {"policy": "trust_lines", "metadata": "participants", "net_positions": BARRIER}


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
async def test_a_deadlock_anywhere_in_the_attempt_is_retried_by_the_owner(site, stand, monkeypatch) -> None:
    seed = await _seed_interlock_case()
    a_id, b_id, c_id = seed["participant_ids"]
    d_ab = seed["debt_ids"][0]
    blocker = stand()
    schedule = _Schedule()
    _instrument(monkeypatch, site, blocker=blocker, cycle_row=d_ab, schedule=schedule)
    retried = _record_retried_codes(monkeypatch)
    deadlocks_before = await _deadlocks(stand)

    clearing_task = None
    outcome: object = None
    try:

        async def clear():
            async with stand() as session:
                try:
                    return await ClearingService(session).execute_occurrence(seed["occurrence"])
                except Exception as exc:  # noqa: BLE001 - compared below
                    return exc

        clearing_task = asyncio.create_task(clear())
        reached = asyncio.create_task(schedule.reached.wait())
        await asyncio.wait({reached, clearing_task}, timeout=30, return_when=asyncio.FIRST_COMPLETED)
        reached.cancel()
        assert schedule.queued is not None, (
            f"the {site} call site was not reached with the blocker queued on the clearing: {clearing_task!r}"
        )
        # The clearing's failure ended its attempt, so the blocker gets the row; its rollback frees the table and the
        # row for the retry.
        await asyncio.wait_for(schedule.queued.waiting, timeout=30)
        await blocker.rollback()
        outcome = await asyncio.wait_for(clearing_task, timeout=60)
    finally:
        try:
            await blocker.rollback()
        finally:
            await blocker.close()
        if clearing_task is not None and not clearing_task.done():
            clearing_task.cancel()
        PaymentRouter.invalidate_cache(seed["equivalent_code"])

    # Controls: the clearing's backend detected the cycle against the blocker (the server's DETAIL), and PostgreSQL
    # counted a deadlock (its own counter; the statistics are flushed asynchronously, hence the poll).
    assert schedule.victim_error is not None, f"the clearing's read at {site} did not fail: outcome {outcome!r}"
    assert_victim_of(schedule.victim_error, schedule.queued)

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
                    Transaction.type == "CLEARING", transactions_of(seed["participant_ids"])
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
