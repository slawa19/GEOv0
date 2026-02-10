import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError


@pytest.mark.asyncio
async def test_prepare_locks_tx_id_fk_exists_and_blocks_orphans_postgres(db_session):
    dialect = None
    try:
        dialect = db_session.get_bind().dialect.name
    except Exception:
        dialect = None

    if dialect not in {"postgresql", "postgres"}:
        pytest.skip("Postgres-only: validates prepare_locks.tx_id FK to transactions")

    from app.db.models.participant import Participant
    from app.db.models.prepare_lock import PrepareLock

    nonce = uuid.uuid4().hex[:10]
    p = Participant(
        pid=f"P_FK_{nonce}",
        display_name="P",
        public_key=f"pk_fk_{nonce}",
        type="person",
        status="active",
        profile={},
    )
    db_session.add(p)
    await db_session.commit()
    await db_session.refresh(p)

    # Orphan lock: tx_id does not exist in transactions => must fail on Postgres.
    orphan_tx_id = str(uuid.uuid4())
    db_session.add(
        PrepareLock(
            tx_id=orphan_tx_id,
            participant_id=p.id,
            effects={},
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=60),
        )
    )

    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()

    async with db_session.connection() as conn:

        def _inspect_schema(sync_conn):
            insp = inspect(sync_conn)

            fks = insp.get_foreign_keys("prepare_locks")
            assert any(
                fk.get("referred_table") == "transactions"
                and fk.get("constrained_columns") == ["tx_id"]
                and fk.get("referred_columns") == ["tx_id"]
                for fk in fks
            )

            indexes = insp.get_indexes("prepare_locks")
            assert any(
                ix.get("name") == "ix_prepare_locks_participant_expires_at"
                and ix.get("column_names") == ["participant_id", "expires_at"]
                for ix in indexes
            )

        await conn.run_sync(_inspect_schema)

