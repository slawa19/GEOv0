from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.config import settings
from app.db.models.audit_log import AuditLog
from app.db.models.participant import Participant
from app.db.models.transaction import Transaction


@pytest.mark.asyncio
async def test_admin_abort_tx_requires_admin_token(client, db_session):
    r = await client.post('/api/v1/admin/transactions/TX_1/abort', json={'reason': 'x'})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_admin_abort_tx_404(client, db_session):
    headers = {'X-Admin-Token': settings.ADMIN_TOKEN}
    r = await client.post('/api/v1/admin/transactions/NO_SUCH/abort', headers=headers, json={'reason': 'x'})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_admin_abort_tx_aborts_and_audits(client, db_session):
    alice = Participant(pid='alice', display_name='Alice', public_key='A' * 64, type='person', status='active')
    db_session.add(alice)
    await db_session.flush()

    now = datetime.now(timezone.utc)
    tx = Transaction(
        tx_id='TX_ABORT_ME',
        type='PAYMENT',
        initiator_id=alice.id,
        payload={'from': 'alice', 'to': 'bob', 'amount': '1.00', 'equivalent': 'UAH', 'routes': []},
        state='WAITING',
        created_at=now - timedelta(minutes=10),
        updated_at=now - timedelta(minutes=10),
    )
    db_session.add(tx)
    await db_session.commit()

    headers = {'X-Admin-Token': settings.ADMIN_TOKEN}

    reason = 'manual abort in test'
    r = await client.post(
        '/api/v1/admin/transactions/TX_ABORT_ME/abort',
        headers=headers,
        json={'reason': reason},
    )
    assert r.status_code == 200
    payload = r.json()
    assert payload == {'tx_id': 'TX_ABORT_ME', 'status': 'aborted'}

    # Transaction is aborted
    await db_session.refresh(tx)
    assert tx.state == 'ABORTED'
    assert isinstance(tx.error, dict)
    assert tx.error.get('message') == reason

    # Audit entry exists
    row = (
        await db_session.execute(
            AuditLog.__table__.select().where(
                AuditLog.action == 'admin.transactions.abort',
                AuditLog.object_type == 'transaction',
                AuditLog.object_id == 'TX_ABORT_ME',
            )
        )
    ).first()
    assert row is not None
