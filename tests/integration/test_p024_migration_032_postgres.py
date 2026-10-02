"""Migration 032 (programme 024, `T2413.2`): an audit row may say that no check ran.

`integrity_audit_log.verification_passed` becomes NULL-able: `null` = no verification ran for the row (money
operations after `T2413.2`), distinct from `false` (a check found a violation) and `true`. Round trip on one
clone at the head: a `null` row refuses the downgrade (the database stays at 032 and keeps the row - turning
"not run" into `false` or `true` would falsify the audit, deleting it would lose the trail); without such
rows the downgrade restores `NOT NULL` over the rows that are there, unchanged, and the upgrade applies again.

MUTATIONS that must redden this: drop the null count from the downgrade (the planted row reaches the
`ALTER` and the error text differs); make the upgrade a no-op (the `null` insert fails).
"""

from __future__ import annotations

import uuid

import pytest

from tests.integration.test_p019_migration_031_postgres import _alembic, _exec, _read, _version
from tests.migrated_schema import repository_head

_BEFORE = "031_drop_prepare_locks"
_AFTER = "032_audit_row_may_record_no_check"
_ROWS = "SELECT id, verification_passed FROM integrity_audit_log ORDER BY id"
_INSERT = (
    "INSERT INTO integrity_audit_log (id, operation_type, equivalent_code, state_checksum_before, "
    "state_checksum_after, affected_participants, invariants_checked, verification_passed) "
    "VALUES (:id, 'PAYMENT', 'M032', '', '', '{}', '{}', :passed)"
)


async def _not_null(url: str) -> bool:
    rows = await _read(url, "SELECT attnotnull FROM pg_attribute WHERE attrelid = 'integrity_audit_log'::regclass "
                            "AND attname = 'verification_passed'")
    return rows[0][0]


@pytest.mark.asyncio
async def test_032_lets_a_row_say_no_check_ran_and_goes_back_only_without_such_rows(committed_database) -> None:
    url = committed_database.url
    await committed_database.engine.dispose()
    assert await _version(url) == [(repository_head(),)]
    if repository_head() != _AFTER:  # a later head (033, 026 `T2603.1`): step down to the revision under test
        stepped = _alembic(url, "downgrade", _AFTER)
        assert stepped.returncode == 0, stepped.stderr
    assert await _version(url) == [(_AFTER,)]
    assert not await _not_null(url)

    kept = [uuid.uuid4(), uuid.uuid4()]
    for row_id, passed in zip(kept, (True, False)):
        await _exec(url, _INSERT, id=row_id, passed=passed)
    not_run = uuid.uuid4()
    await _exec(url, _INSERT, id=not_run, passed=None)
    before = await _read(url, _ROWS)

    refused = _alembic(url, "downgrade", _BEFORE)
    assert refused.returncode != 0, "032 went down over a row that says no check ran"
    assert "refusing to downgrade 032: 1 integrity_audit_log row(s) record that no check ran" in refused.stderr, (
        refused.stderr
    )
    assert await _version(url) == [(_AFTER,)]
    assert await _read(url, _ROWS) == before, "the refused downgrade changed the audit rows"

    await _exec(url, "DELETE FROM integrity_audit_log WHERE id = :id", id=not_run)  # the test's own planted row
    down = _alembic(url, "downgrade", _BEFORE)
    assert down.returncode == 0, down.stderr
    assert await _version(url) == [(_BEFORE,)]
    assert await _not_null(url)
    remaining = [row for row in before if row[0] != not_run]
    assert await _read(url, _ROWS) == remaining and len(remaining) == 2

    up = _alembic(url, "upgrade", _AFTER)
    assert up.returncode == 0, up.stderr
    assert not await _not_null(url)
    assert await _read(url, _ROWS) == remaining, "the round trip changed the audit rows"
