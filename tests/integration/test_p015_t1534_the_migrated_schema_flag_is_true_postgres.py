"""T1534: what `GEO_TEST_USE_MIGRATED_SCHEMA=1` claims, asserted against the live catalogue.

THE DEFECT THIS HOLDS SHUT. Until 2026-09-13 the flag's entire body was
`SELECT version_num FROM alembic_version`. It built no schema, compared the stamp with nothing, and
therefore reported "migrated" for whatever the database happened to contain. Measured that day: the
PostgreSQL gate ran against a database stamped two revisions behind the tree and missing the money
constraint `chk_debt_journal_entries_delta_arithmetic` altogether, and it went red ONLY because new
tests happened to need that constraint. Without them, "green on PostgreSQL" would have meant "green on
a schema production never sees".

WHY BOTH HALVES ARE HERE, and why the first is not enough on its own. Comparing the stamp with the
repository head catches STALENESS and says nothing about PROVENANCE, and on this tree provenance rots
through an ordinary path rather than an exotic one: `alembic_version` is not part of `Base.metadata`,
so one flag-OFF run of the conftest replaces every application table with `create_all`'s and LEAVES THE
STAMP AT HEAD. Reproduced on `geov0_test_t1534`, 2026-09-13: stamp `024_debt_journal_delta` - exactly
head - over a `create_all` schema carrying 87 constraints and 82 indexes where the migrated form has 90
and 93, with `chk_equivalents_code_format` missing entirely. A stamp test alone passes on that database.

HOW PROVENANCE IS READ WITHOUT A SECOND DATABASE. `Base` declares no naming convention, so for a table
whose primary key carries no explicit name in the metadata, `create_all` gets PostgreSQL's default
`<table>_pkey`, while the migrations that name their constraints produce `pk_<table>`. The witness is
therefore computed FROM THE METADATA rather than from a list of names written here: any table whose
live primary key differs from the name `create_all` would have produced cannot have been built by
`create_all`. Measured witnesses on this tree: `debt_operations`, `debt_journal_entries` and
`debt_operation_equivalents`, named by migration 022.

WHAT THIS DOES NOT SEE, so its silence is not read as more than it is. It reads the primary-key
NAMES, so it would not notice a migration and the metadata disagreeing about a column, an index or a
CHECK - that comparison needs both databases at once and lives in
`tests/integration/test_p015_t1530_delta_arithmetic_postgres.py`. It also cannot distinguish "built by
`create_all`" from "the migrations stopped naming any primary key": both leave no witness, and both are
red here, because in either case this module can no longer certify what the flag claims.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import Base
from tests.migrated_schema import repository_head

_LIVE_PRIMARY_KEYS = text(
    "SELECT c.conrelid::regclass::text, c.conname "
    "FROM pg_constraint c "
    "JOIN pg_class t ON t.oid = c.conrelid "
    "JOIN pg_namespace n ON n.oid = t.relnamespace "
    "WHERE c.contype = 'p' AND n.nspname = 'public'"
)


def _create_all_primary_key_names() -> dict[str, str]:
    """For each metadata table with an UNNAMED primary key, the name `create_all` would produce.

    An unnamed `PrimaryKeyConstraint` is emitted without a `CONSTRAINT` clause, and PostgreSQL then
    assigns `<table>_pkey`. A table whose metadata DOES name its primary key is excluded: both paths
    would then produce the same name and it can witness nothing.
    """

    return {
        name: f"{name}_pkey"
        for name, table in Base.metadata.tables.items()
        if table.primary_key is not None
        and table.primary_key.name is None
        and len(table.primary_key.columns) > 0
    }


@pytest.mark.asyncio
async def test_t1534_the_gate_schema_is_stamped_at_the_repository_head(
    db_session: AsyncSession,
) -> None:
    """The database this tier runs on carries exactly one stamp, and it is this tree's head.

    ASSERTED: `alembic_version` holds exactly one row, and it equals the single head of
    `migrations/`. A database two revisions behind - the state measured on `geov0_test_ci` on
    2026-09-13 - is red here whether or not any test happens to need what the missing revisions add.

    MUTATION that must redden this: in `tests/migrated_schema.py::run_alembic_upgrade_head`, replace
    the argument `"head"` with an earlier revision such as `"023_debt_journal_counts"`. The schema is
    then still migration-built, so the provenance test below stays green and only this one falls -
    which is the point of keeping the two apart.
    """

    head = repository_head()
    stamps = (await db_session.execute(text("SELECT version_num FROM alembic_version"))).scalars().all()

    assert stamps == [head], (
        f"the tier's database is stamped {stamps!r}, the repository head is [{head!r}]. Every "
        f"measurement taken on this database describes a schema that is not this tree's."
    )


@pytest.mark.asyncio
async def test_t1534_the_gate_schema_was_built_by_the_migrations_not_by_create_all(
    db_session: AsyncSession,
) -> None:
    """The schema's provenance is the migrations, read off the catalogue rather than off the stamp.

    ASSERTED: at least one table whose metadata leaves its primary key unnamed carries a primary key
    name that `Base.metadata.create_all` could not have produced. The comparison set is built from
    the metadata, so it follows the tree instead of a list of names frozen here.

    ANTI-VACUUM, both directions: the run fails if no metadata table's primary key could be found in
    the catalogue at all (nothing was compared), and it fails if the witness set is empty - which is
    either a `create_all` schema or a tree in which the migrations no longer name any primary key.
    This module cannot tell those apart and must not pass under either.

    MUTATION that must redden this: in `tests/conftest.py::_build_migrated_schema`, replace the
    `run_alembic_upgrade_head(...)` call with `Base.metadata.create_all` followed by
    `alembic stamp head`. That is precisely the state T1534 was raised for, the stamp test above
    stays GREEN on it, and only this one falls.
    """

    expected_from_create_all = _create_all_primary_key_names()
    live = {
        table: name
        for table, name in (
            await db_session.execute(_LIVE_PRIMARY_KEYS)
        ).all()
    }

    comparable = {
        table: expected_from_create_all[table]
        for table in expected_from_create_all
        if table in live
    }
    assert comparable, (
        f"not one of the {len(expected_from_create_all)} metadata tables with an unnamed primary key "
        f"was found in `public` ({sorted(live)}). NOTHING WAS COMPARED, so this is an absent "
        f"measurement and not a passing one."
    )

    witnesses = {
        table: (live[table], default)
        for table, default in comparable.items()
        if live[table] != default
    }
    assert witnesses, (
        "every primary key in this database carries the name `create_all` would have produced, so "
        "the schema shows no evidence of having been built by the migrations. Either "
        "GEO_TEST_USE_MIGRATED_SCHEMA=1 did not build it (the T1534 defect) or the migrations have "
        "stopped naming their primary keys, in which case this check can no longer tell the two "
        "construction paths apart and needs a different witness. Compared: "
        f"{sorted(comparable)}"
    )
