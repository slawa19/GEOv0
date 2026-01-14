from __future__ import annotations

import pytest

from app.config import settings
from app.db.models.audit_log import AuditLog


@pytest.mark.asyncio
async def test_admin_audit_log_requires_admin_token(client, db_session):
    r = await client.get('/api/v1/admin/audit-log')
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_admin_audit_log_pagination_and_q_search(client, db_session):
    db_session.add_all(
        [
            AuditLog(
                actor_id=None,
                actor_role='admin',
                action='admin.participants.freeze',
                object_type='participant',
                object_id='alice',
                reason='test freeze',
                before_state={'status': 'active'},
                after_state={'status': 'suspended'},
                request_id='req-1',
                ip_address='127.0.0.1',
                user_agent='pytest',
            ),
            AuditLog(
                actor_id=None,
                actor_role='admin',
                action='admin.participants.unfreeze',
                object_type='participant',
                object_id='alice',
                reason='test unfreeze',
                before_state={'status': 'suspended'},
                after_state={'status': 'active'},
                request_id='req-2',
                ip_address='127.0.0.1',
                user_agent='pytest',
            ),
            AuditLog(
                actor_id=None,
                actor_role='admin',
                action='admin.equivalents.create',
                object_type='equivalent',
                object_id='UAH',
                reason='create eq',
                before_state=None,
                after_state={'code': 'UAH'},
                request_id='req-3',
                ip_address='127.0.0.1',
                user_agent='pytest',
            ),
        ]
    )
    await db_session.commit()

    headers = {'X-Admin-Token': settings.ADMIN_TOKEN}

    # List all
    r = await client.get('/api/v1/admin/audit-log', headers=headers)
    assert r.status_code == 200
    payload = r.json()
    assert payload['page'] == 1
    assert payload['per_page'] == 50
    assert payload['total'] == 3
    assert len(payload['items']) == 3

    # Pagination
    r = await client.get('/api/v1/admin/audit-log?per_page=1&page=2', headers=headers)
    assert r.status_code == 200
    payload = r.json()
    assert payload['page'] == 2
    assert payload['per_page'] == 1
    assert payload['total'] == 3
    assert len(payload['items']) == 1

    # q search should work server-side
    r = await client.get('/api/v1/admin/audit-log?q=equivalents.create', headers=headers)
    assert r.status_code == 200
    payload = r.json()
    assert payload['total'] == 1
    assert payload['items'][0]['action'] == 'admin.equivalents.create'

    r = await client.get('/api/v1/admin/audit-log?q=UAH', headers=headers)
    assert r.status_code == 200
    payload = r.json()
    assert payload['total'] == 1
    assert payload['items'][0]['object_id'] == 'UAH'

    # Exact filters still apply
    r = await client.get('/api/v1/admin/audit-log?action=admin.participants.freeze', headers=headers)
    assert r.status_code == 200
    payload = r.json()
    assert payload['total'] == 1
    assert payload['items'][0]['action'] == 'admin.participants.freeze'
