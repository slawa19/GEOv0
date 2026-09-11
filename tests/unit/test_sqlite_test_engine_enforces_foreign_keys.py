"""The test engine must enforce foreign keys, or the default tier cannot see a referential effect.

The application engine sets `PRAGMA foreign_keys=ON` (`app/db/session.py:48`). The test engine did
not until 2026-09-11, so every foreign key was unenforced on the default SQLite tier while the
application it tests enforced them: a CASCADE that destroys a debt, a RESTRICT that protects one and
a reference to a row that does not exist all looked identical. This module holds the switch in place.

It is written as a counter-proof rather than as a reading of the setting: it performs an insert that
violates a foreign key and requires the database to refuse. Checking that the pragma is "set" would
pass on a connection the listener never reached.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from app.db.models.prepare_lock import PrepareLock
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
            state="NEW",
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_a_child_written_before_its_parent_is_refused(db_session) -> None:
    """The second failure form found when enforcement was switched on.

    `PrepareLock.tx_id` is a bare ForeignKey with no ORM relationship, so SQLAlchemy gives it no
    insert ordering relative to `transactions`. A lock whose transaction does not exist yet must be
    refused rather than stored.
    """
    db_session.add(
        PrepareLock(
            tx_id=str(uuid.uuid4()),  # no such transaction
            participant_id=uuid.uuid4(),
            effects={"flows": []},
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()
