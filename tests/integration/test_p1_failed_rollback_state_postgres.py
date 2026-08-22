"""RT-010-1: what a session really looks like after `rollback()` itself fails.

Program 010, finding `F-010-1`.

`app/core/payments/engine.py:537-541` calls `await self.session.rollback()` inside the retry
loop and swallows its failure, after which the loop sleeps and replays the whole unit of
work.  The finding says the replay then runs "on a session in an unknown state".

That claim is the whole finding, and it is a claim about SQLAlchemy, not about this
codebase.  Two independent readings of the source reached opposite conclusions - one that
the state is restored and the replay is harmless, one that the identity map survives with
the previous attempt's mutation still applied, which would double a money movement.  A
finding cannot be closed or refuted on a disagreement between readings, so this measures it.

The failure is real, not patched: a second, independent connection terminates the backend
between the moment the session is in a transaction and the moment `rollback()` is called, so
the ROLLBACK really goes to a socket that is gone.  Only the timing is arranged.

Why PostgreSQL: on SQLite there is no backend to terminate, and the retry loop this belongs
to only treats PostgreSQL SQLSTATEs as retryable (`app/core/payments/engine.py:392-419`).
"""

from __future__ import annotations

import asyncio
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.models.equivalent import Equivalent

pytestmark = pytest.mark.postgres


def _url() -> str:
    url = os.environ.get("TEST_DATABASE_URL", "")
    if "postgresql" not in url:
        pytest.skip("a backend has to exist before it can be terminated")
    return url


@pytest_asyncio.fixture
async def sessions():
    engine = create_async_engine(_url())
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


def _terminate(url: str, pid: int) -> None:
    """Kill `pid` from a genuinely separate connection, synchronously."""

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
async def test_a_failed_rollback_still_leaves_no_stale_value_behind(sessions) -> None:
    url = _url()
    code = ("F" + uuid.uuid4().hex[:8]).upper()

    async with sessions() as setup:
        setup.add(Equivalent(code=code, precision=2, is_active=True, symbol="F"))
        await setup.commit()

    try:
        async with sessions() as session:
            row = (
                await session.execute(select(Equivalent).where(Equivalent.code == code))
            ).scalar_one()
            pid = int((await session.execute(text("SELECT pg_backend_pid()"))).scalar_one())

            # Mutate in the session, as `_apply_flow` does to a Debt before a retry.
            row.precision = 9
            await session.flush()

            _terminate(url, pid)

            with pytest.raises(Exception) as excinfo:
                await session.rollback()
            assert "connection" in str(excinfo.value).lower(), str(excinfo.value)

            # THE MEASUREMENT. `_restore_snapshot` runs in a `finally`, so it executes even
            # when the ROLLBACK itself failed, and for a non-nested transaction it expires
            # every state in the identity map. So the next read must come from the database
            # rather than from the object the previous attempt mutated.
            refreshed = (
                await session.execute(select(Equivalent).where(Equivalent.code == code))
            ).scalar_one()
            assert refreshed.precision == 2, (
                "the retry would have seen the previous attempt's mutation as if it were "
                f"committed state: precision={refreshed.precision}. If this fails, F-010-1 "
                "is real and its consequence is a doubled delta on replay"
            )
    finally:
        async with sessions() as cleanup:
            await cleanup.execute(delete(Equivalent).where(Equivalent.code == code))
            await cleanup.commit()


@pytest.mark.asyncio
async def test_the_session_is_usable_again_after_a_failed_rollback(sessions) -> None:
    """The other half of the claim: is the session left unusable, so a replay would break?"""

    url = _url()
    code = ("G" + uuid.uuid4().hex[:8]).upper()

    try:
        async with sessions() as session:
            pid = int((await session.execute(text("SELECT pg_backend_pid()"))).scalar_one())
            _terminate(url, pid)
            with pytest.raises(Exception):
                await session.rollback()

            # A replay would do exactly this: run its statements again on the same session.
            session.add(Equivalent(code=code, precision=2, is_active=True, symbol="G"))
            await session.commit()

        async with sessions() as observer:
            found = (
                await observer.execute(select(Equivalent).where(Equivalent.code == code))
            ).scalar_one_or_none()
        assert found is not None and found.precision == 2, (
            "the replay after a swallowed rollback failure could not complete, which would "
            "make the swallow harmful rather than merely untidy"
        )
    finally:
        async with sessions() as cleanup:
            await cleanup.execute(delete(Equivalent).where(Equivalent.code == code))
            await cleanup.commit()
