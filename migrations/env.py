import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from app.config import settings
from app.db.base import Base
from app.db.models import * # noqa

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# target_metadata = None
target_metadata = Base.metadata


# =====================================================================================================
# THE `alembic_version` PRECONDITION LIVES HERE, AND NOWHERE ELSE (T1535 -> T1701, 2026-09-21).
#
# WHY IT IS NEEDED. Alembic 1.13.1 builds its version table with `Column("version_num", String(32))`
# hardcoded in `alembic/runtime/migration.py::MigrationContext.__init__`; the `version_table_column_type`
# option that older notes in this repository referred to DOES NOT EXIST in this version - grepped over
# the installed package on 2026-09-21 and found nowhere. This repository's revision identifiers run to
# 46 characters (`011_transactions_payment_payload_btree_indexes`), so a bare `alembic upgrade head` on
# a fresh database dies at 010 -> 011 with `StringDataRightTruncationError` and rolls the whole run
# back. There is no configuration switch; the column has to be created or widened before the run.
#
# WHY HERE. Until 2026-09-21 the same two effects were spelled three times - `docker/docker-entrypoint.sh`
# as a `DO $$ ... $$` block, `tests/migrated_schema.py` as two idempotent statements, and
# `.github/workflows/quality.yml` as a bare `CREATE TABLE` for one container fixture - and the entry
# that actually needs the precondition, this file, did not have it. Every new caller of
# `alembic upgrade head` had to know the secret or fail at 011. It is now a property of the migration
# entry: whoever runs the migrations gets it, and callers CALL the migrations rather than carrying a
# copy.
#
# FAIL-CLOSED, NOT BEST-EFFORT. If the precondition cannot be established the run aborts with the
# statement and the driver's own error. It is not retried: the connection is already open (Alembic's
# engine made it), the statements are idempotent DDL, and a failure here means a privilege, a lock or a
# dead connection - none of which a retry fixes. `SET LOCAL lock_timeout` bounds the one wait that can
# otherwise hang forever, an `ACCESS EXCLUSIVE` lock on `alembic_version` held by another session.
# =====================================================================================================

#: Wide enough for every revision identifier in this tree, with room to spare.
ALEMBIC_VERSION_COLUMN_LENGTH = 128

#: How long the widening may wait for a lock on `alembic_version` before failing loudly.
ALEMBIC_VERSION_LOCK_TIMEOUT = "30s"

#: Create-or-widen, as two idempotent statements rather than a `DO $$ ... $$` block: dollar quoting
#: through a driver that spells its own placeholders `$1` is a needless risk for the same effect.
ALEMBIC_VERSION_BOOTSTRAP = (
    f"SET LOCAL lock_timeout = '{ALEMBIC_VERSION_LOCK_TIMEOUT}'",
    "CREATE TABLE IF NOT EXISTS alembic_version "
    f"(version_num VARCHAR({ALEMBIC_VERSION_COLUMN_LENGTH}) NOT NULL PRIMARY KEY)",
    "ALTER TABLE alembic_version "
    f"ALTER COLUMN version_num TYPE VARCHAR({ALEMBIC_VERSION_COLUMN_LENGTH})",
)


class AlembicVersionBootstrapError(RuntimeError):
    """The `alembic_version` precondition could not be established. Never swallowed."""


def _require_postgresql_migration_url(database_url: str) -> None:
    backend = make_url(database_url).get_backend_name()
    if backend != "postgresql":
        raise RuntimeError(
            "Alembic migrations support PostgreSQL only. "
            "For a local SQLite database, run: python scripts/init_sqlite_db.py"
        )


async def _bootstrap_alembic_version(connection) -> None:
    """Create or widen `alembic_version.version_num` on `connection`, and commit it.

    Committed separately from the migration run on purpose: the entrypoint's block did the same on
    its own connection, and a precondition that rolls back with a failed migration would have to be
    re-established by the next attempt anyway.
    """

    async def discard_transaction() -> None:
        # A rollback on a connection that has just died raises in turn and would mask the real
        # cause, which is the only thing the caller needs.
        try:
            await connection.rollback()
        except Exception:  # noqa: BLE001 - deliberately subordinate to the error being raised
            pass

    for statement in ALEMBIC_VERSION_BOOTSTRAP:
        try:
            await connection.exec_driver_sql(statement)
        except (OperationalError, InterfaceError) as exc:
            # The connection or the server, not the statement: nothing ran, and saying so is the
            # difference between "fix your database" and "fix your privileges".
            await discard_transaction()
            raise AlembicVersionBootstrapError(
                f"the `alembic_version` precondition could not be established because the database "
                f"connection failed while running {statement!r}. NO MIGRATION WAS RUN. "
                f"Underlying error: {exc}"
            ) from exc
        except DBAPIError as exc:
            # The server refused the statement: a privilege, a lock timeout, an incompatible column.
            await discard_transaction()
            raise AlembicVersionBootstrapError(
                f"the `alembic_version` precondition was REFUSED while running {statement!r}, so "
                f"`alembic upgrade head` would die at 010 -> 011 on a fresh database and NO "
                f"MIGRATION WAS RUN. The role needs to be able to create and alter "
                f"`alembic_version`, and the widening needs a lock on it "
                f"(waited {ALEMBIC_VERSION_LOCK_TIMEOUT}). Underlying error: {exc}"
            ) from exc
    await connection.commit()


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    OFFLINE MODE CANNOT ESTABLISH THE PRECONDITION, and that is measured rather than assumed
    (2026-09-21, T1701). There is no connection to create or widen anything on, and emitting the DDL
    into the script does not help: Alembic then emits its OWN
    `CREATE TABLE alembic_version (version_num VARCHAR(32) ...)` - without `IF NOT EXISTS`, because
    offline mode has no catalogue to check - and the generated script dies on the duplicate. Tried,
    read, reverted. So this path is left exactly as it was: the generated SQL carries Alembic's
    32-character column and stops at 010 -> 011 when applied to a fresh database. Whoever applies it
    runs the statements in `ALEMBIC_VERSION_BOOTSTRAP` first, by hand - they are printed for that
    purpose in `docs/ru/05-deployment.md`. Nothing in this repository generates offline SQL today;
    the online path above is the one every caller uses.
    """
    url = settings.DATABASE_URL
    _require_postgresql_migration_url(url)
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    database_url = settings.DATABASE_URL
    _require_postgresql_migration_url(database_url)
    configuration = config.get_section(config.config_ini_section)
    configuration["sqlalchemy.url"] = database_url

    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    try:
        async with connectable.connect() as connection:
            await _bootstrap_alembic_version(connection)
            await connection.run_sync(do_run_migrations)
    finally:
        await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
