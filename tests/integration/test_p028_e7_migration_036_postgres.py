"""Migration 036 (028 `T2871`, F-028-45): a CLEARING row's initiator becomes NULL, a PAYMENT keeps it and must have one.

Upgrade: the clearing's stored first debtor is cleared, the payment untouched, the CHECK refuses a PAYMENT without an
initiator and admits a CLEARING without one. Downgrade: the first debtor of `edges` comes back and NOT NULL with it; a
row with nothing to restore makes it refuse. MUTATIONS that redden it: drop the UPDATE; drop the CHECK; drop the
downgrade's restore."""

from __future__ import annotations

import json
import uuid

import pytest

from tests.integration.test_p019_migration_031_postgres import _alembic, _exec, _read, _version
from tests.migrated_schema import repository_head

_BEFORE = "035_trust_line_status_without_frozen"
_AFTER = "036_clearing_records_no_initiator"
_TX = "INSERT INTO transactions (id, tx_id, type, initiator_id, payload, state) VALUES (:id, :tx, :type, :ini, " \
      "CAST(:payload AS jsonb), 'COMMITTED')"


@pytest.mark.asyncio
async def test_036_clears_the_clearing_initiator_and_keeps_the_payment_one(committed_database) -> None:
    url = committed_database.url
    await committed_database.engine.dispose()
    assert await _version(url) == [(repository_head(),)]
    if repository_head() != _AFTER:
        assert _alembic(url, "downgrade", _AFTER).returncode == 0
    assert _alembic(url, "downgrade", _BEFORE).returncode == 0

    a, b = uuid.uuid4(), uuid.uuid4()
    pids = {a: f"m036a{a.hex[:8]}", b: f"m036b{b.hex[:8]}"}
    for pid_id, pid in pids.items():
        await _exec(url, "INSERT INTO participants (id, pid, display_name, public_key, type, status, verification_level) "
                         "VALUES (:id, :pid, 'p', :pk, 'person', 'active', 0)", id=pid_id, pid=pid, pk=f"pk-{pid_id.hex}")
    edges = json.dumps({"edges": [{"debtor": pids[b], "creditor": pids[a]}]})
    await _exec(url, _TX, id=uuid.uuid4(), tx="m036-pay", type="PAYMENT", ini=a, payload="{}")
    await _exec(url, _TX, id=uuid.uuid4(), tx="m036-clr", type="CLEARING", ini=b, payload=edges)
    rows = "SELECT tx_id, initiator_id FROM transactions WHERE tx_id LIKE 'm036-%' ORDER BY tx_id"
    assert await _read(url, rows) == [("m036-clr", b), ("m036-pay", a)]

    up = _alembic(url, "upgrade", _AFTER)
    assert up.returncode == 0, up.stderr
    assert await _read(url, rows) == [("m036-clr", None), ("m036-pay", a)]
    with pytest.raises(Exception, match="chk_transaction_payment_has_initiator"):
        await _exec(url, _TX, id=uuid.uuid4(), tx="m036-pay-null", type="PAYMENT", ini=None, payload="{}")

    assert _alembic(url, "downgrade", _BEFORE).returncode == 0  # the first debtor of the edges comes back
    assert await _read(url, rows) == [("m036-clr", b), ("m036-pay", a)]
    with pytest.raises(Exception, match="initiator_id"):
        await _exec(url, _TX, id=uuid.uuid4(), tx="m036-clr-null", type="CLEARING", ini=None, payload="{}")

    assert _alembic(url, "upgrade", _AFTER).returncode == 0
    await _exec(url, _TX, id=uuid.uuid4(), tx="m036-bare", type="CLEARING", ini=None, payload="{}")
    refused = _alembic(url, "downgrade", _BEFORE)  # nothing to restore for a clearing with no edges
    assert refused.returncode != 0 and "refusing to downgrade 036" in refused.stderr, refused.stderr
    assert await _version(url) == [(_AFTER,)]
