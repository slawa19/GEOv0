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
async def test_admin_incidents_lists_only_stuck_payments(client, db_session, monkeypatch):
    # Arrange
    alice = Participant(pid='alice', display_name='Alice', public_key='A' * 64, type='person', status='active')
    db_session.add(alice)
    await db_session.flush()

    now = datetime.now(timezone.utc)

    # Stuck PAYMENT (updated_at older than SLA)
    stuck = Transaction(
        tx_id='TX_STUCK_1',
        type='PAYMENT',
        initiator_id=alice.id,
        payload={'from': 'alice', 'to': 'bob', 'amount': '1.00', 'equivalent': 'UAH', 'routes': []},
        state='PREPARE_IN_PROGRESS',
        created_at=now - timedelta(hours=2),
        updated_at=now - timedelta(hours=2),
    )

    # Not stuck (recent)
    fresh = Transaction(
        tx_id='TX_OK_1',
        type='PAYMENT',
        initiator_id=alice.id,
        payload={'from': 'alice', 'to': 'bob', 'amount': '1.00', 'equivalent': 'USD', 'routes': []},
        state='PREPARE_IN_PROGRESS',
        created_at=now,
        updated_at=now,
    )

    # Not a PAYMENT
    non_payment = Transaction(
        tx_id='TX_NONPAY_1',
        type='CLEARING',
        initiator_id=alice.id,
        payload={'equivalent': 'UAH'},
        state='WAITING',
        created_at=now - timedelta(hours=3),
        updated_at=now - timedelta(hours=3),
    )

    db_session.add_all([stuck, fresh, non_payment])
    await db_session.commit()

    # Make SLA short so the test is deterministic
    monkeypatch.setattr(settings, 'PAYMENT_TX_STUCK_TIMEOUT_SECONDS', 60)

    headers = {'X-Admin-Token': settings.ADMIN_TOKEN}

    # Act
    r = await client.get('/api/v1/admin/incidents', headers=headers)
    assert r.status_code == 200
    payload = r.json()

    # Assert
    assert payload['page'] == 1
    assert payload['per_page'] == 20
    assert payload['total'] == 1
    assert len(payload['items']) == 1

    item = payload['items'][0]
    assert item['tx_id'] == 'TX_STUCK_1'
    assert item['state'] == 'PREPARE_IN_PROGRESS'
    assert item['initiator_pid'] == 'alice'
    assert item['equivalent'] == 'UAH'
    assert item['sla_seconds'] == 60
    assert item['age_seconds'] >= 60


@pytest.mark.asyncio
async def test_admin_incidents_pagination(client, db_session, monkeypatch):
    # Arrange
    alice = Participant(pid='alice', display_name='Alice', public_key='A' * 64, type='person', status='active')
    db_session.add(alice)
    await db_session.flush()

    now = datetime.now(timezone.utc)
    monkeypatch.setattr(settings, 'PAYMENT_TX_STUCK_TIMEOUT_SECONDS', 1)

    for i in range(3):
        db_session.add(
            Transaction(
                tx_id=f'TX_STUCK_{i}',
                type='PAYMENT',
                initiator_id=alice.id,
                payload={'equivalent': 'UAH'},
                state='WAITING',
                created_at=now - timedelta(minutes=10 + i),
                updated_at=now - timedelta(minutes=10 + i),
            )
        )
    await db_session.commit()

    headers = {'X-Admin-Token': settings.ADMIN_TOKEN}

    # Act: page size 1
    r = await client.get('/api/v1/admin/incidents?per_page=1&page=2', headers=headers)
    assert r.status_code == 200
    payload = r.json()

    # Assert
    assert payload['page'] == 2
    assert payload['per_page'] == 1
    assert payload['total'] == 3
    assert len(payload['items']) == 1
