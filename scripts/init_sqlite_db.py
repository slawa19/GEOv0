import asyncio
import os
import sys

# Add repo root to import path (so `import app` works when run as a script)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.models import Base  # noqa: F401  (ensures models are registered)
from app.db.session import engine

# 2026-08-22 / p009.  Migration 019 narrowed trust-line uniqueness to live rows, but
# Alembic refuses to run on anything but PostgreSQL (`migrations/env.py:26-30`), and
# `create_all` is checkfirst -- it never alters a table that already exists.  A SQLite
# database created before the wave therefore keeps the old unconditional constraint
# forever, and on it the P1 that program 009 closed is not closed: create -> close ->
# create passes the service guard, trips the OLD constraint, and the conflict classifier
# reads SQLite's three-column message as a live-triple clash, so the user is told
# "Active trustline already exists" about a pair that has none and can never have one.
#
# In SQLite the old constraint is part of the CREATE TABLE statement, so no ALTER can drop
# it; the table has to be rebuilt.  The copy is always safe because the old constraint was
# strictly stronger than the new index -- any dataset that satisfied it satisfies the
# partial one.  That is the same argument migration 019 makes for PostgreSQL.
OLD_TRUSTLINE_UNIQUE_CONSTRAINT = "uq_trust_lines_from_to_equivalent"
LIVE_TRUSTLINE_INDEX = "uq_trust_lines_live_from_to_equivalent"
_BACKUP_TABLE = "_trust_lines_pre019"


def repair_stale_trustline_uniqueness(conn) -> bool:
    """Rebuild `trust_lines` if it still carries the pre-019 unconditional UNIQUE.

    Takes a *sync* SQLAlchemy connection.  Returns True when a rebuild happened, False
    when the schema is already current (or the table does not exist yet).
    """

    row = conn.exec_driver_sql(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='trust_lines'"
    ).fetchone()
    ddl = str(row[0]) if row and row[0] else ""
    if not ddl or OLD_TRUSTLINE_UNIQUE_CONSTRAINT not in ddl:
        return False

    table = Base.metadata.tables["trust_lines"]
    old_columns = [str(r[1]) for r in conn.exec_driver_sql("PRAGMA table_info(trust_lines)")]
    shared = [c.name for c in table.columns if c.name in old_columns]
    if not shared:  # pragma: no cover - a trust_lines with no known column is not ours
        raise RuntimeError(
            "trust_lines carries the pre-019 constraint but shares no column with the "
            "model; refusing to rebuild a table this script does not recognise"
        )

    conn.exec_driver_sql(f"ALTER TABLE trust_lines RENAME TO {_BACKUP_TABLE}")
    # The old indexes followed the table through the rename and still hold their names, so
    # they would collide with the ones `create()` is about to make.
    for (name,) in conn.exec_driver_sql(
        "SELECT name FROM sqlite_master "
        f"WHERE type='index' AND tbl_name='{_BACKUP_TABLE}' AND sql IS NOT NULL"
    ).fetchall():
        conn.exec_driver_sql(f'DROP INDEX IF EXISTS "{name}"')

    table.create(bind=conn)

    collist = ", ".join(f'"{c}"' for c in shared)
    conn.exec_driver_sql(
        f"INSERT INTO trust_lines ({collist}) SELECT {collist} FROM {_BACKUP_TABLE}"
    )
    conn.exec_driver_sql(f"DROP TABLE {_BACKUP_TABLE}")
    return True


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # After create_all, so a brand-new database skips it on the first check.
        rebuilt = await conn.run_sync(repair_stale_trustline_uniqueness)

    if rebuilt:
        print(
            "trust_lines rebuilt: the pre-019 unconditional UNIQUE was replaced by the "
            f"live-only index {LIVE_TRUSTLINE_INDEX}; rows were preserved."
        )

    await engine.dispose()


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    asyncio.run(init_db())
