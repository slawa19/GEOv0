from __future__ import annotations

import pytest

from app.config import settings
from app.db.models.participant import Participant


@pytest.mark.asyncio
async def test_admin_participants_requires_admin_token(client, db_session):
    r = await client.get('/api/v1/admin/participants')
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_admin_participants_list_pagination_and_filters(client, db_session):
    db_session.add_all(
        [
            Participant(pid='alice', display_name='Alice', public_key='A' * 64, type='person', status='active'),
            Participant(pid='bob', display_name='Bob', public_key='B' * 64, type='business', status='suspended'),
            Participant(pid='carol', display_name='Carol Co', public_key='C' * 64, type='person', status='deleted'),
        ]
    )
    await db_session.commit()

    headers = {'X-Admin-Token': settings.ADMIN_TOKEN}

    # List all (default pagination)
    r = await client.get('/api/v1/admin/participants', headers=headers)
    assert r.status_code == 200
    payload = r.json()
    assert payload['page'] == 1
    assert payload['per_page'] == 20
    assert payload['total'] == 3
    assert len(payload['items']) == 3

    # Filter by type
    r = await client.get('/api/v1/admin/participants?type=person', headers=headers)
    assert r.status_code == 200
    payload = r.json()
    assert payload['total'] == 2
    assert {it['pid'] for it in payload['items']} == {'alice', 'carol'}

    # Filter by status
    r = await client.get('/api/v1/admin/participants?status=frozen', headers=headers)
    assert r.status_code == 200
    payload = r.json()
    assert payload['total'] == 1
    assert payload['items'][0]['pid'] == 'bob'
    assert payload['items'][0]['status'] == 'suspended'

    # Deleted should be mapped to banned in responses
    r = await client.get('/api/v1/admin/participants?status=banned', headers=headers)
    assert r.status_code == 200
    payload = r.json()
    assert payload['total'] == 1
    assert payload['items'][0]['pid'] == 'carol'
    assert payload['items'][0]['status'] == 'deleted'

    # q search should match pid or display_name
    r = await client.get('/api/v1/admin/participants?q=carol', headers=headers)
    assert r.status_code == 200
    payload = r.json()
    assert payload['total'] == 1
    assert payload['items'][0]['pid'] == 'carol'

    r = await client.get('/api/v1/admin/participants?q=Carol%20Co', headers=headers)
    assert r.status_code == 200
    payload = r.json()
    assert payload['total'] == 1
    assert payload['items'][0]['pid'] == 'carol'

    # Pagination
    r = await client.get('/api/v1/admin/participants?per_page=1&page=2', headers=headers)
    assert r.status_code == 200
    payload = r.json()
    assert payload['page'] == 2
    assert payload['per_page'] == 1
    assert payload['total'] == 3
    assert len(payload['items']) == 1
