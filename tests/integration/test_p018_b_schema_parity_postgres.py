"""018 `T1803`: `alembic upgrade head` and `Base.metadata.create_all` build the SAME journal writer.

Spec 018, stage B: "Совпадение двух путей сборки проверяется по содержанию и поведению". Mode A of the
test fixtures builds its schema with `create_all` (`tests/conftest.py`), mode B clones a migrated
template; without the metadata DDL (`app/db/journal_triggers.py`) mode A would run with no journal
writer at all. Matching NAMES proves nothing - a disabled trigger of the same name passes a name check -
so this compares, on the two databases:

* DEFINITIONS: `pg_get_functiondef` of every `geo_*` function, `pg_get_triggerdef` of every trigger on
  `debts` and the three journal tables, the CHECK and UNIQUE constraints of those tables, and the
  columns of the three journal tables (type, nullability, default);
* STATE AND TIMING: `tgenabled`, `tgtype` (timing, level and events), `tgdeferrable`, `tginitdeferred`;
* THE SEQUENCE: type, start, increment, bounds, cycle, and that it is OWNED BY `ordinal`;
* BEHAVIOUR, on BOTH: one successful `Book` operation, and one refusal each of `GE001`, `GE002`, a
  direct entry INSERT (with a valid OPEN context) and the COMMIT of an OPEN envelope.

The migrated side is a clone of the session's migrated template (no second `alembic upgrade`); the
metadata side is an empty scratch database given `create_all`. Mutation that must redden this: drop any
one `DDL` registration in `app/db/journal_triggers.py`, or edit one copy of the SQL only.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.ledger.book import Book, NewDebt, operation_for
from app.db.base import Base
from tests.p018_support import (
    GE001,
    GE002,
    GUARD,
    module_clone,
    refused,
    seed_world,
    serializable_engine,
    sqlstate_of,
)

_TABLES = ("debts", "debt_operations", "debt_journal_entries", "debt_operation_equivalents")


@pytest.fixture(scope="module")
def migrated_url():
    with module_clone("p018bparity") as url:
        yield url


async def _catalogue(url: str) -> dict[str, object]:
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:

            async def rows(sql: str, **params):
                return (await connection.execute(text(sql), params)).all()

            functions = {
                row[0]: row[1]
                for row in await rows(
                    "SELECT p.proname, pg_get_functiondef(p.oid) FROM pg_proc p "
                    "JOIN pg_namespace n ON n.oid = p.pronamespace "
                    "WHERE n.nspname = 'public' AND p.proname LIKE 'geo\\_%'"
                )
            }
            triggers = {
                (row[0], row[1]): tuple(row[2:])
                for row in await rows(
                    "SELECT c.relname, t.tgname, pg_get_triggerdef(t.oid), t.tgenabled::text, "
                    "t.tgtype, "
                    "t.tgdeferrable, t.tginitdeferred, t.tgconstraint <> 0 "
                    "FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
                    "WHERE NOT t.tgisinternal AND c.relname = ANY(:tables)",
                    tables=list(_TABLES),
                )
            }
            constraints = {
                (row[0], row[1]): " ".join(row[2].split())
                for row in await rows(
                    "SELECT c.relname, k.conname, pg_get_constraintdef(k.oid) FROM pg_constraint k "
                    "JOIN pg_class c ON c.oid = k.conrelid "
                    "WHERE c.relname = ANY(:tables) AND k.contype IN ('c', 'u')",
                    tables=list(_TABLES),
                )
            }
            columns = {
                (row[0], row[1]): tuple(row[2:])
                for row in await rows(
                    "SELECT table_name, column_name, data_type, is_nullable, column_default "
                    "FROM information_schema.columns WHERE table_schema = 'public' "
                    "AND table_name = ANY(:tables)",
                    # THE JOURNAL TABLES ONLY. `debts` differs between the two paths for reasons
                    # older than this programme (measured 2026-09-24: `id` default, `version` default,
                    # `created_at`/`updated_at` nullability) and none of them is the journal's.
                    tables=list(_TABLES[1:]),
                )
            }
            sequence = await rows(
                "SELECT s.seqtypid::regtype::text, s.seqstart, s.seqincrement, s.seqmin, s.seqmax, "
                "s.seqcycle, pg_get_serial_sequence('debt_journal_entries', 'ordinal') "
                "FROM pg_sequence s "
                "WHERE s.seqrelid = 'debt_journal_entries_ordinal_seq'::regclass"
            )
            return {
                "functions": functions,
                "triggers": triggers,
                "constraints": constraints,
                "columns": columns,
                "sequence": [tuple(row) for row in sequence],
            }
    finally:
        await engine.dispose()


async def _behaviour(url: str) -> dict[str, object]:
    """One success and four refusals, measured on the database at `url`."""

    engine = serializable_engine(url)
    outcome: dict[str, object] = {}
    try:
        identity = f"parity-{uuid.uuid4()}"
        async with AsyncSession(bind=engine, expire_on_commit=False, autoflush=False) as session:
            world = await seed_world(session)
            await Book.post(
                session,
                operation_for("TEST_FIXTURE", identity, {"parity": True}),
                [NewDebt(world.p(0), world.p(1), world.eq, Decimal("10"))],
            )
            await session.commit()
        async with engine.connect() as connection:
            outcome["success"] = (
                await connection.execute(
                    text(
                        "SELECT o.state, o.schema_version, count(e.id) FROM debt_operations o "
                        "JOIN debt_journal_entries e ON e.operation_id = o.id "
                        "WHERE o.identity = :i GROUP BY o.state, o.schema_version"
                    ),
                    {"i": identity},
                )
            ).one()._tuple()
            debt_id = (
                await connection.execute(
                    text("SELECT id FROM debts WHERE equivalent_id = :eq"), {"eq": world.eq}
                )
            ).scalar_one()
            outcome["GE001"] = await refused(
                connection, f"UPDATE debts SET amount = amount + 1 WHERE id = '{debt_id}'"
            )
            operation_id = uuid.uuid4()
            await connection.exec_driver_sql(
                "INSERT INTO debt_operations (id, kind, identity, intent, intent_digest, "
                "schema_version, money_encoding_version, intent_encoding_version, state) VALUES "
                f"('{operation_id}', 'TEST_FIXTURE', 'parity-open-{operation_id}', '{{}}', "
                f"'{'0' * 64}', 2, 1, 1, 'OPEN')"
            )
            await connection.exec_driver_sql(
                f"SELECT set_config('geo.operation_id', '{operation_id}', true)"
            )
            outcome["GE002"] = await refused(
                connection, f"UPDATE debts SET creditor_id = '{world.p(2)}' WHERE id = '{debt_id}'"
            )
            outcome["direct entry"] = await refused(
                connection,
                "INSERT INTO debt_journal_entries (id, operation_id, ordinal, equivalent_id, "
                "debtor_id, creditor_id, effect, amount_before, amount_after, delta) VALUES "
                f"('{uuid.uuid4()}', '{operation_id}', 1, '{world.eq}', '{world.p(0)}', "
                f"'{world.p(1)}', 'I', NULL, 5, 5)",
            )
            try:
                await connection.commit()
                outcome["commit OPEN"] = None
            except DBAPIError as exc:
                outcome["commit OPEN"] = sqlstate_of(exc)
    finally:
        await engine.dispose()
    return outcome


@pytest.mark.asyncio
async def test_t1803_both_construction_paths_build_the_same_writer_and_it_behaves_the_same(
    migrated_url,
) -> None:
    from tests.conftest import TEST_DATABASE_URL
    from tests.migrated_schema import scratch_databases

    async with scratch_databases(TEST_DATABASE_URL, "p018bmeta") as (metadata_url,):
        engine = create_async_engine(metadata_url, poolclass=NullPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
        finally:
            await engine.dispose()

        migrated = await _catalogue(migrated_url)
        from_metadata = await _catalogue(metadata_url)

        # NON-VACUITY: the objects exist on the migrated side and are what the spec names.
        assert len(migrated["functions"]) == 6, sorted(migrated["functions"])
        assert len(migrated["triggers"]) == 9, sorted(migrated["triggers"])
        assert {state[1] for state in migrated["triggers"].values()} == {"O"}, "a trigger is disabled"
        deferred = [key for key, state in migrated["triggers"].items() if state[3] and state[4]]
        assert deferred == [("debt_operations", "trg_debt_operations_complete_at_commit")]
        assert migrated["sequence"] == [
            ("bigint", 1, 1, 1, 9223372036854775807, False,
             "public.debt_journal_entries_ordinal_seq")
        ]

        for part in ("functions", "triggers", "constraints", "columns", "sequence"):
            left, right = migrated[part], from_metadata[part]
            if isinstance(left, dict):
                only_migrated = {k: left[k] for k in left.keys() - right.keys()}
                only_metadata = {k: right[k] for k in right.keys() - left.keys()}
                differing = {k: (left[k], right[k]) for k in left.keys() & right.keys()
                             if left[k] != right[k]}
                assert not (only_migrated or only_metadata or differing), (
                    f"{part} differ between alembic and create_all:\n"
                    f"  only alembic:  {only_migrated}\n  only metadata: {only_metadata}\n"
                    f"  differing:     {differing}"
                )
            else:
                assert left == right, f"{part}: alembic {left} != metadata {right}"

        expected = {
            "success": ("COMPLETED", 2, 1),
            "GE001": GE001,
            "GE002": GE002,
            "direct entry": GUARD,
            "commit OPEN": GUARD,
        }
        for label, url in (("alembic", migrated_url), ("create_all", metadata_url)):
            assert await _behaviour(url) == expected, label
