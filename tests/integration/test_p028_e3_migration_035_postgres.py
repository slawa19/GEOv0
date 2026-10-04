"""Migration 035 (028 `T2832`, F-028-29): `frozen` lines become `active`, the CHECK admits `active | closed`.

Both `frozen` rows (one with a close request) become `active`, nothing else changes, the CHECK refuses `frozen`; the
downgrade restores the old CHECK only (irreversible by data). MUTATIONS that redden it: drop the UPDATE; keep `frozen`
in the new CHECK."""

from __future__ import annotations

import uuid

import pytest

from tests.integration.test_p019_migration_031_postgres import _alembic, _exec, _read, _version
from tests.migrated_schema import repository_head

_BEFORE = "034_simulator_run_seed_bigint"
_AFTER = "035_trust_line_status_without_frozen"
_LINES = ("SELECT to_participant_id, status, \"limit\", close_requested_at IS NOT NULL, policy::text FROM trust_lines "
          "WHERE equivalent_id = :eq ORDER BY \"limit\", status")


@pytest.mark.asyncio
async def test_035_turns_frozen_lines_active_and_refuses_frozen(committed_database) -> None:
    url = committed_database.url
    await committed_database.engine.dispose()
    assert repository_head() == _AFTER and await _version(url) == [(_AFTER,)]
    assert _alembic(url, "downgrade", _BEFORE).returncode == 0

    eq, a, *debtors = (uuid.uuid4() for _ in range(6))
    await _exec(url, "INSERT INTO equivalents (id, code, precision, is_active) VALUES (:id, 'M035', 2, true)", id=eq)
    for pid in (a, *debtors):
        await _exec(url, "INSERT INTO participants (id, pid, display_name, public_key, type, status, verification_level) "
                         "VALUES (:id, :pid, 'p', :pk, 'person', 'active', 0)", id=pid, pid=f"m035-{pid.hex[:8]}",
                    pk=f"pk-{pid.hex}")
    rows = [("frozen", 40, False), ("frozen", 0, True), ("active", 30, False), ("closed", 20, False)]
    for debtor, (status, limit, requested) in zip(debtors, rows):
        await _exec(url, "INSERT INTO trust_lines (id, from_participant_id, to_participant_id, equivalent_id, \"limit\", "
                         "status, policy, close_requested_at) VALUES (:id, :f, :t, :eq, :lim, :st, '{\"x\": 1}', "
                         "CASE WHEN :rq THEN now() END)",
                    id=uuid.uuid4(), f=a, t=debtor, eq=eq, lim=limit, st=status, rq=requested)
    before = await _read(url, _LINES, eq=eq)
    assert [r[1] for r in before] == ["frozen", "closed", "active", "frozen"], before

    up = _alembic(url, "upgrade", _AFTER)
    assert up.returncode == 0, up.stderr
    after = await _read(url, _LINES, eq=eq)
    assert after == [(*r[:1], "active" if r[1] == "frozen" else r[1], *r[2:]) for r in before], (before, after)
    with pytest.raises(Exception, match="chk_trust_line_status"):
        await _exec(url, "UPDATE trust_lines SET status = 'frozen' WHERE equivalent_id = :eq AND status = 'active'",
                    eq=eq)

    assert _alembic(url, "downgrade", _BEFORE).returncode == 0
    assert await _read(url, _LINES, eq=eq) == after  # irreversible by data: nothing becomes `frozen` again
    assert _alembic(url, "upgrade", _AFTER).returncode == 0
    assert await _version(url) == [(_AFTER,)]
