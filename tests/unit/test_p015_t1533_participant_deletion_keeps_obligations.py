"""T1533, the MODEL half: deleting a participant must never delete obligations.

The full account is in the PostgreSQL half,
`tests/integration/test_p015_t1533_participant_deletion_keeps_obligations_postgres.py`. In short:
`debts.debtor_id` and `debts.creditor_id` were `ondelete='CASCADE'`, so deleting a participant
removed every obligation they owed or were owed inside the database, with no `Debt` row ever loaded
- no grant, no `_RowState`, no journal entry, nothing for the journal or anything else in the
application to observe. It is the participant half of what T1524 closed for the equivalent.

WHAT THIS HALF PROVES THAT THE OTHER DOES NOT. This half builds its schema from `Base.metadata`
(`create_all`, on a scratch database of its own), so it is the MODEL that is under test here; the
PostgreSQL half runs on a clone of the migrated template and tests the MIGRATION. The two artefacts
are separate and T1540 records that they have measurably diverged before, so one is not evidence for
the other. (Until 017 stage 3 this half ran on SQLite, whose foreign keys the test engine switched on
only on 2026-09-11.)

THE DEBT IS SEEDED WITHOUT JOURNAL HISTORY, deliberately - `debt_journal_entries` already RESTRICTs
both participant columns (`C17`), so a journalled debt would make every refusal below attributable
to that constraint and the test would be green under CASCADE. That is the state of every debt written
before migration 022 and of every debt whose history has been disposed of.

SINCE 018 STAGE B1 ONLY THE NAMED CORRUPTION HELPER CAN PRODUCE THAT STATE (spec 018 `FORK-4`, a named
use; `tests/ledger_corruption.py`). The database now refuses a write to `debts` with no operation named
in the transaction (`GE001`), and a debt written inside an operation has history. So the row goes in
on the helper's own connection with the journal's triggers off, in its own committed transaction;
EVERYTHING THE TESTS THEN DO runs on an ordinary connection with the triggers and the foreign keys ON
- asserted, not assumed - so the refusal measured is the one a real deletion would meet.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.db.base import Base
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from tests.ledger_corruption import corrupt

#: The amount the PostgreSQL reproducer measured disappearing.
AMOUNT = Decimal("925.31000000")


@pytest.fixture(scope="module")
def model_url():
    """A scratch database next to the tier, built by `Base.metadata.create_all`, for this module.

    Built and dropped on a private event loop (`tests.conftest._run_in_fresh_thread`): a module
    fixture outlives every per-test loop. Its name carries the tier's `__` separator, so it is a
    database the corruption helper accepts as disposable.
    """

    from tests.conftest import TEST_DATABASE_URL, _run_in_fresh_thread
    from tests.migrated_schema import (
        assert_may_create_databases,
        create_database,
        drop_database,
        maintenance_connection,
        scratch_database_url,
    )

    url, name = scratch_database_url(TEST_DATABASE_URL, "p018t1533model")

    async def _create() -> None:
        connection = await maintenance_connection(TEST_DATABASE_URL)
        try:
            await assert_may_create_databases(connection)
            await drop_database(connection, name)
            await create_database(connection, name)
        finally:
            await connection.close()
        engine = create_async_engine(url, poolclass=NullPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
        finally:
            await engine.dispose()

    async def _drop() -> None:
        connection = await maintenance_connection(TEST_DATABASE_URL)
        try:
            await drop_database(connection, name)
        finally:
            await connection.close()

    _run_in_fresh_thread(_create)
    try:
        yield url
    finally:
        _run_in_fresh_thread(_drop)


async def _seed(url: str, *, with_debt: bool):
    """An equivalent and two participants, committed; with `with_debt`, ONE DEBT WITHOUT HISTORY."""

    nonce = uuid.uuid4().hex[:8]
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with AsyncSession(bind=engine, expire_on_commit=False) as session:
            eq = Equivalent(
                code=("Q" + nonce).upper()[:16], description="T1533", precision=2, is_active=True
            )
            debtor = Participant(pid="qd" + nonce, display_name="D", public_key="pkqd-" + nonce)
            creditor = Participant(pid="qc" + nonce, display_name="C", public_key="pkqc-" + nonce)
            session.add_all([eq, debtor, creditor])
            await session.commit()
    finally:
        await engine.dispose()
    if with_debt:
        await corrupt(
            url,
            [
                "INSERT INTO debts (id, debtor_id, creditor_id, equivalent_id, amount, version) "
                f"VALUES ('{uuid.uuid4()}', '{uuid.UUID(str(debtor.id))}', "
                f"'{uuid.UUID(str(creditor.id))}', '{uuid.UUID(str(eq.id))}', {AMOUNT}, 0)"
            ],
        )
    return eq.id, debtor.id, creditor.id


async def _session(url: str):
    """An ordinary session - triggers and foreign keys ON, which is asserted on its connection."""

    engine = create_async_engine(url, poolclass=NullPool)
    session = AsyncSession(bind=engine, expire_on_commit=False)
    role = (await session.execute(text("SHOW session_replication_role"))).scalar_one()
    assert role == "origin", f"stand: the deleting connection runs with triggers {role!r}"
    return engine, session


async def _debt_sum(session, eq_id) -> Decimal:
    amounts = (
        await session.execute(select(Debt.amount).where(Debt.equivalent_id == eq_id))
    ).scalars().all()
    return sum(amounts, Decimal("0"))


async def _refused_deletion(url: str, participant_id, eq_id) -> tuple[Decimal, Decimal]:
    engine, session = await _session(url)
    try:
        before = await _debt_sum(session, eq_id)
        await session.rollback()
        participant = await session.get(Participant, participant_id)
        await session.delete(participant)
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()
        after = await _debt_sum(session, eq_id)
        return before, after
    finally:
        await session.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_the_database_refuses_to_delete_a_debtor_who_still_owes(model_url) -> None:
    """RED before T1533: the cascade removes the obligation and nothing refuses."""
    eq_id, debtor_id, _creditor_id = await _seed(model_url, with_debt=True)

    before, after = await _refused_deletion(model_url, debtor_id, eq_id)
    assert before == AMOUNT, f"stand: the unjournalled debt was not seeded, sum is {before}"
    assert after == before, (
        f"deleting the debtor destroyed what they owed: {before - after} cascaded away inside the "
        f"database, with no Debt row ever loaded"
    )


@pytest.mark.asyncio
async def test_the_database_refuses_to_delete_a_creditor_who_is_still_owed(model_url) -> None:
    """The second constraint, not a copy of the first: closing one column leaves half the hole."""
    eq_id, _debtor_id, creditor_id = await _seed(model_url, with_debt=True)

    before, after = await _refused_deletion(model_url, creditor_id, eq_id)
    assert before == AMOUNT, f"stand: the unjournalled debt was not seeded, sum is {before}"
    assert after == before, (
        f"deleting the creditor destroyed what they were owed: {before - after} cascaded away"
    )


@pytest.mark.asyncio
async def test_no_journal_history_names_these_participants(model_url) -> None:
    """ANTI-VACUITY. Without it the two tests above could be passing on `C17` instead of on T1533.

    It attributes by construction: if no journal row names either participant, the only constraint
    that can refuse is `debts`'. (The PostgreSQL half also names the refusing constraint.)
    """
    eq_id, debtor_id, creditor_id = await _seed(model_url, with_debt=True)

    engine, session = await _session(model_url)
    try:
        named = (
            await session.execute(
                text(
                    "SELECT count(*) FROM debt_journal_entries "
                    "WHERE debtor_id IN (:d, :c) OR creditor_id IN (:d, :c)"
                ),
                {"d": debtor_id, "c": creditor_id},
            )
        ).scalar_one()
        total = await _debt_sum(session, eq_id)
    finally:
        await session.close()
        await engine.dispose()

    assert named == 0, (
        f"{named} journal entries name these participants, so the refusals asserted above are "
        f"C17's RESTRICT and say nothing about debts' own foreign keys"
    )
    assert total == AMOUNT, "stand: the debt itself is missing"


@pytest.mark.asyncio
async def test_a_participant_who_owes_nothing_still_deletes(model_url) -> None:
    """Control. RESTRICT must not turn every participant deletion into a refusal.

    A policy that refused everything would satisfy the tests above and break every teardown in this
    suite, which is the same class of defect as the hole itself.
    """
    _eq_id, debtor_id, _creditor_id = await _seed(model_url, with_debt=False)

    engine, session = await _session(model_url)
    try:
        await session.rollback()
        await session.delete(await session.get(Participant, debtor_id))
        await session.flush()
        await session.commit()
        survived = (
            await session.execute(select(Participant.id).where(Participant.id == debtor_id))
        ).scalar_one_or_none()
    finally:
        await session.close()
        await engine.dispose()
    assert survived is None, "a participant with no obligations was not deleted"
