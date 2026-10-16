"""Migration 037 (042 S1, `T4202`): `chk_simulator_runs_mode` narrows to `'real'`; a `fixtures` row refuses it.

The server run mode `fixtures` is gone (spec 042). Upgrade from 036 narrows the CHECK; while any `simulator_runs` row
says `mode = 'fixtures'` it REFUSES, naming the count, and deletes nothing (spec, "Запрещено": no row is removed by
a migration) - the database stays at 036 with the row. Without such a row it applies, and the database then refuses
a new `fixtures` row. The downgrade widens the CHECK back to `('fixtures','real')` and always applies. One head.

MUTATIONS that must redden this: drop the count from the upgrade (it fails on the constraint instead of refusing by
name - or, with `NOT VALID`, applies over the row); delete the rows in the upgrade (the row is gone); leave the
CHECK as it was (the `fixtures` insert after the upgrade is stored).
"""

from __future__ import annotations

import pytest

from tests.integration.test_p019_migration_031_postgres import _alembic, _exec, _read, _version
from tests.migrated_schema import repository_head

_BEFORE = "036_clearing_records_no_initiator"
_AFTER = "037_simulator_run_mode_real_only"
_CHECK = (
    "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
    "WHERE conrelid = 'public.simulator_runs'::regclass AND conname = 'chk_simulator_runs_mode'"
)
_ROWS = "SELECT run_id, mode FROM simulator_runs WHERE run_id LIKE 'p042-m037-%' ORDER BY run_id"
_ROW = "INSERT INTO simulator_runs (run_id, scenario_id, mode, state, owner_id) VALUES (:id, 's', :mode, 'stopped', 'test')"


@pytest.mark.asyncio
async def test_037_narrows_the_mode_and_refuses_over_a_fixtures_row(committed_database) -> None:
    url = committed_database.url
    await committed_database.engine.dispose()
    assert await _version(url) == [(repository_head(),)]
    if repository_head() != _AFTER:  # a later head: step down to the revision under test
        stepped = _alembic(url, "downgrade", _AFTER)
        assert stepped.returncode == 0, stepped.stderr
    narrowed = await _read(url, _CHECK)

    down = _alembic(url, "downgrade", _BEFORE)
    assert down.returncode == 0, down.stderr
    assert await _version(url) == [(_BEFORE,)]
    widened = await _read(url, _CHECK)
    assert widened != narrowed and "fixtures" in widened[0][0], (widened, narrowed)
    await _exec(url, _ROW, id="p042-m037-legacy", mode="fixtures")
    await _exec(url, _ROW, id="p042-m037-live", mode="real")
    before = await _read(url, _ROWS)
    assert before == [("p042-m037-legacy", "fixtures"), ("p042-m037-live", "real")]

    refused = _alembic(url, "upgrade", _AFTER)
    assert refused.returncode != 0 and "refusing to upgrade to 037: 1 simulator run(s)" in refused.stderr, (
        refused.stderr[-2000:])
    assert await _version(url) == [(_BEFORE,)] and await _read(url, _ROWS) == before

    await _exec(url, "DELETE FROM simulator_runs WHERE run_id = 'p042-m037-legacy'")
    up = _alembic(url, "upgrade", _AFTER)
    assert up.returncode == 0, up.stderr
    assert await _version(url) == [(_AFTER,)] and await _read(url, _CHECK) == narrowed
    assert "fixtures" not in narrowed[0][0], narrowed
    assert await _read(url, _ROWS) == before[1:]
    with pytest.raises(Exception, match="chk_simulator_runs_mode"):
        await _exec(url, _ROW, id="p042-m037-again", mode="fixtures")
    assert await _read(url, _ROWS) == before[1:]
