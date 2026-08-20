"""Migration 018 executed for real, on a disposable schema (spec 007 / T715).

Prerequisites: a disposable PostgreSQL test database
(``TEST_DATABASE_URL=postgresql+asyncpg://.../geov0_test_*``) and
``GEO_TEST_ALLOW_DB_RESET=1``.

Why this file exists: the sibling module asserts the *model* - the schema the
test suite builds with ``Base.metadata.create_all`` - which says nothing about
whether the migration produces it. Here the revision's own ``upgrade()`` and
``downgrade()`` run against a real PostgreSQL connection whose ``search_path``
points at a throwaway schema, so the DDL and the data movement are the ones
that would run in production.

Covered:

* the column really becomes ``numeric(20, 8)`` *because of the migration*;
* every live row lands in the archive and the live table is emptied;
* ``NULL`` ("not measured") stays ``NULL`` and never becomes a string;
* the archive survives a hostile ``extra_float_digits``: every archived string
  casts back to the identical ``float8``, including values the session GUC
  would otherwise truncate;
* without the pin the round-trip check aborts the migration instead of
  deleting the live rows;
* ``downgrade()`` restores the column type and keeps the archive.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from alembic.migration import MigrationContext
from alembic.operations import Operations


pytestmark = pytest.mark.postgres


PROBE_SCHEMA = "t715_migration_probe"

# Values whose text form is destroyed by `extra_float_digits = 0`, which is the
# PostgreSQL default before v12 and a common pooler setting:
#   0.30000000000000004 -> "0.3"
#   12345678901.123457  -> "12345678901.1235"
_GUC_SENSITIVE = ("0.30000000000000004", "12345678901.123457")
_GUC_TRUNCATED = ("0.3", "12345678901.1235")

_ROWS = [
    ("run-a", "UAH", "total_debt", 1_000, "0.30000000000000004"),
    ("run-a", "UAH", "total_debt", 2_000, "12345678901.123457"),
    ("run-a", "UAH", "clearing_volume", 1_000, "0"),
    ("run-a", "UAH", "avg_route_length", 1_000, None),
    ("run-b", "USD", "success_rate", 5_000, "66.66666666666667"),
    ("run-b", "USD", "bottlenecks_score", 5_000, "-0.5"),
]

_PRE_018_TABLE = """
CREATE TABLE simulator_run_metrics (
  run_id VARCHAR(64) NOT NULL,
  equivalent_code VARCHAR(50) NOT NULL,
  key VARCHAR(50) NOT NULL,
  t_ms INTEGER NOT NULL,
  value DOUBLE PRECISION NULL,
  PRIMARY KEY (run_id, equivalent_code, key, t_ms),
  CONSTRAINT chk_simulator_run_metrics_t_ms CHECK (t_ms >= 0)
)
"""

_INSERT_ROW = (
    "INSERT INTO simulator_run_metrics "
    "(run_id, equivalent_code, key, t_ms, value) "
    "VALUES (:run_id, :eq, :key, :t_ms, CAST(:value AS double precision))"
)


def _load_revision() -> Any:
    path = (
        Path(__file__).resolve().parents[2]
        / "migrations"
        / "versions"
        / "018_simulator_run_metrics_numeric_value.py"
    )
    spec = importlib.util.spec_from_file_location("t715_revision_018", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_revision(sync_connection: Any, step: str) -> None:
    """Execute the revision's own upgrade()/downgrade() on this connection."""

    module = _load_revision()
    context = MigrationContext.configure(sync_connection)
    with Operations.context(context):
        getattr(module, step)()


def _upgrade_without_the_pin(sync_connection: Any) -> None:
    """Same revision, with only the extra_float_digits pin suppressed."""

    module = _load_revision()
    context = MigrationContext.configure(sync_connection)
    with Operations.context(context) as operations:
        real_execute = operations.execute

        def _execute(sql: Any, *args: Any, **kwargs: Any) -> Any:
            if "extra_float_digits" in str(sql):
                return None
            return real_execute(sql, *args, **kwargs)

        operations.execute = _execute  # type: ignore[method-assign]
        module.upgrade()


@pytest.fixture
async def probe_engine():
    """Engine bound to a throwaway schema, created and dropped per test."""

    url = os.environ.get("TEST_DATABASE_URL", "")
    if "postgresql" not in url:
        pytest.skip("PostgreSQL TEST_DATABASE_URL required")

    async def _reset_schema(create: bool) -> None:
        admin = create_async_engine(url)
        try:
            async with admin.begin() as conn:
                await conn.execute(
                    text(f"DROP SCHEMA IF EXISTS {PROBE_SCHEMA} CASCADE")
                )
                if create:
                    await conn.execute(text(f"CREATE SCHEMA {PROBE_SCHEMA}"))
        finally:
            await admin.dispose()

    await _reset_schema(create=True)

    engine = create_async_engine(
        url,
        connect_args={"server_settings": {"search_path": PROBE_SCHEMA}},
    )
    try:
        yield engine
    finally:
        await engine.dispose()
        await _reset_schema(create=False)


async def _column_type(conn: Any) -> tuple[str, Any, Any]:
    row = (
        await conn.execute(
            text(
                "SELECT data_type, numeric_precision, numeric_scale "
                "FROM information_schema.columns "
                "WHERE table_schema = :schema "
                "AND table_name = 'simulator_run_metrics' "
                "AND column_name = 'value'"
            ),
            {"schema": PROBE_SCHEMA},
        )
    ).one()
    return str(row[0]), row[1], row[2]


async def _seed_pre_018(conn: Any, rows: list[tuple[Any, ...]]) -> None:
    await conn.execute(text(_PRE_018_TABLE))
    for run_id, eq, key, t_ms, value in rows:
        # Bound as a real float8: Python parses and prints these literals
        # round-trippably, so the stored double is exactly the one named above.
        await conn.execute(
            text(_INSERT_ROW),
            {
                "run_id": run_id,
                "eq": eq,
                "key": key,
                "t_ms": t_ms,
                "value": None if value is None else float(value),
            },
        )


async def test_migration_018_archives_and_retypes_under_hostile_float_digits(
    probe_engine,
) -> None:
    async with probe_engine.connect() as conn:
        # The hostile setting, applied to this session the way a server default
        # or a connection pooler would apply it.
        await conn.execute(text("SET extra_float_digits = 0"))
        assert (await conn.execute(text("SHOW extra_float_digits"))).scalar_one() == "0"

        await _seed_pre_018(conn, _ROWS)
        await conn.commit()

        assert (await _column_type(conn))[0] == "double precision"

        # Anti-vacuum: prove the hostile GUC really destroys the text form here.
        # Without this the assertions below could pass on a harmless setting.
        truncated = {
            str(row[0])
            for row in (
                await conn.execute(
                    text(
                        "SELECT CAST(value AS TEXT) FROM simulator_run_metrics "
                        "WHERE value IS NOT NULL"
                    )
                )
            ).all()
        }
        for lost in _GUC_TRUNCATED:
            assert lost in truncated
        for exact in _GUC_SENSITIVE:
            assert exact not in truncated
        await conn.commit()

        # --- the migration itself ---
        await conn.run_sync(_run_revision, "upgrade")
        await conn.commit()

        # The type moved because of the migration, not because of a model.
        assert await _column_type(conn) == ("numeric", 20, 8)

        live = (
            await conn.execute(text("SELECT count(*) FROM simulator_run_metrics"))
        ).scalar_one()
        assert live == 0

        archived = (
            await conn.execute(
                text(
                    "SELECT run_id, equivalent_code, key, t_ms, value_text "
                    "FROM simulator_run_metrics_float_archive ORDER BY id"
                )
            )
        ).all()
        assert [
            (str(r[0]), str(r[1]), str(r[2]), int(r[3])) for r in archived
        ] == [(r[0], r[1], r[2], r[3]) for r in _ROWS]

        # "Not measured" stayed NULL: it did not become "None" or a zero.
        by_key = {(str(r[2]), int(r[3])): r[4] for r in archived}
        assert by_key[("avg_route_length", 1_000)] is None
        assert by_key[("clearing_volume", 1_000)] == "0"

        # The pin defeated the hostile GUC: full digits survived...
        texts = {str(r[4]) for r in archived if r[4] is not None}
        for exact in _GUC_SENSITIVE:
            assert exact in texts
        for lost in _GUC_TRUNCATED:
            assert lost not in texts

        # ...and every archived string parses back to the identical float8.
        # Checked client-side on purpose: a SQL round-trip would render the
        # result through the same hostile GUC and prove nothing.
        expected = {
            (r[0], r[2], r[3]): (None if r[4] is None else float(r[4])) for r in _ROWS
        }
        for row in archived:
            identity = (str(row[0]), str(row[2]), int(row[3]))
            archived_value = None if row[4] is None else float(str(row[4]))
            assert archived_value == expected[identity], identity

        # The retyped column accepts a value float8 could not hold.
        await conn.execute(
            text(
                "INSERT INTO simulator_run_metrics "
                "(run_id, equivalent_code, key, t_ms, value) "
                "VALUES ('run-new', 'UAH', 'total_debt', 1, 12345678901.12345678)"
            )
        )
        stored = (
            await conn.execute(
                text(
                    "SELECT value FROM simulator_run_metrics WHERE run_id = 'run-new'"
                )
            )
        ).scalar_one()
        assert str(stored) == "12345678901.12345678"
        await conn.commit()

        # --- downgrade: type back, archive kept ---
        await conn.run_sync(_run_revision, "downgrade")
        await conn.commit()

        assert (await _column_type(conn))[0] == "double precision"
        kept = (
            await conn.execute(
                text("SELECT count(*) FROM simulator_run_metrics_float_archive")
            )
        ).scalar_one()
        assert kept == len(_ROWS), "downgrade must not destroy the only copy"


async def test_migration_018_aborts_instead_of_losing_digits(probe_engine) -> None:
    """The round-trip check is a gate, not a log line.

    The pin is suppressed here so the verification has something to catch.
    Without the gate the live rows would already be deleted by the time anyone
    noticed the archived text had lost digits - and they are the only copy.
    """

    async with probe_engine.connect() as conn:
        await conn.execute(text("SET extra_float_digits = 0"))
        await _seed_pre_018(conn, _ROWS[:1])
        await conn.commit()

        with pytest.raises(RuntimeError, match="round-trippable text"):
            await conn.run_sync(_upgrade_without_the_pin)

        await conn.rollback()

        # The live row is still there: the gate refused to delete it.
        surviving = (
            await conn.execute(text("SELECT count(*) FROM simulator_run_metrics"))
        ).scalar_one()
        assert surviving == 1
        assert (await _column_type(conn))[0] == "double precision"
