"""`scripts/cleanup_test_databases.py` on a real PostgreSQL server - and only on databases this test creates.

The selection logic is pinned without a server in
`tooling-tests/portable/test_p035_cleanup_test_databases_selection.py`. What needs the server is here:
* the dry run changes nothing;
* an apply drops a clone, a template and a tier database, in that order, and leaves a database that is not in the
  manifest;
* a session that connects after the manifest was written makes the apply REFUSE - and that session is not ended.
  Both ways it can be met: seen by the re-check (nothing is sent), and arriving between the re-check and the drop
  (PostgreSQL itself refuses the ordinary `DROP DATABASE`; the template's flag is put back).

THE SERVER IS SHARED AND THE MANIFEST OF A DRY RUN NAMES EVERY DATABASE ON IT. Each test therefore REDUCES the
manifest to the databases it created, under a slug no other run can have (`uuid4`), and asserts that reduction
before anything is applied (`_only_ours`). A test that applied the manifest as written would drop the neighbours'
databases - which is exactly what the command's review step exists to prevent.

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
from tests.migrated_schema import create_database, drop_database, maintenance_connection


class _OwnDatabases:
    """One family of three - tier, template, clone - and the tier database of a second family."""

    def __init__(self, maintenance) -> None:
        self.maintenance = maintenance
        self.slug = f"p035clean{uuid.uuid4().hex[:10]}"
        self.tier = f"geov0_test_{self.slug}"
        self.template = f"{self.tier}__tpl"
        self.clone = f"{self.tier}__c1"
        self.other_slug = f"{self.slug}x"
        self.other = f"geov0_test_{self.other_slug}"
        self.names = [self.tier, self.template, self.clone, self.other]

    async def create(self) -> "_OwnDatabases":
        for name in self.names:
            await create_database(self.maintenance, name)
        await self.maintenance.execute(f'ALTER DATABASE "{self.template}" IS_TEMPLATE true')
        return self

    async def destroy(self) -> None:
        for name in self.names:  # this test's own databases: the forceful helper is its to use on them
            if await self.exists(name):
                await self.maintenance.execute(f'ALTER DATABASE "{name}" IS_TEMPLATE false')
                await drop_database(self.maintenance, name)

    async def exists(self, name: str) -> bool:
        return bool(await self.maintenance.fetchval("select count(*) from pg_database where datname = $1", name))

    async def state(self) -> list[tuple]:
        rows = await self.maintenance.fetch(
            "select datname, oid::bigint, datistemplate from pg_database where datname = any($1::text[]) order by 1",
            self.names,
        )
        return [tuple(row) for row in rows]

    def _only_ours(self, manifest: dict) -> dict:
        """The manifest reduced to the first family. NOTHING ELSE may reach an apply on a shared server."""

        manifest["databases"] = [entry for entry in manifest["databases"] if entry["family"] == self.slug]
        names = sorted(entry["name"] for entry in manifest["databases"])
        assert names == sorted([self.tier, self.template, self.clone]), names
        assert all(name.startswith(self.tier) for name in names)
        return manifest

    async def manifest(self) -> dict:
        return self._only_ours(await cleanup.build_manifest(self.maintenance, protected=[]))

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

    whole = await cleanup.build_manifest(own.maintenance, protected=[own.other_slug])

    assert await own.state() == before and len(before) == 4
    mine = {entry["name"]: entry for entry in whole["databases"] if entry["name"] in own.names}
    assert {name: (e["kind"], e["disposition"]) for name, e in mine.items()} == {
        own.tier: ("tier", "DROP"), own.template: ("template", "DROP"), own.clone: ("clone", "DROP"),
        own.other: ("tier", "KEEP"),
    }
    assert mine[own.template]["is_template"] is True and mine[own.tier]["size_bytes"] > 0
    assert {name: e["oid"] for name, e in mine.items()} == {name: oid for name, oid, _template in before}
    assert whole["server"]["system_identifier"] and whole["protected"] == [own.other_slug]


@pytest.mark.asyncio
async def test_an_apply_drops_the_manifest_in_order_and_nothing_outside_it(own: _OwnDatabases) -> None:
    manifest = await own.manifest()

    outcomes = await cleanup.apply_manifest(own.maintenance, manifest, protected=[])

    assert [outcome["name"] for outcome in outcomes] == [own.clone, own.template, own.tier]
    assert [name for name, _oid, _template in await own.state()] == [own.other], "only the database outside the manifest stays"


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["the clone", "the template of the family"])
async def test_a_session_that_arrived_after_the_manifest_stops_the_apply_and_is_not_ended(
    own: _OwnDatabases, target: str
) -> None:
    manifest = await own.manifest()
    before = await own.state()
    arrived = await own.connect_to(own.clone if target == "the clone" else own.template)
    try:
        with pytest.raises(cleanup.CleanupRefused) as refused:
            await cleanup.apply_manifest(own.maintenance, manifest, protected=[])

        assert "connection(s) in family" in str(refused.value), refused.value
        assert refused.value.outcomes == [] and await own.state() == before, "something was dropped or changed"
        assert await arrived.fetchval("select 1") == 1, "the arriving session was ended"
    finally:
        await arrived.close()


class _ASessionArrivesBeforeTheDrop:
    """The maintenance connection, with one thing added: just before the first `DROP DATABASE` is sent, a session
    connects to that database - after the re-check has passed."""

    def __init__(self, real, own: _OwnDatabases) -> None:
        self._real, self._own, self.arrived = real, own, None

    def __getattr__(self, name: str):
        return getattr(self._real, name)

    async def execute(self, sql: str, *args, **kwargs):
        if sql.startswith("DROP DATABASE") and self.arrived is None:
            self.arrived = await self._own.connect_to(sql.split('"')[1])
        return await self._real.execute(sql, *args, **kwargs)


@pytest.mark.asyncio
async def test_a_session_that_arrives_between_the_recheck_and_the_drop_is_refused_by_the_server_itself(
    own: _OwnDatabases,
) -> None:
    manifest = await own.manifest()
    manifest["databases"] = [e for e in manifest["databases"] if e["kind"] != "clone"]  # the template goes first
    before = await own.state()
    connection = _ASessionArrivesBeforeTheDrop(own.maintenance, own)
    try:
        with pytest.raises(cleanup.CleanupRefused) as refused:
            await cleanup.apply_manifest(connection, manifest, protected=[])

        assert connection.arrived is not None, "premise: the drop was reached, the re-check had passed"
        assert "PostgreSQL refused the drop" in str(refused.value) and own.template in str(refused.value), refused.value
        assert await own.state() == before, "the template lost its flag, or something was dropped"
        assert await connection.arrived.fetchval("select 1") == 1, "the arriving session was ended"
    finally:
        if connection.arrived is not None:
            await connection.arrived.close()


@pytest.mark.asyncio
async def test_a_database_recreated_under_the_same_name_is_not_the_one_the_manifest_named(own: _OwnDatabases) -> None:
    manifest = await own.manifest()
    await drop_database(own.maintenance, own.clone)
    await create_database(own.maintenance, own.clone)  # the same name, another database: a new OID
    before = await own.state()

    with pytest.raises(cleanup.CleanupRefused) as refused:
        await cleanup.apply_manifest(own.maintenance, manifest, protected=[])

    assert "OID" in str(refused.value) and await own.state() == before, refused.value
