"""T1701: the schema template and its clones, measured against a live PostgreSQL server.

WHAT THIS MODULE IS FOR. Programme 017 stage 1 moves the tier's schema onto one path: the migrations
build a TEMPLATE database once, and every task or test that needs its own database gets a CLONE made
with `CREATE DATABASE ... TEMPLATE`. Stage 2 is what will use it at scale; this module is what proves
it works, and - more to the point - that each of its three refusals is real rather than decorative.

THE THREE THINGS IT MEASURES, all of them named in the spec as "not costed before":

1. `CREATEDB` is a precondition. `tests/unit/test_p017_t1701_provisioning_refuses_rather_than_skips.py`
   proves the refusal fires and what it says; this module proves the probe asks a real server a
   question it can answer, which the unit test cannot see.
2. A template cannot be copied while anything is connected to it. The copy therefore terminates the
   template's sessions first, and the test below HOLDS a connection open across the copy and asserts
   the server really saw it - a copy that worked only because nothing was connected would prove
   nothing.
3. A clone left behind by a run that died must not be inherited or accumulate. Two tests: one plants
   a clone with a marker table and shows the next run destroys it rather than reusing it, one plants
   an orphan next to a decoy belonging to another task slug and shows the sweep takes the first and
   leaves the second (`AGENTS.md` §7).

THE TEMPLATE IS BUILT ONCE for the whole module and cached: it costs a full `alembic upgrade head`,
and nothing here mutates it.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from scripts.validate_test_database_url import (
    UnsafeTestDatabaseError,
    assert_safe_test_database_url,
)
from tests.conftest import TEST_DATABASE_URL
from tests.migrated_schema import (
    REPO_ROOT,
    SCRATCH_SEPARATOR,
    assert_may_create_databases,
    cloned_database,
    create_database,
    disconnect_everyone_from,
    drop_database,
    drop_stale_scratch_databases,
    maintenance_connection,
    provision_migrated_template,
    repository_head,
    scratch_database_name,
    scratch_database_url,
)

pytestmark = pytest.mark.postgres

_TEMPLATE_SUFFIX = "p017tpl"
_template_name: str | None = None


def _url() -> str:
    """The tier's URL, refused rather than skipped when it is not PostgreSQL.

    A RAISE and not a `pytest.skip`: T1701's whole subject is that a provisioning precondition
    reported as a skip is a measurement nobody took. `tests/conftest.py::pytest_collection_finish`
    already fails the session closed when a postgres-marked test is selected on another backend, so
    this is the second lock on the same door rather than a new policy.
    """

    if "postgresql" not in TEST_DATABASE_URL:
        raise RuntimeError(
            f"this module provisions PostgreSQL databases and TEST_DATABASE_URL is "
            f"{TEST_DATABASE_URL!r}. Nothing here can be measured on another backend."
        )
    return TEST_DATABASE_URL


def _base_name() -> str:
    return make_url(_url()).database or ""


async def _hold_a_session_on(name: str):
    """A raw asyncpg session on `name`, to be terminated by provisioning.

    Raw asyncpg and not an engine: this connection is about to be killed by the server, and a
    SQLAlchemy connection killed mid-block has to be unwound through the pool, which is not the
    subject here.
    """

    import asyncpg

    parsed = make_url(_url())
    return await asyncpg.connect(
        host=parsed.host,
        port=parsed.port or 5432,
        user=parsed.username,
        password=parsed.password,
        database=name,
    )


async def _release(held) -> None:
    try:
        await held.close(timeout=5)
    except Exception:  # noqa: BLE001 - it was terminated on purpose; closing it is a courtesy
        pass


async def _template() -> str:
    """The module's template database name, built by the migrations on first use."""

    global _template_name
    if _template_name is None:
        _, _template_name = await provision_migrated_template(
            _url(), suffix=_TEMPLATE_SUFFIX
        )
    return _template_name


async def _database_exists(name: str) -> bool:
    connection = await maintenance_connection(_url())
    try:
        return bool(
            await connection.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", name)
        )
    finally:
        await connection.close()


async def _backends_on(name: str) -> int:
    connection = await maintenance_connection(_url())
    try:
        return int(
            await connection.fetchval(
                "SELECT count(*) FROM pg_stat_activity WHERE datname = $1", name
            )
        )
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_the_live_role_may_create_databases_and_the_probe_says_so() -> None:
    """The precondition probe asks the server, and on a tier that works the answer is yes.

    ANTI-VACUUM for the unit test's refusal: that test scripts the answer, so it would pass against a
    probe whose SQL is nonsense. This one runs the same SQL on the real server.

    MUTATION that must redden this: change the probe's column to one that does not exist - the query
    raises here, while the unit test stays green.
    """

    connection = await maintenance_connection(_url())
    try:
        await assert_may_create_databases(connection)
        row = await connection.fetchrow(
            "SELECT current_user AS role_name, rolsuper OR rolcreatedb AS may_create "
            "FROM pg_roles WHERE rolname = current_user"
        )
        assert row is not None and row["may_create"] is True, row
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_a_clone_carries_the_migrated_schema_and_the_repository_head_stamp() -> None:
    """The clone is the migrations' schema, not an empty database and not a `create_all` one.

    The stamp alone would not show this - `alembic_version` survives a `drop_all` + `create_all`, which
    is the whole T1534 finding - so the provenance witness is a primary key NAMED by a migration,
    which `Base.metadata.create_all` could not have produced.

    MUTATION that must redden this: drop the `TEMPLATE` clause in
    `tests/migrated_schema.py::create_database`. The clone is then empty and both assertions fall.
    """

    template = await _template()
    async with cloned_database(
        _url(), template_name=template, suffix="p017clone"
    ) as clone_url:
        engine = create_async_engine(clone_url, poolclass=NullPool)
        try:
            async with engine.connect() as connection:
                stamps = (
                    await connection.execute(text("SELECT version_num FROM alembic_version"))
                ).scalars().all()
                tables = (
                    await connection.execute(
                        text("SELECT count(*) FROM pg_tables WHERE schemaname = 'public'")
                    )
                ).scalar_one()
                named_primary_keys = (
                    await connection.execute(
                        text(
                            "SELECT conname FROM pg_constraint c "
                            "JOIN pg_class t ON t.oid = c.conrelid "
                            "JOIN pg_namespace n ON n.oid = t.relnamespace "
                            "WHERE c.contype = 'p' AND n.nspname = 'public' "
                            "AND conname NOT LIKE '%\\_pkey'"
                        )
                    )
                ).scalars().all()
        finally:
            await engine.dispose()

    assert stamps == [repository_head()], stamps
    assert tables > 20, f"the clone holds {tables} tables; the migrated schema has many more"
    assert named_primary_keys, (
        "every primary key in the clone carries the name `create_all` would have produced, so the "
        "clone shows no evidence of having come from a migrated template. Either the template was "
        "not built by the migrations or the migrations stopped naming primary keys, in which case "
        "this witness can no longer tell the two construction paths apart."
    )


@pytest.mark.asyncio
async def test_a_session_held_open_on_the_template_does_not_block_the_clone() -> None:
    """PostgreSQL refuses to copy a template that anything is connected to; provisioning clears it.

    NON-VACUITY: the held connection is asserted to be visible in `pg_stat_activity` BEFORE the copy,
    so a copy that succeeded merely because nothing was connected cannot pass this.

    MUTATION that must redden this: remove the `disconnect_everyone_from(connection, template_name)`
    call from `cloned_database`. The copy then fails with `ObjectInUseError`, surfaced as a
    `MigratedSchemaError` naming the busy template.
    """

    template = await _template()
    held = await _hold_a_session_on(template)
    try:
        assert await _backends_on(template) >= 1, (
            "no session is connected to the template, so this test would pass whether or not "
            "provisioning disconnects anything. Nothing was measured."
        )

        async with cloned_database(
            _url(), template_name=template, suffix="p017busy"
        ) as clone_url:
            assert clone_url.endswith(scratch_database_name(_base_name(), "p017busy"))
    finally:
        await _release(held)


@pytest.mark.asyncio
async def test_a_clone_left_by_a_dead_run_is_destroyed_rather_than_inherited() -> None:
    """A crashed run leaves a database standing; the next run of that name must not build on it.

    MUTATION that must redden this: remove the `drop_database(connection, clone_name)` call that
    precedes the copy in `cloned_database`. The marker table then survives into the new clone, and
    `CREATE DATABASE` would in any case fail as a duplicate.
    """

    template = await _template()
    stale_name = scratch_database_name(_base_name(), "p017stale")
    stale_url, _ = scratch_database_url(_url(), "p017stale")

    connection = await maintenance_connection(_url())
    try:
        await drop_database(connection, stale_name)
        await create_database(connection, stale_name, template=template)
    finally:
        await connection.close()

    engine = create_async_engine(stale_url, poolclass=NullPool)
    try:
        async with engine.begin() as marker_connection:
            await marker_connection.exec_driver_sql(
                "CREATE TABLE p017_marker_of_a_dead_run (id integer PRIMARY KEY)"
            )
        async with engine.connect() as marker_connection:
            planted = (
                await marker_connection.execute(
                    text("SELECT to_regclass('public.p017_marker_of_a_dead_run')")
                )
            ).scalar_one()
        assert planted is not None, "the marker was not planted, so nothing would be measured"
    finally:
        await engine.dispose()

    async with cloned_database(
        _url(), template_name=template, suffix="p017stale"
    ) as clone_url:
        engine = create_async_engine(clone_url, poolclass=NullPool)
        try:
            async with engine.connect() as fresh:
                survived = (
                    await fresh.execute(
                        text("SELECT to_regclass('public.p017_marker_of_a_dead_run')")
                    )
                ).scalar_one()
                stamped = (
                    await fresh.execute(text("SELECT version_num FROM alembic_version"))
                ).scalars().all()
        finally:
            await engine.dispose()

    assert survived is None, (
        "the clone inherited a table from the database a dead run left behind: the schema under test "
        "is then whatever that run happened to leave, not the template's."
    )
    assert stamped == [repository_head()], stamped


@pytest.mark.asyncio
async def test_the_clone_is_dropped_when_the_block_ends() -> None:
    """Clones do not accumulate: the database is gone once the context manager returns."""

    template = await _template()
    async with cloned_database(
        _url(), template_name=template, suffix="p017tidy"
    ) as clone_url:
        name = scratch_database_name(_base_name(), "p017tidy")
        assert clone_url.endswith(name)
        assert await _database_exists(name), "the clone was never created"

    assert not await _database_exists(name), "the clone outlived the block that created it"


@pytest.mark.asyncio
async def test_the_sweep_takes_this_tasks_orphans_and_leaves_a_neighbours_database() -> None:
    """The stale-database sweep is bounded by this task's own name (`AGENTS.md` §7).

    Both halves are the measurement: an orphan of this task IS dropped (otherwise the sweep is a
    no-op that would pass by doing nothing), and a database whose name merely starts with this task's
    name is NOT (otherwise one agent's run deletes another's tier).

    AND THE CASE THIS TEST USED TO MISS (2026-09-22, Codex external review of `e2e1380..37fec08`).
    The decoy was `<base>_decoy`, with a SINGLE underscore - which the prefix `<base>__` cannot match
    whatever the rest of the module does. The dangerous neighbour is the one with a DOUBLED
    underscore: `p017fixa__probe` is a valid task slug, its tier database is
    `geov0_test_p017fixa__probe`, and the sweep for the task `p017fixa` dropped it. Reproduced on
    PostgreSQL 16 with two disposable databases. What closed it is not a third underscore but the
    reservation asserted below: no TIER database may carry the separator, so a `<base>__*` name can
    only be a scratch database of `<base>` - and dropping it is then correct rather than lucky.

    MUTATIONS that must redden this: change the sweep's separator back to a single underscore (the
    `..._decoy` is then swept too); or let `assert_safe_test_database_url` accept a tier name
    carrying the separator again (the reservation assertion below then fails, and with it the only
    reason the doubled-underscore drop is legitimate).
    """

    base = _base_name()
    orphan = scratch_database_name(base, "p017orphan")
    decoy = f"{base}_decoy"
    doubled = f"{base}{SCRATCH_SEPARATOR}p017probe"

    # THE RESERVATION FIRST, because it is what makes the drop below defensible. If a tier could be
    # named like this, the sweep would be destroying a neighbour's database, not collecting its own
    # orphan - and no assertion about `dropped` could tell the two apart.
    with pytest.raises(UnsafeTestDatabaseError, match="doubled underscore"):
        assert_safe_test_database_url(
            make_url(_url()).set(database=doubled).render_as_string(hide_password=False),
            allow_destructive_reset="1",
            repo_root=REPO_ROOT,
            required_backend="postgresql",
        )

    connection = await maintenance_connection(_url())
    try:
        await drop_database(connection, orphan)
        await drop_database(connection, decoy)
        await drop_database(connection, doubled)
        await create_database(connection, orphan)
        await create_database(connection, decoy)
        await create_database(connection, doubled)

        dropped = await drop_stale_scratch_databases(connection, base)

        assert orphan in dropped, dropped
        assert doubled in dropped, (
            "a `<tier>__<name>` database was left standing. It cannot be a tier database of any "
            "task - the guard above refuses that name - so it is this task's orphan and the sweep "
            "has to take it."
        )
        assert decoy not in dropped, dropped
        assert base not in dropped, "the sweep dropped the tier's own database"
        assert bool(
            await connection.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", decoy)
        ), "a database belonging to another task slug was swept away"

        # The sweep is deliberately broad WITHIN this task: the template matches `<base>__*` too and
        # is dropped here. Saying so out loud and rebuilding is honest; leaving the cached name
        # pointing at a database that no longer exists would make the next test fail for an
        # unrelated reason.
        assert await _template() in dropped, dropped
        global _template_name
        _template_name = None
    finally:
        try:
            await drop_database(connection, decoy)
            await drop_database(connection, doubled)
        finally:
            await connection.close()


@pytest.mark.asyncio
async def test_disconnecting_reports_what_it_terminated() -> None:
    """The count is what makes the disconnection step observable rather than assumed."""

    template = await _template()
    held = await _hold_a_session_on(template)
    connection = await maintenance_connection(_url())
    try:
        terminated = await disconnect_everyone_from(connection, template)
        assert terminated >= 1, (
            "a session was connected to the template and nothing was terminated, so the step that "
            "makes the copy possible did not run."
        )
        assert await disconnect_everyone_from(connection, template) == 0
    finally:
        await _release(held)
        await connection.close()
