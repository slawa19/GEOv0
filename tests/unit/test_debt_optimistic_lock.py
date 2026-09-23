"""Two sessions cannot overwrite each other's debt amount, and the database refuses the stale writer.

THE DATABASE REFUSES FIRST - measured 2026-09-23 (017 stage 2b), when this module first ran on
PostgreSQL in mode B. The tier engine runs at the application's isolation level (T1549, default
SERIALIZABLE), and at that level the stale writer's UPDATE is refused with SQLSTATE 40001
("could not serialize access ... Canceled on identification as a pivot, during write") before the
ORM compares `version`. `StaleDataError` would be the READ COMMITTED outcome, an isolation level the
application does not use. 40001 is what `PaymentEngine._is_retryable_db_error` retries.

The test asserts the invariant - the committed value survives, the stale writer is refused - and
names the mechanism rather than accepting "some exception". Until 017 stage 3 (slice S3) it also
had a SQLite branch expecting SQLITE_BUSY_SNAPSHOT; that branch left with SQLite.
"""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm.exc import StaleDataError

from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant

from tests.conftest import MODE_B, sessionmaker_of
from tests.debt_setup import debt_fixture_setup


# MODE B (017 stage 2b, T1702). Three sessions besides `db_session` read the seeded debt. In mode A
# on PostgreSQL the seed is never committed - `commit()` releases a SAVEPOINT inside the fixture's
# outer transaction - so another session cannot see it: `NoResultFound` on the first of them
# (stage-2 catalogue, class VIS). Mode B commits for real on a clone; the other sessions reach the
# clone through `sessionmaker_of`, not through `TestingSessionLocal`, which is the tier's database
# there.
@MODE_B
@pytest.mark.asyncio
async def test_a_stale_writer_cannot_overwrite_the_committed_debt_amount(db_session):
    nonce = uuid.uuid4().hex[:10]
    eq = Equivalent(
        code=("OL" + nonce[:14]).upper(),
        symbol="OL",
        description=None,
        precision=2,
        metadata_={},
        is_active=True,
    )
    a = Participant(
        pid="A" + nonce,
        display_name="A",
        public_key="pkA-" + nonce,
        type="person",
        status="active",
        profile={},
    )
    b = Participant(
        pid="B" + nonce,
        display_name="B",
        public_key="pkB-" + nonce,
        type="person",
        status="active",
        profile={},
    )
    db_session.add_all([eq, a, b])
    await db_session.flush()

    async with debt_fixture_setup(db_session, label="setup"):
        d = Debt(debtor_id=a.id, creditor_id=b.id, equivalent_id=eq.id, amount=Decimal("100"))
        db_session.add(d)
    await db_session.commit()

    # Two separate sessions, so the second one holds a genuinely stale view of the row.
    sessions = sessionmaker_of(db_session)

    async with sessions() as s1:
        async with sessions() as s2:
            debt1 = (
                await s1.execute(select(Debt).where(Debt.id == d.id))
            ).scalar_one()
            debt2 = (
                await s2.execute(select(Debt).where(Debt.id == d.id))
            ).scalar_one()

            version_both_read = int(debt1.version)
            assert int(debt2.version) == version_both_read

            # EACH WRITER DECLARES ITS OWN OPERATION. Two sessions on two connections are two
            # transactions, so the journal sees two units of work and each has to name itself; the
            # race under test - the second writer's view of `version` is older than the first
            # writer's commit - is untouched by the declaration.
            async with debt_fixture_setup(s1, label="winner"):
                debt1.amount = Decimal("70")
            await s1.commit()

            with pytest.raises((StaleDataError, DBAPIError)) as refusal:
                async with debt_fixture_setup(s2, label="loser"):
                    debt2.amount = Decimal("130")
                await s2.commit()
            await s2.rollback()

    # The database refuses the write itself: SERIALIZABLE cannot let a writer whose snapshot predates
    # s1's commit update the row s1 changed. By SQLSTATE, not by class: a `DBAPIError` of any other
    # code is not this refusal.
    assert isinstance(refusal.value, DBAPIError), refusal.value
    sqlstate = getattr(refusal.value.orig, "sqlstate", None) or getattr(
        refusal.value.orig, "pgcode", None
    )
    assert sqlstate == "40001", refusal.value

    # The invariant, read back on a third session: the committed update stands, the stale one is
    # nowhere, and the row moved forward exactly one version.
    async with sessions() as fresh:
        stored = (
            await fresh.execute(
                select(Debt.amount, Debt.version).where(Debt.id == d.id)
            )
        ).one()
    assert Decimal(str(stored.amount)) == Decimal("70")
    # Exactly one version step past what both sessions read: the winner's, and only the winner's.
    assert int(stored.version) == version_both_read + 1
