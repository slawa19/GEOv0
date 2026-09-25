"""019 stage 4 (`T1906`, `FORK-3`): migration 030 - the legacy payment-write fence and the drained cutover.

Spec 019, "Перевод": `030` REFUSES while any `PAYMENT` row is not `COMMITTED`/`ABORTED` or any
`prepare_locks` row exists, and otherwise adds the IMMEDIATE CHECK
`type <> 'PAYMENT' OR state IN ('COMMITTED', 'ABORTED')` - a fence that makes a pre-019 binary fail on
its very first `NEW` insert. `CLEARING` and the other types stay outside it. `downgrade` is allowed and
removes only the CHECK. The same CHECK is declared on the model, so `Base.metadata.create_all` (mode A)
and `alembic upgrade head` (mode B) must build the same constraint.

One clone of the migrated template: parity with `create_all`; down to 029 (allowed); history at 029 -
terminal payments, a clearing in `NEW`, and a PLANTED `NEW` payment - the upgrade is refused and the
database stays at 029; a reservation row alone refuses too; after the drain (the old recovery's job,
done here by hand) the upgrade applies and the history is intact; at 030 a direct `NEW`/`PREPARED`
payment write is refused by the CHECK (SQLSTATE 23514, the constraint named) while a clearing `NEW` is
not; down again, and the fence is gone.

MUTATIONS that must redden this: drop `op.create_check_constraint` from the migration (the planted
insert after the upgrade succeeds); drop the precondition (the planted-NEW upgrade applies, or fails
with the raw CHECK error instead of the drain message); drop the CHECK from the model (parity fails).
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.db.base import Base
from tests.migrated_schema import REPO_ROOT, repository_head
from tests.p018_support import seed_world, sqlstate_of

_BEFORE = "029_debt_journal_by_the_database"
_AFTER = "030_payment_rows_are_terminal"
_CONSTRAINT = "chk_transaction_payment_terminal"


def _alembic(url: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(  # noqa: S603 - fixed argv, no shell
        [sys.executable, "-m", "alembic", "-c", "migrations/alembic.ini", *args],
        capture_output=True,
        text=True,
        env=dict(os.environ, DATABASE_URL=url),
        cwd=str(REPO_ROOT),
        timeout=600,
    )


async def _read(url: str, sql: str, **params):
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            return (await connection.execute(text(sql), params)).all()
    finally:
        await engine.dispose()


async def _exec(url: str, sql: str, **params) -> None:
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.begin() as connection:
            await connection.execute(text(sql), params)
    finally:
        await engine.dispose()


async def _refused(url: str, sql: str, **params) -> tuple[str | None, str]:
    """(SQLSTATE, message) of a write that must fail; (None, '') when it was accepted (and rolled back)."""

    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                await connection.execute(text(sql), params)
            except DBAPIError as exc:
                return sqlstate_of(exc), str(exc.orig)
            finally:
                await transaction.rollback()
        return None, ""
    finally:
        await engine.dispose()


_INSERT_TX = (
    "INSERT INTO transactions (id, tx_id, type, initiator_id, payload, state) "
    "VALUES (:id, :tx_id, :type, :initiator, '{}', :state)"
)


async def _transaction_checks(url: str) -> dict[str, str]:
    rows = await _read(
        url,
        "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conrelid = 'transactions'::regclass AND contype = 'c' ORDER BY conname",
    )
    return {row[0]: row[1] for row in rows}


async def _version(url: str) -> list[tuple]:
    return [tuple(row) for row in await _read(url, "SELECT version_num FROM alembic_version")]


@pytest.mark.asyncio
async def test_030_fences_legacy_payment_writes_refuses_an_undrained_database_and_goes_back(
    committed_database,
) -> None:
    from tests.conftest import TEST_DATABASE_URL
    from tests.migrated_schema import scratch_databases

    url = committed_database.url
    await committed_database.engine.dispose()
    assert repository_head() == _AFTER, "this module pins migration 030 as the head it tests"
    assert await _version(url) == [(_AFTER,)]

    # 0. Both construction paths build the same CHECK constraints on `transactions`.
    async with scratch_databases(TEST_DATABASE_URL, "p019m030meta") as (metadata_url,):
        engine = create_async_engine(metadata_url, poolclass=NullPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
        finally:
            await engine.dispose()
        migrated_checks = await _transaction_checks(url)
        metadata_checks = await _transaction_checks(metadata_url)
    assert _CONSTRAINT in migrated_checks, migrated_checks  # non-vacuity: the fence exists
    assert migrated_checks == metadata_checks, (
        f"alembic and create_all disagree on transactions' CHECKs:\n"
        f"  alembic:    {migrated_checks}\n  create_all: {metadata_checks}"
    )

    # 1. Down to 029 is allowed and removes the fence only.
    down = _alembic(url, "downgrade", _BEFORE)
    assert down.returncode == 0, down.stderr
    assert await _version(url) == [(_BEFORE,)]
    assert _CONSTRAINT not in await _transaction_checks(url)

    # 2. History at 029, as a pre-019 binary leaves it: terminal payments, a clearing in NEW, and one
    #    payment it had not finished (the planted NEW).
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with AsyncSession(bind=engine, expire_on_commit=False, autoflush=False) as session:
            world = await seed_world(session, label="m030")
            await session.commit()
    finally:
        await engine.dispose()
    history = {
        "committed": ("PAYMENT", "COMMITTED"),
        "aborted": ("PAYMENT", "ABORTED"),
        "clearing": ("CLEARING", "NEW"),
        "planted": ("PAYMENT", "NEW"),
    }
    tx_ids = {name: f"m030-{name}-{uuid.uuid4()}" for name in history}
    for name, (tx_type, state) in history.items():
        await _exec(url, _INSERT_TX, id=uuid.uuid4(), tx_id=tx_ids[name], type=tx_type,
                    initiator=world.p(0), state=state)

    refused = _alembic(url, "upgrade", "head")
    assert refused.returncode != 0, "030 applied over a non-terminal PAYMENT row"
    assert "refusing to apply 030 on an undrained database" in refused.stderr, refused.stderr
    assert "1 PAYMENT transaction(s) are not terminal (NEW: 1)" in refused.stderr, refused.stderr
    assert await _version(url) == [(_BEFORE,)]

    # 3. The drain (under the old binary its recovery decides; here the planted row is aborted by hand).
    #    A reservation row alone still refuses: the fence is added only over an empty reservation table.
    await _exec(url, "UPDATE transactions SET state = 'ABORTED' WHERE tx_id = :tx_id", tx_id=tx_ids["planted"])
    await _exec(
        url,
        "INSERT INTO prepare_locks (id, tx_id, participant_id, lock_type, effects, expires_at) "
        "VALUES (:id, :tx_id, :participant, 'PAYMENT', '{}', now() + interval '1 minute')",
        id=uuid.uuid4(), tx_id=tx_ids["planted"], participant=world.p(0),
    )
    refused = _alembic(url, "upgrade", "head")
    assert refused.returncode != 0, "030 applied over a live reservation"
    assert "0 PAYMENT transaction(s) are not terminal (none) and 1 prepare_locks row(s)" in refused.stderr, (
        refused.stderr
    )
    assert await _version(url) == [(_BEFORE,)]
    await _exec(url, "DELETE FROM prepare_locks WHERE tx_id = :tx_id", tx_id=tx_ids["planted"])

    before = sorted(
        tuple(row) for row in await _read(
            url, "SELECT tx_id, type, state FROM transactions WHERE tx_id = ANY(:ids)",
            ids=list(tx_ids.values()),
        )
    )
    up = _alembic(url, "upgrade", "head")
    assert up.returncode == 0, up.stderr
    assert await _version(url) == [(_AFTER,)]
    after = sorted(
        tuple(row) for row in await _read(
            url, "SELECT tx_id, type, state FROM transactions WHERE tx_id = ANY(:ids)",
            ids=list(tx_ids.values()),
        )
    )
    assert after == before, "030 changed a historical transaction row"
    assert ("CLEARING", "NEW") in {(row[1], row[2]) for row in after}, "the clearing NEW row is gone"

    # 4. The fence, IMMEDIATE, at 030: a pre-019 binary's NEW insert and a PREPARED update are refused;
    #    CLEARING NEW and terminal payments are not.
    for sql, params in (
        (_INSERT_TX, {"type": "PAYMENT", "state": "NEW"}),
        (_INSERT_TX, {"type": "PAYMENT", "state": "PREPARED"}),
    ):
        sqlstate, message = await _refused(
            url, sql, id=uuid.uuid4(), tx_id=f"m030-legacy-{uuid.uuid4()}",
            initiator=world.p(0), **params,
        )
        assert sqlstate == "23514" and _CONSTRAINT in message, (params, sqlstate, message)
    sqlstate, message = await _refused(
        url, "UPDATE transactions SET state = 'PREPARED' WHERE tx_id = :tx_id", tx_id=tx_ids["committed"]
    )
    assert sqlstate == "23514" and _CONSTRAINT in message, (sqlstate, message)
    for tx_type, state in (("CLEARING", "NEW"), ("PAYMENT", "COMMITTED"), ("PAYMENT", "ABORTED")):
        accepted, message = await _refused(
            url, _INSERT_TX, id=uuid.uuid4(), tx_id=f"m030-ok-{uuid.uuid4()}", type=tx_type,
            initiator=world.p(0), state=state,
        )
        assert accepted is None, (tx_type, state, message)

    # 5. Down again (allowed under the same stop): the fence is gone, the old write is accepted.
    down = _alembic(url, "downgrade", _BEFORE)
    assert down.returncode == 0, down.stderr
    assert await _version(url) == [(_BEFORE,)]
    accepted, message = await _refused(
        url, _INSERT_TX, id=uuid.uuid4(), tx_id=f"m030-back-{uuid.uuid4()}", type="PAYMENT",
        initiator=world.p(0), state="NEW",
    )
    assert accepted is None, message
