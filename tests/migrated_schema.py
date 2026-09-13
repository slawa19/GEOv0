"""Building a PostgreSQL schema the way a deployment builds it: ONE definition (T1534).

WHY THIS MODULE EXISTS. `GEO_TEST_USE_MIGRATED_SCHEMA=1` claimed that the tier ran on the schema the
migrations produce, and until 2026-09-13 it verified none of that: `tests/conftest.py` read
`SELECT version_num FROM alembic_version` and returned. Measured on this tree that day, the flag
accepted a database whose tables had been built by `Base.metadata.create_all` - 87 constraints and 82
indexes where the migrated form has 90 and 93, with `chk_equivalents_code_format` absent entirely.

AND THE STAMP WAS EXACTLY THE REPOSITORY HEAD WHILE THAT WAS TRUE, so comparing the stamp with head
would have accepted it too. The reason is not an operator slip: `alembic_version` is not in
`Base.metadata`, so the flag-OFF path's `drop_all` + `create_all` replaces every application table and
LEAVES THE STAMP STANDING. One ordinary run of the same conftest without the flag turns a migrated
database into a `create_all` database that still reads as migrated. That is why the flag is now made
TRUE BY CONSTRUCTION - the schema is built here, by the migrations, on every run with the flag up -
rather than checked: checking provenance needs a migrated database to compare against, which costs an
entire `alembic upgrade head` anyway.

THE BOOTSTRAP PRECONDITION IS T1535's AND IS USED, NOT SOLVED, HERE. `migrations/env.py` does not set
`version_table_column_type`, so Alembic would create `alembic_version.version_num` as `VARCHAR(32)`
while this repository's revision identifiers run to 46 characters
(`011_transactions_payment_payload_btree_indexes`). A bare `alembic upgrade head` on a fresh database
therefore dies at 010 -> 011 with `StringDataRightTruncationError` and rolls the whole run back. The
only place that prevents it today is `docker/docker-entrypoint.sh`, which creates-or-widens the table
first. `ALEMBIC_VERSION_BOOTSTRAP` below is that same preconditioning, and it is a WORKAROUND standing
in for a canonical fix that belongs in `migrations/env.py` under T1535.

A SUBPROCESS, and that is a fact about the tree rather than a preference: `migrations/env.py` ends in
`asyncio.run(...)` at import, so it cannot be invoked from inside a running event loop, and both
callers here are async.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Create-or-widen `alembic_version.version_num`, the T1535 precondition, as two idempotent
#: statements rather than the entrypoint's `DO $$ ... $$` block - dollar quoting through a driver
#: that spells its own placeholders `$1` is a needless risk for the same effect.
ALEMBIC_VERSION_BOOTSTRAP = (
    "CREATE TABLE IF NOT EXISTS alembic_version "
    "(version_num VARCHAR(128) NOT NULL PRIMARY KEY)",
    "ALTER TABLE alembic_version ALTER COLUMN version_num TYPE VARCHAR(128)",
)


class MigratedSchemaError(RuntimeError):
    """A migrated schema was asked for and could not be produced. Never swallowed."""


def repository_head() -> str:
    """The single Alembic head of this working tree.

    Refuses on anything but exactly one head for the same reason
    `scripts/check_alembic_heads.py` does: with two heads there is no single answer to "is this
    database current", and returning one of them would make the comparison below meaningless.
    """

    config = Config()
    config.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    heads = ScriptDirectory.from_config(config).get_heads()
    if len(heads) != 1:
        rendered = ", ".join(heads) if heads else "<none>"
        raise MigratedSchemaError(
            f"the migration graph does not have exactly one head ({len(heads)}: {rendered}), so "
            f"'the schema is at head' has no single meaning. Run scripts/check_alembic_heads.py."
        )
    return heads[0]


def run_alembic_upgrade_head(database_url: str, *, timeout: float = 600) -> str:
    """Run `alembic -c migrations/alembic.ini upgrade head` against `database_url`.

    The database must already carry the `ALEMBIC_VERSION_BOOTSTRAP` preconditioning. Returns the
    run's combined output; raises `MigratedSchemaError` carrying stdout AND stderr on any non-zero
    exit, because a migration run that failed halfway leaves a database that looks built.

    `cwd=REPO_ROOT` and not the process's working directory: `migrations/alembic.ini` carries
    `script_location = migrations` and `prepend_sys_path = .`, both relative.
    """

    environment = dict(os.environ, DATABASE_URL=database_url)
    completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [sys.executable, "-m", "alembic", "-c", "migrations/alembic.ini", "upgrade", "head"],
        capture_output=True,
        text=True,
        env=environment,
        cwd=str(REPO_ROOT),
        timeout=timeout,
    )
    if completed.returncode != 0:
        raise MigratedSchemaError(
            f"`alembic upgrade head` exited {completed.returncode}, so the migrated schema was "
            f"NEVER BUILT and nothing measured against this database describes the migrated form."
            f"\nstdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
    return completed.stdout + completed.stderr
