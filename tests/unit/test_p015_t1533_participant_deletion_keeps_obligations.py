"""T1533, the SQLite half: deleting a participant must never delete obligations.

The full account is in the PostgreSQL half,
`tests/integration/test_p015_t1533_participant_deletion_keeps_obligations_postgres.py`. In short:
`debts.debtor_id` and `debts.creditor_id` were `ondelete='CASCADE'`, so deleting a participant
removed every obligation they owed or were owed inside the database, with no `Debt` row ever loaded
- no grant, no `_RowState`, no journal entry, nothing for the journal or anything else in the
application to observe. It is the participant half of what T1524 closed for the equivalent.

WHAT THIS HALF PROVES THAT THE OTHER DOES NOT. This tier builds its schema from `Base.metadata`, so
it is the MODEL that is under test here; the PostgreSQL half runs with
`GEO_TEST_USE_MIGRATED_SCHEMA=1` and tests the MIGRATION. The two artefacts are separate and T1540
records that they have measurably diverged before, so one is not evidence for the other.

AND IT CAN EXIST AT ALL ONLY BECAUSE OF 2026-09-11: until then the SQLite test engine did not set
`PRAGMA foreign_keys=ON`, so every assertion below would have passed under CASCADE, RESTRICT or no
constraint whatsoever.

THE DEBT IS SEEDED WITHOUT JOURNAL HISTORY, deliberately - `debt_journal_entries` already RESTRICTs
both participant columns (`C17`), so a journalled debt would make every refusal below attributable
to that constraint and the test would be green under CASCADE. `exec_driver_sql` dispatches no
`before_execute`, so it reaches `debts` past the journal's write guard and leaves no envelope: the
state of every debt written before migration 022 and of every debt whose history has been disposed
of.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant

#: The amount the PostgreSQL reproducer measured disappearing.
AMOUNT = Decimal("925.31000000")


def _uuid_literal(value, dialect: str) -> str:
    """A UUID as a SQL literal of this dialect's storage. `uuid.UUID` first, which is the guard.

    `Uuid(as_uuid=True)` stores a native `uuid` on PostgreSQL; the SQLite arm (32-character hex)
    left with SQLite in 017 stage 3, and `dialect` is kept only so the call sites stay as they were.
    The measured failure behind the rule is recorded in `tests/debt_setup.py::_uuid_literals`.
    """

    parsed = uuid.UUID(str(value))
    return str(parsed)


async def _seed(db_session, *, with_debt: bool):
    nonce = uuid.uuid4().hex[:8]
    eq = Equivalent(
        code=("Q" + nonce).upper()[:16], description="T1533", precision=2, is_active=True
    )
    debtor = Participant(pid="qd" + nonce, display_name="D", public_key="pkqd-" + nonce)
    creditor = Participant(pid="qc" + nonce, display_name="C", public_key="pkqc-" + nonce)
    db_session.add_all([eq, debtor, creditor])
    await db_session.flush()
    if with_debt:
        connection = await db_session.connection()
        dialect = connection.dialect.name
        await connection.exec_driver_sql(
            "INSERT INTO debts (id, debtor_id, creditor_id, equivalent_id, amount, version) "
            f"VALUES ('{_uuid_literal(uuid.uuid4(), dialect)}', "  # noqa: S608 - UUID-parsed above
            f"'{_uuid_literal(debtor.id, dialect)}', "
            f"'{_uuid_literal(creditor.id, dialect)}', "
            f"'{_uuid_literal(eq.id, dialect)}', {AMOUNT}, 0)"
        )
    await db_session.commit()
    return eq, debtor, creditor


async def _debt_sum(db_session, eq_id) -> Decimal:
    amounts = (
        await db_session.execute(select(Debt.amount).where(Debt.equivalent_id == eq_id))
    ).scalars().all()
    return sum(amounts, Decimal("0"))


@pytest.mark.asyncio
async def test_the_database_refuses_to_delete_a_debtor_who_still_owes(db_session) -> None:
    """RED before T1533: the cascade removes the obligation and nothing refuses."""
    eq, debtor, _creditor = await _seed(db_session, with_debt=True)
    eq_id = eq.id

    before = await _debt_sum(db_session, eq_id)
    assert before == AMOUNT, f"stand: the unjournalled debt was not seeded, sum is {before}"

    await db_session.delete(debtor)
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()

    after = await _debt_sum(db_session, eq_id)
    assert after == before, (
        f"deleting the debtor destroyed what they owed: {before - after} cascaded away inside the "
        f"database, with no Debt row ever loaded"
    )


@pytest.mark.asyncio
async def test_the_database_refuses_to_delete_a_creditor_who_is_still_owed(db_session) -> None:
    """The second constraint, not a copy of the first: closing one column leaves half the hole."""
    eq, _debtor, creditor = await _seed(db_session, with_debt=True)
    eq_id = eq.id

    before = await _debt_sum(db_session, eq_id)

    await db_session.delete(creditor)
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()

    after = await _debt_sum(db_session, eq_id)
    assert after == before, (
        f"deleting the creditor destroyed what they were owed: {before - after} cascaded away"
    )


@pytest.mark.asyncio
async def test_no_journal_history_names_these_participants(db_session) -> None:
    """ANTI-VACUITY. Without it the two tests above could be passing on `C17` instead of on T1533.

    SQLite's foreign key errors carry no constraint name, so this tier cannot attribute a refusal by
    reading the message the way the PostgreSQL half does. It attributes by construction instead:
    if no journal row names either participant, the only constraint that can refuse is `debts`'.
    """
    eq, debtor, creditor = await _seed(db_session, with_debt=True)

    connection = await db_session.connection()
    dialect = connection.dialect.name
    debtor_literal = _uuid_literal(debtor.id, dialect)
    creditor_literal = _uuid_literal(creditor.id, dialect)
    named = (
        await connection.exec_driver_sql(
            "SELECT count(*) FROM debt_journal_entries "  # noqa: S608 - UUID-parsed above
            f"WHERE debtor_id IN ('{debtor_literal}', '{creditor_literal}') "
            f"OR creditor_id IN ('{debtor_literal}', '{creditor_literal}')"
        )
    ).scalar_one()

    assert named == 0, (
        f"{named} journal entries name these participants, so the refusals asserted above are "
        f"C17's RESTRICT and say nothing about debts' own foreign keys"
    )
    assert await _debt_sum(db_session, eq.id) == AMOUNT, "stand: the debt itself is missing"


@pytest.mark.asyncio
async def test_a_participant_who_owes_nothing_still_deletes(db_session) -> None:
    """Control. RESTRICT must not turn every participant deletion into a refusal.

    A policy that refused everything would satisfy the tests above and break every teardown in this
    suite, which is the same class of defect as the hole itself.
    """
    _eq, debtor, _creditor = await _seed(db_session, with_debt=False)
    debtor_id = debtor.id

    await db_session.delete(debtor)
    await db_session.flush()
    await db_session.commit()

    survived = (
        await db_session.execute(select(Participant.id).where(Participant.id == debtor_id))
    ).scalar_one_or_none()
    assert survived is None, "a participant with no obligations was not deleted"
