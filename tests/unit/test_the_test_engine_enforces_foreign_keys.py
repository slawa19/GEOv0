"""The test tier's database must enforce foreign keys, or it cannot see a referential effect.

Written 2026-09-11 for the SQLite tier, whose test engine did not set `PRAGMA foreign_keys=ON` while
the application engine did: every foreign key was unenforced there, and a CASCADE that destroys a
debt, a RESTRICT that protects one and a reference to a row that does not exist all looked identical.
Since 017 the tier runs only on PostgreSQL, where enforcement is not a switch, but the property the
module holds - the schema the tier tests against refuses a dangling reference, including a bare
foreign key that carries no ORM insert ordering - is still the one the money tests rely on. The bare
example was `PrepareLock.tx_id` until programme 019 stage 5 (T1909) removed reservations; it is now
`debt_operations.tx_id -> transactions.tx_id` (RESTRICT), a Core table with no ORM relationship. Renamed from `test_sqlite_test_engine_enforces_foreign_keys.py` in 017 stage 3, slice S3.

It is written as a counter-proof rather than as a reading of the setting: it performs an insert that
violates a foreign key and requires the database to refuse.
"""

from __future__ import annotations

import uuid
import pytest
from sqlalchemy import insert, select
from sqlalchemy.exc import IntegrityError

from app.db.journal_tables import debt_operations
from app.db.models.transaction import Transaction


@pytest.mark.asyncio
async def test_a_dangling_reference_is_refused(db_session) -> None:
    """A Transaction whose initiator does not exist must not be storable.

    `transactions.initiator_id` references `participants.id`. With enforcement off this insert
    succeeds silently, which is exactly how twenty tests came to certify states the application
    cannot reach.
    """
    db_session.add(
        Transaction(
            id=uuid.uuid4(),
            tx_id=str(uuid.uuid4()),
            type="PAYMENT",
            initiator_id=uuid.uuid4(),  # no such participant
            payload={},
            # Terminal on purpose: since migration 030 (019 stage 4) a NEW payment is refused by the
            # payment-state CHECK with the SAME exception class, and this test would pass without any
            # foreign key at all. The SQLSTATE below pins the refusal to the foreign key.
            state="COMMITTED",
        )
    )
    with pytest.raises(IntegrityError) as refused:
        await db_session.flush()
    await db_session.rollback()
    assert getattr(refused.value.orig, "sqlstate", None) == "23503", refused.value


@pytest.mark.asyncio
async def test_a_child_written_before_its_parent_is_refused(db_session) -> None:
    """The second failure form found when enforcement was switched on.

    `debt_operations.tx_id` is a bare ForeignKey on a Core table with no ORM relationship, so
    nothing orders its insert relative to `transactions`. An envelope whose transaction does not
    exist yet must be refused rather than stored.

    The envelope is otherwise valid (OPEN, a kind that owns a tx_id, a 64-character digest), so the
    only thing that can refuse it is the foreign key; the SQLSTATE and the message pin that.
    """
    missing_tx_id = str(uuid.uuid4())  # no such transaction
    assert (
        await db_session.execute(select(Transaction.id).where(Transaction.tx_id == missing_tx_id))
    ).first() is None

    with pytest.raises(IntegrityError) as refused:
        await db_session.execute(
            insert(debt_operations).values(
                id=uuid.uuid4(),
                kind="PAYMENT",
                identity=missing_tx_id,
                tx_id=missing_tx_id,
                intent={},
                intent_digest="0" * 64,
                state="OPEN",
            )
        )
    await db_session.rollback()
    assert getattr(refused.value.orig, "sqlstate", None) == "23503", refused.value
    assert 'is not present in table "transactions"' in str(refused.value), refused.value
