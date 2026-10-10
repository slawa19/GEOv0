"""`scripts/cleanup_test_databases.py`: WHICH databases it proposes to drop, and what it refuses at apply time.

No PostgreSQL here: the catalog is a list of rows and the connection is a recorder, which is enough for the
selection logic and for "the dry run sends no statement that changes anything". That an ordinary `DROP DATABASE` is
refused by the server while a session is connected, and that the neighbour's session survives, is NOT shown here -
that is `tests/integration/test_p035_cleanup_test_databases_postgres.py`, on real databases.

What is pinned:
* a name that only LOOKS like a test database is kept (`geov0_test`, `xgeov0_test_a`, `geov0_testing_a`,
  `geov0_dev_a`, a doubled or trailing underscore);
* a protected family is kept whole - tier, template and clone - and so is a database of another owner;
* a connection anywhere in a family holds the whole family back, the disconnected template included;
* the dry run sends SELECT statements only;
* at apply time a changed OID, another server, another owner, a connection that appeared and a protected family
  each stop the run before anything is sent, and a refused drop of a template puts its flag back;
* importing the module and `--help` open no connection and do not import the application.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import cleanup_test_databases as cleanup  # noqa: E402

ROLE = "geo"
SERVER = {"system_identifier": "7400000000000000001", "server_version_num": "160009", "port": "5432", "role": ROLE}


def _row(name: str, oid: int, *, owner: str = ROLE, template: bool = False, connections: int = 0) -> dict:
    return {"name": name, "oid": oid, "owner": owner, "is_template": template, "size_bytes": 8_000_000,
            "connections": connections}


def _family(slug: str, first_oid: int, **overrides) -> list[dict]:
    return [
        _row(f"geov0_test_{slug}", first_oid, **overrides),
        _row(f"geov0_test_{slug}__tpl", first_oid + 1, template=True, **overrides),
        _row(f"geov0_test_{slug}__c1", first_oid + 2, **overrides),
    ]


class _Recorder:
    """Stands where the asyncpg connection stands: answers the two reads from `catalog`, records every statement."""

    def __init__(self, catalog: list[dict], server: dict | None = None) -> None:
        self.catalog, self.server = catalog, dict(server or SERVER)
        self.statements: list[str] = []
        self.refuse_drop_of: set[str] = set()

    async def fetchrow(self, sql: str):
        self.statements.append(sql)
        return dict(self.server)

    async def fetch(self, sql: str):
        self.statements.append(sql)
        return [dict(row) for row in self.catalog]

    async def execute(self, sql: str):
        self.statements.append(sql)
        name = sql.split('"')[1]
        if sql.startswith("DROP DATABASE"):
            if name in self.refuse_drop_of:
                import asyncpg

                raise asyncpg.ObjectInUseError(f'database "{name}" is being accessed by other users')
            self.catalog[:] = [row for row in self.catalog if row["name"] != name]
        elif sql.startswith("ALTER DATABASE"):
            next(row for row in self.catalog if row["name"] == name)["is_template"] = sql.endswith("true")

    def changes(self) -> list[str]:
        return [s for s in self.statements if not s.lstrip().lower().startswith("select")]


def _dispositions(entries) -> dict[str, str]:
    return {entry.name: entry.disposition for entry in entries}


@pytest.mark.parametrize(
    "name",
    ["geov0_test", "xgeov0_test_a", "geov0_testing_a", "geov0_dev_a", "GEOV0_TEST_a", "geov0_test_", "geov0_test_a_",
     "geov0_test__a", "geov0_test_a__b__c", "geov0_test_a b", "geov0_test_" + "a" * 60],
)
def test_a_name_that_only_looks_like_a_test_database_is_kept(name: str) -> None:
    (entry,) = cleanup.classify([_row(name, 10)], role=ROLE, protected=[])
    assert (entry.disposition, entry.family) == (cleanup.KEEP, None), entry
    assert cleanup.validated_name(name) is not None


def test_the_literal_prefix_holds_on_its_own_whatever_the_shared_validation_says(monkeypatch) -> None:
    """Two layers refuse a misleading name: the literal prefix, then the repository's validation. With the second
    made to accept everything, the first still refuses - so loosening one of them alone is not enough to let a
    foreign database through, and loosening the prefix is seen here."""

    monkeypatch.setattr(cleanup, "assert_safe_test_database_url", lambda *a, **k: None)
    assert cleanup.validated_name("geov0_test_a") is None
    for name in ("geov0_test", "geov0_testing_a", "xgeov0_test_a", "geov0_dev_a"):
        assert "literal" in (cleanup.validated_name(name) or ""), name


def test_an_unprotected_idle_family_of_the_connecting_role_is_proposed_whole() -> None:
    entries = cleanup.classify(_family("done", 100), role=ROLE, protected=[])
    assert _dispositions(entries) == {
        "geov0_test_done": cleanup.DROP, "geov0_test_done__tpl": cleanup.DROP, "geov0_test_done__c1": cleanup.DROP,
    }
    assert [(e.kind, e.family) for e in entries] == [("tier", "done"), ("template", "done"), ("clone", "done")]


def test_a_protected_family_is_kept_whole_and_its_neighbour_is_not_touched_by_that() -> None:
    entries = cleanup.classify(_family("live", 100) + _family("live_2", 200), role=ROLE, protected=["live"])
    kept = {name for name, disposition in _dispositions(entries).items() if disposition == cleanup.KEEP}
    assert kept == {"geov0_test_live", "geov0_test_live__tpl", "geov0_test_live__c1"}
    assert all(d == cleanup.DROP for n, d in _dispositions(entries).items() if n.startswith("geov0_test_live_2"))


def test_a_database_of_another_owner_or_of_an_unknown_kind_is_kept() -> None:
    rows = _family("other", 100, owner="postgres") + [
        _row("geov0_test_odd", 300, template=True),        # a tier database flagged as a template
    ]
    assert set(_dispositions(cleanup.classify(rows, role=ROLE, protected=[])).values()) == {cleanup.KEEP}


def test_a_template_is_told_by_provisionings_naming_or_by_the_catalog_flag() -> None:
    """Provisioning does not flag its templates; their suffix ends in `tpl`. A flagged scratch database is a
    template whatever it is called. The kind decides the order of an apply, nothing else."""

    rows = [
        _row("geov0_test_s__tpl", 1), _row("geov0_test_s__modebtpl", 2), _row("geov0_test_s__p017t1711tpl", 3),
        _row("geov0_test_s__c1", 4, template=True),
        _row("geov0_test_s__modeb", 5), _row("geov0_test_s__p017t1711seeded", 6), _row("geov0_test_s__tplx", 7),
    ]
    kinds = {entry.name.split("__")[1]: entry.kind for entry in cleanup.classify(rows, role=ROLE, protected=[])}
    assert kinds == {
        "tpl": "template", "modebtpl": "template", "p017t1711tpl": "template", "c1": "template",
        "modeb": "clone", "p017t1711seeded": "clone", "tplx": "clone",
    }


def test_a_connection_anywhere_in_a_family_holds_the_whole_family_back() -> None:
    rows = _family("busy", 100) + _family("idle", 200)
    rows[2]["connections"] = 1  # the clone of `busy`; its tier and template have none
    dispositions = _dispositions(cleanup.classify(rows, role=ROLE, protected=[]))
    assert {n: d for n, d in dispositions.items() if "busy" in n} == {
        "geov0_test_busy": cleanup.VERIFY_FIRST, "geov0_test_busy__tpl": cleanup.VERIFY_FIRST,
        "geov0_test_busy__c1": cleanup.VERIFY_FIRST,
    }
    assert all(d == cleanup.DROP for n, d in dispositions.items() if "idle" in n)


def test_the_dry_run_sends_only_selects_and_changes_nothing() -> None:
    catalog = _family("done", 100) + _family("live", 200) + [_row("somebody_elses", 900, owner="postgres")]
    before = [dict(row) for row in catalog]
    connection = _Recorder(catalog)

    manifest = asyncio.run(cleanup.build_manifest(connection, protected=["live"]))

    assert connection.changes() == [] and catalog == before
    assert len(connection.statements) == 2, connection.statements
    totals = manifest["totals"]
    assert (totals["databases"], totals["test_databases"], totals["families"]) == (7, 6, 2)
    assert totals["by_disposition"]["DROP"]["databases"] == 3 and totals["by_disposition"]["KEEP"]["databases"] == 4
    assert manifest["server"] == SERVER and manifest["protected"] == ["live"]


def _manifest_of(catalog: list[dict], protected=()) -> dict:
    return asyncio.run(cleanup.build_manifest(_Recorder([dict(r) for r in catalog]), protected=list(protected)))


def test_apply_drops_exactly_the_manifest_in_order_clones_then_templates_then_tiers() -> None:
    catalog = _family("done", 100) + _family("done2", 200) + _family("live", 300)
    manifest = _manifest_of(catalog, protected=["live"])
    connection = _Recorder(catalog)

    outcomes = asyncio.run(cleanup.apply_manifest(connection, manifest, protected=[]))

    assert [o["name"] for o in outcomes] == [
        "geov0_test_done__c1", "geov0_test_done2__c1", "geov0_test_done__tpl", "geov0_test_done2__tpl",
        "geov0_test_done", "geov0_test_done2",
    ]
    assert sorted(row["name"] for row in catalog) == ["geov0_test_live", "geov0_test_live__c1", "geov0_test_live__tpl"]
    drops = [s for s in connection.changes() if s.startswith("DROP")]
    assert len(drops) == 6 and not any("FORCE" in s.upper() or "terminate" in s.lower() for s in connection.statements)


@pytest.mark.parametrize(
    "what",
    ["the OID changed", "another server", "another owner", "a connection appeared", "a connection in the family",
     "protected at apply", "the database is gone", "the template flag changed", "hand-edited to DROP"],
)
def test_apply_stops_before_sending_anything_when_the_evidence_changed(what: str) -> None:
    catalog = _family("done", 100) + _family("kept", 200, owner="postgres")
    manifest = _manifest_of(catalog)
    connection, protected = _Recorder(catalog), []
    clone = next(row for row in catalog if row["name"] == "geov0_test_done__c1")
    if what == "the OID changed":
        clone["oid"] = 999  # dropped and created again under the same name by somebody
    elif what == "another server":
        connection.server["system_identifier"] = "7400000000000000002"
    elif what == "another owner":
        clone["owner"] = "postgres"
    elif what == "a connection appeared":
        clone["connections"] = 1
    elif what == "a connection in the family":
        next(row for row in catalog if row["name"] == "geov0_test_done__tpl")["connections"] = 1
    elif what == "protected at apply":
        protected = ["done"]
    elif what == "the database is gone":
        catalog.remove(clone)
    elif what == "the template flag changed":
        clone["is_template"] = True  # somebody made it a template since: not the database the manifest described
    else:  # a KEEP entry of the manifest rewritten by hand: the re-check does not take the manifest's word
        for entry in manifest["databases"]:
            if entry["name"].startswith("geov0_test_kept"):
                entry["disposition"] = cleanup.DROP
        for entry in manifest["databases"]:
            if entry["name"].startswith("geov0_test_done"):
                entry["disposition"] = cleanup.KEEP
    before = sorted(row["name"] for row in catalog)

    with pytest.raises(cleanup.CleanupRefused) as refused:
        asyncio.run(cleanup.apply_manifest(connection, manifest, protected=protected))

    assert connection.changes() == [], f"{what}: a statement was sent before the refusal: {connection.changes()}"
    assert sorted(row["name"] for row in catalog) == before and refused.value.outcomes == []


def test_a_refused_drop_stops_the_run_and_puts_the_template_flag_back() -> None:
    catalog = _family("done", 100)
    manifest = _manifest_of(catalog)
    connection = _Recorder(catalog)
    connection.refuse_drop_of = {"geov0_test_done__tpl"}  # somebody connected between the re-check and the drop

    with pytest.raises(cleanup.CleanupRefused) as refused:
        asyncio.run(cleanup.apply_manifest(connection, manifest, protected=[]))

    assert [o["name"] for o in refused.value.outcomes] == ["geov0_test_done__c1"]
    remaining = {row["name"]: row["is_template"] for row in catalog}
    assert remaining == {"geov0_test_done": False, "geov0_test_done__tpl": True}, remaining
    assert connection.changes()[-1] == 'ALTER DATABASE "geov0_test_done__tpl" IS_TEMPLATE true'


def test_importing_the_module_and_asking_for_help_connect_to_nothing() -> None:
    probe = (
        "import socket, sys\n"
        "def refuse(*a, **k): raise AssertionError('a connection was attempted')\n"
        "socket.socket.connect = refuse; socket.create_connection = refuse\n"
        "sys.argv = ['cleanup_test_databases.py', '--help']\n"
        "import scripts.cleanup_test_databases as c\n"
        "leaked = sorted(m for m in sys.modules if m == 'app' or m.startswith('app.') or m.startswith('asyncpg'))\n"
        "assert not leaked, leaked\n"
        "try:\n    c.main(['--help'])\nexcept SystemExit as done:\n    assert done.code == 0, done.code\n"
        "print('NO-CONNECTION-OK')\n"
    )
    run = subprocess.run([sys.executable, "-c", probe], cwd=REPO_ROOT, capture_output=True, text=True, timeout=120)
    assert run.returncode == 0 and "NO-CONNECTION-OK" in run.stdout, run.stdout[-600:] + run.stderr[-600:]


@pytest.mark.parametrize(
    "arguments, message",
    [
        ([], "exactly one of"),
        (["--manifest", ".local-run/x.json", "--apply", ".local-run/x.json"], "exactly one of"),
        (["--apply", ".local-run/x.json"], "--maintenance-window-confirmed"),
    ],
)
def test_the_command_line_refuses_an_apply_without_its_precondition(arguments, message, capsys) -> None:
    with pytest.raises(SystemExit) as stopped:
        cleanup.main(arguments)
    assert stopped.value.code == 2 and message in capsys.readouterr().err


@pytest.mark.parametrize(
    "arguments, environment, message",
    [
        (["--manifest", "elsewhere/manifest.json"], "postgresql://geo:geo@127.0.0.1:5432/x", ".local-run"),
        (["--manifest", ".local-run/x.json"], "postgresql://geo:geo@db.example.org:5432/x", "local server only"),
        (["--manifest", ".local-run/x.json"], "", "TEST_DATABASE_URL"),
    ],
)
def test_a_remote_host_a_missing_url_and_a_path_outside_local_run_are_refused_before_connecting(
    arguments, environment, message, monkeypatch, capsys
) -> None:
    import asyncpg

    async def no_connection(*_a, **_k):
        raise AssertionError("a connection was attempted")

    monkeypatch.setattr(asyncpg, "connect", no_connection)
    monkeypatch.setenv("TEST_DATABASE_URL", environment)
    assert cleanup.main(arguments) == 2
    assert message in capsys.readouterr().err


# ---------------------------------------------------------------- reproducers of the review of ddcb97b6 (F1, F2, F4)


@pytest.mark.parametrize(
    "name",
    ["geov0_test_x?archive", 'geov0_test_x?" WITH (FORCE)--', "geov0_test_x?", "geov0_test_x#y", "geov0_test_x/y",
     'geov0_test_x"y', "geov0_test_x;drop", "geov0_test_x--y z", "geov0_test_x\n", "geov0_test_x\ny", "geov0_test_x y"],
)
def test_F1_the_whole_identifier_is_validated_not_the_part_a_url_parser_keeps(name: str) -> None:
    assert cleanup.validated_name(name) is not None, f"{name!r} was accepted as a test database name"
    (entry,) = cleanup.classify([_row(name, 10)], role=ROLE, protected=[])
    assert entry.disposition == cleanup.KEEP, entry


def test_F1_a_statement_can_only_name_the_database_that_was_checked() -> None:
    """`geov0_test_x?" WITH (FORCE)--` with a matching OID: unescaped, its DROP statement is
    `DROP DATABASE "geov0_test_x?" WITH (FORCE)--"` - another database, and with FORCE."""

    evil, target = 'geov0_test_x?" WITH (FORCE)--', "geov0_test_x?"
    catalog = [_row(evil, 1), _row(target, 2)]
    manifest = _manifest_of(catalog)
    for entry in manifest["databases"]:
        if entry["name"] == evil:  # as a dry run that accepted the name would have written it
            family, kind = cleanup.family_and_kind(evil, False)
            entry.update(disposition=cleanup.DROP, family=family, kind=kind)
    connection = _Recorder(catalog)

    with pytest.raises(cleanup.CleanupRefused):
        asyncio.run(cleanup.apply_manifest(connection, manifest, protected=[]))

    assert connection.changes() == [], connection.changes()
    assert sorted(row["name"] for row in catalog) == sorted([evil, target]), "a database outside the manifest was dropped"


def test_F2_an_apply_without_a_declaration_of_what_is_protected_is_refused(monkeypatch, capsys) -> None:
    import asyncpg

    async def no_connection(*_a, **_k):
        raise AssertionError("a connection was attempted")

    monkeypatch.setattr(asyncpg, "connect", no_connection)
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql://geo:geo@127.0.0.1:5432/x")
    try:
        code = cleanup.main(["--apply", ".local-run/db-cleanup/none.json", "--maintenance-window-confirmed"])
    except SystemExit as stopped:
        code = stopped.code
    assert code == 2 and "protect" in capsys.readouterr().err.lower()


def test_F4_a_malformed_manifest_is_refused_before_anything_is_dropped() -> None:
    catalog = _family("done", 100)
    manifest = _manifest_of(catalog)
    next(e for e in manifest["databases"] if e["name"] == "geov0_test_done")["oid"] = "bad"
    connection = _Recorder(catalog)

    with pytest.raises(cleanup.CleanupRefused):
        asyncio.run(cleanup.apply_manifest(connection, manifest, protected=[]))

    assert connection.changes() == [] and len(catalog) == 3, "part of a malformed manifest was applied"
