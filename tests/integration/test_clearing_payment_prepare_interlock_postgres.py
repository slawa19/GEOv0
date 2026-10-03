"""PostgreSQL schedules for the shared clearing/payment boundary.

027 STAGE 2 (`T2704`, 2026-10-03): the clearing's exclusive lock, its pinned connection and its interlock are
REMOVED, and with them every schedule here that pinned them (the reverse payment waiting on the exclusive lock,
the refusal of a connection-bound session, cancellation at checkout, inside the interlocked work, during the
preflight, during the release, and the interlock timeout). What remains is the helper check and the clearing
completing on a one-connection pool. A payment and a clearing over one edge now queue on its line rows:
`test_p027_t2703_stage2_counterexamples_postgres.py::test_clearing_and_payment_on_a_shared_edge`. The text below
is the 019 history.

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
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from tests.integration.p019_interlock_support import (
    _no_advisory_lock_is_held,
    _seed_interlock_case,
)
from tests.p023_support import TEST_PLAN_ID, occurrence_of

# A test that seeds (`_seed_interlock_case`) commits through several sessions and runs on a disposable
# clone of the migrated template: `@pytest.mark.usefixtures("tier_on_a_clone")`, and its rows go with
# the clone's drop (018 B0b; see `tests/tier_on_a_clone.py`). Its own one-connection engine is built
# over `committed_database.url`. Tests that commit nothing stay on the tier and pay for no clone.
from tests.tier_on_a_clone import tier_on_a_clone  # noqa: E402,F401 - opt-in fixture



def _occurrence_of_absent_debts():
    """025 `T2508.1`: an occurrence whose three debts and equivalent do not exist - for the boundary checks that
    refuse or block before any cycle row is read (the external bind, the preflight SELECT)."""

    return occurrence_of(
        [uuid.uuid4() for _ in range(3)], equivalent_id=uuid.uuid4(), amount="1", plan_id=TEST_PLAN_ID, ordinal=0
    )


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
        isolation_level="READ COMMITTED",
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
            ClearingService(clearing_session).execute_occurrence(
                seed["occurrence"]
            ),
            timeout=3.0,
        )
        assert amount == Decimal("30.00000000")
        assert not clearing_session.in_transaction()
    finally:
        await clearing_session.rollback()
        await clearing_session.close()
        await one_connection_engine.dispose()
