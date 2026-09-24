"""018 `T1803`: migration 029 on a database WITH HISTORY - refusal, preservation, and the way back.

Spec 018: "Миграция отказывает, если в базе есть конверт OPEN"; "исторические строки сохраняют свои
значения"; "`downgrade` миграции 029 отказывает, если есть конверты `schema_version = 2`"; and the
manifest (`T1808` 7.C item 12): the downgrade must still work on a database without them, because
`test_p015_step5c_hold_races_postgres.py` downgrades through 029.

THE HISTORY IS WRITTEN AT 028, BY HAND, and that is legitimate here: at 028 there are no triggers, and
the rows are shaped exactly as the listener journal wrote them (schema_version 1, `flush_ordinal`,
`flush_count`, entries matching the debts). The same upgrade was ALSO measured on a history the real
listener wrote - `riverside-town-50` seeded by its recipe on `2fb1056` (34 operations, 40 entries,
reconciliation PASSED on all three equivalents before and after the upgrade); that evidence is in the
spec's Changelog, because the listener no longer exists in this tree to write it again.

One clone of the migrated template (no upgrade from scratch): down to 028, history in, up with an OPEN
envelope (refused), up without it (history intact), down again (`flush_count` reconstructed), up, one
operation through the book (`schema_version = 2`), down (refused).
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

from app.core.ledger.book import Book, operation_for
from tests.migrated_schema import REPO_ROOT
from tests.p018_support import seed_world, serializable_engine

_BEFORE = "028_equivalent_integrity_hold"
_AFTER = "029_debt_journal_by_the_database"


def _alembic(url: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(  # noqa: S603 - fixed argv, no shell
        [sys.executable, "-m", "alembic", "-c", "migrations/alembic.ini", *args],
        capture_output=True,
        text=True,
        env=dict(os.environ, DATABASE_URL=url),
        cwd=str(REPO_ROOT),
        timeout=600,
    )


async def _history_at_028(url: str) -> dict[str, object]:
    """Two COMPLETED version-1 operations with entries that explain the debts, and one OPEN envelope."""

    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with AsyncSession(bind=engine, expire_on_commit=False, autoflush=False) as session:
            world = await seed_world(session)
            await session.commit()
        ops = [uuid.uuid4(), uuid.uuid4()]
        open_op = uuid.uuid4()
        debt = uuid.uuid4()
        async with engine.begin() as connection:
            for index, op in enumerate(ops):
                await connection.execute(
                    text(
                        "INSERT INTO debt_operations (id, kind, identity, intent, intent_digest, "
                        "schema_version, money_encoding_version, intent_encoding_version, state, "
                        "completed_at, flush_count, effect_count, effect_digest) VALUES (:id, "
                        "'TEST_FIXTURE', :identity, '{}', :digest, 1, 1, 1, 'COMPLETED', now(), "
                        ":flushes, 1, :digest)"
                    ),
                    {"id": op, "identity": f"m029-{op}", "digest": "0" * 64, "flushes": 2 + index},
                )
            await connection.execute(
                text(
                    "INSERT INTO debts (id, debtor_id, creditor_id, equivalent_id, amount, version) "
                    "VALUES (:id, :d, :c, :eq, 12, 1)"
                ),
                {"id": debt, "d": world.p(0), "c": world.p(1), "eq": world.eq},
            )
            for op, ordinal, effect, before, after, delta in (
                (ops[0], 1, "I", None, 10, 10),
                (ops[1], 7, "U", 10, 12, 2),
            ):
                await connection.execute(
                    text(
                        "INSERT INTO debt_journal_entries (id, operation_id, flush_ordinal, "
                        "equivalent_id, debtor_id, creditor_id, effect, amount_before, "
                        "amount_after, delta) VALUES (:id, :op, :ordinal, :eq, :d, :c, :effect, "
                        ":before, :after, :delta)"
                    ),
                    {"id": uuid.uuid4(), "op": op, "ordinal": ordinal, "eq": world.eq,
                     "d": world.p(0), "c": world.p(1), "effect": effect, "before": before,
                     "after": after, "delta": delta},
                )
            await connection.execute(
                text(
                    "INSERT INTO debt_operations (id, kind, identity, intent, intent_digest, "
                    "schema_version, money_encoding_version, intent_encoding_version, state) "
                    "VALUES (:id, 'TEST_FIXTURE', :identity, '{}', :digest, 1, 1, 1, 'OPEN')"
                ),
                {"id": open_op, "identity": f"m029-open-{open_op}", "digest": "0" * 64},
            )
        return {"world": world, "ops": ops, "open": open_op}
    finally:
        await engine.dispose()


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


@pytest.mark.asyncio
async def test_t1803_the_migration_keeps_history_refuses_open_and_goes_back_only_without_version_2(
    committed_database,
) -> None:
    url = committed_database.url
    await committed_database.engine.dispose()

    # Down to 028 on an EMPTY database first: the step `test_p015_step5c_hold_races_postgres.py` takes.
    down = _alembic(url, "downgrade", _BEFORE)
    assert down.returncode == 0, down.stderr

    history = await _history_at_028(url)
    ops, world = history["ops"], history["world"]

    # 1. An OPEN envelope refuses the upgrade, and the database stays at 028.
    refused = _alembic(url, "upgrade", "head")
    assert refused.returncode != 0
    assert "while 1 envelope(s) are OPEN" in refused.stderr
    assert await _read(url, "SELECT version_num FROM alembic_version") == [(_BEFORE,)]

    # 2. Without it, the upgrade keeps every historical row as it was.
    await _exec(url, "DELETE FROM debt_operations WHERE id = :id", id=history["open"])
    up = _alembic(url, "upgrade", "head")
    assert up.returncode == 0, up.stderr
    entries = await _read(
        url,
        "SELECT operation_id, ordinal, effect, delta FROM debt_journal_entries "
        "WHERE equivalent_id = :eq ORDER BY ordinal",
        eq=world.eq,
    )
    assert [(row[0], row[1], row[2], row[3]) for row in entries] == [
        (ops[0], 1, "I", Decimal("10.00000000")),
        (ops[1], 7, "U", Decimal("2.00000000")),
    ]
    envelopes = await _read(
        url, "SELECT schema_version, state FROM debt_operations WHERE id = ANY(:ids)", ids=ops
    )
    assert sorted(envelopes) == [(1, "COMPLETED"), (1, "COMPLETED")]

    # 3. Down again, with history and no version 2: `flush_ordinal` is back with the same values and
    # `flush_count` is reconstructed as the distinct ordinals of each operation (one each here).
    down = _alembic(url, "downgrade", _BEFORE)
    assert down.returncode == 0, down.stderr
    restored = await _read(
        url,
        "SELECT o.id, o.flush_count, e.flush_ordinal FROM debt_operations o "
        "JOIN debt_journal_entries e ON e.operation_id = o.id WHERE o.id = ANY(:ids) "
        "ORDER BY e.flush_ordinal",
        ids=ops,
    )
    assert [(row[0], row[1], row[2]) for row in restored] == [(ops[0], 1, 1), (ops[1], 1, 7)]

    # 4. Up, one operation through the book (version 2), and the way down is refused.
    up = _alembic(url, "upgrade", "head")
    assert up.returncode == 0, up.stderr
    engine = serializable_engine(url)
    try:
        async with AsyncSession(bind=engine, expire_on_commit=False) as session:
            await Book.post(session, operation_for("TEST_FIXTURE", f"m029-v2-{uuid.uuid4()}", {}), [])
            await session.commit()
    finally:
        await engine.dispose()
    blocked = _alembic(url, "downgrade", _BEFORE)
    assert blocked.returncode != 0
    assert "carry schema_version 2" in blocked.stderr
    assert await _read(url, "SELECT version_num FROM alembic_version") == [(_AFTER,)]
