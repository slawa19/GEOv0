"""The launcher may destroy its own database and nothing else.

Programme 017 `T1710`. `scripts/dev_database.py` is the only thing in the repository that issues
`DROP DATABASE` outside the test harness, and the consultation of 2026-09-21 required its boundary
to be drawn with the strictness of the test harness's own
(`scripts/validate_test_database_url.py:112-118`): a name outside the contract must be IMPOSSIBLE to
reset, not merely discouraged.

So these tests are written the unhelpful way round. A handful of names are accepted; most of the
table is names that must be refused, and the last group is the one that matters - it proves the
guard is reached BEFORE anything connects, because a boundary that is checked after the connection
is a boundary that a refactor can walk around without failing a test.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts import dev_database  # noqa: E402


def _url(database: str, *, host: str = "127.0.0.1") -> str:
    authority = f"[{host}]" if ":" in host else host
    return f"postgresql+asyncpg://geo:geo@{authority}:5432/{database}"


@pytest.mark.parametrize(
    "database",
    [
        "geov0_dev_local",
        "geov0_dev_p017t1710",
        "geov0_dev_phase4-admin-real-contract-1a2b3c4d",
        "geov0_dev_a_b_c",
    ],
)
def test_the_launcher_owns_these_names(database: str) -> None:
    assert dev_database.assert_safe_dev_database_url(_url(database)).database == database


#: Every entry is a database this launcher must not be able to destroy, and the reason it exists.
_FORBIDDEN = [
    # The neighbouring task's ACTUAL database on this machine (programme 017, T1711). This is the
    # single most important row in the table: it is not hypothetical.
    ("geov0_test_p017t1711", "another task's test-tier database"),
    ("geov0_test_ci", "the CI test database from the portable-Postgres runbook"),
    ("postgres", "the maintenance database"),
    ("template0", "a template database"),
    ("template1", "a template database"),
    ("geov0", "a production-shaped name"),
    ("geov0_prod", "a production-shaped name"),
    ("geov0_devlocal", "missing the separator, so it is not geov0_dev_<slug>"),
    ("geov0_dev", "no slug at all"),
    ("geov0_dev_", "an empty slug"),
    ("GEOV0_DEV_local", "the prefix is case-sensitive"),
    ("geov0_dev_a__b", "the reserved doubled underscore (AGENTS.md section 5)"),
    ("geov0_dev__b", "a leading doubled underscore"),
    ("geov0_dev_a_", "a trailing underscore"),
    ("geov0_dev_local;DROP DATABASE postgres", "a name carrying a statement"),
    ('geov0_dev_local"', "a name carrying the quote this script uses"),
    ("geov0_dev_" + "x" * 60, "longer than PostgreSQL's 63-byte identifier limit"),
]


@pytest.mark.parametrize("database,reason", _FORBIDDEN, ids=[row[0] for row in _FORBIDDEN])
def test_the_launcher_refuses_every_other_name(database: str, reason: str) -> None:
    with pytest.raises(dev_database.UnsafeDevDatabaseError) as refusal:
        dev_database.assert_safe_dev_database_url(_url(database))
    assert refusal.value.code == 2, reason
    # The message has to name the database, because the operator's next move depends on which one
    # it was.
    assert database[:40] in str(refusal.value) or "identifier limit" in str(refusal.value)


@pytest.mark.parametrize("host", ["10.0.0.5", "db.example.com", "192.168.1.10", "::2"])
def test_the_launcher_refuses_a_database_it_would_have_to_reach_over_a_network(host: str) -> None:
    with pytest.raises(dev_database.UnsafeDevDatabaseError, match="loopback"):
        dev_database.assert_safe_dev_database_url(_url("geov0_dev_local", host=host))


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1", "127.0.0.2"])
def test_loopback_hosts_are_accepted(host: str) -> None:
    assert dev_database.assert_safe_dev_database_url(_url("geov0_dev_local", host=host))


@pytest.mark.parametrize(
    "database_url",
    [
        "",
        "   ",
        "sqlite+aiosqlite:///./.local-run/geov0.db",
        "sqlite+aiosqlite:///./.local-run/geov0_dev_local.db",
        "mysql://geo:geo@127.0.0.1:3306/geov0_dev_local",
        "not a url at all",
    ],
)
def test_only_a_postgresql_url_is_considered_at_all(database_url: str) -> None:
    with pytest.raises(dev_database.UnsafeDevDatabaseError):
        dev_database.assert_safe_dev_database_url(database_url)


# ==================================================================================================
# The counter-check: the guard runs BEFORE anything connects
# ==================================================================================================


@pytest.mark.parametrize("command", ["reset", "drop", "ensure", "ready"])
def test_a_forbidden_url_never_reaches_the_cluster(
    command: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A refusal that happened after the connection would still be a refusal - and still be wrong.

    `DROP DATABASE` is not the only way to damage a database somebody else is using; opening a
    session against it under SERIALIZABLE already is. This asserts the order, not just the verdict:
    the connector is replaced by a tripwire, and it must not fire.
    """

    def _tripwire(*_args: object, **_kwargs: object) -> None:
        raise AssertionError(
            f"dev_database {command} opened a connection before validating the database name"
        )

    monkeypatch.setattr(dev_database, "_connect", _tripwire)
    monkeypatch.setenv("DATABASE_URL", _url("geov0_test_p017t1711"))

    assert dev_database.main([command]) == 2


@pytest.mark.parametrize("command", ["reset", "drop", "ensure", "ready"])
def test_an_absent_url_never_reaches_the_cluster(
    command: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _tripwire(*_args: object, **_kwargs: object) -> None:
        raise AssertionError(f"dev_database {command} connected with no DATABASE_URL set")

    monkeypatch.setattr(dev_database, "_connect", _tripwire)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    assert dev_database.main([command]) == 2


def test_the_tripwire_can_actually_fire(monkeypatch: pytest.MonkeyPatch) -> None:
    """ANTI-VACUUM. A tripwire nothing can trip proves nothing about the two tests above it.

    With a database the launcher DOES own, the same monkeypatched connector must be reached - which
    is what makes "it was never reached" meaningful in the forbidden cases.
    """

    reached: list[str] = []

    async def _record(_url: object, database: str) -> object:
        reached.append(database)
        raise dev_database.DevDatabaseRefusal("tripwire reached", code=1)

    monkeypatch.setattr(dev_database, "_connect", _record)
    monkeypatch.setenv("DATABASE_URL", _url("geov0_dev_local"))

    assert dev_database.main(["ensure"]) == 1
    assert reached == ["postgres"]


def test_each_database_checks_its_own_ref_to_pid_table() -> None:
    """Two databases seeded from one community must not share the table readiness is checked against.

    Reproduced on 2026-09-23 with the previous design: the Admin e2e seeded its disposable database
    from `riverside-town-50`, which overwrote the one table the seed keeps per community, and the
    launcher's next `start` refused its own untouched database because the PIDs it compared against
    belonged to the e2e's run. The paths must differ per database, and neither may be the seed's own
    community-wide path.
    """

    from scripts.seed_recipe import key_table_path

    launcher = dev_database.adopted_key_table_path("geov0_dev_local")
    disposable = dev_database.adopted_key_table_path("geov0_dev_phase4-1a2b3c4d")

    assert launcher != disposable
    assert launcher != key_table_path("riverside-town-50")
    assert disposable != key_table_path("riverside-town-50")
    # Under the repository's runtime root, like every other artefact family (`AGENTS.md` section 12).
    assert (_REPO_ROOT / ".local-run") in launcher.parents


def test_dropping_a_database_forgets_its_adopted_table(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A dropped database's adopted table is stale metadata, and stale metadata answers falsely.

    Left behind, it would be read by the next `ready` on a database that was re-created under the
    same name - reporting the population of a database that no longer exists.
    """

    table = tmp_path / "geov0_dev_local" / "participants.json"
    table.parent.mkdir(parents=True)
    table.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(dev_database, "adopted_key_table_path", lambda _database: table)

    dev_database._forget_adopted_key_table(
        dev_database.assert_safe_dev_database_url(_url("geov0_dev_local"))
    )

    assert not table.exists()
    # Idempotent: a second call on an absent file is not an error.
    dev_database._forget_adopted_key_table(
        dev_database.assert_safe_dev_database_url(_url("geov0_dev_local"))
    )


def test_the_reset_path_never_forces_a_drop() -> None:
    """The server is the last line of defence, so nothing here may take that defence away.

    `DROP DATABASE ... WITH (FORCE)` and `pg_terminate_backend` both turn "PostgreSQL refuses while
    sessions are open" into "the launcher disconnects them first". That is precisely the failure the
    ordering requirement exists to prevent, so their ABSENCE is asserted rather than assumed.
    """

    import ast

    source = (_REPO_ROOT / "scripts" / "dev_database.py").read_text(encoding="utf-8")
    # Only what is actually SENT: the first argument of every execute/fetch call. Prose about these
    # spellings lives in this module's docstrings, and a substring search over the source would find
    # the explanation instead of the behaviour.
    statements: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in {"execute", "fetch", "fetchval", "fetchrow"} or not node.args:
            continue
        statements.append(ast.unparse(node.args[0]).lower())
    assert any("drop database" in text for text in statements), (
        "ANTI-VACUUM: no DROP DATABASE statement was found, so this test proved nothing"
    )
    for statement in statements:
        assert "force" not in statement, statement
        assert "pg_terminate_backend" not in statement, statement


def test_the_environment_carries_the_url_and_argv_does_not() -> None:
    """The password must not reach a process listing (`AGENTS.md` section 12).

    The parser is asked directly: there is no option that takes a URL, so no caller can put one in
    argv even by mistake.
    """

    with pytest.raises(SystemExit):
        dev_database.main(["ensure", "--database-url", _url("geov0_dev_local")])


# ==================================================================================================
# Closing review of stage 1 (Codex, `37fec08..5e687dd`, 2026-09-23): F3 and F4
# ==================================================================================================


@pytest.mark.parametrize(
    "database_url",
    [
        "postgresql+asyncpg://geo:geo@/geov0_dev_local",
        "postgresql+asyncpg:///geov0_dev_local",
        "postgresql+asyncpg://geo:geo@:5432/geov0_dev_local",
    ],
)
def test_an_omitted_host_is_refused_because_the_driver_fills_it_from_pghost(
    database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F3. A URL with no host is NOT a local socket: asyncpg takes the host from `PGHOST` first.

    With `PGHOST` pointing at another machine, the omitted host is that machine, and this module
    drops databases. The control is the explicit loopback URL under the same `PGHOST`: accepted,
    so the refusal is about the omission and not about the environment variable.
    """

    monkeypatch.setenv("PGHOST", "db.example.com")
    assert dev_database.assert_safe_dev_database_url(_url("geov0_dev_local"))

    with pytest.raises(dev_database.UnsafeDevDatabaseError, match="loopback"):
        dev_database.assert_safe_dev_database_url(database_url)


class _FakeMaintenanceConnection:
    """Just enough of an asyncpg connection for `create`: an existing name makes CREATE fail."""

    def __init__(self, existing: set[str]) -> None:
        self.existing = existing
        self.executed: list[str] = []

    async def execute(self, statement: str, **_kwargs: object) -> None:
        import asyncpg

        self.executed.append(statement)
        name = statement.split('"')[1]
        if name in self.existing:
            raise asyncpg.DuplicateDatabaseError(f'database "{name}" already exists')
        self.existing.add(name)

    async def fetchval(self, _statement: str, name: str, **_kwargs: object) -> object:
        return 1 if name in self.existing else None

    async def close(self) -> None:
        return None


@pytest.mark.parametrize("already_there", [False, True])
def test_create_succeeds_only_for_the_caller_that_created_the_database(
    already_there: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F4. The Admin e2e drops, in its `finally`, the database it believes it created.

    `ensure` answers 0 whether it created the database or found it, so it cannot carry that belief.
    `create` answers 0 ONLY when this call created it, and refuses a name that is already taken -
    for a disposable name with a run id in it, a collision means something is wrong, and the database
    found there belongs to somebody else. The control is the absent-name half of the same test.
    """

    name = "geov0_dev_phase4-1a2b3c4d"
    connection = _FakeMaintenanceConnection({name} if already_there else set())

    async def _connect(_url: object, _database: str) -> _FakeMaintenanceConnection:
        return connection

    monkeypatch.setattr(dev_database, "_connect", _connect)
    monkeypatch.setenv("DATABASE_URL", _url(name))

    assert dev_database.main(["create"]) == (1 if already_there else 0)
    assert name in connection.existing
    # And `ensure` keeps its meaning for the launchers, which re-use their database on every start.
    assert dev_database.main(["ensure"]) == 0
