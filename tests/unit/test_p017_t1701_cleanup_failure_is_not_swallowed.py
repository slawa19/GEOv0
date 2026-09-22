"""T1701 fix-delta: a refused cleanup DROP must reach the runner, not stderr (2026-09-22).

WHY THIS FILE EXISTS. Until today both provisioning context managers ended like this::

    except MigratedSchemaError as cleanup_error:
        if sys.exc_info()[0] is None:
            raise
        print("WARNING: ...", file=sys.stderr)

The intent was readable and the mechanism was not: INSIDE an `except` handler `sys.exc_info()[0]`
is the exception being handled, so it is never `None` and the `raise` was unreachable code. A
`DROP DATABASE` that the server refused after a SUCCESSFUL test body became a line on stderr and the
test reported green - `AGENTS.md` §9, the standard way to manufacture a false green, delivered by the
very slice that was removing four of them. Codex found it in the external review of
`e2e1380..37fec08`; it was reproduced by execution before it was fixed.

The four schema-comparison modules that call `scratch_databases`
(`test_p015_step5a_reconciliation_postgres.py:186`, `test_p015_step5b_criterion_b_postgres.py:154`,
`test_p015_step5c_hold_races_postgres.py:620`, `test_p015_t1530_delta_arithmetic_postgres.py:229`)
propagated their failed drops before T1701 and stopped propagating them with it. This is the
regression test for that.

WHAT THIS FILE CAN AND CANNOT SEE. It drives both context managers through stand-ins for the
maintenance connection and for `drop_database`/`create_database`, so it measures WHICH EXCEPTION
LEAVES THE BLOCK on each of the four reachable paths - not the SQL, not a live server. That the
statements themselves work is measured by
`tests/integration/test_p017_t1701_schema_provisioning_postgres.py` against a real PostgreSQL.
"""

from __future__ import annotations

import pytest

from tests import migrated_schema
from tests.migrated_schema import MigratedSchemaError

_BASE_URL = "postgresql+asyncpg://geo:secret@127.0.0.1:5432/geov0_test_p017cleanup"


class _Maintenance:
    """The maintenance connection, reduced to the one thing the finally block does with it."""

    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class _BodyFailed(RuntimeError):
    """What a failing test body raises. Deliberately NOT a MigratedSchemaError."""


@pytest.fixture
def provisioning(monkeypatch):
    """Both context managers, with every database call replaced by a recording stand-in.

    `refuse_drops` is switched on from INSIDE the `async with` body, so the drop that precedes the
    copy succeeds and only the cleanup drop is refused - which is the state the defect needed.
    """

    connection = _Maintenance()
    state = {"refuse_drops": False}
    dropped: list[str] = []
    created: list[tuple[str, str | None]] = []

    async def _maintenance_connection(base_url: str):
        return connection

    async def _assert_may_create_databases(conn) -> None:
        return None

    async def _disconnect_everyone_from(conn, name: str) -> int:
        return 0

    async def _drop_database(conn, name: str) -> None:
        dropped.append(name)
        if state["refuse_drops"]:
            raise MigratedSchemaError(f"the database {name!r} could not be dropped (stand-in)")

    async def _create_database(conn, name: str, *, template: str | None = None) -> None:
        created.append((name, template))

    monkeypatch.setenv("GEO_TEST_ALLOW_DB_RESET", "1")
    monkeypatch.setattr(migrated_schema, "maintenance_connection", _maintenance_connection)
    monkeypatch.setattr(
        migrated_schema, "assert_may_create_databases", _assert_may_create_databases
    )
    monkeypatch.setattr(migrated_schema, "disconnect_everyone_from", _disconnect_everyone_from)
    monkeypatch.setattr(migrated_schema, "drop_database", _drop_database)
    monkeypatch.setattr(migrated_schema, "create_database", _create_database)

    return {
        "connection": connection,
        "state": state,
        "dropped": dropped,
        "created": created,
    }


def _clone():
    return migrated_schema.cloned_database(
        _BASE_URL, template_name="geov0_test_p017cleanup__tpl", suffix="p017c"
    )


def _scratch():
    return migrated_schema.scratch_databases(_BASE_URL, "p017sa", "p017sb")


# =====================================================================================================
# cloned_database: the four reachable paths (AGENTS.md §16.2)
# =====================================================================================================


@pytest.mark.asyncio
async def test_clone_body_passed_and_cleanup_passed_is_silent(provisioning) -> None:
    """ANTI-VACUUM (§9) and the control for every refusal below.

    A rule that let nothing through would also be red here, and a stand-in that could not produce a
    clean run at all would make the three refusals below evidence about the fixture instead of about
    the code.
    """

    async with _clone() as clone_url:
        assert clone_url.endswith("geov0_test_p017cleanup__p017c")

    assert provisioning["connection"].closed
    assert provisioning["dropped"] == [
        "geov0_test_p017cleanup__p017c",  # the stale-clone drop before the copy
        "geov0_test_p017cleanup__p017c",  # the cleanup drop
    ]


@pytest.mark.asyncio
async def test_clone_body_passed_and_cleanup_refused_reaches_the_runner(provisioning) -> None:
    """THE REPRODUCER. Before the fix this block returned normally and pytest reported a pass."""

    with pytest.raises(MigratedSchemaError, match="could not be dropped"):
        async with _clone():
            provisioning["state"]["refuse_drops"] = True

    assert provisioning["connection"].closed, (
        "the maintenance connection has to be closed even when the cleanup drop is propagating"
    )


@pytest.mark.asyncio
async def test_clone_body_failed_and_cleanup_passed_shows_the_body_error(provisioning) -> None:
    with pytest.raises(_BodyFailed):
        async with _clone():
            raise _BodyFailed("what the reader needs to see")

    assert provisioning["connection"].closed


@pytest.mark.asyncio
async def test_clone_body_failed_and_cleanup_refused_still_shows_the_body_error(
    provisioning, capsys
) -> None:
    """The half of the intent that was right: a cleanup failure must not MASK the body's failure."""

    with pytest.raises(_BodyFailed):
        async with _clone():
            provisioning["state"]["refuse_drops"] = True
            raise _BodyFailed("what the reader needs to see")

    warning = capsys.readouterr().err
    assert "geov0_test_p017cleanup__p017c" in warning, (
        "the clone was left standing and its name was not printed, so nobody can find it"
    )
    assert provisioning["connection"].closed


# =====================================================================================================
# scratch_databases: the same four paths, because it carried the same handler
# =====================================================================================================


@pytest.mark.asyncio
async def test_scratch_body_passed_and_cleanup_passed_is_silent(provisioning) -> None:
    """ANTI-VACUUM for the scratch half."""

    async with _scratch() as urls:
        assert [url.rsplit("/", 1)[-1] for url in urls] == [
            "geov0_test_p017cleanup__p017sa",
            "geov0_test_p017cleanup__p017sb",
        ]

    assert provisioning["connection"].closed


@pytest.mark.asyncio
async def test_scratch_body_passed_and_cleanup_refused_reaches_the_runner(provisioning) -> None:
    """THE REPRODUCER for the four schema-comparison modules that call this."""

    with pytest.raises(MigratedSchemaError, match="could not be dropped"):
        async with _scratch():
            provisioning["state"]["refuse_drops"] = True

    assert provisioning["connection"].closed


@pytest.mark.asyncio
async def test_scratch_body_failed_and_cleanup_passed_shows_the_body_error(provisioning) -> None:
    with pytest.raises(_BodyFailed):
        async with _scratch():
            raise _BodyFailed("what the reader needs to see")

    assert provisioning["connection"].closed


@pytest.mark.asyncio
async def test_scratch_body_failed_and_cleanup_refused_still_shows_the_body_error(
    provisioning, capsys
) -> None:
    with pytest.raises(_BodyFailed):
        async with _scratch():
            provisioning["state"]["refuse_drops"] = True
            raise _BodyFailed("what the reader needs to see")

    assert "left standing" in capsys.readouterr().err
    assert provisioning["connection"].closed


def test_no_provisioning_cleanup_decides_by_sys_exc_info() -> None:
    """The mechanism itself, because the defect was invisible in behaviour until it was executed.

    `sys.exc_info()[0] is None` inside an `except` block is ALWAYS false: the handler is running
    because an exception is being handled, and that exception is what `sys.exc_info()` reports. Any
    future reader who reaches for it to answer "did the body fail?" reintroduces exactly this bug, so
    the shape is refused here as well as its effect above.

    LIMITS (`AGENTS.md` §11 - a guard checks form, and has to say where its form ends). It reads
    CODE lines only, skipping whole-line comments, because the comment that records this lesson
    quotes the defective expression on purpose. A call hidden in a docstring, in a trailing comment
    or built from string pieces passes it. The three behavioural tests above are what actually
    measure the effect; this one only stops the shape coming back by hand.
    """

    source = (migrated_schema.REPO_ROOT / "tests" / "migrated_schema.py").read_text(
        encoding="utf-8"
    )
    offenders = [
        line.strip()
        for line in source.splitlines()
        if "sys.exc_info(" in line and not line.strip().startswith("#")
    ]
    assert not offenders, (
        "tests/migrated_schema.py decides something with sys.exc_info(): "
        f"{offenders}. Inside an except handler it cannot answer 'did the protected block fail?' - "
        "it reports the exception being handled. Record whether the block finished, on the way out "
        "of it."
    )
