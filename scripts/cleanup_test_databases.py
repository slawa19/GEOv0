"""Reclaim the disk of test databases on a shared PostgreSQL server - by a reviewed manifest, never by itself.

WHY THIS EXISTS (decision of 2026-10-10, `QD-TEST-DATABASES: BUILD-NARROW`; AGENTS.md section 12). Every task slug
leaves its tier database `geov0_test_<slug>` and its template `geov0_test_<slug>__tpl` on the server; a run sweeps
only the scratch family of ITS OWN slug, so the databases of slugs that never run again stay for good (523
databases, about 5 GB, were counted on one machine). Nothing here deletes them automatically, and nothing should:
the server is shared by parallel sessions, and THE ABSENCE OF CONNECTIONS DOES NOT PROVE THAT A TASK HAS LET GO OF ITS
DATABASE OR ITS TEMPLATE - a session between two runs holds none. The catalog has no creation time and no last-use
time either, so "unused for N hours" cannot be read from it and is not invented here.

WHAT THE COMMAND DOES.

* DRY RUN (the default). Reads the catalog through the maintenance database `postgres` and writes a MANIFEST: the
  server's identity, and for every database its exact name, OID, owner, size, template flag, number of
  connections, family (the task slug), kind (tier / template / clone) and a proposed disposition with its reason:
  `DROP`, `KEEP` or `VERIFY-FIRST`. It sends SELECT statements only.
* APPLY (`--apply <manifest> --maintenance-window-confirmed`). Drops exactly the `DROP` entries of that manifest,
  clones first, then templates, then tier databases. Before EACH drop it reads the catalog again and compares the
  server, the OID, the owner and the template flag with the manifest, checks the name again, and requires that no
  database of that family has a connection. On any difference it STOPS - it does not skip and go on. The drop is
  an ordinary `DROP DATABASE`, which PostgreSQL itself refuses while anyone is connected; a refusal stops the run
  too. A result file is written beside the manifest.

WHAT IT NEVER DOES: `DROP DATABASE ... WITH (FORCE)`, `pg_terminate_backend`, or the provisioning helper
`tests/migrated_schema.py::drop_database`, which disconnects everyone first. A neighbour's session is never ended.

WHAT MAKES A DATABASE A CANDIDATE (`DROP`), all of it:
* the name starts with the literal `geov0_test_`;
* the name passes the repository's own validation of a test database name
  (`scripts/validate_test_database_url.py::assert_safe_test_database_url`, scratch names allowed) and is of a kind
  this command knows: a tier database, a template (a scratch name whose suffix ends in `tpl` - provisioning's
  naming - or any scratch database flagged as a template), a clone (any other scratch name);
* it is owned by the role this command connects as - the role the test runner creates databases with;
* its family is not protected (`--protect <slug>`, repeatable: the whole family, its disconnected template
  included);
* no database of its family has a connection now.
Anything whose belonging cannot be established this way is `KEEP`. A family with a connection is `VERIFY-FIRST`.

THE PRECONDITION THE COMMAND CANNOT CHECK, and therefore asks the operator to state: a maintenance window. Every
session that uses the server has finished or paused its database work and starts no test until the cleanup is over.
Zero connections is necessary, not sufficient; `--protect` every family that is still wanted.

WHAT IT DOES NOT SEE. Whether a task is finished - only the operator knows; the dispositions are a proposal for
review. A session that connects between the re-check and the `DROP`: PostgreSQL refuses the drop (that is the
guard), but a session that connects to a family's OTHER database in that instant is not seen. Debugging evidence
somebody wanted to keep. Databases of another role.

RUN (PowerShell). The connection comes from `TEST_DATABASE_URL` (host and credentials only; the database named in
it is not used) or from `--dsn`; the host must be the local one.

    $env:TEST_DATABASE_URL = 'postgresql+asyncpg://geo:geo@127.0.0.1:5432/geov0_test_any'
    python scripts/cleanup_test_databases.py --manifest .local-run/db-cleanup/manifest.json --protect my_live_slug
    # review the manifest, then, in the agreed window:
    python scripts/cleanup_test_databases.py --apply .local-run/db-cleanup/manifest.json --maintenance-window-confirmed

Exit codes: 0 done; 1 the apply stopped on changed evidence, activity or a refused drop; 2 wrong arguments, an
unusable connection or manifest.

Importing this module and `--help` connect to nothing; the application and simulator packages are not imported.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
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
MANIFEST_FORMAT = 1
LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

DROP, KEEP, VERIFY_FIRST = "DROP", "KEEP", "VERIFY-FIRST"
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
    """The apply met something that differs from the manifest, or activity. Nothing more is dropped."""


class UsageRefused(Exception):
    """The command line, the connection source or a path is not one this command works with (exit 2)."""


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


def validated_name(name: str) -> str | None:
    """None when `name` is a test database name by the repository's own rule; otherwise why it is not.

    The literal prefix is checked here first and on its own, so that it holds whatever the shared validator does.
    """

    if not name.startswith(PREFIX):
        return f"the name does not start with the literal {PREFIX!r}"
    if len(name.encode("utf-8")) > MAX_IDENTIFIER_LENGTH:
        return f"the name is longer than {MAX_IDENTIFIER_LENGTH} bytes"
    try:
        assert_safe_test_database_url(
            f"postgresql://validation@127.0.0.1/{name}",
            allow_destructive_reset="1",  # the name is what is validated here; the reset opt-in is not this command's
            repo_root=REPO_ROOT,
            required_backend="postgresql",
            allow_scratch_suffix=True,
        )
    except UnsafeTestDatabaseError as exc:
        return f"the name fails the repository's test-database validation: {exc}"
    return None


def family_and_kind(name: str, is_template: bool) -> tuple[str | None, str | None]:
    """The task slug a VALIDATED name belongs to, and what the database is; (family, None) when it is neither."""

    slug, separator, suffix = name[len(PREFIX):].partition(SCRATCH_SEPARATOR)
    if not separator:
        return slug, (None if is_template else TIER)  # a tier database flagged as a template is nothing we make
    # Provisioning does NOT set `datistemplate` (measured on a live server, 2026-10-10: 301 templates, none
    # flagged). What tells its templates is its own naming: every template suffix it uses ends in `tpl`
    # (`TEMPLATE_SUFFIX`, `modebtpl`, `<stage>tpl`). That is a naming convention, not a catalog fact, and it decides
    # ONLY the order of an apply - a clone does not depend on its template once it exists. A scratch database that
    # IS flagged is a template whatever its name, and its flag is what an apply has to lift.
    return slug, (TEMPLATE if is_template or suffix.endswith(TEMPLATE_SUFFIX) else CLONE)


def classify(rows: Iterable[dict[str, Any]], *, role: str, protected: Iterable[str]) -> list[Entry]:
    """The proposed disposition of every database of the catalog. Pure: it reads `rows` and nothing else."""

    protected = set(protected)
    entries: list[Entry] = []
    for row in rows:
        name = str(row["name"])
        entry = Entry(
            name=name, oid=int(row["oid"]), owner=str(row["owner"]), size_bytes=int(row["size_bytes"] or 0),
            is_template=bool(row["is_template"]), connections=int(row["connections"] or 0),
            family=None, kind=None, disposition=KEEP, reason="",
        )
        entries.append(entry)
        problem = validated_name(name)
        if problem is not None:
            entry.reason = f"not a test database of this repository: {problem}"
            continue
        entry.family, entry.kind = family_and_kind(name, entry.is_template)
        if entry.kind is None:
            entry.reason = "unknown kind: a tier database flagged as a template is not something provisioning makes"
        elif entry.owner != role:
            entry.reason = f"owned by {entry.owner!r}, not by the connecting role {role!r}: its provenance is not established"
        elif entry.family in protected:
            entry.reason = f"family {entry.family!r} is protected"
        else:
            entry.disposition, entry.reason = DROP, "a test database of an unprotected family with no connection in the family"
    # A connection anywhere in a family holds the whole family back, its disconnected template included.
    busy = {e.family: e.name for e in entries if e.family is not None and e.connections > 0}
    for entry in entries:
        if entry.disposition == DROP and entry.family in busy:
            entry.disposition = VERIFY_FIRST
            entry.reason = (
                f"{entry.connections} connection(s) to this database now" if entry.connections
                else f"its family has a connected database ({busy[entry.family]})"
            )
    return entries


def recheck(entry: dict[str, Any], fresh: dict[str, Any] | None, family_connections: int, *,
            manifest_server: dict[str, Any], server: dict[str, Any], role: str, protected: Iterable[str]) -> str | None:
    """Why this manifest entry may NOT be dropped now, or None. Everything is compared again; nothing is trusted
    from the manifest but the identity it names."""

    name = str(entry.get("name", ""))
    if entry.get("disposition") != DROP:
        return f"{name}: the manifest does not say DROP"
    if server.get("system_identifier") != manifest_server.get("system_identifier") or not server.get("system_identifier"):
        return f"{name}: this is not the server the manifest was made on"
    problem = validated_name(name)
    if problem is not None:
        return f"{name}: {problem}"
    if fresh is None:
        return f"{name}: the database is not in the catalog any more"
    if int(fresh["oid"]) != int(entry.get("oid", -1)):
        return f"{name}: its OID is {fresh['oid']}, the manifest names {entry.get('oid')} - another database took the name"
    if str(fresh["owner"]) != str(entry.get("owner")) or str(fresh["owner"]) != role:
        return f"{name}: its owner is {fresh['owner']!r} (manifest {entry.get('owner')!r}, connecting role {role!r})"
    if bool(fresh["is_template"]) != bool(entry.get("is_template")):
        return f"{name}: its template flag changed since the manifest"
    family, kind = family_and_kind(name, bool(fresh["is_template"]))
    if kind is None or kind != entry.get("kind") or family != entry.get("family"):
        return f"{name}: it is not the {entry.get('kind')} of family {entry.get('family')!r} the manifest describes"
    if family in set(protected):
        return f"{name}: family {family!r} is protected"
    if family_connections > 0:
        return f"{name}: {family_connections} connection(s) in family {family!r} now"
    return None


def quoted(name: str) -> str:
    problem = validated_name(name)
    if problem is not None:
        raise CleanupRefused(f"refusing to write {name!r} into a statement: {problem}")
    return f'"{name}"'


async def read_server(connection) -> dict[str, Any]:
    return dict(await connection.fetchrow(SERVER_SQL))


async def read_catalog(connection, *, sizes: bool = True) -> list[dict[str, Any]]:
    """Every database but the system ones. `sizes=False` is the re-check before a drop: the same rows without
    `pg_database_size`, which walks each database's files and is not evidence the re-check uses."""

    sql = CATALOG_SQL if sizes else CATALOG_SQL.replace("pg_database_size(d.oid)::bigint", "0::bigint")
    return [dict(row) for row in await connection.fetch(sql)]


async def build_manifest(connection, *, protected: Sequence[str]) -> dict[str, Any]:
    """The dry run: two SELECT statements, a classification, a dictionary."""

    server = await read_server(connection)
    entries = classify(await read_catalog(connection), role=str(server["role"]), protected=protected)
    return {
        "format": MANIFEST_FORMAT,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "server": server,
        "protected": sorted(set(protected)),
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
        for disposition in (DROP, KEEP, VERIFY_FIRST)
    }
    return {
        "databases": len(entries),
        "test_databases": len(ours),
        "families": len({e.family for e in ours}),
        "size_bytes": sum(e.size_bytes for e in entries),
        "test_size_bytes": sum(e.size_bytes for e in ours),
        "by_disposition": by_disposition,
    }


async def apply_manifest(connection, manifest: dict[str, Any], *, protected: Sequence[str]) -> list[dict[str, str]]:
    """Drop the `DROP` entries of `manifest`, re-checking each. Raises `CleanupRefused` at the first that may not be
    dropped; the outcomes so far travel on the exception (`.outcomes`)."""

    if manifest.get("format") != MANIFEST_FORMAT:
        raise CleanupRefused(f"the manifest format is {manifest.get('format')!r}, this command reads {MANIFEST_FORMAT}")
    protected = sorted(set(protected) | set(manifest.get("protected") or []))
    wanted = [e for e in manifest.get("databases", []) if e.get("disposition") == DROP]
    unknown = sorted({str(e.get("kind")) for e in wanted} - set(APPLY_ORDER))
    if unknown:
        raise CleanupRefused(f"the manifest asks to drop databases of unknown kind {unknown}")
    ordered = [e for kind in APPLY_ORDER for e in wanted if e.get("kind") == kind]
    outcomes: list[dict[str, str]] = []
    try:
        for entry in ordered:
            await _drop_one(connection, entry, manifest_server=manifest.get("server") or {}, protected=protected)
            outcomes.append({"name": entry["name"], "outcome": "dropped"})
    except CleanupRefused as refusal:
        refusal.outcomes = outcomes  # type: ignore[attr-defined]
        raise
    return outcomes


async def _drop_one(connection, entry: dict[str, Any], *, manifest_server: dict[str, Any], protected: Sequence[str]) -> None:
    import asyncpg

    server = await read_server(connection)
    catalog = await read_catalog(connection, sizes=False)
    name = str(entry.get("name", ""))
    fresh = next((row for row in catalog if row["name"] == name), None)
    family_connections = sum(
        int(row["connections"] or 0) for row in catalog
        if validated_name(str(row["name"])) is None
        and family_and_kind(str(row["name"]), bool(row["is_template"]))[0] == entry.get("family")
    )
    problem = recheck(entry, fresh, family_connections, manifest_server=manifest_server, server=server,
                      role=str(server["role"]), protected=protected)
    if problem is not None:
        raise CleanupRefused(problem)

    was_template = bool(fresh["is_template"])  # type: ignore[index]
    if was_template:
        # Only for a template the manifest names and the re-check just confirmed: a template cannot be dropped.
        await connection.execute(f"ALTER DATABASE {quoted(name)} IS_TEMPLATE false")
    try:
        # An ordinary DROP DATABASE: PostgreSQL refuses it while any session is connected. No FORCE, ever.
        await connection.execute(f"DROP DATABASE {quoted(name)}")
    except asyncpg.PostgresError as exc:
        if was_template:
            await connection.execute(f"ALTER DATABASE {quoted(name)} IS_TEMPLATE true")
        raise CleanupRefused(f"{name}: PostgreSQL refused the drop ({type(exc).__name__}: {exc})") from exc


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
    for disposition in (DROP, KEEP, VERIFY_FIRST):
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
        raise UsageRefused(f"refused: the host is {url.host!r}; this command works on the local server only (127.0.0.1)")
    return {"host": url.host, "port": url.port or 5432, "user": url.username, "password": url.password,
            "database": MAINTENANCE_DATABASE, "timeout": 15.0}


def _arguments(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inventory the test databases of a shared PostgreSQL server (dry run, the default), or drop "
                    "exactly the DROP entries of a reviewed manifest. Never ends another session.",
    )
    parser.add_argument("--manifest", type=Path, help="dry run: where to write the manifest (under .local-run/)")
    parser.add_argument("--apply", type=Path, metavar="MANIFEST", help="drop the DROP entries of this reviewed manifest")
    parser.add_argument("--maintenance-window-confirmed", action="store_true",
                        help="required with --apply: every session using the server has paused its database work")
    parser.add_argument("--protect", action="append", default=[], metavar="SLUG",
                        help="a family (task slug) that must be kept whole; repeatable")
    parser.add_argument("--dsn", help="connection URL (default: host and credentials of TEST_DATABASE_URL)")
    arguments = parser.parse_args(argv)
    if (arguments.manifest is None) == (arguments.apply is None):
        parser.error("give exactly one of --manifest (dry run) and --apply")
    if arguments.apply is not None and not arguments.maintenance_window_confirmed:
        parser.error("--apply needs --maintenance-window-confirmed: an agreed window in which no session uses the server")
    return arguments


async def _run(arguments: argparse.Namespace) -> int:
    import asyncpg

    # Everything that can be refused without the server is refused before a connection is opened.
    path = _inside_local_run(arguments.manifest if arguments.manifest is not None else arguments.apply)
    connection = await asyncpg.connect(**_connection_arguments(arguments.dsn))
    try:
        if arguments.manifest is not None:
            target = path
            manifest = await build_manifest(connection, protected=arguments.protect)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(manifest, indent=1, ensure_ascii=False), encoding="utf-8", newline="\n")
            print(summary(manifest))
            print(f"DRY RUN: nothing was changed. Manifest: {target}")
            return 0

        source = path
        manifest = json.loads(source.read_text(encoding="utf-8"))
        result = source.with_suffix(".result.json")
        try:
            outcomes = await apply_manifest(connection, manifest, protected=arguments.protect)
            stopped = None
        except CleanupRefused as refusal:
            outcomes, stopped = getattr(refusal, "outcomes", []), str(refusal)
        result.write_text(
            json.dumps({"manifest": str(source), "dropped": outcomes, "stopped_on": stopped}, indent=1, ensure_ascii=False),
            encoding="utf-8", newline="\n",
        )
        print(f"dropped {len(outcomes)} database(s). Result: {result}")
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
    except (OSError, json.JSONDecodeError) as exc:
        print(f"unusable connection or manifest: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
