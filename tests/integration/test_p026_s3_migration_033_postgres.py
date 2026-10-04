"""Migration 033 (026 `T2603.1`): `trust_lines.close_requested_at` and its CHECK (a request means limit 0).

Upgrade from 032 with legacy rows - a closed line and an active line with limit 0 - leaves both with NULL (a zero
limit is not a request); the CHECK refuses a request on a positive limit. A live line holding a request refuses the
downgrade (the database stays at 033 and keeps the row); without one the downgrade drops the column and the
upgrade applies again. One head.

MUTATIONS that must redden this: drop the CHECK (the positive-limit request is stored); drop the pending count from
the downgrade (it goes through and the request is lost).
"""

from __future__ import annotations

import uuid

import pytest

from tests.integration.test_p019_migration_031_postgres import _alembic, _exec, _read, _version
from tests.migrated_schema import repository_head

_BEFORE = "032_audit_row_may_record_no_check"
_AFTER = "033_trust_line_close_requested_at"
_LINES = "SELECT id, status, \"limit\", close_requested_at IS NOT NULL FROM trust_lines WHERE equivalent_id = :eq ORDER BY status"


@pytest.mark.asyncio
async def test_033_adds_the_request_and_goes_back_only_without_a_pending_one(committed_database) -> None:
    url = committed_database.url
    await committed_database.engine.dispose()
    assert await _version(url) == [(repository_head(),)]
    if repository_head() != _AFTER:  # a later head (034, 028 `T2811`): step down to the revision under test
        stepped = _alembic(url, "downgrade", _AFTER)
        assert stepped.returncode == 0, stepped.stderr
    assert await _version(url) == [(_AFTER,)]
    down = _alembic(url, "downgrade", _BEFORE)
    assert down.returncode == 0, down.stderr

    eq, a, b, c = (uuid.uuid4() for _ in range(4))
    await _exec(url, "INSERT INTO equivalents (id, code, precision, is_active) VALUES (:id, 'M033', 2, true)", id=eq)
    for pid, name in ((a, "a"), (b, "b"), (c, "c")):
        await _exec(url, "INSERT INTO participants (id, pid, display_name, public_key, type, status, verification_level) "
                         "VALUES (:id, :pid, :n, :pk, 'person', 'active', 0)", id=pid, pid=f"m033-{name}-{pid.hex[:6]}",
                    n=name, pk=f"pk-{pid.hex}")
    for creditor, debtor, status, limit in ((a, b, "closed", 50), (a, c, "active", 0), (b, c, "active", 10)):
        await _exec(url, "INSERT INTO trust_lines (id, from_participant_id, to_participant_id, equivalent_id, "
                         "\"limit\", status, policy) VALUES (:id, :f, :t, :eq, :lim, :st, '{}')",
                    id=uuid.uuid4(), f=creditor, t=debtor, eq=eq, lim=limit, st=status)

    up = _alembic(url, "upgrade", _AFTER)
    assert up.returncode == 0, up.stderr
    legacy = await _read(url, _LINES, eq=eq)
    assert [row[3] for row in legacy] == [False, False, False], legacy  # no legacy row becomes a request

    with pytest.raises(Exception, match="chk_trust_line_close_request_zero_limit"):
        await _exec(url, "UPDATE trust_lines SET close_requested_at = now() WHERE equivalent_id = :eq "
                         "AND \"limit\" > 0 AND status = 'active'", eq=eq)
    await _exec(url, "UPDATE trust_lines SET close_requested_at = now() WHERE equivalent_id = :eq "
                     "AND \"limit\" = 0 AND status = 'active'", eq=eq)
    before = await _read(url, _LINES, eq=eq)
    refused = _alembic(url, "downgrade", _BEFORE)
    assert refused.returncode != 0 and "refusing to downgrade 033: 1 live trust line(s)" in refused.stderr, (
        refused.stderr)
    assert await _version(url) == [(_AFTER,)] and await _read(url, _LINES, eq=eq) == before

    await _exec(url, "UPDATE trust_lines SET status = 'closed' WHERE equivalent_id = :eq AND \"limit\" = 0", eq=eq)
    assert _alembic(url, "downgrade", _BEFORE).returncode == 0
    assert _alembic(url, "upgrade", _AFTER).returncode == 0
    assert await _version(url) == [(_AFTER,)]
