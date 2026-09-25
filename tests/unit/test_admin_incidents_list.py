"""`GET /admin/incidents` - the "stuck payments" list, a compatibility surface since programme 019 stage 4.

A stuck payment was a durable `NEW`/`PREPARED` row between the payment's commits. Since stage 4 a
payment is one transaction inserted `COMMITTED`/`ABORTED`, and migration 030 refuses any other
`PAYMENT` state, so the list is empty by construction and the endpoint answers it without reading
(owner decision Q2; its fate is decided with the incidents screen after 019, П4 / `T1911`). The item
content assertions of the old list (tx_id, state, age, SLA) left with the rows they described (manifest
t1901, 5.2, rows of this file); the wire shape `AdminIncidentsListResponse` is unchanged.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.config import settings
from app.db.models.participant import Participant
from app.db.models.transaction import Transaction


@pytest.mark.asyncio
async def test_admin_incidents_requires_admin_token(client, db_session):
    r = await client.get('/api/v1/admin/incidents')
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_admin_incidents_is_empty_whatever_old_rows_exist(client, db_session, monkeypatch):
    """Old terminal payments and a non-terminal CLEARING, all past any SLA: none is a stuck payment."""

    alice = Participant(pid='alice', display_name='Alice', public_key='A' * 64, type='person', status='active')
    db_session.add(alice)
    await db_session.flush()
    old = datetime.now(timezone.utc) - timedelta(hours=3)
    db_session.add_all(
        [
            Transaction(
                tx_id=f'TX_OLD_{state}',
                type='PAYMENT',
                initiator_id=alice.id,
                payload={'from': 'alice', 'to': 'bob', 'amount': '1.00', 'equivalent': 'UAH', 'routes': []},
                state=state,
                created_at=old,
                updated_at=old,
            )
            for state in ('COMMITTED', 'ABORTED')
        ]
        + [
            Transaction(
                tx_id='TX_NONPAY_1',
                type='CLEARING',
                initiator_id=alice.id,
                payload={'equivalent': 'UAH'},
                state='WAITING',
                created_at=old,
                updated_at=old,
            )
        ]
    )
    await db_session.commit()
    monkeypatch.setattr(settings, 'PAYMENT_TX_STUCK_TIMEOUT_SECONDS', 60)

    r = await client.get('/api/v1/admin/incidents', headers={'X-Admin-Token': settings.ADMIN_TOKEN})

    assert r.status_code == 200
    assert r.json() == {'items': [], 'page': 1, 'per_page': 20, 'total': 0}


@pytest.mark.asyncio
async def test_admin_incidents_pagination_is_echoed_on_the_empty_list(client, db_session):
    r = await client.get(
        '/api/v1/admin/incidents?per_page=1&page=2', headers={'X-Admin-Token': settings.ADMIN_TOKEN}
    )

    assert r.status_code == 200
    assert r.json() == {'items': [], 'page': 2, 'per_page': 1, 'total': 0}
