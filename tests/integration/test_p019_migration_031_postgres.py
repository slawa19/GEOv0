"""019 stage 5 (`T1909`, fourth consultation precondition 4): migration 031 drops `prepare_locks`.

Spec 019, "Решения консультации, четвёртый круг", precondition 4: the upgrade from `030` runs under a
coordinated writer stop, REFUSES while any reservation row exists (never discards one silently) and then
drops the table; transactions, debts and the debt journal are not touched. `downgrade` restores the FULL
EMPTY MIGRATED SCHEMA of the table - not today's ORM model, which no longer exists - as migrations `001`,
`004`, `005`, `006` and `014` built it.

THE REFERENCE IS BUILT, NOT WRITTEN DOWN. A scratch database is migrated from empty to `030` by the real
Alembic chain; its catalogue of `prepare_locks` (columns in order with type, nullability and default;
every constraint by `pg_get_constraintdef`, so both foreign keys carry their `ON DELETE`; every index by
`pg_indexes.indexdef`, so the GIN index carries its access method) is the reference. The database under
test goes down from `031` to `030` and must produce the SAME catalogue. A hand-written expectation could
agree with a wrong downgrade; the chain that built every existing database cannot.

One clone of the migrated template: at `031` the table is absent; history (a world, terminal payments,
a clearing, debts) is seeded; down to `030` - the table is back, empty, catalogue-equal to the reference;
a reservation row refuses the upgrade (the database stays at `030`); after the row is removed the upgrade
applies, the table is gone, and the history rows are identical before and after the round trip.

MUTATIONS that must redden this (recorded in the `T1909` changelog): drop one `create_index` from the
downgrade (catalogue differs); drop the reservation count (the planted row is discarded, the upgrade
applies).
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.db.models.debt import Debt
from tests.debt_setup import debt_fixture_setup
from tests.migrated_schema import REPO_ROOT, repository_head
from tests.p018_support import seed_world

_BEFORE = "030_payment_rows_are_terminal"
_AFTER = "031_drop_prepare_locks"


def _alembic(url: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(  # noqa: S603 - fixed argv, no shell
        [sys.executable, "-m", "alembic", "-c", "migrations/alembic.ini", *args],
        capture_output=True,
        text=True,
        env=dict(os.environ, DATABASE_URL=url),
        cwd=str(REPO_ROOT),
        timeout=600,
    )


async def _read(url: str, sql: str, **params) -> list[tuple]:
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            return [tuple(row) for row in (await connection.execute(text(sql), params)).all()]
    finally:
        await engine.dispose()


async def _exec(url: str, sql: str, **params) -> None:
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.begin() as connection:
            await connection.execute(text(sql), params)
    finally:
        await engine.dispose()


async def _version(url: str) -> list[tuple]:
    return await _read(url, "SELECT version_num FROM alembic_version")


async def _exists(url: str) -> bool:
    return (await _read(url, "SELECT to_regclass('public.prepare_locks') IS NOT NULL"))[0][0]


async def _catalogue(url: str) -> dict[str, list[tuple]]:
    """Everything the schema says about `prepare_locks`, in a form two databases can be compared by."""

    return {
        "columns": await _read(
            url,
            "SELECT row_number() OVER (ORDER BY a.attnum), a.attname, format_type(a.atttypid, a.atttypmod), "
            "a.attnotnull, pg_get_expr(d.adbin, d.adrelid) "
            "FROM pg_attribute a LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum "
            "WHERE a.attrelid = 'public.prepare_locks'::regclass AND a.attnum > 0 AND NOT a.attisdropped "
            "ORDER BY a.attnum",
        ),
        "constraints": await _read(
            url,
            "SELECT conname, contype, pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conrelid = 'public.prepare_locks'::regclass ORDER BY conname",
        ),
        "indexes": await _read(
            url,
            "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'public' "
            "AND tablename = 'prepare_locks' ORDER BY indexname",
        ),
    }


_HISTORY_SQL = {
    "transactions": "SELECT tx_id, type, state, payload::text FROM transactions ORDER BY tx_id",
    "debts": "SELECT id, debtor_id, creditor_id, equivalent_id, amount, version FROM debts ORDER BY id",
    "debt_operations": "SELECT id, kind, identity, state, tx_id FROM debt_operations ORDER BY id",
    "debt_journal_entries": "SELECT operation_id, ordinal, delta FROM debt_journal_entries ORDER BY 1, 2",
}


async def _history(url: str) -> dict[str, list[tuple]]:
    return {name: await _read(url, sql) for name, sql in _HISTORY_SQL.items()}


_INSERT_TX = (
    "INSERT INTO transactions (id, tx_id, type, initiator_id, payload, state) "
    "VALUES (:id, :tx_id, :type, :initiator, '{}', :state)"
)


@pytest.mark.asyncio
async def test_031_drops_the_reservations_refuses_an_undrained_database_and_restores_the_full_schema(
    committed_database,
) -> None:
    from tests.conftest import TEST_DATABASE_URL
    from tests.migrated_schema import scratch_databases

    url = committed_database.url
    await committed_database.engine.dispose()
    assert repository_head() == _AFTER, "this module pins migration 031 as the head it tests"
    assert await _version(url) == [(_AFTER,)]
    assert not await _exists(url), "031 is applied but prepare_locks exists"

    # THE REFERENCE: the real chain, empty database -> 030.
    async with scratch_databases(TEST_DATABASE_URL, "p019m031ref") as (reference_url,):
        built = _alembic(reference_url, "upgrade", _BEFORE)
        assert built.returncode == 0, built.stderr
        reference = await _catalogue(reference_url)
    # Non-vacuity: the reference carries every element the consultation named.
    names = {row[0] for row in reference["constraints"]} | {row[0] for row in reference["indexes"]}
    for required in (
        "uq_prepare_locks_tx_participant", "chk_prepare_locks_lock_type", "fk_prepare_locks_participant_id",
        "fk_prepare_locks_tx_id", "idx_prepare_locks_tx_id", "idx_prepare_locks_expires_at",
        "ix_prepare_locks_lock_type", "ix_prepare_locks_participant_expires_at", "ix_prepare_locks_effects_gin",
    ):
        assert required in names, (required, reference)
    definitions = " ".join(row[2] for row in reference["constraints"])
    assert "ON DELETE CASCADE" in definitions, reference["constraints"]
    assert any("USING gin" in row[1] for row in reference["indexes"]), reference["indexes"]
    assert [row[1] for row in reference["columns"]] == [
        "id", "tx_id", "participant_id", "effects", "expires_at", "created_at", "lock_type",
    ], reference["columns"]

    # History, as the 019 binary leaves it: a world with debts, terminal payments, a clearing.
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with AsyncSession(bind=engine, expire_on_commit=False, autoflush=False) as session:
            world = await seed_world(session, label="m031")
            history_debt = Debt(
                debtor_id=world.p(0), creditor_id=world.p(1), equivalent_id=world.equivalent.id,
                amount=Decimal("12.34"),
            )
            async with debt_fixture_setup(session, label="m031-history"):
                session.add(history_debt)
            await session.commit()
    finally:
        await engine.dispose()
    tx_ids = {}
    for name, tx_type, state in (
        ("committed", "PAYMENT", "COMMITTED"),
        ("aborted", "PAYMENT", "ABORTED"),
        ("clearing", "CLEARING", "COMMITTED"),
    ):
        tx_ids[name] = f"m031-{name}-{uuid.uuid4()}"
        await _exec(url, _INSERT_TX, id=uuid.uuid4(), tx_id=tx_ids[name], type=tx_type,
                    initiator=world.p(0), state=state)
    history = await _history(url)
    assert history["transactions"] and history["debts"] and history["debt_journal_entries"], (
        "the history under test is empty"
    )

    # 1. Down to 030 (allowed under the stop): the table is back, EMPTY, with the full migrated schema.
    down = _alembic(url, "downgrade", _BEFORE)
    assert down.returncode == 0, down.stderr
    assert await _version(url) == [(_BEFORE,)]
    assert await _exists(url)
    assert await _read(url, "SELECT count(*) FROM prepare_locks") == [(0,)], "the downgrade recreated rows"
    restored = await _catalogue(url)
    for part in ("columns", "constraints", "indexes"):
        assert restored[part] == reference[part], (
            f"031 downgrade restored a different {part} of prepare_locks than the 001..030 chain builds:\n"
            f"  chain:     {reference[part]}\n  downgrade: {restored[part]}"
        )
    assert await _history(url) == history, "the downgrade changed history"

    # 2. A reservation row refuses the upgrade; nothing is discarded and the database stays at 030.
    await _exec(
        url,
        "INSERT INTO prepare_locks (id, tx_id, participant_id, lock_type, effects, expires_at) "
        "VALUES (:id, :tx_id, :participant, 'PAYMENT', '{}', now() + interval '1 minute')",
        id=uuid.uuid4(), tx_id=tx_ids["aborted"], participant=world.p(0),
    )
    refused = _alembic(url, "upgrade", _AFTER)
    assert refused.returncode != 0, "031 applied over a reservation row"
    assert "refusing to apply 031 on an undrained database: 1 prepare_locks row(s)" in refused.stderr, (
        refused.stderr
    )
    assert await _version(url) == [(_BEFORE,)]
    assert await _read(url, "SELECT count(*) FROM prepare_locks") == [(1,)], "the refused upgrade lost the row"

    # 3. Drained: the upgrade applies, the table is gone, history is identical across the round trip.
    await _exec(url, "DELETE FROM prepare_locks")
    up = _alembic(url, "upgrade", _AFTER)
    assert up.returncode == 0, up.stderr
    assert await _version(url) == [(_AFTER,)]
    assert not await _exists(url)
    assert await _history(url) == history, "the round trip changed history"
