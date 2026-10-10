"""Reclaim the disk of test databases on a shared PostgreSQL server - by a reviewed manifest, never by itself.

WHY THIS EXISTS (decision of 2026-10-10, `QD-TEST-DATABASES: BUILD-NARROW`; AGENTS.md section 12). Every task slug
leaves its tier database `geov0_test_<slug>` and its templates `geov0_test_<slug>__<...>tpl` on the server; a run
sweeps only the scratch family of ITS OWN slug, so the databases of slugs that never run again stay for good (551
databases, about 5.5 GB, were counted on one machine). Nothing here deletes them automatically, and nothing should:
the server is shared by parallel sessions, and THE ABSENCE OF CONNECTIONS DOES NOT PROVE THAT A TASK HAS LET GO OF ITS
DATABASE OR ITS TEMPLATE - a session between two runs holds none. The catalog has no creation time and no last-use
time either, so "unused for N hours" cannot be read from it and is not invented here.

WHAT THE COMMAND DOES.

* DRY RUN (the default). Reads the catalog through the maintenance database `postgres` and writes a MANIFEST: the
  server's identity, and for every database its exact name, OID, owner, size, template flag, number of
  connections, family (the task slug), kind (tier / template / clone) and a proposed disposition with its reason:
  `DROP`, `KEEP` or `VERIFY-FIRST`. It sends SELECT statements only.
* APPLY (`--apply <manifest>` with `--maintenance-window-confirmed`, a declaration of what is protected -
  `--protect <slug>` at least once, or `--protect-none` - and `--expect-drop-count N`). The manifest is read and
  its whole structure checked BEFORE a connection is opened; it must be younger than `MANIFEST_MAX_AGE_MINUTES`.
  Then, before the first drop, EVERY `DROP` row is compared with the catalog (server, name, OID, owner, kind,
  protection), no unprotected test family on the server may have a connection - also one that is not among the
  rows - and the number of rows must equal `N`; the set is printed. Only then are the rows dropped, clones first,
  then templates, then tier databases, each compared with the catalog once more. On any difference the run STOPS -
  it does not skip and go on. The drop is an ordinary `DROP DATABASE`, which PostgreSQL itself refuses while anyone
  is connected to that database; a refusal stops the run too. A result file beside the manifest records what was
  dropped, and names a drop whose outcome is not known as UNCERTAIN, never as dropped.

WHAT IT NEVER DOES: `DROP DATABASE ... WITH (FORCE)`, `pg_terminate_backend`, `ALTER DATABASE`, or the provisioning
helper `tests/migrated_schema.py::drop_database`, which disconnects everyone first. A neighbour's session is never
ended. THE ONLY STATEMENT IT SENDS THAT CHANGES ANYTHING is `DROP DATABASE "<name>"`, the name written by
`escaped()` - which doubles every `"` whatever the name is - after the name passed `validated_name()`.

WHAT MAKES A DATABASE A CANDIDATE (`DROP`), all of it:
* its COMPLETE name matches this command's own grammar of a test database name (`NAME_RE`: the literal
  `geov0_test_`, a slug, at most one `__<suffix>`; nothing else, not one character) and then the repository's own
  validation (`scripts/validate_test_database_url.py`), whose answer must be about that same complete name;
* it is not flagged as a template in the catalog (provisioning never sets the flag; a flagged database is
  something else's and is KEPT);
* it is owned by the role this command connects as - the role the test runner creates databases with. This excludes
  another ROLE's databases. It says nothing about WHICH TASK of that role a database belongs to;
* its family is not protected;
* no database of its family has a connection now.
Anything whose belonging cannot be established this way is `KEEP`. A family with a connection is `VERIFY-FIRST`.

WHAT THE COMMAND CANNOT KNOW, and the operator therefore has to establish: that a task is FINISHED and has released
its databases. Every `DROP` row is a proposal that needs that evidence at review. `--protect-none` is an
acknowledgement that nothing is protected, not a proof that every family is disposable. A row of another task of the
same role, added to a manifest by hand and described correctly, is dropped like any other - the manifest is trusted
to be the reviewed one. The maintenance window is the operator's statement; the command enforces no lock.

WHAT IT DOES NOT SEE. A session that connects to a family's OTHER database between the last comparison and the
`DROP`. A database dropped and created again under the same name after its OID was compared. Debugging evidence
somebody wanted to keep. That the server is a disposable one: a local host is not a disposable server - look at the
endpoint and the server identity the summary prints.

RUN (PowerShell). The connection comes from `TEST_DATABASE_URL` (host and credentials only; the database named in
it is not used) or from `--dsn`; the host must be the local one.

    $env:TEST_DATABASE_URL = 'postgresql+asyncpg://geo:geo@127.0.0.1:5432/geov0_test_any'
    python scripts/cleanup_test_databases.py --manifest .local-run/db-cleanup/manifest.json --protect my_live_slug
    # review every DROP row of the manifest, then, in the agreed window:
    python scripts/cleanup_test_databases.py --apply .local-run/db-cleanup/manifest.json `
        --maintenance-window-confirmed --protect my_live_slug --expect-drop-count 412

Exit codes: 0 done; 1 the apply refused or stopped (changed evidence, activity, a refused drop, an uncertain
outcome); 2 wrong arguments, a manifest that cannot be used, or a server that cannot be reached or read.

Importing this module and `--help` connect to nothing; the application and simulator packages are not imported.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.validate_test_database_url import (  # noqa: E402
    UnsafeTestDatabaseError,
    assert_safe_test_database_url,
)

PREFIX = "geov0_test_"
MAINTENANCE_DATABASE = "postgres"
SCRATCH_SEPARATOR = "__"  # `tests/migrated_schema.py::SCRATCH_SEPARATOR`
TEMPLATE_SUFFIX = "tpl"  # `tests/migrated_schema.py::TEMPLATE_SUFFIX`
MAX_IDENTIFIER_LENGTH = 63
MANIFEST_FORMAT = 2
#: How old a manifest may be when it is applied. The freshness of the MANIFEST - of the review it records - and
#: nothing about the age of any database.
MANIFEST_MAX_AGE_MINUTES = 60
LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

#: A task slug, and a scratch suffix: alphanumeric/dash groups joined by SINGLE underscores.
_PART = r"[A-Za-z0-9-]+(?:_[A-Za-z0-9-]+)*"
SLUG_RE = re.compile(_PART)
#: THE COMPLETE GRAMMAR of a name this command may act on. Used with `fullmatch` on the name as the catalog gives
#: it: no character outside it survives - no `?`, quote, space, newline, semicolon.
NAME_RE = re.compile(rf"geov0_test_(?P<slug>{_PART})(?:__(?P<suffix>{_PART}))?")

DROP, KEEP, VERIFY_FIRST = "DROP", "KEEP", "VERIFY-FIRST"
DISPOSITIONS = (DROP, KEEP, VERIFY_FIRST)
TIER, TEMPLATE, CLONE = "tier", "template", "clone"
#: The order of an apply: what depends on nothing first.
APPLY_ORDER = (CLONE, TEMPLATE, TIER)

CATALOG_SQL = """
select d.oid::bigint as oid, d.datname as name, r.rolname as owner, d.datistemplate as is_template,
       pg_database_size(d.oid)::bigint as size_bytes,
       (select count(*) from pg_stat_activity a where a.datid = d.oid)::int as connections
from pg_database d join pg_roles r on r.oid = d.datdba
where d.datname not in ('postgres', 'template0', 'template1')
order by d.datname
"""
SERVER_SQL = """
select (select system_identifier::text from pg_control_system()) as system_identifier,
       current_setting('server_version_num') as server_version_num,
       current_setting('port') as port,
       current_user::text as role
"""


class CleanupRefused(Exception):
    """The apply met something that differs from the manifest, or activity. Nothing more is dropped.

    `outcomes` - the drops that completed; `uncertain` - the name of a drop that was sent and whose outcome is not
    known (the connection was lost, the call was cancelled), or None.
    """

    def __init__(self, message: str, *, outcomes: list[dict[str, str]] | None = None, uncertain: str | None = None):
        super().__init__(message)
        self.outcomes = list(outcomes or [])
        self.uncertain = uncertain


class UsageRefused(Exception):
    """The command line, the connection source, a path or a manifest is not one this command works with (exit 2)."""


@dataclass
class Entry:
    name: str
    oid: int
    owner: str
    size_bytes: int
    is_template: bool
    connections: int
    family: str | None
    kind: str | None
    disposition: str
    reason: str


# ---------------------------------------------------------------------------------------------------- names


def validated_name(name: Any) -> str | None:
    """None when `name` - the COMPLETE identifier, as the catalog gives it - is a test database name; otherwise why
    it is not.

    Three layers, each able to refuse on its own: the literal prefix; this command's full grammar (`NAME_RE`,
    `fullmatch`); the repository's validation. The last takes a URL, and a URL parser keeps only part of what it
    is given (everything from a `?` on is "query"), so it is asked only about a name the grammar has already
    accepted - which has no such character - and its answer must be about that same complete name.
    """

    if not isinstance(name, str):
        return "the name is not text"
    if not name.startswith(PREFIX):
        return f"the name does not start with the literal {PREFIX!r}"
    if len(name.encode("utf-8")) > MAX_IDENTIFIER_LENGTH:
        return f"the name is longer than {MAX_IDENTIFIER_LENGTH} bytes"
    if NAME_RE.fullmatch(name) is None:
        return "the name is not geov0_test_<slug> or geov0_test_<slug>__<suffix> and nothing more"
    try:
        url = assert_safe_test_database_url(
            f"postgresql://validation@127.0.0.1/{name}",
            allow_destructive_reset="1",  # the name is what is validated here; the reset opt-in is not this command's
            repo_root=REPO_ROOT,
            required_backend="postgresql",
            allow_scratch_suffix=True,
        )
    except UnsafeTestDatabaseError as exc:
        return f"the name fails the repository's test-database validation: {exc}"
    if url.database != name:
        return "the repository's validation answered about another name than the one it was asked"
    return None


def escaped(name: str) -> str:
    """`name` as a quoted SQL identifier: every `"` doubled. Correct for ANY name, and deliberately independent of
    `validated_name` - a statement built with it can name only the database called exactly `name`."""

    return '"' + name.replace('"', '""') + '"'


def drop_statement(name: str) -> str:
    """The one changing statement of this command. Refused for a name that is not a validated one."""

    problem = validated_name(name)
    if problem is not None:
        raise CleanupRefused(f"refusing to write {name!r} into a statement: {problem}")
    return f"DROP DATABASE {escaped(name)}"


def family_and_kind(name: str) -> tuple[str, str]:
    """The task slug a VALIDATED name belongs to, and what the database is.

    Provisioning does not set `datistemplate`; what tells its templates is its own naming: every template suffix it
    uses ends in `tpl` (`TEMPLATE_SUFFIX`, `modebtpl`, `<stage>tpl`). That is a naming convention, not a catalog
    fact, and it decides ONLY the order of an apply - a clone does not depend on its template once it exists.
    """

    match = NAME_RE.fullmatch(name)
    assert match is not None, name
    suffix = match.group("suffix")
    if suffix is None:
        return match.group("slug"), TIER
    return match.group("slug"), (TEMPLATE if suffix.endswith(TEMPLATE_SUFFIX) else CLONE)


def validated_slugs(slugs: Iterable[Any]) -> list[str]:
    """Protected families as given, or `UsageRefused`: a slug that is not a slug protects nothing."""

    checked = []
    for slug in slugs:
        if not isinstance(slug, str) or SLUG_RE.fullmatch(slug) is None:
            raise UsageRefused(f"{slug!r} is not a task slug (letters, digits, dashes, single underscores)")
        checked.append(slug)
    return sorted(set(checked))


# ------------------------------------------------------------------------------------------------ selection


def _not_ours(row: dict[str, Any], *, role: str) -> str | None:
    """Why this catalog row is not a database this command may act on at all, or None. One predicate for the dry
    run and for every comparison of an apply."""

    problem = validated_name(row["name"])
    if problem is not None:
        return f"not a test database of this repository: {problem}"
    if bool(row["is_template"]):
        return "flagged as a template in the catalog: provisioning never sets that flag, so it is not provisioning's"
    if str(row["owner"]) != role:
        return f"owned by {row['owner']!r}, not by the connecting role {role!r}: its provenance is not established"
    return None


def busy_unprotected_families(rows: Iterable[dict[str, Any]], *, protected: Iterable[str]) -> dict[str, str]:
    """Every test family on the server that has a connection and is not protected -> one connected database of it.
    Whatever the owner and whether or not any row of a manifest names it."""

    protected = set(protected)
    busy: dict[str, str] = {}
    for row in rows:
        if int(row["connections"] or 0) > 0 and validated_name(row["name"]) is None:
            family = family_and_kind(row["name"])[0]
            if family not in protected:
                busy.setdefault(family, str(row["name"]))
    return busy


def classify(rows: Iterable[dict[str, Any]], *, role: str, protected: Iterable[str]) -> list[Entry]:
    """The proposed disposition of every database of the catalog. Pure: it reads `rows` and nothing else."""

    rows = [dict(row) for row in rows]
    protected = set(protected)
    busy = busy_unprotected_families(rows, protected=protected)
    entries: list[Entry] = []
    for row in rows:
        entry = Entry(
            name=str(row["name"]), oid=int(row["oid"]), owner=str(row["owner"]), size_bytes=int(row["size_bytes"] or 0),
            is_template=bool(row["is_template"]), connections=int(row["connections"] or 0),
            family=None, kind=None, disposition=KEEP, reason="",
        )
        entries.append(entry)
        if validated_name(entry.name) is None:
            entry.family, entry.kind = family_and_kind(entry.name)
        problem = _not_ours(row, role=role)
        if problem is not None:
            entry.reason = problem
        elif entry.family in protected:
            entry.reason = f"family {entry.family!r} is protected"
        elif entry.family in busy:
            entry.disposition = VERIFY_FIRST
            entry.reason = (
                f"{entry.connections} connection(s) to this database now" if entry.connections
                else f"its family has a connected database ({busy[entry.family]})"
            )
        else:
            entry.disposition = DROP
            entry.reason = "a test database of an unprotected family; no connection in the family now. Is the task finished?"
    return entries


def recheck(entry: dict[str, Any], rows: list[dict[str, Any]], *, manifest_server: dict[str, Any],
            server: dict[str, Any], protected: Iterable[str]) -> str | None:
    """Why this manifest row may NOT be dropped now, or None. Everything is read again from `rows` (the catalog
    now); from the manifest only the identity it names is taken."""

    name = entry["name"]
    if not server.get("system_identifier") or server.get("system_identifier") != manifest_server.get("system_identifier"):
        return f"{name}: this is not the server the manifest was made on"
    fresh = next((row for row in rows if row["name"] == name), None)
    if fresh is None:
        return f"{name}: the database is not in the catalog any more"
    problem = _not_ours(fresh, role=str(server["role"]))
    if problem is not None:
        return f"{name}: {problem}"
    if int(fresh["oid"]) != entry["oid"]:
        return f"{name}: its OID is {fresh['oid']}, the manifest names {entry['oid']} - another database took the name"
    if str(fresh["owner"]) != entry["owner"]:
        return f"{name}: its owner is {fresh['owner']!r}, the manifest names {entry['owner']!r}"
    if family_and_kind(name) != (entry["family"], entry["kind"]):
        return f"{name}: it is not the {entry['kind']} of family {entry['family']!r} the manifest describes"
    if entry["family"] in set(protected):
        return f"{name}: family {entry['family']!r} is protected"
    busy = busy_unprotected_families(rows, protected=protected)
    if busy:
        family, database = sorted(busy.items())[0]
        return (f"{name}: {len(busy)} unprotected test famil{'y has' if len(busy) == 1 else 'ies have'} a connection "
                f"now ({family!r}: {database}) - the server is not quiet")
    return None


# ------------------------------------------------------------------------------------------------- manifest


def validated_manifest(manifest: Any, *, now: datetime | None = None) -> dict[str, Any]:
    """The manifest, its WHOLE structure checked - before any connection and before any drop - or `UsageRefused`.
    Returns it with `protected` validated and the `DROP` rows in `drops`, in the order of an apply."""

    def refuse(what: str):
        raise UsageRefused(f"the manifest cannot be used: {what}")

    if not isinstance(manifest, dict):
        refuse("it is not an object")
    if manifest.get("format") != MANIFEST_FORMAT:
        refuse(f"its format is {manifest.get('format')!r}, this command reads {MANIFEST_FORMAT} - make a new dry run")
    try:
        created = datetime.fromisoformat(str(manifest.get("created_at")))
    except ValueError:
        refuse("`created_at` is not a time")
    if created.tzinfo is None:
        refuse("`created_at` has no time zone")
    age = (now or datetime.now(timezone.utc)) - created
    if age > timedelta(minutes=MANIFEST_MAX_AGE_MINUTES) or age < timedelta(minutes=-5):
        refuse(f"it was made {age} ago; a manifest is applied within {MANIFEST_MAX_AGE_MINUTES} minutes of its dry "
               f"run - make a new one and review it again (this is the age of the manifest, not of any database)")
    server = manifest.get("server")
    if not isinstance(server, dict) or not isinstance(server.get("system_identifier"), str) or not server["system_identifier"]:
        refuse("`server.system_identifier` is missing")
    if not isinstance(manifest.get("protected"), list):
        refuse("`protected` is not a list")
    protected = validated_slugs(manifest["protected"])
    databases = manifest.get("databases")
    if not isinstance(databases, list):
        refuse("`databases` is not a list")
    seen: set[str] = set()
    drops: list[dict[str, Any]] = []
    for index, row in enumerate(databases):
        where = f"`databases[{index}]`"
        if not isinstance(row, dict):
            refuse(f"{where} is not an object")
        name = row.get("name")
        if not isinstance(name, str) or not name:
            refuse(f"{where}.name is not text")
        if name in seen:
            refuse(f"{name!r} is listed twice")
        seen.add(name)
        if row.get("disposition") not in DISPOSITIONS:
            refuse(f"{where}.disposition is {row.get('disposition')!r}")
        if row["disposition"] != DROP:
            continue
        if isinstance(row.get("oid"), bool) or not isinstance(row.get("oid"), int) or row["oid"] <= 0:
            refuse(f"{name!r}: `oid` is {row.get('oid')!r}, not a positive integer")
        if not isinstance(row.get("owner"), str) or not row["owner"]:
            refuse(f"{name!r}: `owner` is missing")
        problem = validated_name(name)
        if problem is not None:
            refuse(f"{name!r} is marked DROP: {problem}")
        if (row.get("family"), row.get("kind")) != family_and_kind(name):
            refuse(f"{name!r}: `family`/`kind` are {row.get('family')!r}/{row.get('kind')!r}, the name says "
                   f"{family_and_kind(name)}")
        if row.get("is_template") is not False:
            refuse(f"{name!r} is marked DROP and flagged as a template")
        drops.append(row)
    return {
        "server": server,
        "protected": protected,
        "drops": [row for kind in APPLY_ORDER for row in drops if row["kind"] == kind],
    }


async def read_server(connection) -> dict[str, Any]:
    return dict(await connection.fetchrow(SERVER_SQL))


async def read_catalog(connection, *, sizes: bool = True) -> list[dict[str, Any]]:
    """Every database but the system ones. `sizes=False` is the comparison before a drop: the same rows without
    `pg_database_size`, which walks each database's files and is not evidence the comparison uses."""

    sql = CATALOG_SQL if sizes else CATALOG_SQL.replace("pg_database_size(d.oid)::bigint", "0::bigint")
    return [dict(row) for row in await connection.fetch(sql)]


async def build_manifest(connection, *, protected: Sequence[str]) -> dict[str, Any]:
    """The dry run: two SELECT statements, a classification, a dictionary."""

    protected = validated_slugs(protected)
    server = await read_server(connection)
    entries = classify(await read_catalog(connection), role=str(server["role"]), protected=protected)
    return {
        "format": MANIFEST_FORMAT,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "server": server,
        "protected": protected,
        "totals": totals(entries),
        "databases": [asdict(entry) for entry in entries],
    }


def totals(entries: Iterable[Entry]) -> dict[str, Any]:
    entries = list(entries)
    ours = [e for e in entries if e.family is not None]
    by_disposition = {
        disposition: {
            "databases": sum(1 for e in entries if e.disposition == disposition),
            "size_bytes": sum(e.size_bytes for e in entries if e.disposition == disposition),
        }
        for disposition in DISPOSITIONS
    }
    return {
        "databases": len(entries),
        "test_databases": len(ours),
        "families": len({e.family for e in ours}),
        "size_bytes": sum(e.size_bytes for e in entries),
        "test_size_bytes": sum(e.size_bytes for e in ours),
        "by_disposition": by_disposition,
    }


# ---------------------------------------------------------------------------------------------------- apply


async def apply_manifest(connection, manifest: dict[str, Any], *, protected: Sequence[str], expect_drop_count: int,
                         now: datetime | None = None, report=print) -> list[dict[str, str]]:
    """Drop the `DROP` rows of `manifest`. Raises `UsageRefused` for a manifest that cannot be used and
    `CleanupRefused` when anything may not be dropped; nothing is sent before EVERY row has passed once."""

    checked = validated_manifest(manifest, now=now)
    protected = sorted(set(validated_slugs(protected)) | set(checked["protected"]))
    drops, manifest_server = checked["drops"], checked["server"]

    # Before the first drop: every row against the catalog as it is now, with sizes for the listing.
    server = await read_server(connection)
    catalog = await read_catalog(connection)
    for row in drops:
        problem = recheck(row, catalog, manifest_server=manifest_server, server=server, protected=protected)
        if problem is not None:
            raise CleanupRefused(problem)
    sizes = {row["name"]: int(row["size_bytes"] or 0) for row in catalog}
    report(f"protected families: {', '.join(protected) or '(none - declared)'}")
    report(f"to drop: {len(drops)} database(s) of {len({row['family'] for row in drops})} families, "
           f"{_megabytes(sum(sizes[row['name']] for row in drops))}")
    for row in drops:
        report(f"  {row['kind']:<8} {row['name']}  oid {row['oid']}  {_megabytes(sizes[row['name']])}")
    if len(drops) != expect_drop_count:
        raise CleanupRefused(
            f"the manifest asks for {len(drops)} drop(s), --expect-drop-count says {expect_drop_count}: "
            f"review the listing above and state the number it shows"
        )

    outcomes: list[dict[str, str]] = []
    for row in drops:
        name = row["name"]
        try:
            problem = recheck(row, await read_catalog(connection, sizes=False), manifest_server=manifest_server,
                              server=await read_server(connection), protected=protected)
        except BaseException as exc:
            raise CleanupRefused(f"{name}: the catalog could not be read before the drop ({type(exc).__name__})",
                                 outcomes=outcomes) from exc
        if problem is not None:
            raise CleanupRefused(problem, outcomes=outcomes)
        await _drop(connection, name, outcomes)
        outcomes.append({"name": name, "outcome": "dropped"})
    return outcomes


async def _drop(connection, name: str, outcomes: list[dict[str, str]]) -> None:
    import asyncpg

    statement = drop_statement(name)
    try:
        # An ordinary DROP DATABASE: PostgreSQL refuses it while any session is connected. No FORCE, ever.
        await connection.execute(statement)
    except asyncpg.PostgresError as exc:
        # The server ANSWERED with an error: the database was not dropped.
        raise CleanupRefused(f"{name}: PostgreSQL refused the drop ({type(exc).__name__}: {exc})",
                             outcomes=outcomes) from exc
    except BaseException as exc:
        # No answer - a lost connection, a cancellation. Whether it was dropped is NOT known.
        raise CleanupRefused(f"{name}: the drop was sent and its outcome is not known ({type(exc).__name__})",
                             outcomes=outcomes, uncertain=name) from exc


# ------------------------------------------------------------------------------------------------- the command line


def _megabytes(size: int) -> str:
    return f"{size / 1024 / 1024:,.1f} MB"


def summary(manifest: dict[str, Any]) -> str:
    server, total = manifest["server"], manifest["totals"]
    lines = [
        f"server {server.get('system_identifier')} (version {server.get('server_version_num')}, port "
        f"{server.get('port')}), connected as {server.get('role')!r}",
        f"databases: {total['databases']} ({_megabytes(total['size_bytes'])}); test databases of this repository: "
        f"{total['test_databases']} in {total['families']} families ({_megabytes(total['test_size_bytes'])})",
        f"protected families: {', '.join(manifest['protected']) or '(none)'}",
    ]
    for disposition in DISPOSITIONS:
        part = total["by_disposition"][disposition]
        lines.append(f"  {disposition:<12} {part['databases']:>5} database(s)  {_megabytes(part['size_bytes']):>14}")
    held = [e for e in manifest["databases"] if e["disposition"] != DROP and e["family"] is not None]
    for entry in held[:40]:
        lines.append(f"  {entry['disposition']:<12} {entry['name']}: {entry['reason']}")
    if len(held) > 40:
        lines.append(f"  ... and {len(held) - 40} more held test database(s); see the manifest")
    return "\n".join(lines)


def _inside_local_run(path: Path) -> Path:
    """A manifest lives under THIS checkout's `.local-run/` (AGENTS.md section 12); a relative path is taken from it."""

    resolved = (path if path.is_absolute() else REPO_ROOT / path).resolve()
    if not resolved.is_relative_to((REPO_ROOT / ".local-run").resolve()):
        raise UsageRefused(f"{resolved} is not under {REPO_ROOT / '.local-run'}")
    return resolved


def _connection_arguments(dsn: str | None) -> dict[str, Any]:
    from sqlalchemy.engine import make_url

    source = dsn or os.environ.get("TEST_DATABASE_URL") or ""
    if not source.strip():
        raise UsageRefused("no connection: set TEST_DATABASE_URL (host and credentials are taken from it) or pass --dsn")
    try:
        url = make_url(source)
    except Exception:
        raise UsageRefused("the connection URL is not a valid database URL") from None
    # Only the host, port and credentials are read from the URL; the connection itself is asyncpg's, to PostgreSQL.
    if (url.host or "") not in LOCAL_HOSTS:
        raise UsageRefused(f"the host is {url.host!r}; this command works on the local server only (127.0.0.1)")
    return {"host": url.host, "port": url.port or 5432, "user": url.username, "password": url.password,
            "database": MAINTENANCE_DATABASE, "timeout": 15.0}


def _arguments(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inventory the test databases of a shared PostgreSQL server (dry run, the default), or drop "
                    "exactly the DROP rows of a reviewed manifest. Never ends another session.",
    )
    parser.add_argument("--manifest", type=Path, help="dry run: where to write the manifest (under .local-run/)")
    parser.add_argument("--apply", type=Path, metavar="MANIFEST", help="drop the DROP rows of this reviewed manifest")
    parser.add_argument("--maintenance-window-confirmed", action="store_true",
                        help="required with --apply: every session using the server has paused its database work")
    parser.add_argument("--protect", action="append", default=[], metavar="SLUG",
                        help="a family (task slug) that must be kept whole; repeatable")
    parser.add_argument("--protect-none", action="store_true",
                        help="with --apply instead of --protect: the explicit statement that no family is protected")
    parser.add_argument("--expect-drop-count", type=int, metavar="N",
                        help="required with --apply: the number of DROP rows you reviewed")
    parser.add_argument("--dsn", help="connection URL (default: host and credentials of TEST_DATABASE_URL)")
    arguments = parser.parse_args(argv)
    if (arguments.manifest is None) == (arguments.apply is None):
        parser.error("give exactly one of --manifest (dry run) and --apply")
    if arguments.protect and arguments.protect_none:
        parser.error("--protect and --protect-none contradict each other")
    if arguments.apply is not None:
        if not arguments.maintenance_window_confirmed:
            parser.error("--apply needs --maintenance-window-confirmed: an agreed window in which no session uses the server")
        if not arguments.protect and not arguments.protect_none:
            parser.error("--apply needs a declaration of what is protected: --protect <slug> for every family still "
                         "wanted, or --protect-none to state that there is none")
        if arguments.expect_drop_count is None:
            parser.error("--apply needs --expect-drop-count N: the number of DROP rows you reviewed")
    return arguments


def _write(path: Path, content: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(content, indent=1, ensure_ascii=False), encoding="utf-8", newline="\n")


async def _run(arguments: argparse.Namespace) -> int:
    import asyncpg

    # Everything that can be refused without the server is refused before a connection is opened: the path, the
    # protected slugs, the connection source and - for an apply - the whole manifest.
    path = _inside_local_run(arguments.manifest if arguments.manifest is not None else arguments.apply)
    protected = validated_slugs(arguments.protect)
    connect = _connection_arguments(arguments.dsn)
    manifest = None
    if arguments.apply is not None:
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise UsageRefused(f"the manifest cannot be read: {type(exc).__name__}: {exc}") from exc
        validated_manifest(manifest)

    try:
        connection = await asyncpg.connect(**connect)
    except (OSError, asyncio.TimeoutError, asyncpg.PostgresError) as exc:
        raise UsageRefused(f"the server cannot be reached: {type(exc).__name__}: {exc}") from exc
    try:
        if manifest is None:
            try:
                manifest = await build_manifest(connection, protected=protected)
            except asyncpg.PostgresError as exc:
                raise UsageRefused(f"the catalog cannot be read: {type(exc).__name__}: {exc}") from exc
            _write(path, manifest)
            print(summary(manifest))
            print(f"DRY RUN: nothing was changed. Manifest: {path}")
            return 0

        result = path.with_suffix(".result.json")
        outcomes: list[dict[str, str]] = []
        stopped: str | None = None
        uncertain: str | None = None
        try:
            outcomes = await apply_manifest(connection, manifest, protected=protected,
                                            expect_drop_count=arguments.expect_drop_count)
        except CleanupRefused as refusal:
            # Every failure from the first drop on arrives as this, carrying what completed and what is uncertain.
            outcomes, stopped, uncertain = refusal.outcomes, str(refusal), refusal.uncertain
        except asyncpg.PostgresError as exc:
            # Only the reads before the first drop can raise this: nothing was dropped.
            stopped = f"the catalog could not be read before anything was dropped: {type(exc).__name__}: {exc}"
        _write(result, {"manifest": str(path), "dropped": outcomes, "uncertain": uncertain, "stopped_on": stopped})
        print(f"dropped {len(outcomes)} database(s). Result: {result}")
        if uncertain is not None:
            print(f"UNCERTAIN: the drop of {uncertain} was sent and its outcome is not known - look at the catalog",
                  file=sys.stderr)
        if stopped is not None:
            print(f"STOPPED, nothing more was dropped: {stopped}", file=sys.stderr)
            return 1
        return 0
    finally:
        await connection.close()


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _arguments(argv)
    try:
        return asyncio.run(_run(arguments))
    except UsageRefused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
