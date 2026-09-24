"""Programme 015, T1530/T1531 on PostgreSQL: the constraint on BOTH construction paths, and asyncpg.

WHY THIS MODULE BUILDS ITS OWN DATABASES INSTEAD OF TRUSTING THE GATE'S, and why that is still true
after T1534. When this module was written, `GEO_TEST_USE_MIGRATED_SCHEMA=1` verified nothing at all -
measured by the owner on 2026-09-13, it only checked that `alembic_version` was readable, and
`geov0_test_ci` was in fact a `create_all` schema wearing a stamp. Since T1534 the flag BUILDS the
gate's schema with the migrations, so the gate's database is now genuinely migrated. It still cannot
answer this module's question: one database is one construction path, and the subject here is whether
the TWO paths agree. So this module keeps creating two scratch databases, builds one with
`Base.metadata.create_all` and the other with `alembic -c migrations/alembic.ini upgrade head`, and
compares what PostgreSQL then holds.

THE ALEMBIC PATH NEEDS A SUBPROCESS, and that is a fact about this tree rather than a choice:
`migrations/env.py` ends in `asyncio.run(...)`, so it cannot be invoked from inside a running event
loop. `tests/migrated_schema.py::run_alembic_upgrade_head` is where that subprocess lives.

IT ALSO NEEDED A PREFLIGHT, AND NO LONGER DOES HERE (T1701, 2026-09-21). `alembic upgrade head` on a
fresh database dies at 010 -> 011 unless `alembic_version.version_num` is widened to `VARCHAR(128)`
first. That used to be true of a bare command and false of `docker/docker-entrypoint.sh`, and three
copies of the same DDL kept the difference alive. `migrations/env.py` now establishes the precondition
itself, so every caller of the migration entry - this module included - gets it, and the sentence
"a bare command does not" is no longer true of this tree.

WHAT IS NO LONGER HERE (018 stage B1). This module also held the listener journal's asyncpg half of
`T1530`/`T1531` - an entry INSERT rewritten by a neighbour and refused by the journal's readback, and a
full-width movement recorded through that readback. The listener is deleted; the `debts` trigger writes
the entry from `OLD`/`NEW` inside the statement, so no client INSERT of an entry exists to rewrite.
Those effects now live in `tests/integration/test_p018_b_the_record_is_the_stored_row_postgres.py`
(an entry never comes from a client statement; a full-width rewrite is stored and journalled as
stored; full width recorded exactly). The two construction paths of the trigger itself are
`tests/integration/test_p018_b_schema_parity_postgres.py`; this module keeps the CHECK.

THE PROBE GOES THROUGH THE NAMED CORRUPTION HELPER (spec 018 `FORK-4`, "пробы CHECK журнала", manifest
`T1808` section 3 item 4). A direct INSERT into `debt_journal_entries` now meets the guard trigger
first (`BEFORE ... FOR EACH ROW` fires before a CHECK is evaluated), so it would measure the guard and
never the CHECK - asserted below as the premise. With the triggers off, in a transaction that is rolled
back, only the CHECK can answer; an honest row is the control.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.db.base import Base
from app.db.journal_tables import debt_journal_entries
from tests.ledger_corruption import probe
from tests.migrated_schema import run_alembic_upgrade_head, scratch_databases

#: The constraint this slice adds, and the predicate both construction paths must produce.
_CONSTRAINT = "chk_debt_journal_entries_delta_arithmetic"

#: SQLSTATE for a CHECK violation. Asserted rather than matched on prose.
_CHECK_VIOLATION = "23514"

#: SQLSTATE of the journal tables' guard triggers (018 B1, `app/db/journal_triggers.py`).
_GUARD = "23000"


def _postgres_url() -> str:
    """The gate's PostgreSQL URL, or a refusal.

    THE REFUSAL THAT MAKES THIS MODULE POSTGRESQL-ONLY (T1525): every SQLite-capable engine
    construction in this repository must install the transaction control, and a construction that can
    only ever be PostgreSQL is exempt only through a refusal that exists in the code. This is it.
    """

    from tests.conftest import TEST_DATABASE_URL

    if "postgresql" not in TEST_DATABASE_URL:
        pytest.skip(f"this module needs a PostgreSQL TEST_DATABASE_URL, got {TEST_DATABASE_URL!r}")
    return TEST_DATABASE_URL


# =================================================================================================
# The two construction paths
# =================================================================================================


async def _check_constraints(url: str, table: str) -> dict[str, str]:
    """Every CHECK constraint PostgreSQL holds for `table`, by name, with its stored definition."""

    engine = create_async_engine(url)
    try:
        async with engine.connect() as connection:
            rows = (
                await connection.execute(
                    text(
                        "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
                        "WHERE conrelid = :table ::regclass AND contype = 'c'"
                    ),
                    {"table": table},
                )
            ).all()
        return {row[0]: " ".join(row[1].split()) for row in rows}
    finally:
        await engine.dispose()


def _entry(delta: int) -> str:
    """An otherwise-valid `U 10 -> 11` entry with the given delta, as one INSERT statement.

    THE ROW IS OTHERWISE VALID, which is what makes this a test of the arithmetic clause and not of
    the shape clause next to it: effect `U`, both ends present and different, a non-zero bounded
    delta. Its references name no rows: under the helper's `replica` setting the foreign keys are off,
    so a refusal can only be a CHECK - and the honest control row shows no CHECK refuses the shape.
    """

    return (
        "INSERT INTO debt_journal_entries (id, operation_id, ordinal, equivalent_id, debtor_id, "
        "creditor_id, effect, amount_before, amount_after, delta) VALUES "
        f"('{uuid.uuid4()}', '{uuid.uuid4()}', 1, '{uuid.uuid4()}', '{uuid.uuid4()}', "
        f"'{uuid.uuid4()}', 'U', 10, 11, {int(delta)})"
    )


async def _directly_refused_by(url: str, statement: str) -> str | None:
    """The SQLSTATE that refuses `statement` on an ordinary connection (triggers on), rolled back."""

    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                await connection.exec_driver_sql(statement)
            except DBAPIError as exc:
                orig = getattr(exc, "orig", None)
                return getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
            finally:
                await transaction.rollback()
        return None
    finally:
        await engine.dispose()


async def _arithmetic_bites(url: str) -> tuple[str | None, str | None, str | None]:
    """(direct INSERT, contradicting row through the helper, honest row through the helper)."""

    return (
        await _directly_refused_by(url, _entry(2)),
        await probe(url, _entry(2)),
        await probe(url, _entry(1)),
    )


@pytest.mark.asyncio
async def test_t1530_p_the_constraint_exists_and_bites_on_both_construction_paths() -> None:
    """T1530, layer 1. `create_all` and `alembic upgrade head` produce the SAME constraint, and it bites.

    WHAT A GREEN RUN HERE MEANS AND WHAT IT WOULD HAVE MEANT WITHOUT THE TWO DATABASES: the gate's flag
    does not verify that its schema came from the migrations, so a single-database assertion would have
    proved the constraint exists on one path and said nothing about the other. Both are built here,
    from scratch, in this test.

    ASSERTED: the constraint is present under the same NAME and the same stored DEFINITION in both, and
    in both an otherwise-valid entry whose delta contradicts its own ends is refused with SQLSTATE
    23514. The names of the other CHECK constraints are compared as a set too, because a migration that
    stops producing one of them is the same class of defect found one table later.

    MUTATION that must redden this: remove the `op.create_check_constraint` call from migration
    `024_debt_journal_delta` - the migrated database then lacks it while the metadata one has it, which
    is precisely the divergence this test exists for. (The constraint's former
    `.ddl_if(dialect="postgresql")` left with SQLite in 017 stage 3, slice S7; on PostgreSQL it
    was always emitted, so this comparison is unchanged by that.)
    """

    # NOT a skip when the role cannot create databases (T1701). This module used to say, at length,
    # that a missing CREATE DATABASE right made the comparison "an ABSENT measurement, not a passing
    # one" - and then report a pass. `scratch_databases` raises instead: since the PostgreSQL tier
    # provisions its schema template by cloning a database, the right is a precondition of the tier.
    async with scratch_databases(_postgres_url(), "t1530mig", "t1530meta") as (
        migrated_url,
        metadata_url,
    ):
        # PATH 1: the metadata, which is what every SQLite tier and the gate's default path use.
        engine = create_async_engine(metadata_url)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
        finally:
            await engine.dispose()

        # PATH 2: the migrations. The `alembic_version` preflight is no longer spelled here or in
        # `docker/docker-entrypoint.sh`: since T1701 `migrations/env.py` owns it and establishes it
        # inside this very run, so a bare command is no longer a different thing from this one.
        # Raises `MigratedSchemaError` carrying stdout and stderr if the run does not reach head, so
        # the migrated path can never be silently unmeasured.
        run_alembic_upgrade_head(migrated_url)

        migrated = await _check_constraints(migrated_url, debt_journal_entries.name)
        from_metadata = await _check_constraints(metadata_url, debt_journal_entries.name)

        # NON-VACUITY: both paths really built the table.
        assert migrated, "the migrated database has no CHECK constraints on the entries table at all"
        assert from_metadata, "the metadata database has no CHECK constraints on the entries table"

        assert _CONSTRAINT in from_metadata, (
            f"`Base.metadata.create_all` did not produce {_CONSTRAINT}: {sorted(from_metadata)}"
        )
        assert _CONSTRAINT in migrated, (
            f"`alembic upgrade head` did not produce {_CONSTRAINT}: {sorted(migrated)}"
        )
        assert migrated[_CONSTRAINT] == from_metadata[_CONSTRAINT], (
            f"the two construction paths produced DIFFERENT predicates for {_CONSTRAINT}:\n"
            f"  alembic:  {migrated[_CONSTRAINT]}\n  metadata: {from_metadata[_CONSTRAINT]}"
        )
        assert set(migrated) == set(from_metadata), (
            f"the two construction paths disagree on which CHECK constraints the entries table has:\n"
            f"  only in alembic:  {sorted(set(migrated) - set(from_metadata))}\n"
            f"  only in metadata: {sorted(set(from_metadata) - set(migrated))}"
        )

        # AND IT BITES, on both, with the same SQLSTATE.
        for label, scratch in (("metadata", metadata_url), ("alembic", migrated_url)):
            direct, contradicting, honest = await _arithmetic_bites(scratch)
            # PREMISE (018 B1): from an ordinary connection the guard trigger answers first, so a
            # direct probe would measure the guard, not the CHECK.
            assert direct == _GUARD, (
                f"{label}: a direct entry INSERT was answered by {direct!r}, not by the journal "
                f"guard; this probe's reason for going through the corruption helper is gone"
            )
            assert contradicting == _CHECK_VIOLATION, (
                f"{label}: an entry saying `10 -> 11, delta 2` was accepted with the triggers off "
                f"(sqlstate={contradicting!r}). The constraint exists in the catalogue and does not "
                f"refuse, which is worse than its absence because the catalogue then lies."
            )
            assert honest is None, (
                f"{label}: the honest `10 -> 11, delta 1` control was refused ({honest!r}), so the "
                f"refusal above may not be the arithmetic clause"
            )


# =================================================================================================
# The two sources of the constraint's text (moved verbatim from the deleted
# `tests/unit/test_p015_t1530_the_journal_reads_its_own_record_back.py`, 018 B1)
# =================================================================================================


def test_t1530_the_migration_and_the_metadata_spell_the_same_predicate() -> None:
    """T1530, layer 1. The two sources of the constraint cannot drift apart silently.

    NEEDS NO SERVER, WHICH IS THE POINT. The real comparison - what PostgreSQL actually holds after each
    construction path - is the test above and needs a database. This one is the cheap half: the
    predicate text in `app/db/journal_tables.py` and the one in migration `024_debt_journal_delta` are
    compared as strings, so an edit to either that is not made to both is red at once.

    MUTATION that must redden this: change the predicate in one of the two files.
    """

    root = Path(__file__).resolve().parents[2]
    migration = (
        root / "migrations" / "versions" / "024_debt_journal_entries_delta_is_arithmetic.py"
    ).read_text(encoding="utf-8")
    tables = (root / "app" / "db" / "journal_tables.py").read_text(encoding="utf-8")
    predicate = "delta = COALESCE(amount_after, 0) - COALESCE(amount_before, 0)"

    assert predicate in migration, (
        f"migration 024 no longer spells the predicate as `{predicate}`; if it moved, this comparison "
        f"has to move with it rather than be deleted"
    )
    assert predicate in tables, f"`app/db/journal_tables.py` no longer spells `{predicate}`"
    assert "chk_debt_journal_entries_delta_arithmetic" in migration
    assert "chk_debt_journal_entries_delta_arithmetic" in tables
