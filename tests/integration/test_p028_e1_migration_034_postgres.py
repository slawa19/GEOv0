"""Migration 034 (028 `T2811`): `simulator_runs.seed` becomes `bigint`, values unchanged.

Upgrade from 033 keeps a legacy row's seed; a 32-bit seed is then stored. A row with a seed >= 2**31 refuses the
downgrade (the database stays at 034 and keeps the row); without one the downgrade restores `integer` and the
upgrade applies again. One head.

MUTATIONS that must redden this: drop the ALTER (the 32-bit seed is refused); drop the count from the downgrade (it
fails on the cast instead of refusing by name).
"""

from __future__ import annotations

import pytest

from tests.integration.test_p019_migration_031_postgres import _alembic, _exec, _read, _version
from tests.migrated_schema import repository_head

_BEFORE = "033_trust_line_close_requested_at"
_AFTER = "034_simulator_run_seed_bigint"
_TYPE = "SELECT data_type FROM information_schema.columns WHERE table_name = 'simulator_runs' AND column_name = 'seed'"
_SEEDS = "SELECT run_id, seed FROM simulator_runs WHERE run_id LIKE 'p028-m034-%' ORDER BY run_id"
_ROW = ("INSERT INTO simulator_runs (run_id, scenario_id, mode, state, owner_id, seed) "
        "VALUES (:id, 's', 'fixtures', 'stopped', 'test', :seed)")


@pytest.mark.asyncio
async def test_034_widens_the_seed_and_goes_back_only_without_a_wide_one(committed_database) -> None:
    url = committed_database.url
    await committed_database.engine.dispose()
    assert repository_head() == _AFTER and await _version(url) == [(_AFTER,)]
    assert _alembic(url, "downgrade", _BEFORE).returncode == 0
    await _exec(url, _ROW, id="p028-m034-legacy", seed=2**31 - 1)

    up = _alembic(url, "upgrade", _AFTER)
    assert up.returncode == 0, up.stderr
    assert await _read(url, _TYPE) == [("bigint",)]
    await _exec(url, _ROW, id="p028-m034-wide", seed=2**32 - 1)
    before = await _read(url, _SEEDS)
    assert before == [("p028-m034-legacy", 2**31 - 1), ("p028-m034-wide", 2**32 - 1)]

    refused = _alembic(url, "downgrade", _BEFORE)
    assert refused.returncode != 0 and "refusing to downgrade 034: 1 simulator run(s)" in refused.stderr, (
        refused.stderr)
    assert await _version(url) == [(_AFTER,)] and await _read(url, _SEEDS) == before

    await _exec(url, "DELETE FROM simulator_runs WHERE run_id = 'p028-m034-wide'")
    assert _alembic(url, "downgrade", _BEFORE).returncode == 0
    assert await _read(url, _TYPE) == [("integer",)]
    assert await _read(url, _SEEDS) == before[:1]
    assert _alembic(url, "upgrade", _AFTER).returncode == 0
    assert await _version(url) == [(_AFTER,)]
