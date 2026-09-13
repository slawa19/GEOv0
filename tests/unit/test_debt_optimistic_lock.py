"""Two sessions cannot overwrite each other's debt amount - and the mechanism has a name per backend.

The lost-update class this guards has not changed. What changed on 2026-09-12 (T1525) is HOW SQLite
reports it. With the transaction control installed, a session that has read holds a real snapshot, so
the second writer is refused by the DATABASE with SQLITE_BUSY_SNAPSHOT before the ORM ever compares
`version`; on PostgreSQL (and on SQLite before the control, where reads were autocommitted) the write
reaches the row and the ORM raises `StaleDataError` because `version` has moved.

The test therefore asserts the invariant on both tiers - the committed value survives, the stale
writer is refused - and names the mechanism for the backend it is running on rather than accepting
"some exception". Only the SQLite tier selects this module today (it carries no `postgres` marker);
the PostgreSQL branch states the contract that tier would have to satisfy.
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
from app.db.sqlite_transaction_control import sqlite_busy_error_name

from tests.debt_setup import debt_fixture_setup


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
    from tests.conftest import TestingSessionLocal, engine

    async with TestingSessionLocal() as s1:
        async with TestingSessionLocal() as s2:
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

    if engine.dialect.name == "sqlite":
        # The database refuses the write itself: the snapshot s2 read at is older than s1's commit,
        # and no amount of waiting can make it current. `PaymentEngine._is_retryable_db_error`
        # classifies exactly this as retryable - see
        # `tests/unit/test_p015_t1525_sqlite_stale_snapshot_is_retried.py`.
        assert sqlite_busy_error_name(refusal.value) == "SQLITE_BUSY_SNAPSHOT", refusal.value
    else:
        # The write reaches the row, matches zero rows on `version`, and the ORM says so.
        assert isinstance(refusal.value, StaleDataError), refusal.value

    # The invariant, read back on a third session: the committed update stands, the stale one is
    # nowhere, and the row moved forward exactly one version.
    async with TestingSessionLocal() as fresh:
        stored = (
            await fresh.execute(
                select(Debt.amount, Debt.version).where(Debt.id == d.id)
            )
        ).one()
    assert Decimal(str(stored.amount)) == Decimal("70")
    # Exactly one version step past what both sessions read: the winner's, and only the winner's.
    assert int(stored.version) == version_both_read + 1
