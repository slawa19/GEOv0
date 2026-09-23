"""Building a PostgreSQL schema the way a deployment builds it: ONE definition (T1534, T1701).

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

THE BOOTSTRAP PRECONDITION HAS MOVED TO ITS OWNER AND IS NO LONGER SPELLED HERE (T1701, 2026-09-21).
`ALEMBIC_VERSION_BOOTSTRAP` used to live in this file as a third copy of the same two statements, next
to `docker/docker-entrypoint.sh` and one line of `.github/workflows/quality.yml`, while the migration
entry itself did not have it. `migrations/env.py` now creates-or-widens `alembic_version.version_num`
before running anything, so every caller of `alembic upgrade head` - this module, the conftest, the
container entrypoint, a launcher - gets the precondition by CALLING the migrations. Nothing here
imports from `migrations/` and nothing in `migrations/` imports from `tests/`: the dependency runs one
way. A caller that still wants the statements themselves reads them from `migrations/env.py`.

The earlier note in this docstring said the canonical fix was for `migrations/env.py` to set
`version_table_column_type`. THAT PREMISE IS WRONG for the pinned Alembic: 1.13.1 hardcodes
`Column("version_num", String(32))` in `MigrationContext.__init__` and the option appears nowhere in
the installed package (grepped 2026-09-21). The create-or-widen DDL is not a workaround standing in
for a switch; it is the only mechanism there is.

A SUBPROCESS, and that is a fact about the tree rather than a preference: `migrations/env.py` ends in
`asyncio.run(...)` at import, so it cannot be invoked from inside a running event loop, and every
caller here is async.

PROVISIONING (T1701). The second half of this module creates the databases a PostgreSQL tier runs on:
a TEMPLATE built once by the migrations, and a CLONE per task or per test made from it with
`CREATE DATABASE ... TEMPLATE`. It lives here rather than in `tests/conftest.py` so that it can be
called before a session exists and tested on its own. Three things it refuses to do quietly:

* `CREATEDB` is a PRECONDITION of the tier, not a nice-to-have. Without it provisioning raises; it
  never degrades to a skip, because a skipped provisioning step reports green for a measurement that
  was never taken (`AGENTS.md` §9).
* A template cannot be copied while anything is connected to it, so the connections are terminated
  first and a clone that still fails says which database was busy.
* A clone left behind by a run that died is DROPPED before the next clone of the same name is made,
  so a crash costs a rebuild rather than an inherited schema, and clones do not accumulate.

The TEMPLATE, unlike a clone, deliberately outlives the run that built it: there is exactly one per
task slug, and the next `provision_migrated_template` sweeps everything belonging to the slug -
including that template - before rebuilding from empty. Bounded at one database per slug is the
trade for not paying an `alembic upgrade head` in a `finally`.

Names are derived from the tier's own database name, which already carries the task slug
(`geov0_test_<slug>`), so two agents' clones cannot collide (`AGENTS.md` §7), and every derived name
is put through `scripts/validate_test_database_url.py` before it is used - the guard is enforced on
provisioning, never relaxed for it.

THE DOUBLED UNDERSCORE IS RESERVED, AND THAT RESERVATION IS THE ONLY THING THAT MAKES THE SWEEP THIS
TASK'S OWN (2026-09-22, Codex external review of `e2e1380..37fec08`). The first version of this
module said the prefix sweep "can only reach databases belonging to this task" because the prefix is
the tier's own name. That reason is false: `p017fixa__probe` is a valid task slug, its tier database
is `geov0_test_p017fixa__probe`, and the sweep run for the task `p017fixa` disconnected and DROPPED
it - reproduced on PostgreSQL 16 with two disposable databases before this was fixed. The separator
establishes nothing by itself; what does is that no TIER database may carry it. The guard refuses
such a `TEST_DATABASE_URL` unless the caller asks for a scratch name on purpose
(`assert_safe_test_database_url(..., allow_scratch_suffix=True)`), and this module refuses one at the
other end, so `<tier>__<rest>` can only ever be a scratch database derived from `<tier>`.
"""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.engine import make_url

REPO_ROOT = Path(__file__).resolve().parents[1]

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.validate_test_database_url import (  # noqa: E402
    UnsafeTestDatabaseError,
    assert_safe_test_database_url,
)

#: The database every `CREATE DATABASE` / `DROP DATABASE` is issued from. It is never the tier's own
#: database: a connection to it would itself block the drop.
MAINTENANCE_DATABASE = "postgres"

#: Suffix of the template database built by the migrations, appended to the tier's database name.
TEMPLATE_SUFFIX = "tpl"

#: PostgreSQL truncates identifiers at 63 bytes, which would silently merge two task slugs' databases
#: into one. Provisioning refuses instead of truncating.
MAX_IDENTIFIER_LENGTH = 63

#: Scratch databases are `<tier database>__<suffix>`. The doubled underscore keeps an ordinary
#: neighbouring slug out of the prefix sweep (`geov0_test_p017` beside `geov0_test_p017_s1a`), but
#: that is only half of what the sweep needs, and taking it for the whole is what let a neighbour's
#: TIER database be dropped. The other half is the RESERVATION spelled in this module's docstring:
#: `scripts/validate_test_database_url.py` refuses a tier database name carrying this separator, and
#: `scratch_database_name` / `drop_stale_scratch_databases` refuse one here.
SCRATCH_SEPARATOR = "__"

#: A scratch suffix: lowercase letters, digits and underscores, at most 24 characters. The doubled
#: underscore is excluded separately below, because a suffix carrying one would put a second
#: separator into the derived name and make it ambiguous again.
_SUFFIX_RE = re.compile(r"^[a-z0-9][a-z0-9_]{0,23}$")

#: The shape `scripts/validate_test_database_url.py` accepts. Re-checked here before a name is ever
#: interpolated into DDL, because these statements cannot take bound parameters.
_DATABASE_NAME_RE = re.compile(r"^geov0_test_[A-Za-z0-9_-]+$")

#: A maintenance connection is made to a server that may still be starting (CI service containers).
_CONNECT_TIMEOUT_SECONDS = 15.0
_CONNECT_ATTEMPTS = 3
_CONNECT_BACKOFF_SECONDS = 2.0

#: `CREATE DATABASE ... TEMPLATE` copies the template's files; it is not instant on a large schema.
_STATEMENT_TIMEOUT_SECONDS = 300.0


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

    The `alembic_version` precondition is established by `migrations/env.py` inside this run; callers
    do not prepare the database first (T1701). Returns the run's combined output; raises
    `MigratedSchemaError` carrying stdout AND stderr on any non-zero exit, because a migration run
    that failed halfway leaves a database that looks built.

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


# =====================================================================================================
# Names
# =====================================================================================================


def scratch_database_name(base_name: str, suffix: str) -> str:
    """`<tier database>__<suffix>`, refused rather than truncated when it would not fit.

    The tier's database name already carries the task slug, so deriving from it is what keeps two
    agents' scratch databases apart (`AGENTS.md` §7). Truncating at 63 would throw that away: two
    slugs long enough to be cut would land on the same database.

    THE SEPARATOR IS REFUSED IN BOTH HALVES (2026-09-22). A tier name that already carried one could
    not be told apart from a neighbour's scratch database - `geov0_test_a__b` is either task `a__b`'s
    own database or task `a`'s scratch `b` - and the stale-database sweep resolved that ambiguity by
    dropping it. `scripts/validate_test_database_url.py` refuses such a tier URL; refusing it here as
    well is what makes it one rule rather than a remote assumption about another module.
    """

    if SCRATCH_SEPARATOR in base_name:
        raise MigratedSchemaError(
            f"the tier database name {base_name!r} contains {SCRATCH_SEPARATOR!r}, which is reserved "
            f"as the separator between a tier database and the scratch databases derived from it. A "
            f"tier name carrying it cannot be told apart from another task's scratch database, and "
            f"the stale-database sweep would drop it. Use a task slug without a doubled underscore."
        )
    if SCRATCH_SEPARATOR in suffix:
        raise MigratedSchemaError(
            f"scratch database suffix {suffix!r} contains a doubled underscore, which would put a "
            f"second separator into the derived name and make it ambiguous in exactly the way a tier "
            f"name carrying one is."
        )
    if not _SUFFIX_RE.fullmatch(suffix):
        raise MigratedSchemaError(
            f"scratch database suffix {suffix!r} must be lowercase letters, digits and underscores, "
            f"starting with a letter or digit, at most 24 characters."
        )
    name = f"{base_name}{SCRATCH_SEPARATOR}{suffix}"
    if len(name) > MAX_IDENTIFIER_LENGTH:
        raise MigratedSchemaError(
            f"the scratch database name {name!r} is {len(name)} bytes and PostgreSQL truncates "
            f"identifiers at {MAX_IDENTIFIER_LENGTH}. Truncating here would merge two task slugs' "
            f"databases into one, so this is refused: shorten the -TaskSlug or the suffix."
        )
    if not _DATABASE_NAME_RE.fullmatch(name):
        raise MigratedSchemaError(
            f"the scratch database name {name!r} is not of the form geov0_test_<task>, which is the "
            f"only shape the test-database guard accepts. The tier's own database name "
            f"({base_name!r}) has to be a geov0_test_* name for anything to be derived from it."
        )
    return name


def scratch_database_url(base_url: str, suffix: str) -> tuple[str, str]:
    """A URL for a scratch database next to `base_url`, and its name.

    The derived URL is put through `assert_safe_test_database_url` before it is returned. That guard
    is the one thing standing between a test run and a developer's data, and provisioning is exactly
    the place where a name is CONSTRUCTED rather than read from the environment, so it is the place
    where skipping the check would be easiest and worst.

    `render_as_string(hide_password=False)` and NOT `str(url)`, measured in T1530 before it was
    written down: `URL.__str__` replaces the password with `***`, and a URL rendered that way fails as
    "password authentication failed for user geo" - a refusal that names the wrong problem.
    """

    parsed = make_url(base_url)
    if parsed.get_backend_name() != "postgresql":
        raise MigratedSchemaError(
            f"scratch databases can only be provisioned on PostgreSQL; this URL uses "
            f"{parsed.get_backend_name()!r}."
        )
    name = scratch_database_name(parsed.database or "", suffix)
    url = parsed.set(database=name).render_as_string(hide_password=False)
    try:
        assert_safe_test_database_url(
            url,
            allow_destructive_reset=os.environ.get("GEO_TEST_ALLOW_DB_RESET"),
            repo_root=REPO_ROOT,
            required_backend="postgresql",
            # The one caller that derives a name carrying the reserved separator, on purpose. Every
            # other caller - a tier's own TEST_DATABASE_URL - is refused such a name.
            allow_scratch_suffix=True,
        )
    except UnsafeTestDatabaseError as exc:
        raise MigratedSchemaError(
            f"the derived scratch database URL for suffix {suffix!r} is not one the test-database "
            f"guard accepts ({exc}). The guard is not relaxed for provisioning."
        ) from exc
    return url, name


def _quoted(name: str) -> str:
    """A database name ready to interpolate into DDL, or a refusal.

    `CREATE DATABASE` and `DROP DATABASE` take no bound parameters, so the name has to be written
    into the statement. It is re-checked against the guard's own shape here rather than trusted from
    the caller: the check costs nothing and this is the only interpolation in the module.
    """

    if not _DATABASE_NAME_RE.fullmatch(name):
        raise MigratedSchemaError(
            f"refusing to interpolate the database name {name!r} into DDL: it is not of the form "
            f"geov0_test_<task>."
        )
    return f'"{name}"'


# =====================================================================================================
# The maintenance connection and its preconditions
# =====================================================================================================


async def maintenance_connection(base_url: str):
    """A raw asyncpg connection to `postgres` on the same server, for CREATE/DROP DATABASE.

    asyncpg directly and not an engine: `CREATE DATABASE` cannot run inside a transaction, and a raw
    connection is the shortest honest way to say so.

    An unreachable server is retried, briefly, because a CI service container can still be starting.
    A refused authentication is NOT retried: it is a configuration error and repeating it only makes
    the failure slower and the reason less obvious.
    """

    import asyncpg

    parsed = make_url(base_url)
    last_transport_error: BaseException | None = None
    for attempt in range(1, _CONNECT_ATTEMPTS + 1):
        try:
            return await asyncpg.connect(
                host=parsed.host,
                port=parsed.port or 5432,
                user=parsed.username,
                password=parsed.password,
                database=MAINTENANCE_DATABASE,
                timeout=_CONNECT_TIMEOUT_SECONDS,
            )
        except (OSError, asyncio.TimeoutError) as exc:
            # The server is not answering yet: transient by class, so it is worth another look.
            last_transport_error = exc
            if attempt < _CONNECT_ATTEMPTS:
                await asyncio.sleep(_CONNECT_BACKOFF_SECONDS)
        except asyncpg.PostgresError as exc:
            # The server answered and said no. Credentials, a missing `postgres` database, a
            # pg_hba rule - none of these change by trying again.
            raise MigratedSchemaError(
                f"the PostgreSQL server at {parsed.host}:{parsed.port or 5432} refused a "
                f"maintenance connection to {MAINTENANCE_DATABASE!r} as {parsed.username!r}: {exc}. "
                f"Scratch databases CANNOT be provisioned, so nothing measured here would be a "
                f"passing run."
            ) from exc
    raise MigratedSchemaError(
        f"the PostgreSQL server at {parsed.host}:{parsed.port or 5432} could not be reached in "
        f"{_CONNECT_ATTEMPTS} attempts ({last_transport_error!r}). Scratch databases CANNOT be "
        f"provisioned. Note AGENTS.md §5: use 127.0.0.1, not localhost."
    )


async def assert_may_create_databases(connection) -> None:
    """Refuse unless the connected role can create databases. A PRECONDITION, never a skip.

    Until T1701 four modules answered a missing `CREATEDB` with `pytest.skip`, each of them saying in
    its own message that this was an absent measurement rather than a passing one - and each of them
    reporting green anyway. With the template and its clones, the right is what the whole PostgreSQL
    tier is built on, so its absence is an environment fault and is raised as one.

    `connection` is anything with asyncpg's `fetchrow`, so the refusal can be tested without a server
    that lacks the right.
    """

    row = await connection.fetchrow(
        "SELECT current_user AS role_name, rolsuper OR rolcreatedb AS may_create "
        "FROM pg_roles WHERE rolname = current_user"
    )
    if row is None:
        raise MigratedSchemaError(
            "the connected role has no row in `pg_roles`, so whether it may create databases cannot "
            "be established. Provisioning refuses rather than finding out by failing halfway."
        )
    if not row["may_create"]:
        raise MigratedSchemaError(
            f"the test role {row['role_name']!r} has neither SUPERUSER nor CREATEDB, so the schema "
            f"template and its per-task clones CANNOT be provisioned. This is a MISSING PRECONDITION "
            f"of the PostgreSQL tier, not a passing run: grant it with "
            f"`ALTER ROLE {row['role_name']} CREATEDB;` as a superuser, or point TEST_DATABASE_URL at "
            f"a server where the role has it."
        )


async def disconnect_everyone_from(connection, name: str) -> int:
    """Terminate every other backend on `name`, and report how many. Used before copy and before drop.

    PostgreSQL refuses `CREATE DATABASE ... TEMPLATE` while anything is connected to the template, and
    refuses `DROP DATABASE` while anything is connected to the database. Both are ordinary here: the
    subprocess that ran the migrations has exited, but a NullPool engine in this process or a
    neighbour's stale session is enough. The databases involved are this task's own, derived from its
    own slug, so terminating their sessions cannot touch another agent's run.
    """

    _quoted(name)  # shape check; the name goes in as a bound parameter below
    terminated = await connection.fetchval(
        "SELECT count(pg_terminate_backend(pid)) FROM pg_stat_activity "
        "WHERE datname = $1 AND pid <> pg_backend_pid()",
        name,
        timeout=_STATEMENT_TIMEOUT_SECONDS,
    )
    return int(terminated or 0)


async def drop_database(connection, name: str) -> None:
    """`DROP DATABASE IF EXISTS`, with the connections cleared first and a named refusal if it fails."""

    import asyncpg

    await disconnect_everyone_from(connection, name)
    try:
        await connection.execute(
            f"DROP DATABASE IF EXISTS {_quoted(name)}", timeout=_STATEMENT_TIMEOUT_SECONDS
        )
    except asyncpg.ObjectInUseError as exc:
        raise MigratedSchemaError(
            f"the database {name!r} could not be dropped because sessions reconnected to it while it "
            f"was being dropped: {exc}. A stale clone left standing will be dropped by the next run "
            f"that needs the same name, but it is occupying disk until then."
        ) from exc
    except asyncpg.InsufficientPrivilegeError as exc:
        raise MigratedSchemaError(
            f"the connected role may not drop {name!r}: {exc}. Provisioning cannot clean up after "
            f"itself, so scratch databases would accumulate silently."
        ) from exc


async def create_database(connection, name: str, *, template: str | None = None) -> None:
    """`CREATE DATABASE`, optionally `TEMPLATE <template>`, with each refusal named by its class."""

    import asyncpg

    statement = f"CREATE DATABASE {_quoted(name)}"
    if template is not None:
        statement += f" TEMPLATE {_quoted(template)}"
    try:
        await connection.execute(statement, timeout=_STATEMENT_TIMEOUT_SECONDS)
    except asyncpg.InsufficientPrivilegeError as exc:
        raise MigratedSchemaError(
            f"the connected role may not create {name!r}: {exc}. CREATEDB is a precondition of this "
            f"tier - see `assert_may_create_databases` - and its absence is an absent measurement, "
            f"not a passing one."
        ) from exc
    except asyncpg.ObjectInUseError as exc:
        raise MigratedSchemaError(
            f"{template!r} could not be copied into {name!r} because sessions are connected to the "
            f"template: {exc}. They are terminated before the copy, so a session that is still there "
            f"reconnected in between - find what is holding {template!r} open."
        ) from exc
    except asyncpg.DuplicateDatabaseError as exc:
        raise MigratedSchemaError(
            f"the database {name!r} already exists although it was dropped a moment ago: {exc}. Two "
            f"runs are provisioning the same name, which means the task slugs collide "
            f"(AGENTS.md §7)."
        ) from exc


async def drop_stale_scratch_databases(connection, base_name: str) -> list[str]:
    """Drop every `<base_name>__*` database on this server, and return what was dropped.

    This is the answer to "a run died and left its clone standing".

    WHAT MAKES THESE DATABASES THIS TASK'S OWN, said precisely, because the first version of this
    docstring said something that is not true (2026-09-22, Codex external review of
    `e2e1380..37fec08`). It said the prefix carries the task slug "so this can only reach databases
    belonging to this task". The prefix alone establishes nothing: `geov0_test_a__b` is a perfectly
    good tier database name for the task slug `a__b`, and this sweep, run for the task `a`,
    terminated its sessions and dropped it. Reproduced on PostgreSQL 16 with two disposable
    databases before the fix; `AGENTS.md` §7 is precisely what that broke.

    Ownership comes from the separator being RESERVED, not from the prefix:
    `scripts/validate_test_database_url.py` refuses a tier `TEST_DATABASE_URL` whose database name
    contains it, so a name shaped `<base_name>__<rest>` cannot be anybody's tier database - it can
    only be a scratch database this module derived from `base_name`. The refusal below is that same
    rule at this end: a `base_name` arriving here with the separator in it means the guard was
    bypassed, and the sweep stops rather than guessing which task owns the neighbours.
    """

    if SCRATCH_SEPARATOR in base_name:
        raise MigratedSchemaError(
            f"refusing to sweep from the tier database name {base_name!r}: it contains "
            f"{SCRATCH_SEPARATOR!r}, which is reserved as the scratch separator. Sweeping "
            f"{base_name}{SCRATCH_SEPARATOR}* from here could drop the tier database of a task whose "
            f"slug merely starts with this one's (AGENTS.md §7)."
        )

    rows = await connection.fetch(
        "SELECT datname FROM pg_database "
        "WHERE datname <> $1 AND left(datname, length($1) + 2) = $1 || '__' "
        "ORDER BY datname",
        base_name,
        timeout=_STATEMENT_TIMEOUT_SECONDS,
    )
    dropped: list[str] = []
    for row in rows:
        await drop_database(connection, row["datname"])
        dropped.append(row["datname"])
    return dropped


# =====================================================================================================
# Template and clones
# =====================================================================================================


async def ensure_tier_database(base_url: str) -> bool:
    """Create the tier's OWN database when it does not exist yet; return whether it was created.

    WHY THE TIER NEEDS THIS (T1702, measured 2026-09-23). Until this function the PostgreSQL tier
    never created its database: pointed at `geov0_test_<slug>` on a server where that database did not
    exist, the default tier produced 705 errors with one cause, `InvalidCatalogNameError: database
    "geov0_test_p017s2probe" does not exist`. CI never saw it because the service container creates
    `geov0_test_ci` itself (`POSTGRES_DB`), so a green CI said nothing about a fresh local clone.

    ONLY WHAT THE GUARD WOULD ALSO LET THE TIER RESET MAY BE CREATED HERE. The URL goes through
    `assert_safe_test_database_url` with the tier's own reset opt-in and WITHOUT the scratch-suffix
    allowance, so a name the tier could not drop (`geov0_dev_*`, a doubled underscore, a missing
    `GEO_TEST_ALLOW_DB_RESET=1`) is refused before any connection is opened. An existing database is
    left exactly as it is - the schema is built afterwards by the tier, not here.

    `CREATEDB` is asked for only when there is something to create, and its absence is the same
    refusal the template raises (`assert_may_create_databases`): a missing precondition, never a skip.
    """

    parsed = make_url(base_url)
    if parsed.get_backend_name() != "postgresql":
        raise MigratedSchemaError(
            f"only a PostgreSQL tier database can be created here; this URL uses "
            f"{parsed.get_backend_name()!r}."
        )
    try:
        assert_safe_test_database_url(
            base_url,
            allow_destructive_reset=os.environ.get("GEO_TEST_ALLOW_DB_RESET"),
            repo_root=REPO_ROOT,
            required_backend="postgresql",
        )
    except UnsafeTestDatabaseError as exc:
        raise MigratedSchemaError(
            f"the tier's TEST_DATABASE_URL is not one the test-database guard accepts ({exc}), so it "
            f"is not created either: the tier may only create what it would be allowed to reset."
        ) from exc

    name = parsed.database or ""
    _quoted(name)  # shape check before the name is used at all
    connection = await maintenance_connection(base_url)
    try:
        exists = await connection.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1", name, timeout=_STATEMENT_TIMEOUT_SECONDS
        )
        if exists:
            return False
        await assert_may_create_databases(connection)
        await create_database(connection, name)
        return True
    finally:
        await connection.close()


async def database_exists(base_url: str, name: str) -> bool:
    """Whether `name` exists on the server `base_url` points at. One maintenance round trip."""

    _quoted(name)
    connection = await maintenance_connection(base_url)
    try:
        return bool(
            await connection.fetchval(
                "SELECT 1 FROM pg_database WHERE datname = $1",
                name,
                timeout=_STATEMENT_TIMEOUT_SECONDS,
            )
        )
    finally:
        await connection.close()


async def provision_migrated_template(
    base_url: str, *, suffix: str = TEMPLATE_SUFFIX, sweep_stale: bool = True
) -> tuple[str, str]:
    """Build the template database with the migrations, from empty, and return `(url, name)`.

    From empty on purpose, and this is the same argument `tests/conftest.py::_build_migrated_schema`
    makes: `alembic upgrade head` against a database that already holds a schema and a head stamp is
    a no-op, and a no-op would preserve exactly the state a rebuild exists to destroy.

    Building the template is the start of a tier run, so it is also where every scratch database this
    task left behind is swept - including clones whose per-test suffix no later run would name again.
    The sweep is bounded by this task's own database name, so it cannot reach a neighbour's.

    `sweep_stale=False` drops only THIS template's own name before rebuilding it (T1702). It is for a
    caller that builds its template in the MIDDLE of a session - the mode-B fixture in
    `tests/conftest.py` - where the sweep would drop the templates other modules of the same session
    have cached (`test_p017_t1701_schema_provisioning_postgres.py`, `test_p017_t1711_seed_recipe_postgres.py`)
    and fail them for a reason that is not theirs. Such a caller keeps its own clones bounded by
    reusing one clone name, which `cloned_database` drops before every copy.
    """

    template_url, template_name = scratch_database_url(base_url, suffix)
    connection = await maintenance_connection(base_url)
    try:
        await assert_may_create_databases(connection)
        if sweep_stale:
            await drop_stale_scratch_databases(connection, make_url(base_url).database or "")
        else:
            await drop_database(connection, template_name)
        await create_database(connection, template_name)
    finally:
        await connection.close()

    # Blocking, outside the loop, on purpose: `migrations/env.py` ends in `asyncio.run(...)`, so the
    # migrations cannot be awaited. This call also establishes the `alembic_version` precondition.
    run_alembic_upgrade_head(template_url)
    return template_url, template_name


@asynccontextmanager
async def cloned_database(
    base_url: str, *, template_name: str, suffix: str
) -> AsyncIterator[str]:
    """A database copied from `template_name`, dropped when the block ends however it ends.

    A clone of the same name left by an earlier run is dropped BEFORE the copy, so a crashed run
    costs a rebuild rather than handing the next run a schema nobody built.
    """

    clone_url, clone_name = scratch_database_url(base_url, suffix)
    connection = await maintenance_connection(base_url)
    try:
        await assert_may_create_databases(connection)
        await drop_database(connection, clone_name)
        await disconnect_everyone_from(connection, template_name)
        await create_database(connection, clone_name, template=template_name)
    except BaseException:
        await connection.close()
        raise

    # WHETHER THE BLOCK FAILED IS RECORDED ON THE WAY OUT OF IT, not read back from
    # `sys.exc_info()` inside the handler (2026-09-22, Codex external review of
    # `e2e1380..37fec08`). The first version asked `if sys.exc_info()[0] is None: raise` from inside
    # `except MigratedSchemaError`, where `sys.exc_info()[0]` is the cleanup error being handled and
    # is therefore NEVER `None` - the `raise` was unreachable, and a refused `DROP DATABASE` after a
    # SUCCESSFUL body became a line on stderr and a passing test. Proved by execution, not by
    # reading.
    body_failed = True
    try:
        yield clone_url
        body_failed = False
    finally:
        try:
            await drop_database(connection, clone_name)
        except MigratedSchemaError as cleanup_error:
            if not body_failed:
                # Nothing else is propagating, so the refused cleanup IS the result of this block
                # and the run has to see it.
                raise
            # Something in the block already failed; that is what the reader needs to see. The clone
            # is named, so it can be found, and the next run of this suffix drops it first.
            print(
                f"WARNING: the clone {clone_name!r} was left standing: {cleanup_error}",
                file=sys.stderr,
            )
        finally:
            await connection.close()


@asynccontextmanager
async def scratch_databases(base_url: str, *suffixes: str) -> AsyncIterator[tuple[str, ...]]:
    """Empty scratch databases next to `base_url`, dropped when the block ends however it ends.

    For the tests that compare two construction paths: one database gets `Base.metadata.create_all`
    and the other a real `alembic upgrade head`, and neither may be a clone of the other. Missing
    `CREATEDB` is refused here rather than skipped - see `assert_may_create_databases`.
    """

    if not suffixes:
        raise MigratedSchemaError("scratch_databases() was called without a suffix.")

    prepared = [scratch_database_url(base_url, suffix) for suffix in suffixes]
    connection = await maintenance_connection(base_url)
    try:
        await assert_may_create_databases(connection)
        for _, name in prepared:
            await drop_database(connection, name)
            await create_database(connection, name)
    except BaseException:
        await connection.close()
        raise

    # Same correction as in `cloned_database` above, and this is the half that regressed something:
    # the four schema-comparison modules that call this
    # (`test_p015_step5a_reconciliation_postgres.py`, `test_p015_step5b_criterion_b_postgres.py`,
    # `test_p015_step5c_hold_races_postgres.py`, `test_p015_t1530_delta_arithmetic_postgres.py`)
    # propagated their failed drops before T1701 moved them onto this helper.
    body_failed = True
    try:
        yield tuple(url for url, _ in prepared)
        body_failed = False
    finally:
        try:
            for _, name in prepared:
                await drop_database(connection, name)
        except MigratedSchemaError as cleanup_error:
            if not body_failed:
                raise
            print(
                f"WARNING: a scratch database was left standing: {cleanup_error}",
                file=sys.stderr,
            )
        finally:
            await connection.close()


@asynccontextmanager
async def migrated_clone(
    base_url: str, *, suffix: str, template_suffix: str = TEMPLATE_SUFFIX
) -> AsyncIterator[str]:
    """The whole pipeline: bootstrap and migrate a template, then hand out a clone of it.

    Building the template costs one `alembic upgrade head`; every clone after that costs a file copy.
    A caller that wants many clones builds the template once with `provision_migrated_template` and
    then opens `cloned_database` per clone.
    """

    _, template_name = await provision_migrated_template(base_url, suffix=template_suffix)
    async with cloned_database(base_url, template_name=template_name, suffix=suffix) as clone_url:
        yield clone_url
