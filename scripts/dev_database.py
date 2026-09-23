"""The launcher's PostgreSQL database: its name contract, its lifecycle and its readiness probe.

Programme 017 `T1710`. Both launchers (`scripts/run_local.ps1`, `scripts/run_full_stack.ps1`) and
the Admin e2e (`scripts/verify_admin_phase4_real_contract.ps1`) run on PostgreSQL, and all three
need the same three answers: may this database be destroyed, does it exist, and is it ready. This
module is the ONE place that answers them, for the same reason `migrations/env.py` is the one owner
of the `alembic_version` precondition: three PowerShell copies of a destructive boundary are three
chances to get it wrong, and the one that is wrong is the one that drops somebody else's database.

The URL arrives in `DATABASE_URL`, never in argv, so the password is not copied into a process
listing or a launcher log (`AGENTS.md` section 12) - the same reason
`scripts/validate_test_database_url.py` reads its URL from the environment.

Exit codes, because PowerShell drives on them:

    0  the command succeeded (for `ready`: the database is seeded and reconciles)
    1  a logical refusal - the state is wrong and retrying will not change it
    2  the URL is not a database this module is allowed to touch, or the arguments are wrong
    3  `ready` only: the database is migrated and EMPTY, so the caller should seed it
    4  PostgreSQL could not be reached - transient, and the caller is told how to start it
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import socket
import sys
from ipaddress import ip_address
from pathlib import Path
from typing import Any, Sequence

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from sqlalchemy.engine import URL, make_url  # noqa: E402


class DevDatabaseRefusal(Exception):
    """A named refusal. Carries the exit code the caller should report."""

    def __init__(self, message: str, *, code: int = 1) -> None:
        super().__init__(message)
        self.code = code


class UnsafeDevDatabaseError(DevDatabaseRefusal):
    """The URL is not provably the launcher's own disposable database."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code=2)


# ==================================================================================================
# The name contract
# ==================================================================================================

#: The launcher's database, by name. Deliberately the same shape and the same strictness as the test
#: harness's `geov0_test_<task>` (`scripts/validate_test_database_url.py:20`, and the rules this file
#: mirrors at `:112-118`): a name that does not match is not "discouraged", it is REFUSED, because
#: everything below this line is `DROP DATABASE`.
_POSTGRES_DEV_DATABASE_RE = re.compile(r"^geov0_dev_[A-Za-z0-9_-]+$")

#: The separator the test harness reserves between a tier database and its scratch databases
#: (`scripts/validate_test_database_url.py::_SCRATCH_SEPARATOR`, reserved 2026-09-22 after a sweep
#: destroyed a neighbouring task's database). No sweep walks `geov0_dev_*` today, and this refusal is
#: here so that the day one is written, the names it has to tell apart are already unambiguous.
_SCRATCH_SEPARATOR = "__"

#: A slug: alphanumeric/dash groups joined by SINGLE underscores. It forbids the doubled underscore
#: at either end and in any position (`AGENTS.md` section 5).
_UNDOUBLED_RE = re.compile(r"^[A-Za-z0-9-]+(?:_[A-Za-z0-9-]+)*$")

#: PostgreSQL truncates an identifier longer than this to `NAMEDATALEN - 1` bytes, silently. A
#: truncated name is a DIFFERENT database from the one the caller named, so it is refused instead:
#: otherwise the caller is told it reset `geov0_dev_<long>` while the server reset something else.
_MAX_IDENTIFIER_BYTES = 63

#: How long to wait for the cluster before calling it unreachable. The local portable cluster answers
#: in well under a second (`docs/ru/backend/postgres-local-portable.md`); ten seconds is generous and
#: still bounded, because an unbounded connect is how a launcher hangs with no message.
_CONNECT_TIMEOUT_SECONDS = 10.0

#: How long a single maintenance statement (`CREATE`/`DROP DATABASE`) may take.
_STATEMENT_TIMEOUT_SECONDS = 60.0


def adopted_key_table_path(database: str) -> Path:
    """Where THIS database's `ref -> PID` table lives.

    The seed writes one table per COMMUNITY (`scripts/seed_recipe.py::key_table_path`), overwritten
    by the next run of that community - which is right for the seed and wrong for a readiness probe,
    because two databases in one checkout are routinely seeded from the same community: the
    launcher's `geov0_dev_local` and the Admin e2e's disposable database. Measured on 2026-09-23: the
    e2e's seed overwrote the community table, and the launcher's next `start` refused its own
    perfectly good database because the PIDs it was checking belonged to the e2e's run.

    So each database keeps its own copy, taken right after its own seed, and readiness reads only
    that. A database with no copy is refused by name rather than checked against somebody else's
    PIDs.
    """

    return _REPO_ROOT / ".local-run" / "dev-databases" / database / "participants.json"


def _is_loopback_host(host: str) -> bool:
    candidate = (host or "").strip()
    if not candidate:
        # An empty host means a local socket, which cannot reach another machine.
        return True
    if candidate.lower() == "localhost":
        return True
    try:
        return ip_address(candidate).is_loopback
    except ValueError:
        return False


def assert_safe_dev_database_url(database_url: str) -> URL:
    """Validate that this URL is the launcher's own disposable database, or refuse.

    Every condition here is a condition under which `DROP DATABASE` would be wrong, and each one is
    checked on the PARSED url rather than on the string, so a URL that merely contains the right
    name somewhere cannot pass.
    """

    if not database_url or not database_url.strip():
        raise UnsafeDevDatabaseError("DATABASE_URL is empty.")
    try:
        url = make_url(database_url)
    except Exception as exc:  # noqa: BLE001 - any parse failure is the same refusal
        raise UnsafeDevDatabaseError("DATABASE_URL is not a valid SQLAlchemy URL.") from exc

    backend = url.get_backend_name()
    if backend != "postgresql":
        raise UnsafeDevDatabaseError(
            f"The launcher database must be PostgreSQL; this URL is {backend or '<missing>'}. "
            f"Programme 017 removed the SQLite engine from the launcher."
        )

    if not _is_loopback_host(url.host or ""):
        raise UnsafeDevDatabaseError(
            f"The launcher database must live on a loopback host; this URL points at "
            f"{url.host!r}. This script drops databases, and it will not do that over a network."
        )

    database = url.database or ""
    if len(database.encode("utf-8")) > _MAX_IDENTIFIER_BYTES:
        raise UnsafeDevDatabaseError(
            f"Database name {database!r} is longer than PostgreSQL's {_MAX_IDENTIFIER_BYTES}-byte "
            f"identifier limit, so the server would silently act on a truncated, different name."
        )
    if not _POSTGRES_DEV_DATABASE_RE.fullmatch(database):
        raise UnsafeDevDatabaseError(
            f"Database name {database!r} is not the launcher's own database: the name contract is "
            f"{_POSTGRES_DEV_DATABASE_RE.pattern}. Nothing outside that contract can be reset, and "
            f"no flag overrides this."
        )

    slug, separator, scratch = database[len("geov0_dev_") :].partition(_SCRATCH_SEPARATOR)
    if separator:
        raise UnsafeDevDatabaseError(
            f"Database name {database!r} contains the doubled underscore that provisioning reserves "
            f"as the separator between a database and the scratch databases derived from it "
            f"(scratch {scratch!r} of 'geov0_dev_{slug}'), so a launcher slug may not contain "
            f"'{_SCRATCH_SEPARATOR}' (AGENTS.md section 5)."
        )
    if not _UNDOUBLED_RE.fullmatch(slug):
        raise UnsafeDevDatabaseError(
            f"Database name {database!r} is not geov0_dev_<slug> with single underscores inside "
            f"<slug>: a leading, trailing or doubled underscore makes it ambiguous with a scratch "
            f"database of a shorter slug."
        )
    return url


def render_safe(url: URL) -> str:
    """The URL with the password removed, for printing."""

    return url.render_as_string(hide_password=True)


# ==================================================================================================
# Talking to the cluster
# ==================================================================================================


def _dsn_for(url: URL, database: str) -> str:
    return URL.create(
        "postgresql",
        username=url.username,
        password=url.password,
        host=url.host,
        port=url.port,
        database=database,
    ).render_as_string(hide_password=False)


async def _connect(url: URL, database: str) -> Any:
    """Open a connection, or raise a refusal that says which KIND of failure it was.

    The two classes are not cosmetic. "The cluster is not running" is transient and the operator's
    next move is to start it; "the password is wrong" or "the role does not exist" will not change
    on a retry, and telling the operator to wait would be a lie.
    """

    import asyncpg

    try:
        return await asyncpg.connect(_dsn_for(url, database), timeout=_CONNECT_TIMEOUT_SECONDS)
    except asyncpg.InvalidPasswordError as exc:
        raise DevDatabaseRefusal(
            f"PostgreSQL rejected the credentials for role {url.username!r} at "
            f"{url.host}:{url.port}: {exc}"
        ) from exc
    except asyncpg.InvalidAuthorizationSpecificationError as exc:
        raise DevDatabaseRefusal(
            f"PostgreSQL refused the connection for role {url.username!r} at "
            f"{url.host}:{url.port}: {exc}"
        ) from exc
    except asyncpg.InvalidCatalogNameError as exc:
        raise DevDatabaseRefusal(
            f"The cluster at {url.host}:{url.port} has no database {database!r}: {exc}"
        ) from exc
    except (OSError, socket.gaierror, asyncio.TimeoutError, TimeoutError) as exc:
        raise DevDatabaseRefusal(
            f"PostgreSQL is not reachable at {url.host}:{url.port} ({type(exc).__name__}: {exc}). "
            f"Start the local cluster - see docs/ru/backend/postgres-local-portable.md section 3 - "
            f"and run this again.",
            code=4,
        ) from exc


async def _database_exists(connection: Any, name: str) -> bool:
    row = await connection.fetchval(
        "SELECT 1 FROM pg_database WHERE datname = $1", name, timeout=_STATEMENT_TIMEOUT_SECONDS
    )
    return row is not None


async def _sessions_on(connection: Any, name: str) -> list[str]:
    """Who else is connected to `name`, described well enough to be found and stopped."""

    rows = await connection.fetch(
        """
        SELECT pid,
               coalesce(application_name, '') AS application_name,
               coalesce(host(client_addr), 'local') AS client,
               coalesce(state, 'unknown') AS state
        FROM pg_stat_activity
        WHERE datname = $1 AND pid <> pg_backend_pid()
        ORDER BY pid
        """,
        name,
        timeout=_STATEMENT_TIMEOUT_SECONDS,
    )
    return [
        f"pid {row['pid']} from {row['client']}"
        + (f" ({row['application_name']})" if row["application_name"] else "")
        + f", state={row['state']}"
        for row in rows
    ]


# ==================================================================================================
# Commands
# ==================================================================================================


async def cmd_validate(url: URL) -> int:
    print(f"Launcher database guard passed (database={url.database!r}, host={url.host}).")
    return 0


async def cmd_ensure(url: URL) -> int:
    connection = await _connect(url, "postgres")
    try:
        if await _database_exists(connection, url.database or ""):
            print(f"Database {url.database!r} already exists.")
            return 0
        await connection.execute(
            f'CREATE DATABASE "{url.database}"', timeout=_STATEMENT_TIMEOUT_SECONDS
        )
        print(f"Database {url.database!r} created.")
        return 0
    finally:
        await connection.close()


async def cmd_reset(url: URL) -> int:
    """Drop and recreate the launcher's database - refusing while anything is connected to it.

    The drop is a PLAIN `DROP DATABASE`. It is not `WITH (FORCE)` and nothing here calls
    `pg_terminate_backend`, so if a backend is still holding the database open, PostgreSQL itself
    refuses and this command fails. That is deliberate: the last line of defence against resetting a
    database out from under a running stack is the server, not a check this script could get wrong.
    The explicit `pg_stat_activity` check above it exists to turn that server error into a message
    that names who is holding the database.
    """

    connection = await _connect(url, "postgres")
    try:
        name = url.database or ""
        if not await _database_exists(connection, name):
            await connection.execute(
                f'CREATE DATABASE "{name}"', timeout=_STATEMENT_TIMEOUT_SECONDS
            )
            _forget_adopted_key_table(url)
            print(f"Database {name!r} did not exist; created empty.")
            return 0

        holders = await _sessions_on(connection, name)
        if holders:
            raise DevDatabaseRefusal(
                f"Refusing to reset {name!r}: {len(holders)} connection(s) are still open to it - "
                + "; ".join(holders)
                + ". Stop the stack first (.\\scripts\\run_local.ps1 stop, "
                ".\\scripts\\run_full_stack.ps1 stop), then reset."
            )

        try:
            await connection.execute(f'DROP DATABASE "{name}"', timeout=_STATEMENT_TIMEOUT_SECONDS)
        except Exception as exc:  # noqa: BLE001 - reported verbatim, never forced through
            raise DevDatabaseRefusal(
                f"PostgreSQL refused to drop {name!r}: {type(exc).__name__}: {exc}. The launcher "
                f"never forces a drop, so this database is untouched."
            ) from exc
        await connection.execute(f'CREATE DATABASE "{name}"', timeout=_STATEMENT_TIMEOUT_SECONDS)
        _forget_adopted_key_table(url)
        print(f"Database {name!r} dropped and recreated empty.")
        return 0
    finally:
        await connection.close()


async def cmd_drop(url: URL) -> int:
    connection = await _connect(url, "postgres")
    try:
        name = url.database or ""
        if not await _database_exists(connection, name):
            print(f"Database {name!r} does not exist; nothing to drop.")
            return 0
        holders = await _sessions_on(connection, name)
        if holders:
            raise DevDatabaseRefusal(
                f"Refusing to drop {name!r}: {len(holders)} connection(s) are still open to it - "
                + "; ".join(holders)
                + "."
            )
        await connection.execute(f'DROP DATABASE "{name}"', timeout=_STATEMENT_TIMEOUT_SECONDS)
        _forget_adopted_key_table(url)
        print(f"Database {name!r} dropped.")
        return 0
    finally:
        await connection.close()


async def cmd_adopt(url: URL, *, community: str) -> int:
    """Copy the seed's freshly written `ref -> PID` table into this database's own place.

    Run immediately after seeding THIS database, before anything else seeds the same community
    somewhere else. Copying rather than referencing is the point: the source is overwritten by the
    next run of that community, and a readiness probe that followed it would be checking the wrong
    PIDs (see `adopted_key_table_path`).
    """

    import shutil

    from scripts.seed_recipe import key_table_path

    source = key_table_path(community)
    if not source.is_file():
        raise DevDatabaseRefusal(
            f"The seed of {community} wrote no ref -> PID table at {source}, so there is nothing to "
            f"adopt for {url.database!r}. Seed it first."
        )
    destination = adopted_key_table_path(url.database or "")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    print(f"Adopted the ref -> PID table of {community} for {url.database!r}: {destination}")
    return 0


def _forget_adopted_key_table(url: URL) -> None:
    """A dropped database's adopted table is stale metadata, and stale metadata is a false answer."""

    table = adopted_key_table_path(url.database or "")
    try:
        table.unlink(missing_ok=True)
    except OSError as exc:
        # Not fatal - the database is gone either way - but never silent, because a leftover table
        # would be read by the next `ready` on a database of the same name.
        print(f"warning: could not remove {table}: {exc}", file=sys.stderr)


async def _assert_schema_at_head(url: URL) -> str:
    """The schema is the one the repository's migrations produce, or this is not a ready database."""

    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config(str(_REPO_ROOT / "migrations" / "alembic.ini"))
    config.set_main_option("script_location", str(_REPO_ROOT / "migrations"))
    heads = list(ScriptDirectory.from_config(config).get_heads())
    if len(heads) != 1:
        raise DevDatabaseRefusal(
            f"The repository has {len(heads)} migration heads {sorted(heads)}, so 'the schema is at "
            f"head' has no single meaning. Run scripts/check_alembic_heads.py."
        )

    maintenance = await _connect(url, "postgres")
    try:
        if not await _database_exists(maintenance, url.database or ""):
            raise DevDatabaseRefusal(
                f"Database {url.database!r} does not exist. Create it: "
                f".\\scripts\\run_local.ps1 reset-db"
            )
    finally:
        await maintenance.close()

    probe = await _connect(url, url.database or "")
    try:
        if await probe.fetchval("SELECT to_regclass('public.alembic_version')") is None:
            raise DevDatabaseRefusal(
                f"Database {url.database!r} has no alembic_version table, so no migration has ever "
                f"run on it. That is an unfinished initialization, not a database to start on."
            )
        stamped = [
            row["version_num"]
            for row in await probe.fetch("SELECT version_num FROM alembic_version")
        ]
    finally:
        await probe.close()

    if sorted(stamped) != sorted(heads):
        raise DevDatabaseRefusal(
            f"Database {url.database!r} is stamped {sorted(stamped)} but the repository's head is "
            f"{sorted(heads)}. Migrate it, or reset it: .\\scripts\\run_local.ps1 reset-db"
        )
    return heads[0]


async def cmd_ready(url: URL, *, community: str) -> int:
    """Schema, population and baseline - the three things an unfinished seed leaves half-done.

    Nothing here is a warning. A database whose schema is stamped but whose population is a partial
    seed is exactly the state programme 017 refuses to start a stack on (`AGENTS.md` section 9: an
    integrity failure is a gate, not a log line).
    """

    import json

    head = await _assert_schema_at_head(url)
    print(f"schema: at head {head}")

    from app.db.session import AsyncSessionLocal
    from scripts.seed_recipe import SeedRefusal, assert_database_is_empty, reverify

    try:
        async with AsyncSessionLocal() as session:
            await assert_database_is_empty(session)
    except SeedRefusal:
        pass  # Not empty. That is the interesting case, and it continues below.
    except Exception as exc:  # noqa: BLE001
        raise DevDatabaseRefusal(
            f"Cannot read {render_safe(url)}: {type(exc).__name__}: {exc}"
        ) from exc
    else:
        print("population: empty (schema only)")
        return 3

    table = adopted_key_table_path(url.database or "")
    if not table.is_file():
        raise DevDatabaseRefusal(
            f"Database {url.database!r} holds data, but no ref -> PID table was adopted for it "
            f"({table}), so its population cannot be checked against the recipe. Either it was "
            f"seeded without `dev_database.py adopt`, or this is an unfinished or foreign "
            f"initialization. Reset it: .\\scripts\\run_local.ps1 reset-db"
        )
    try:
        document = json.loads(table.read_text(encoding="utf-8"))
        refs_to_pid = {ref: entry["pid"] for ref, entry in document["participants"].items()}
    except Exception as exc:  # noqa: BLE001
        raise DevDatabaseRefusal(
            f"The ref -> PID table {table} cannot be read: {type(exc).__name__}: {exc}"
        ) from exc
    adopted_community = document.get("community_id")
    if adopted_community != community:
        raise DevDatabaseRefusal(
            f"Database {url.database!r} was seeded from {adopted_community!r}, not {community!r}. "
            f"Ask for that community, or reset the database."
        )

    try:
        checks = await reverify(AsyncSessionLocal, community_id=community, refs_to_pid=refs_to_pid)
    except SeedRefusal as refusal:
        raise DevDatabaseRefusal(
            f"Database {url.database!r} is not a finished seed of {community}: {refusal} "
            f"Reset it: .\\scripts\\run_local.ps1 reset-db"
        ) from refusal

    for name in sorted(checks):
        verdict = "PASSED" if checks[name]["passed"] else "FAILED"
        print(f"readiness {name}: {verdict} - {checks[name]['detail']}")
    failed = sorted(name for name, check in checks.items() if not check["passed"])
    if failed:
        raise DevDatabaseRefusal(
            f"Database {url.database!r} holds a seed of {community} that no longer satisfies "
            f"{len(failed)} check(s): {failed}. Reset it: .\\scripts\\run_local.ps1 reset-db"
        )
    print(f"population: {community}, ready")
    return 0


async def _run(args: argparse.Namespace) -> int:
    url = assert_safe_dev_database_url(os.environ.get("DATABASE_URL", ""))
    if args.command == "validate":
        return await cmd_validate(url)
    if args.command == "ensure":
        return await cmd_ensure(url)
    if args.command == "reset":
        return await cmd_reset(url)
    if args.command == "drop":
        return await cmd_drop(url)
    if args.command == "adopt":
        return await cmd_adopt(url, community=args.community)
    if args.command == "ready":
        return await cmd_ready(url, community=args.community)
    raise UnsafeDevDatabaseError(f"Unknown command {args.command!r}.")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Lifecycle and readiness of the launcher's PostgreSQL database."
    )
    parser.add_argument(
        "command", choices=("validate", "ensure", "reset", "drop", "adopt", "ready")
    )
    parser.add_argument(
        "--community",
        default="riverside-town-50",
        help=(
            "For `adopt` and `ready`: the community whose recipe this database was seeded from, "
            "and is expected to still hold."
        ),
    )
    args = parser.parse_args(argv)

    try:
        return asyncio.run(_run(args))
    except DevDatabaseRefusal as refusal:
        print(f"dev_database {args.command} refused: {refusal}", file=sys.stderr)
        return refusal.code


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    raise SystemExit(main())
