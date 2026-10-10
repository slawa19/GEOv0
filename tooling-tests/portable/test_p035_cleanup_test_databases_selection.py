"""`scripts/cleanup_test_databases.py`: WHICH databases it proposes to drop, and what it refuses at apply time.

No PostgreSQL here: the catalog is a list of rows and the connection is a recorder, which is enough for the
selection logic and for "the dry run sends no statement that changes anything". That an ordinary `DROP DATABASE` is
refused by the server while a session is connected, that the neighbour's session survives, and that the server reads
an escaped identifier as the name it was built from, is NOT shown here - that is
`tests/integration/test_p035_cleanup_test_databases_postgres.py`, on real databases.

THE RECORDER PARSES THE STATEMENT, it does not split it on a quote: the only changing statement it accepts is
`DROP DATABASE "<identifier>"` with every `"` of the identifier doubled, and it drops the database called exactly
what that identifier unescapes to. Anything else - a trailing `WITH (FORCE)`, a second statement, an `ALTER` - is an
`AssertionError`. (The first edition split on `"` and so could not see the defect of F1.)

What is pinned:
* a name that only LOOKS like a test database is kept - a wrong prefix, a doubled or trailing underscore, and every
  name carrying a character outside the grammar (`?`, `"`, `#`, `/`, `;`, a space, a newline), the WHOLE name
  being what is judged (review of `ddcb97b6`, F1);
* an escaped identifier names only the database it was built from, whatever the name;
* a protected family is kept whole; a database of another owner, and one flagged as a template, are kept;
* a connection anywhere in a family holds the whole family back;
* the dry run sends SELECT statements only;
* an apply needs a declaration of what is protected, the number of rows reviewed and a fresh manifest (F2); it
  refuses a manifest whose structure is wrong BEFORE connecting (F4); it compares every row before the first drop,
  and stops on a changed OID, server, owner, flag, a vanished database, a protected family, and a connection in ANY
  unprotected test family - also one no row names; a failure after the first drop reports what completed and names
  an unanswered drop as uncertain;
* importing the module and `--help` open no connection and do not import the application.
"""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import cleanup_test_databases as cleanup  # noqa: E402

ROLE = "geo"
SERVER = {"system_identifier": "7400000000000000001", "server_version_num": "160009", "port": "5432", "role": ROLE}
_DROP = re.compile(r'DROP DATABASE "((?:[^"]|"")*)"')

#: Names that carry something outside the grammar. The whole name is what must be judged.
ADVERSARIAL = [
    "geov0_test_x?archive", 'geov0_test_x?" WITH (FORCE)--', "geov0_test_x?", "geov0_test_x#y", "geov0_test_x/y",
    'geov0_test_x"y', "geov0_test_x;drop", "geov0_test_x--y z", "geov0_test_x\n", "geov0_test_x\ny", "geov0_test_x y",
    "geov0_test_x\x00y", "geov0_test_x'y", "geov0_test_x%20y", "geov0_test_x@host",
]


def _row(name: str, oid: int, *, owner: str = ROLE, template: bool = False, connections: int = 0) -> dict:
    return {"name": name, "oid": oid, "owner": owner, "is_template": template, "size_bytes": 8_000_000,
            "connections": connections}


def _family(slug: str, first_oid: int, **overrides) -> list[dict]:
    return [
        _row(f"geov0_test_{slug}", first_oid, **overrides),
        _row(f"geov0_test_{slug}__modebtpl", first_oid + 1, **overrides),
        _row(f"geov0_test_{slug}__c1", first_oid + 2, **overrides),
    ]


class _Recorder:
    """Stands where the asyncpg connection stands: answers the two reads from `catalog`, records every statement,
    and executes ONLY a well-formed `DROP DATABASE "<identifier>"`."""

    def __init__(self, catalog: list[dict], server: dict | None = None) -> None:
        self.catalog, self.server = catalog, dict(server or SERVER)
        self.statements: list[str] = []
        self.refuse_drop_of: set[str] = set()
        self.lose_connection_on: set[str] = set()

    async def fetchrow(self, sql: str):
        self.statements.append(sql)
        return dict(self.server)

    async def fetch(self, sql: str):
        self.statements.append(sql)
        return [dict(row) for row in self.catalog]

    async def execute(self, sql: str):
        self.statements.append(sql)
        match = _DROP.fullmatch(sql)
        assert match is not None, f"a statement that is not exactly DROP DATABASE \"<identifier>\": {sql!r}"
        name = match.group(1).replace('""', '"')
        if name in self.lose_connection_on:
            raise ConnectionResetError("the connection was lost while the statement was in flight")
        if name in self.refuse_drop_of:
            import asyncpg

            raise asyncpg.ObjectInUseError(f'database "{name}" is being accessed by other users')
        assert any(row["name"] == name for row in self.catalog), f"DROP of a database that does not exist: {name!r}"
        self.catalog[:] = [row for row in self.catalog if row["name"] != name]

    def changes(self) -> list[str]:
        return [s for s in self.statements if not s.lstrip().lower().startswith("select")]


def _dispositions(entries) -> dict[str, str]:
    return {entry.name: entry.disposition for entry in entries}


def _manifest_of(catalog: list[dict], protected=()) -> dict:
    return asyncio.run(cleanup.build_manifest(_Recorder([dict(r) for r in catalog]), protected=list(protected)))


def _apply(connection, manifest, *, protected=(), count=None, now=None):
    rows = manifest["databases"] if isinstance(manifest.get("databases"), list) else []
    drops = sum(1 for e in rows if isinstance(e, dict) and e.get("disposition") == cleanup.DROP)
    return asyncio.run(cleanup.apply_manifest(
        connection, manifest, protected=list(protected), expect_drop_count=drops if count is None else count,
        now=now, report=lambda _line: None,
    ))


# ------------------------------------------------------------------------------------------------------ names


@pytest.mark.parametrize(
    "name",
    ["geov0_test", "xgeov0_test_a", "geov0_testing_a", "geov0_dev_a", "GEOV0_TEST_a", "geov0_test_", "geov0_test_a_",
     "geov0_test__a", "geov0_test_a__b__c", "geov0_test_a__", "geov0_test_" + "a" * 60, *ADVERSARIAL],
)
def test_a_name_that_only_looks_like_a_test_database_is_kept(name: str) -> None:
    assert cleanup.validated_name(name) is not None, f"{name!r} was accepted as a test database name"
    (entry,) = cleanup.classify([_row(name, 10)], role=ROLE, protected=[])
    assert (entry.disposition, entry.family, entry.kind) == (cleanup.KEEP, None, None), entry
    with pytest.raises(cleanup.CleanupRefused):
        cleanup.drop_statement(name)


@pytest.mark.parametrize("name", ["geov0_test_a", "geov0_test_A-b_c9", "geov0_test_a__tpl", "geov0_test_a_b__modebtpl"])
def test_a_real_test_database_name_is_accepted_whole(name: str) -> None:
    assert cleanup.validated_name(name) is None
    assert cleanup.drop_statement(name) == f'DROP DATABASE "{name}"'


def test_the_literal_prefix_and_the_grammar_each_hold_on_their_own(monkeypatch) -> None:
    """Three layers refuse a misleading name: the literal prefix, this command's grammar, the repository's
    validation. With the last made to accept everything and to echo the name, the first two still refuse."""

    class _Echo:
        def __init__(self, url: str) -> None:
            self.database = url.split("/", 3)[3]

    monkeypatch.setattr(cleanup, "assert_safe_test_database_url", lambda url, **_k: _Echo(url))
    assert cleanup.validated_name("geov0_test_a") is None
    for name in ("geov0_test", "geov0_testing_a", "xgeov0_test_a", "geov0_dev_a"):
        assert "literal" in (cleanup.validated_name(name) or ""), name
    for name in ADVERSARIAL:
        assert "nothing more" in (cleanup.validated_name(name) or ""), name


def test_an_answer_about_another_name_than_the_one_asked_is_a_refusal(monkeypatch) -> None:
    """The shared validation takes a URL; should it ever answer about a part of the name, that is not an answer."""

    class _Shorter:
        database = "geov0_test_a"

    monkeypatch.setattr(cleanup, "assert_safe_test_database_url", lambda *_a, **_k: _Shorter())
    assert "another name" in (cleanup.validated_name("geov0_test_a_b") or "")


@pytest.mark.parametrize("name", [*ADVERSARIAL, 'a"b""c', '"', '""', 'x" WITH (FORCE); DROP DATABASE "y'])
def test_an_escaped_identifier_names_only_the_database_it_was_built_from(name: str) -> None:
    """Independently of any validation: the statement the recorder parses unescapes to exactly `name`."""

    match = _DROP.fullmatch(f"DROP DATABASE {cleanup.escaped(name)}")
    assert match is not None, cleanup.escaped(name)
    assert match.group(1).replace('""', '"') == name


# -------------------------------------------------------------------------------------------------- selection


def test_an_unprotected_idle_family_of_the_connecting_role_is_proposed_whole() -> None:
    entries = cleanup.classify(_family("done", 100), role=ROLE, protected=[])
    assert set(_dispositions(entries).values()) == {cleanup.DROP} and len(entries) == 3
    assert [(e.kind, e.family) for e in entries] == [("tier", "done"), ("template", "done"), ("clone", "done")]


def test_a_protected_family_is_kept_whole_and_its_neighbour_is_not_touched_by_that() -> None:
    entries = cleanup.classify(_family("live", 100) + _family("live_2", 200), role=ROLE, protected=["live"])
    kept = {name for name, disposition in _dispositions(entries).items() if disposition == cleanup.KEEP}
    assert kept == {"geov0_test_live", "geov0_test_live__modebtpl", "geov0_test_live__c1"}
    assert all(d == cleanup.DROP for n, d in _dispositions(entries).items() if n.startswith("geov0_test_live_2"))


def test_a_database_of_another_owner_and_one_flagged_as_a_template_are_kept() -> None:
    rows = _family("other", 100, owner="postgres") + [
        _row("geov0_test_flagged", 300, template=True),
        _row("geov0_test_flagged2__tpl", 301, template=True),
        _row("geov0_test_flagged3__c1", 302, template=True),
    ]
    entries = cleanup.classify(rows, role=ROLE, protected=[])
    assert set(_dispositions(entries).values()) == {cleanup.KEEP}
    assert all("flagged as a template" in e.reason for e in entries if "flagged" in e.name)


def test_a_template_is_told_by_provisionings_naming_and_that_decides_the_order_only() -> None:
    rows = [_row(f"geov0_test_s__{suffix}", oid) for oid, suffix in enumerate(
        ["tpl", "modebtpl", "p017t1711tpl", "modeb", "p017t1711seeded", "tplx"], start=1)]
    entries = cleanup.classify(rows, role=ROLE, protected=[])
    assert {e.name.split("__")[1]: e.kind for e in entries} == {
        "tpl": "template", "modebtpl": "template", "p017t1711tpl": "template",
        "modeb": "clone", "p017t1711seeded": "clone", "tplx": "clone",
    }
    assert set(_dispositions(entries).values()) == {cleanup.DROP}


def test_a_connection_anywhere_in_a_family_holds_the_whole_family_back() -> None:
    rows = _family("busy", 100) + _family("idle", 200)
    rows[2]["connections"] = 1  # the clone of `busy`; its tier and template have none
    dispositions = _dispositions(cleanup.classify(rows, role=ROLE, protected=[]))
    assert {d for n, d in dispositions.items() if "busy" in n} == {cleanup.VERIFY_FIRST}
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


@pytest.mark.parametrize("slug", ["", "a__b", "a b", "geov0_test_a?", "a_", 'a"b'])
def test_a_protected_slug_that_is_not_a_slug_is_refused(slug: str) -> None:
    with pytest.raises(cleanup.UsageRefused):
        asyncio.run(cleanup.build_manifest(_Recorder([]), protected=[slug]))


# ------------------------------------------------------------------------------------------------------ apply


def test_apply_drops_exactly_the_manifest_in_order_clones_then_templates_then_tiers() -> None:
    catalog = _family("done", 100) + _family("done2", 200) + _family("live", 300)
    manifest = _manifest_of(catalog, protected=["live"])
    connection = _Recorder(catalog)

    outcomes = _apply(connection, manifest)

    assert [o["name"] for o in outcomes] == [
        "geov0_test_done__c1", "geov0_test_done2__c1", "geov0_test_done__modebtpl", "geov0_test_done2__modebtpl",
        "geov0_test_done", "geov0_test_done2",
    ]
    assert sorted(row["name"] for row in catalog) == sorted(row["name"] for row in _family("live", 300))
    assert len(connection.changes()) == 6  # the recorder accepts nothing but the six well-formed drops


def test_a_statement_can_only_name_the_database_that_was_checked() -> None:
    """F1. `geov0_test_x?" WITH (FORCE)--` with a matching OID: unescaped, its statement is
    `DROP DATABASE "geov0_test_x?" WITH (FORCE)--"` - ANOTHER database, and with FORCE. The manifest that names it
    is refused, and even built by hand the statement could not be written."""

    evil, target = 'geov0_test_x?" WITH (FORCE)--', "geov0_test_x?"
    catalog = [_row(evil, 1), _row(target, 2), *_family("done", 100)]
    manifest = _manifest_of(catalog)
    assert {e["name"]: e["disposition"] for e in manifest["databases"] if e["name"] in (evil, target)} == {
        evil: cleanup.KEEP, target: cleanup.KEEP,
    }
    next(e for e in manifest["databases"] if e["name"] == evil).update(disposition=cleanup.DROP, family="x", kind="tier")
    connection = _Recorder(catalog)

    with pytest.raises(cleanup.UsageRefused):
        _apply(connection, manifest)

    assert connection.statements == [] and len(catalog) == 5, "something was sent for a manifest naming such a row"


@pytest.mark.parametrize(
    "what",
    ["the OID changed", "another server", "another owner", "a connection appeared", "a connection in the family",
     "a connection in a family no row names", "protected at apply", "the database is gone", "flagged as a template since"],
)
def test_apply_stops_before_sending_anything_when_the_evidence_changed(what: str) -> None:
    catalog = _family("done", 100) + _family("kept", 200, owner="postgres") + _family("idle", 300)
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
        next(row for row in catalog if row["name"] == "geov0_test_done__modebtpl")["connections"] = 1
    elif what == "a connection in a family no row names":
        catalog.append(_row("geov0_test_arrived", 800, connections=1))  # created after the dry run, and in use
    elif what == "protected at apply":
        protected = ["done"]
    elif what == "the database is gone":
        catalog.remove(clone)
    else:
        clone["is_template"] = True
    before = sorted(row["name"] for row in catalog)

    with pytest.raises(cleanup.CleanupRefused) as refused:
        _apply(connection, manifest, protected=protected)

    assert connection.changes() == [], f"{what}: a statement was sent before the refusal: {connection.changes()}"
    assert sorted(row["name"] for row in catalog) == before and refused.value.outcomes == []


def test_the_last_row_is_compared_before_the_first_drop() -> None:
    """Every row passes once before anything is sent: a changed OID on the row that would be dropped LAST."""

    catalog = _family("done", 100)
    manifest = _manifest_of(catalog)
    next(row for row in catalog if row["name"] == "geov0_test_done")["oid"] = 999
    connection = _Recorder(catalog)

    with pytest.raises(cleanup.CleanupRefused):
        _apply(connection, manifest)

    assert connection.changes() == [] and len(catalog) == 3


def test_a_connected_family_that_is_protected_does_not_stop_an_apply() -> None:
    """The counter-check of the server-wide rule: a PROTECTED family may be in use - that is what protecting it
    is for."""

    catalog = _family("done", 100) + _family("live", 200)
    manifest = _manifest_of(catalog, protected=["live"])
    next(row for row in catalog if row["name"] == "geov0_test_live")["connections"] = 3
    connection = _Recorder(catalog)

    assert len(_apply(connection, manifest)) == 3
    assert sorted(row["name"] for row in catalog) == sorted(row["name"] for row in _family("live", 200))


def test_the_number_of_rows_reviewed_must_be_the_number_the_manifest_asks_for() -> None:
    catalog = _family("done", 100)
    manifest = _manifest_of(catalog)
    connection, lines = _Recorder(catalog), []

    with pytest.raises(cleanup.CleanupRefused) as refused:
        asyncio.run(cleanup.apply_manifest(connection, manifest, protected=[], expect_drop_count=2, report=lines.append))

    assert "3 drop(s)" in str(refused.value) and connection.changes() == [] and len(catalog) == 3
    listing = "\n".join(lines)
    assert "protected families: (none - declared)" in listing and "to drop: 3 database(s) of 1 families" in listing
    assert all(name in listing for name in ("geov0_test_done__c1", "geov0_test_done__modebtpl", "oid 100"))


@pytest.mark.parametrize("age_minutes, usable", [(0, True), (59, True), (61, False), (60 * 24, False), (-30, False)])
def test_a_manifest_is_applied_only_while_it_is_fresh(age_minutes: int, usable: bool) -> None:
    catalog = _family("done", 100)
    manifest = _manifest_of(catalog)
    connection = _Recorder(catalog)
    now = datetime.fromisoformat(manifest["created_at"]) + timedelta(minutes=age_minutes)

    if usable:
        assert len(_apply(connection, manifest, now=now)) == 3
    else:
        with pytest.raises(cleanup.UsageRefused) as refused:
            _apply(connection, manifest, now=now)
        assert "age of the manifest" in str(refused.value) and connection.statements == [] and len(catalog) == 3


def _broken(manifest: dict, what: str) -> dict:
    tier = next(e for e in manifest["databases"] if e["name"] == "geov0_test_done")
    if what == "an OID that is not a number":
        tier["oid"] = "bad"
    elif what == "an OID that is true":
        tier["oid"] = True
    elif what == "no owner":
        del tier["owner"]
    elif what == "a kind the name does not have":
        tier["kind"] = "clone"
    elif what == "a family the name does not have":
        tier["family"] = "other"
    elif what == "a DROP row flagged as a template":
        tier["is_template"] = True
    elif what == "an unknown disposition":
        tier["disposition"] = "PURGE"
    elif what == "a name listed twice":
        manifest["databases"].append(dict(tier))
    elif what == "a row that is not an object":
        manifest["databases"].append("geov0_test_done")
    elif what == "the old format":
        manifest["format"] = 1
    elif what == "no server identity":
        manifest["server"] = {}
    elif what == "a created_at that is not a time":
        manifest["created_at"] = "yesterday"
    elif what == "a protected entry that is not a slug":
        manifest["protected"] = ["a b"]
    elif what == "databases that are not a list":
        manifest["databases"] = {"geov0_test_done": tier}
    else:
        raise AssertionError(what)
    return manifest


_BROKEN = ["an OID that is not a number", "an OID that is true", "no owner", "a kind the name does not have",
           "a family the name does not have", "a DROP row flagged as a template", "an unknown disposition",
           "a name listed twice", "a row that is not an object", "the old format", "no server identity",
           "a created_at that is not a time", "a protected entry that is not a slug", "databases that are not a list"]


@pytest.mark.parametrize("what", _BROKEN)
def test_a_manifest_whose_structure_is_wrong_is_refused_before_anything_is_read_or_dropped(what: str) -> None:
    """F4. The first edition dropped the valid rows that came before the broken one and then died."""

    catalog = _family("done", 100)
    manifest = _broken(_manifest_of(catalog), what)
    connection = _Recorder(catalog)

    with pytest.raises(cleanup.UsageRefused):
        _apply(connection, manifest, count=3)

    assert connection.statements == [] and len(catalog) == 3, f"{what}: the server was touched"


def test_a_refused_drop_stops_the_run_and_reports_what_completed() -> None:
    catalog = _family("done", 100)
    manifest = _manifest_of(catalog)
    connection = _Recorder(catalog)
    connection.refuse_drop_of = {"geov0_test_done__modebtpl"}  # somebody connected after the last comparison

    with pytest.raises(cleanup.CleanupRefused) as refused:
        _apply(connection, manifest)

    assert [o["name"] for o in refused.value.outcomes] == ["geov0_test_done__c1"] and refused.value.uncertain is None
    assert sorted(row["name"] for row in catalog) == ["geov0_test_done", "geov0_test_done__modebtpl"]


def test_a_drop_that_got_no_answer_is_uncertain_and_never_reported_as_dropped() -> None:
    catalog = _family("done", 100)
    manifest = _manifest_of(catalog)
    connection = _Recorder(catalog)
    connection.lose_connection_on = {"geov0_test_done__modebtpl"}

    with pytest.raises(cleanup.CleanupRefused) as refused:
        _apply(connection, manifest)

    assert refused.value.uncertain == "geov0_test_done__modebtpl"
    assert [o["name"] for o in refused.value.outcomes] == ["geov0_test_done__c1"]


# ------------------------------------------------------------------------------------------- the command line


def test_importing_the_module_and_asking_for_help_connect_to_nothing() -> None:
    probe = (
        "import socket, sys\n"
        "def refuse(*a, **k): raise AssertionError('a connection was attempted')\n"
        "socket.socket.connect = refuse; socket.create_connection = refuse\n"
        "import scripts.cleanup_test_databases as c\n"
        "leaked = sorted(m for m in sys.modules if m == 'app' or m.startswith('app.') or m.startswith('asyncpg'))\n"
        "assert not leaked, leaked\n"
        "try:\n    c.main(['--help'])\nexcept SystemExit as done:\n    assert done.code == 0, done.code\n"
        "print('NO-CONNECTION-OK')\n"
    )
    run = subprocess.run([sys.executable, "-c", probe], cwd=REPO_ROOT, capture_output=True, text=True, timeout=120)
    assert run.returncode == 0 and "NO-CONNECTION-OK" in run.stdout, run.stdout[-600:] + run.stderr[-600:]


_APPLY = ["--apply", ".local-run/x.json", "--maintenance-window-confirmed"]


@pytest.mark.parametrize(
    "arguments, message",
    [
        ([], "exactly one of"),
        (["--manifest", ".local-run/x.json", "--apply", ".local-run/x.json"], "exactly one of"),
        (["--apply", ".local-run/x.json", "--protect-none", "--expect-drop-count", "1"], "--maintenance-window-confirmed"),
        ([*_APPLY, "--expect-drop-count", "1"], "declaration of what is protected"),
        ([*_APPLY, "--protect", "a", "--protect-none", "--expect-drop-count", "1"], "contradict"),
        ([*_APPLY, "--protect-none"], "--expect-drop-count"),
        (["--manifest", ".local-run/x.json", "--protect", "a", "--protect-none"], "contradict"),
    ],
)
def test_the_command_line_refuses_an_apply_without_its_declarations(arguments, message, capsys) -> None:
    with pytest.raises(SystemExit) as stopped:
        cleanup.main(arguments)
    assert stopped.value.code == 2 and message in capsys.readouterr().err


def _no_connection(monkeypatch) -> None:
    import asyncpg

    async def refuse(*_a, **_k):
        raise AssertionError("a connection was attempted")

    monkeypatch.setattr(asyncpg, "connect", refuse)


@pytest.mark.parametrize(
    "arguments, environment, message",
    [
        (["--manifest", "elsewhere/manifest.json"], "postgresql://geo:geo@127.0.0.1:5432/x", ".local-run"),
        (["--manifest", ".local-run/x.json"], "postgresql://geo:geo@db.example.org:5432/x", "local server only"),
        (["--manifest", ".local-run/x.json"], "", "TEST_DATABASE_URL"),
        (["--manifest", ".local-run/x.json", "--protect", "a b"], "postgresql://geo:geo@127.0.0.1:5432/x", "not a task slug"),
        ([*_APPLY, "--protect", "a__b", "--expect-drop-count", "1"], "postgresql://geo:geo@127.0.0.1:5432/x", "not a task slug"),
    ],
)
def test_what_can_be_refused_without_the_server_is_refused_before_connecting(
    arguments, environment, message, monkeypatch, capsys
) -> None:
    _no_connection(monkeypatch)
    monkeypatch.setenv("TEST_DATABASE_URL", environment)
    assert cleanup.main(arguments) == 2
    assert message in capsys.readouterr().err


def _manifest_file(tmp_name: str, manifest) -> str:
    target = REPO_ROOT / ".local-run" / "test-runs" / "p035dbclean-portable" / tmp_name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(manifest if isinstance(manifest, str) else json.dumps(manifest), encoding="utf-8")
    return str(target)


@pytest.mark.parametrize("what", ["not json", "an OID that is not a number", "stale"])
def test_a_manifest_file_that_cannot_be_used_is_exit_2_before_connecting(what: str, monkeypatch, capsys) -> None:
    _no_connection(monkeypatch)
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql://geo:geo@127.0.0.1:5432/x")
    manifest = _manifest_of(_family("done", 100))
    if what == "not json":
        content = "{ this is not json"
    elif what == "stale":
        manifest["created_at"] = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat(timespec="seconds")
        content = manifest
    else:
        content = _broken(manifest, what)
    path = _manifest_file(f"unusable-{what.replace(' ', '-')}.json", content)

    assert cleanup.main(["--apply", path, "--maintenance-window-confirmed", "--protect-none", "--expect-drop-count", "3"]) == 2
    assert "manifest" in capsys.readouterr().err
    assert not Path(path).with_suffix(".result.json").exists(), "a result was written for an apply that never began"


def test_a_server_that_cannot_be_reached_is_exit_2(monkeypatch, capsys) -> None:
    import asyncpg

    async def unreachable(*_a, **_k):
        raise ConnectionRefusedError("nobody listens there")

    monkeypatch.setattr(asyncpg, "connect", unreachable)
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql://geo:geo@127.0.0.1:5432/x")
    assert cleanup.main(["--manifest", ".local-run/test-runs/p035dbclean-portable/never.json"]) == 2
    assert "cannot be reached" in capsys.readouterr().err


def test_the_command_writes_a_result_that_names_an_uncertain_drop(monkeypatch, capsys) -> None:
    """End to end through `main()` on the recorder: one drop completes, the next gets no answer."""

    import asyncpg

    catalog = _family("done", 100)
    manifest = _manifest_of(catalog)
    recorder = _Recorder(catalog)
    recorder.lose_connection_on = {"geov0_test_done__modebtpl"}

    async def close() -> None:
        return None

    recorder.close = close  # type: ignore[attr-defined]

    async def connect(**_k):
        return recorder

    monkeypatch.setattr(asyncpg, "connect", connect)
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql://geo:geo@127.0.0.1:5432/x")
    path = _manifest_file("uncertain.json", manifest)

    code = cleanup.main(["--apply", path, "--maintenance-window-confirmed", "--protect-none", "--expect-drop-count", "3"])

    result = json.loads(Path(path).with_suffix(".result.json").read_text(encoding="utf-8"))
    assert code == 1 and "UNCERTAIN" in capsys.readouterr().err
    assert [o["name"] for o in result["dropped"]] == ["geov0_test_done__c1"]
    assert result["uncertain"] == "geov0_test_done__modebtpl" and "not known" in result["stopped_on"]
