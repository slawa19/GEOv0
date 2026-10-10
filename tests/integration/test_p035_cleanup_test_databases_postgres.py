"""`scripts/cleanup_test_databases.py` on a real PostgreSQL server - and only on databases this test creates.

The selection logic is pinned without a server in
`tooling-tests/portable/test_p035_cleanup_test_databases_selection.py`. What needs the server is here:
* the dry run changes nothing;
* an apply drops clones, then templates, then the tier database, and leaves a database that is not in the manifest;
* a session that connects after the manifest was written makes the apply REFUSE - and that session is not ended.
  All the ways it can be met: seen in the family (nothing is sent), seen in ANOTHER unprotected family that no row
  names (nothing is sent; protecting that family lets the apply proceed), and arriving between the last comparison
  and the drop (PostgreSQL itself refuses the ordinary `DROP DATABASE`);
* a database recreated under the same name is not the one the manifest named;
* a database whose NAME carries a quote and a `?` (review of `ddcb97b6`, F1): the server quotes that name exactly
  as the command's `escaped()` does, the dry run keeps it, a manifest naming it is refused, and a statement built
  from it reaches that database and not the one its unescaped text would name.

THE SERVER IS SHARED, AND TWO THINGS FOLLOW.
1. The command is given a view of the catalog NARROWED TO THIS TEST'S NAMES (`_OwnView`: every catalog read gets
   one more predicate, `datname LIKE '<this test's unique prefix>%'`; every other statement goes to the server
   unchanged). Without it a dry run would name every neighbour's database, and the apply's rule "no unprotected
   test family on the server may have a connection" would make these tests fail whenever a neighbour runs.
2. The manifest is still REDUCED to the three names of the family and that is asserted before any apply
   (`_only_ours`).

THE TEARDOWN ACTS ONLY ON WHAT THIS TEST CREATED (same review, F3): a name is recorded with its OID after its
`CREATE DATABASE` succeeded, and only a database that still has that name AND that OID is dropped - by an ordinary
`DROP DATABASE`, never the forceful helper.

No sleep and no timer: the arriving session is a connection the test opens and holds.
"""

from __future__ import annotations

import os
import uuid

import asyncpg
import pytest
import pytest_asyncio
from sqlalchemy.engine import make_url

from scripts import cleanup_test_databases as cleanup
from tests.migrated_schema import create_database, maintenance_connection

_QUIET = {"report": lambda _line: None}


class _OwnView:
    """The maintenance connection as the command sees it: catalog reads narrowed to this test's names."""

    def __init__(self, real, prefix: str) -> None:
        self._real, self._prefix = real, prefix
        self.before_drop = None  # an async callable run once, just before the first DROP is sent

    def __getattr__(self, name: str):
        return getattr(self._real, name)

    async def fetch(self, sql: str, *args, **kwargs):
        marker = "where d.datname not in ("
        if marker in sql:
            assert "'" not in self._prefix and "%" not in self._prefix
            sql = sql.replace(marker, f"where d.datname like '{self._prefix}%' and d.datname not in (")
        return await self._real.fetch(sql, *args, **kwargs)

    async def execute(self, sql: str, *args, **kwargs):
        if sql.startswith("DROP DATABASE") and self.before_drop is not None:
            hook, self.before_drop = self.before_drop, None
            await hook(sql)
        return await self._real.execute(sql, *args, **kwargs)


class _OwnDatabases:
    """One family of four - tier, two templates, a clone - and the tier database of a second family."""

    def __init__(self, maintenance) -> None:
        self.maintenance = maintenance
        self.slug = f"p035clean{uuid.uuid4().hex[:10]}"
        self.tier = f"geov0_test_{self.slug}"
        self.template = f"{self.tier}__modebtpl"
        self.clone = f"{self.tier}__c1"
        self.other_slug = f"{self.slug}x"
        self.other = f"geov0_test_{self.other_slug}"
        self.family = [self.tier, self.template, self.clone]
        self.created: dict[str, int] = {}  # name -> OID, ONLY of databases this object created
        self.server = _OwnView(maintenance, self.tier)

    async def oid(self, name: str) -> int | None:
        return await self.maintenance.fetchval("select oid::bigint from pg_database where datname = $1", name)

    async def make(self, name: str) -> None:
        """Create `name` and record it - in that order, so a name that could not be created is never recorded."""

        if cleanup.validated_name(name) is None:
            await create_database(self.maintenance, name)
        else:  # a name outside the grammar: quoted by the SERVER, not by the code under test
            await self.maintenance.execute(
                await self.maintenance.fetchval("select format('CREATE DATABASE %I', $1::text)", name)
            )
        self.created[name] = await self.oid(name)

    async def create(self) -> "_OwnDatabases":
        for name in [*self.family, self.other]:
            await self.make(name)
        return self

    async def destroy(self) -> None:
        for name, oid in reversed(list(self.created.items())):
            if await self.oid(name) != oid:
                continue  # already gone - or no longer the database this test created: not this test's to touch
            await self.maintenance.execute(
                await self.maintenance.fetchval("select format('DROP DATABASE %I', $1::text)", name)
            )

    async def state(self) -> dict[str, int]:
        rows = await self.maintenance.fetch(
            "select datname, oid::bigint from pg_database where datname = any($1::text[])", list(self.created)
        )
        return {row["datname"]: row["oid"] for row in rows}

    def _only_ours(self, manifest: dict) -> dict:
        """The manifest reduced to the first family. NOTHING ELSE may reach an apply on a shared server."""

        manifest["databases"] = [entry for entry in manifest["databases"] if entry["family"] == self.slug]
        names = sorted(entry["name"] for entry in manifest["databases"])
        assert names == sorted(self.family), names
        assert all(self.created.get(entry["name"]) == entry["oid"] for entry in manifest["databases"])
        return manifest

    async def manifest(self) -> dict:
        return self._only_ours(await cleanup.build_manifest(self.server, protected=[]))

    async def connect_to(self, name: str):
        url = make_url(os.environ["TEST_DATABASE_URL"])
        return await asyncpg.connect(
            host=url.host, port=url.port or 5432, user=url.username, password=url.password, database=name
        )


@pytest_asyncio.fixture
async def own():
    maintenance = await maintenance_connection(os.environ["TEST_DATABASE_URL"])
    databases = _OwnDatabases(maintenance)
    try:
        yield await databases.create()
    finally:
        try:
            await databases.destroy()
        finally:
            await maintenance.close()


@pytest.mark.asyncio
async def test_the_dry_run_describes_the_family_and_changes_nothing(own: _OwnDatabases) -> None:
    before = await own.state()

    whole = await cleanup.build_manifest(own.server, protected=[own.other_slug])

    assert await own.state() == before == own.created and len(before) == 4
    assert {e["name"]: (e["kind"], e["disposition"], e["oid"]) for e in whole["databases"]} == {
        own.tier: ("tier", "DROP", own.created[own.tier]),
        own.template: ("template", "DROP", own.created[own.template]),
        own.clone: ("clone", "DROP", own.created[own.clone]),
        own.other: ("tier", "KEEP", own.created[own.other]),
    }
    assert all(e["size_bytes"] > 0 and e["is_template"] is False for e in whole["databases"])
    assert whole["server"]["system_identifier"] and whole["protected"] == [own.other_slug]


@pytest.mark.asyncio
async def test_an_apply_drops_the_manifest_in_order_and_nothing_outside_it(own: _OwnDatabases) -> None:
    manifest = await own.manifest()

    outcomes = await cleanup.apply_manifest(own.server, manifest, protected=[], expect_drop_count=3, **_QUIET)

    assert [outcome["name"] for outcome in outcomes] == [own.clone, own.template, own.tier]
    assert await own.state() == {own.other: own.created[own.other]}, "only the database outside the manifest stays"


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["the clone", "the template of the family"])
async def test_a_session_that_arrived_after_the_manifest_stops_the_apply_and_is_not_ended(
    own: _OwnDatabases, target: str
) -> None:
    manifest = await own.manifest()
    arrived = await own.connect_to(own.clone if target == "the clone" else own.template)
    try:
        with pytest.raises(cleanup.CleanupRefused) as refused:
            await cleanup.apply_manifest(own.server, manifest, protected=[], expect_drop_count=3, **_QUIET)

        assert "has a connection now" in str(refused.value), refused.value
        assert refused.value.outcomes == [] and await own.state() == own.created, "something was dropped"
        assert await arrived.fetchval("select 1") == 1, "the arriving session was ended"
    finally:
        await arrived.close()


@pytest.mark.asyncio
async def test_a_session_in_another_unprotected_family_stops_the_apply_and_protecting_it_lets_it_run(
    own: _OwnDatabases,
) -> None:
    """The server-wide rule and its counter-check: a family NO ROW NAMES is in use. Unprotected, that stops the
    apply - the server is not quiet; declared protected, it is exactly what protection is for."""

    manifest = await own.manifest()
    neighbour = await own.connect_to(own.other)
    try:
        with pytest.raises(cleanup.CleanupRefused) as refused:
            await cleanup.apply_manifest(own.server, manifest, protected=[], expect_drop_count=3, **_QUIET)
        assert own.other in str(refused.value) and await own.state() == own.created, refused.value

        outcomes = await cleanup.apply_manifest(
            own.server, manifest, protected=[own.other_slug], expect_drop_count=3, **_QUIET
        )

        assert len(outcomes) == 3 and await own.state() == {own.other: own.created[own.other]}
        assert await neighbour.fetchval("select 1") == 1, "the neighbour's session was ended"
    finally:
        await neighbour.close()


@pytest.mark.asyncio
async def test_a_session_that_arrives_between_the_last_comparison_and_the_drop_is_refused_by_the_server_itself(
    own: _OwnDatabases,
) -> None:
    manifest = await own.manifest()
    arrived: list = []

    async def a_session_connects(sql: str) -> None:
        assert sql == f'DROP DATABASE "{own.clone}"', sql
        arrived.append(await own.connect_to(own.clone))

    own.server.before_drop = a_session_connects
    try:
        with pytest.raises(cleanup.CleanupRefused) as refused:
            await cleanup.apply_manifest(own.server, manifest, protected=[], expect_drop_count=3, **_QUIET)

        assert arrived, "premise: the drop was reached, every comparison had passed"
        assert "PostgreSQL refused the drop" in str(refused.value) and own.clone in str(refused.value), refused.value
        assert refused.value.outcomes == [] and refused.value.uncertain is None
        assert await own.state() == own.created, "something was dropped"
        assert await arrived[0].fetchval("select 1") == 1, "the arriving session was ended"
    finally:
        for connection in arrived:
            await connection.close()


@pytest.mark.asyncio
async def test_a_database_recreated_under_the_same_name_is_not_the_one_the_manifest_named(own: _OwnDatabases) -> None:
    manifest = await own.manifest()
    await own.maintenance.execute(f'DROP DATABASE "{own.clone}"')
    await own.make(own.clone)  # the same name, another database: a new OID (recorded, so the teardown removes it)
    before = await own.state()

    with pytest.raises(cleanup.CleanupRefused) as refused:
        await cleanup.apply_manifest(own.server, manifest, protected=[], expect_drop_count=3, **_QUIET)

    assert "OID" in str(refused.value) and await own.state() == before, refused.value


@pytest.mark.asyncio
async def test_a_name_with_a_quote_and_a_question_mark_cannot_redirect_a_statement(own: _OwnDatabases) -> None:
    """F1 on a real server. `<tier>?" WITH (FORCE)--` exists beside `<tier>?`. Unescaped, a statement built from
    the first would read `DROP DATABASE "<tier>?" WITH (FORCE)--"` - the second database, and with FORCE."""

    evil, target = f'{own.tier}?" WITH (FORCE)--', f"{own.tier}?"
    await own.make(evil)
    await own.make(target)

    # The server's own quoting of these names is the command's.
    for name in (evil, target, own.tier + '"'):
        assert cleanup.escaped(name) == await own.maintenance.fetchval("select quote_ident($1::text)", name), name

    # The dry run keeps both; a manifest naming one of them cannot be used; a statement cannot be written.
    whole = await cleanup.build_manifest(own.server, protected=[own.other_slug])
    assert {e["name"]: e["disposition"] for e in whole["databases"] if e["name"] in (evil, target)} == {
        evil: "KEEP", target: "KEEP",
    }
    next(e for e in whole["databases"] if e["name"] == evil).update(disposition="DROP", family=own.slug, kind="tier")
    with pytest.raises(cleanup.UsageRefused):
        await cleanup.apply_manifest(own.server, whole, protected=[own.other_slug], expect_drop_count=4, **_QUIET)
    with pytest.raises(cleanup.CleanupRefused):
        cleanup.drop_statement(evil)
    assert await own.state() == own.created, "something was dropped for a manifest that names such a database"

    # And the escaping alone, with no validation in front of it: the statement reaches `evil`, not `target`.
    await own.maintenance.execute(f"DROP DATABASE {cleanup.escaped(evil)}")
    remaining = await own.state()
    assert evil not in remaining and remaining[target] == own.created[target], "the statement named another database"


@pytest.mark.asyncio
async def test_the_fixtures_teardown_leaves_a_database_it_did_not_create() -> None:
    """F3. A name the fixture wants is already taken by somebody: creation fails, and the teardown that follows
    must not touch that database - it was never this test's."""

    maintenance = await maintenance_connection(os.environ["TEST_DATABASE_URL"])
    databases = _OwnDatabases(maintenance)
    neighbour = _OwnDatabases(maintenance)
    neighbour.other = databases.other
    try:
        await neighbour.make(databases.other)  # "somebody else's", standing where the fixture will create
        with pytest.raises(Exception):
            await databases.create()
        assert sorted(databases.created) == sorted(databases.family), "premise: three were created, the fourth was not"
        # And one the fixture DID create is replaced by somebody under the same name: another database, a new OID.
        await maintenance.execute(f'DROP DATABASE "{databases.clone}"')
        await neighbour.make(databases.clone)

        await databases.destroy()

        assert await neighbour.state() == neighbour.created, (
            "the teardown dropped a database the fixture had not created (a name it failed to create, or a name "
            "that is no longer its database)"
        )
        assert sorted(await databases.state()) == [databases.clone], "the teardown left what the fixture did create"
    finally:
        try:
            await databases.destroy()
            await neighbour.destroy()
        finally:
            await maintenance.close()
