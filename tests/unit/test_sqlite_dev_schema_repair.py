"""RT-009-9: a SQLite database made before migration 019 must not keep the old invariant.

Program 009, follow-up to `F-009-3` (`B-A3-004`, P1).  Found by an independent scan of the
merged wave.

Migration 019 narrowed uniqueness to live rows, and that is what closed the P1.  But
Alembic refuses to run on anything other than PostgreSQL (`migrations/env.py:26-30`, pinned
by `tests/unit/test_alembic_postgres_only.py`), and the SQLite development database is
built by `scripts/init_sqlite_db.py` through `Base.metadata.create_all`, which is
`checkfirst=True` and therefore does not touch a table that already exists.  A `local.db`
created before the wave keeps the old unconditional
`UNIQUE(from_participant_id, to_participant_id, equivalent_id)` forever.

What the developer sees on such a database is worse than a crash.  create -> close ->
create passes the service guard (`status != 'closed'`), the INSERT then trips the OLD
constraint, and SQLite's message names all three columns -- so
`_is_live_trustline_uniqueness_violation` classifies it as a live-triple clash and answers
`409 Active trustline already exists` for a pair that has no active line and can never have
one again.  The P1 is not closed there; it changed disguise.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from scripts.init_sqlite_db import (
    LIVE_TRUSTLINE_INDEX,
    OLD_TRUSTLINE_UNIQUE_CONSTRAINT,
    repair_stale_trustline_uniqueness,
)


# The table exactly as the model declared it before the wave: a table-level UNIQUE, which
# in SQLite is part of the CREATE TABLE statement and cannot be dropped by any ALTER.
_PRE_019_DDL = f"""
CREATE TABLE trust_lines (
    id CHAR(32) NOT NULL,
    from_participant_id CHAR(32) NOT NULL,
    to_participant_id CHAR(32) NOT NULL,
    equivalent_id CHAR(32) NOT NULL,
    "limit" NUMERIC(20, 8) NOT NULL,
    policy JSON,
    status VARCHAR(20) NOT NULL,
    created_at DATETIME NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    updated_at DATETIME NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    PRIMARY KEY (id),
    CONSTRAINT {OLD_TRUSTLINE_UNIQUE_CONSTRAINT} UNIQUE (from_participant_id, to_participant_id, equivalent_id),
    CONSTRAINT chk_trust_line_status CHECK (status IN ('active', 'frozen', 'closed')),
    CONSTRAINT chk_trust_line_limit_positive CHECK ("limit" >= 0)
)
"""

# The pre-019 schema also carried the per-column indexes the model declares; the repair
# has to bring every one of them back, not only the composite one.
_PRE_019_INDEXES = (
    "CREATE INDEX ix_trust_lines_from_status ON trust_lines (from_participant_id, status)",
    "CREATE INDEX ix_trust_lines_from_participant_id ON trust_lines (from_participant_id)",
    "CREATE INDEX ix_trust_lines_to_participant_id ON trust_lines (to_participant_id)",
    "CREATE INDEX ix_trust_lines_equivalent_id ON trust_lines (equivalent_id)",
    "CREATE INDEX ix_trust_lines_status ON trust_lines (status)",
)


def _stale_db(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'stale.db'}")
    with engine.begin() as conn:
        conn.exec_driver_sql(_PRE_019_DDL)
        for stmt in _PRE_019_INDEXES:
            conn.exec_driver_sql(stmt)
        conn.exec_driver_sql(
            "INSERT INTO trust_lines "
            "(id, from_participant_id, to_participant_id, equivalent_id, \"limit\", "
            "policy, status) "
            "VALUES ('aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', 'ffffffffffffffffffffffffffffffff', 'tttttttttttttttttttttttttttttttt', 'eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee', 100, '{}', 'closed')"
        )
    return engine


def _table_ddl(conn) -> str:
    row = conn.exec_driver_sql(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='trust_lines'"
    ).fetchone()
    return str(row[0]) if row and row[0] else ""


def _index_names(conn) -> set[str]:
    return {
        str(r[0])
        for r in conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='trust_lines'"
        ).fetchall()
    }


def test_a_stale_database_cannot_reopen_a_closed_trustline(tmp_path: Path) -> None:
    """The defect itself, stated as a database fact rather than as an HTTP symptom."""

    engine = _stale_db(tmp_path)
    with engine.begin() as conn:
        with pytest.raises(Exception) as excinfo:
            conn.exec_driver_sql(
                "INSERT INTO trust_lines "
                "(id, from_participant_id, to_participant_id, equivalent_id, \"limit\", "
                "policy, status) "
                "VALUES ('bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb', 'ffffffffffffffffffffffffffffffff', 'tttttttttttttttttttttttttttttttt', 'eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee', 100, '{}', 'active')"
            )
    # And this is why the API answers 409 rather than 500: the message names all three
    # columns, so the conflict classifier reads it as a live-triple clash.
    assert "unique" in str(excinfo.value).lower()
    assert "from_participant_id" in str(excinfo.value)


def test_repair_replaces_the_old_constraint_with_the_live_only_index(tmp_path: Path) -> None:
    engine = _stale_db(tmp_path)

    with engine.begin() as conn:
        assert OLD_TRUSTLINE_UNIQUE_CONSTRAINT in _table_ddl(conn)
        repaired = repair_stale_trustline_uniqueness(conn)

    assert repaired is True

    with engine.begin() as conn:
        ddl = _table_ddl(conn)
        assert OLD_TRUSTLINE_UNIQUE_CONSTRAINT not in ddl, ddl
        indexes = _index_names(conn)
        assert LIVE_TRUSTLINE_INDEX in indexes, indexes
        # Every neighbouring index the old schema carried must come back, not vanish.
        for name in (
            "ix_trust_lines_from_status",
            "ix_trust_lines_from_participant_id",
            "ix_trust_lines_to_participant_id",
            "ix_trust_lines_equivalent_id",
            "ix_trust_lines_status",
        ):
            assert name in indexes, (name, indexes)


def test_repair_keeps_the_rows_and_unblocks_reopening(tmp_path: Path) -> None:
    engine = _stale_db(tmp_path)
    with engine.begin() as conn:
        repair_stale_trustline_uniqueness(conn)

    with engine.begin() as conn:
        rows = conn.execute(text("SELECT id, status FROM trust_lines")).fetchall()
        assert len(rows) == 1, rows
        assert rows[0][1] == "closed"

        # The whole point: a live line for the same triple is possible again.
        conn.exec_driver_sql(
            "INSERT INTO trust_lines "
            "(id, from_participant_id, to_participant_id, equivalent_id, \"limit\", "
            "policy, status) "
            "VALUES ('bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb', 'ffffffffffffffffffffffffffffffff', 'tttttttttttttttttttttttttttttttt', 'eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee', 100, '{}', 'active')"
        )

        # ...but only one of them.
        with pytest.raises(Exception):
            conn.exec_driver_sql(
                "INSERT INTO trust_lines "
                "(id, from_participant_id, to_participant_id, equivalent_id, \"limit\", "
                "policy, status) "
                "VALUES ('cccccccccccccccccccccccccccccccc', 'ffffffffffffffffffffffffffffffff', 'tttttttttttttttttttttttttttttttt', 'eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee', 100, '{}', 'active')"
            )


def test_repair_is_a_no_op_on_a_current_schema(tmp_path: Path) -> None:
    """Running it twice must not rebuild the table again."""

    engine = _stale_db(tmp_path)
    with engine.begin() as conn:
        assert repair_stale_trustline_uniqueness(conn) is True
    with engine.begin() as conn:
        assert repair_stale_trustline_uniqueness(conn) is False
