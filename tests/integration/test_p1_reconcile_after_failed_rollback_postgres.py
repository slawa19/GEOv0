"""RT-010-3: the reconciliation read still gets a fresh snapshot after a failed rollback.

Program 010, finding `F-010-2`.

`_reconcile_committed_execution` (`app/core/clearing/service.py:249`) exists to answer one
question from a FRESH snapshot: did this clearing already commit?  It opens that snapshot by
rolling back first (`:256-259`), and it swallows the failure of that rollback - logging it
and carrying on to read anyway.

Whether that matters depends entirely on what the following read then does, and two
independent readings disagreed about it.  One held that the recovery sessionmaker is built on
the SAME bind and joins the same transaction, so a failed rollback leaves it reading a stale
snapshot and answering "no committed occurrence" about a clearing that is durably committed -
the same class as `F-009-6`, a durable mutation reported as a failure.  The other held that a
failed rollback leaves the transaction aborted, so all three attempts raise, `last_error` is
re-raised at `:288-289`, and the path is fail-closed.

The difference is a P2 finding versus nothing, so it was measured rather than argued.  The
rollback is made to fail for real - a second, independent connection terminates the backend -
and the recovery read is then performed the way the resolver performs it: a new session on
the same bind.

**Neither reading was right.**  The read does not raise, and it does not answer from a stale
snapshot: it returns the durably committed row.  The reason is a third one, that a failed
rollback invalidates the connection, and the next use of that `Connection` transparently
opens a new DBAPI connection - so the fresh snapshot the docstring promises is delivered
anyway, by a mechanism the resolver does not mention.

This test is therefore not a reproducer but a regression guard on a verdict: it pins the
behaviour the REFUTED verdict of `F-010-2` rests on, so that a future change to pooling,
bind handling or `join_transaction_mode` cannot quietly turn a correct answer into a stale
one without failing something.

**The isolation level is SERIALIZABLE on purpose, and the first version of this test got it
wrong.**  A stale snapshot is only possible when the snapshot belongs to the transaction
rather than to the statement, i.e. under REPEATABLE READ or SERIALIZABLE.  Built on a
default engine the test ran under READ COMMITTED, where every statement takes a new
snapshot - so the assertion below would have held even if the mechanism the verdict rests
on did not exist, which is the shape of an assertion satisfied by the wrong outcome.
Production runs SERIALIZABLE on every connection (`app/config.py:68`,
`app/db/session.py:62`), so that is what this measures.

The connection identity is asserted too: without it, "the answer was fresh" cannot be told
apart from "the connection was silently replaced", and the replacement IS the mechanism the
verdict names.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models.equivalent import Equivalent

pytestmark = pytest.mark.postgres


def _url() -> str:
    url = os.environ.get("TEST_DATABASE_URL", "")
    if "postgresql" not in url:
        pytest.skip("a backend has to exist before it can be terminated")
    return url


@pytest_asyncio.fixture
async def engine():
    # SERIALIZABLE, as production runs it: under READ COMMITTED a stale snapshot cannot
    # occur at all, and the measurement below would be vacuous.
    eng = create_async_engine(_url(), isolation_level="SERIALIZABLE")
    try:
        yield eng
    finally:
        await eng.dispose()


def _terminate(url: str, pid: int) -> None:
    async def _kill() -> None:
        import asyncpg

        conn = await asyncpg.connect(url.replace("postgresql+asyncpg://", "postgresql://"))
        try:
            await conn.execute("SELECT pg_terminate_backend($1)", pid)
        finally:
            await conn.close()

    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(lambda: asyncio.run(_kill())).result(timeout=30)


@pytest.mark.asyncio
async def test_the_recovery_read_still_sees_durable_state_after_a_failed_rollback(
    engine,
) -> None:
    url = _url()
    durable_code = ("H" + uuid.uuid4().hex[:8]).upper()

    # Something durably committed by somebody else, which a stale snapshot would not see.
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as writer:
        writer.add(Equivalent(code=durable_code, precision=2, is_active=True, symbol="H"))
        await writer.commit()

    try:
        # The shape the resolver runs in on PostgreSQL: a session pinned to a connection the
        # service owns (`app/core/clearing/service.py:1332-1336`).
        async with engine.connect() as connection:
            session = AsyncSession(bind=connection, expire_on_commit=False)
            pid = int((await session.execute(text("SELECT pg_backend_pid()"))).scalar_one())

            _terminate(url, pid)

            rollback_failed = False
            try:
                await session.rollback()
            except Exception:
                rollback_failed = True
            assert rollback_failed, (
                "the rollback did not fail, so this run did not exercise the subject"
            )

            # Exactly what `_reconcile_committed_execution` does next: a new session on the
            # same bind, then the read.
            recovery_maker = async_sessionmaker(
                bind=connection, class_=AsyncSession, expire_on_commit=False, autoflush=False
            )
            answer = None
            recovery_pid = None
            raised = None
            try:
                async with recovery_maker() as recovery:
                    recovery_pid = int(
                        (await recovery.execute(text("SELECT pg_backend_pid()"))).scalar_one()
                    )
                    answer = (
                        await recovery.execute(
                            select(Equivalent).where(Equivalent.code == durable_code)
                        )
                    ).scalar_one_or_none()
            except Exception as exc:
                raised = exc

        assert raised is None, (
            f"the recovery read failed outright: {raised!r}. That is fail-closed rather "
            "than wrong, but it would still mean the resolver cannot do its job after a "
            "failed rollback"
        )
        assert answer is not None, (
            "the recovery read answered from a stale snapshot: it did not see a row that "
            "is durably committed. Applied to a clearing, that means reporting 'no "
            "committed occurrence' about one that DID commit - the same class as F-009-6, "
            "and F-010-2 would be real"
        )
        assert recovery_pid is not None and recovery_pid != pid, (
            "the recovery read ran on the SAME backend as the failed rollback "
            f"({recovery_pid}), so the freshness above is not explained by the mechanism "
            "the verdict names - the connection being invalidated and transparently "
            "replaced. Without that, the verdict rests on something unmeasured"
        )
    finally:
        async with maker() as cleanup:
            await cleanup.execute(delete(Equivalent).where(Equivalent.code == durable_code))
            await cleanup.commit()
