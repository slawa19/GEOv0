"""Mode A keeps a test's writes inside the test even when the application commits.

Programme 017, stage 2b. The mode-A `db_session` fixture used to carry the SQLAlchemy 1.4
"join an external transaction" recipe - an explicit begin_nested() plus an after_transaction_end
listener that reopened a SAVEPOINT whenever one ended. On PostgreSQL that listener broke the
payment engine, which opens savepoints of its own (class FIXA of the stage 2a catalogue: 62 tests
in 24 files failed with "Can't operate on closed transaction inside context manager"). The recipe
was removed; the sessionmaker's join_transaction_mode="create_savepoint" is SQLAlchemy 2.0's
built-in form of the same thing.

Removing a mechanism whose whole purpose was isolation is only safe if isolation survives, and a
green tier cannot show that by itself: tests that leaked into one another would still pass. This
module is the measurement that it does. It exists for the reason AGENTS.md section 15 gives for
every stand that proves an absence - the stand must be able to see the outcome it rules out - so
the counter-check below first proves the probe CAN see a committed row, then that the fixture's
committed row stays invisible.

What this guard does NOT see: a leak through a second engine, a background task, or a session the
test opens itself - those bypass the fixture and are mode B's business. It checks the fixture,
not every path a test can take to the database.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text


async def _visible_from_a_fresh_connection(code: str) -> bool:
    """Whether a row with this code is visible to a connection that shares nothing with the test."""

    from tests.conftest import engine as test_engine

    async with test_engine.connect() as fresh:
        found = await fresh.scalar(
            text("SELECT 1 FROM equivalents WHERE code = :code"), {"code": code}
        )
    return found is not None


async def test_the_probe_can_see_a_row_that_really_was_committed():
    """Counter-check first: without it the next test could pass because the probe sees nothing."""

    from tests.conftest import engine as test_engine

    code = "Q" + uuid.uuid4().hex[:5].upper()
    async with test_engine.connect() as writer:
        await writer.execute(
            text(
                "INSERT INTO equivalents (id, code, precision, is_active) "
                "VALUES (:id, :code, 2, true)"
            ),
            {"id": uuid.uuid4(), "code": code},
        )
        await writer.commit()
    try:
        assert await _visible_from_a_fresh_connection(code), (
            "the probe could not see a row committed on its own connection, so a green verdict "
            "below would prove nothing"
        )
    finally:
        async with test_engine.connect() as cleaner:
            await cleaner.execute(text("DELETE FROM equivalents WHERE code = :code"), {"code": code})
            await cleaner.commit()


async def test_an_application_commit_inside_mode_a_stays_inside_the_test(db_session):
    """The fixture's session commits the way application code does; nothing may escape it."""

    from app.db.models.equivalent import Equivalent

    code = "Q" + uuid.uuid4().hex[:5].upper()
    db_session.add(Equivalent(code=code, precision=2, is_active=True))
    await db_session.commit()

    # Visible to the test's own session: the commit really happened as far as the test can tell.
    assert (
        await db_session.scalar(text("SELECT 1 FROM equivalents WHERE code = :code"), {"code": code})
    ) is not None

    # Invisible to everyone else: it is a SAVEPOINT release inside an outer transaction that the
    # fixture rolls back. If the listener's removal had let commit() reach the outer transaction,
    # the fresh connection would see this row and the tier would start leaking between tests.
    assert not await _visible_from_a_fresh_connection(code), (
        "an application commit inside the mode-A fixture reached the outer transaction: the "
        "fixture no longer isolates tests"
    )
