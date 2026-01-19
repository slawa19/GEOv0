from __future__ import annotations

from decimal import Decimal

import pytest

from app.config import settings
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine


@pytest.mark.asyncio
async def test_admin_graph_snapshot_requires_admin_token(client, db_session):
    r = await client.get('/api/v1/admin/graph/snapshot')
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_admin_graph_snapshot_hydrates_trustlines_and_debts(client, db_session):
    # Arrange
    alice = Participant(pid='alice', display_name='Alice', public_key='A' * 64, type='person', status='active')
    bob = Participant(pid='bob', display_name='Bob', public_key='B' * 64, type='person', status='active')
    db_session.add_all([alice, bob])

    uah = Equivalent(code='UAH', symbol='₴', description='Hryvnia', precision=2, metadata_={}, is_active=True)
    db_session.add(uah)
    await db_session.flush()

    tl = TrustLine(
        from_participant_id=alice.id,
        to_participant_id=bob.id,
        equivalent_id=uah.id,
        limit=Decimal('100.00'),
        policy={'auto_clearing': True, 'can_be_intermediate': True},
        status='active',
    )
    db_session.add(tl)
    await db_session.flush()

    # Debt direction: debtor=bob (to), creditor=alice (from)
    db_session.add(
        Debt(
            debtor_id=bob.id,
            creditor_id=alice.id,
            equivalent_id=uah.id,
            amount=Decimal('7.25'),
        )
    )

    await db_session.commit()

    headers = {'X-Admin-Token': settings.ADMIN_TOKEN}

    # Act
    r = await client.get('/api/v1/admin/graph/snapshot', headers=headers)
    assert r.status_code == 200
    payload = r.json()

    # Assert: participants
    pids = {p['pid'] for p in payload['participants']}
    assert pids == {'alice', 'bob'}

    # Assert: equivalents
    assert [e['code'] for e in payload['equivalents']] == ['UAH']

    # Assert: trustline direction and used/available
    assert len(payload['trustlines']) == 1
    t = payload['trustlines'][0]
    assert t['from'] == 'alice'
    assert t['to'] == 'bob'
    assert t['equivalent'] == 'UAH'
    assert t['status'] == 'active'
    assert t['used'] in ('7.25', '7.25000000')
    assert t['available'] in ('92.75', '92.75000000')

    # Assert: debts
    assert len(payload['debts']) == 1
    d = payload['debts'][0]
    assert d['equivalent'] == 'UAH'
    assert d['debtor'] == 'bob'
    assert d['creditor'] == 'alice'
    assert d['amount'] in ('7.25', '7.25000000')


@pytest.mark.asyncio
async def test_admin_graph_snapshot_equivalent_enables_net_viz(client, db_session):
    # Arrange
    alice = Participant(pid='alice', display_name='Alice', public_key='A' * 64, type='person', status='active')
    bob = Participant(pid='bob', display_name='Bob', public_key='B' * 64, type='person', status='active')
    db_session.add_all([alice, bob])

    uah = Equivalent(code='UAH', symbol='₴', description='Hryvnia', precision=2, metadata_={}, is_active=True)
    db_session.add(uah)
    await db_session.flush()

    db_session.add(
        TrustLine(
            from_participant_id=alice.id,
            to_participant_id=bob.id,
            equivalent_id=uah.id,
            limit=Decimal('100.00'),
            policy={'auto_clearing': True, 'can_be_intermediate': True},
            status='active',
        )
    )
    db_session.add(
        Debt(
            debtor_id=bob.id,
            creditor_id=alice.id,
            equivalent_id=uah.id,
            amount=Decimal('7.25'),
        )
    )
    await db_session.commit()

    headers = {'X-Admin-Token': settings.ADMIN_TOKEN}

    # Act
    r = await client.get('/api/v1/admin/graph/snapshot', headers=headers, params={'equivalent': 'UAH'})
    assert r.status_code == 200
    payload = r.json()

    # Assert: viz fields present and deterministic for this tiny case.
    participants_by_pid = {p['pid']: p for p in payload['participants']}
    assert set(participants_by_pid.keys()) == {'alice', 'bob'}

    # 7.25 with precision=2 => 725 atoms
    assert participants_by_pid['alice']['net_balance_atoms'] == '725'
    assert participants_by_pid['alice']['net_sign'] == 1
    assert participants_by_pid['alice']['viz_color_key'] == 'person'
    assert participants_by_pid['alice']['viz_size'] == {'w': 30, 'h': 30}

    assert participants_by_pid['bob']['net_balance_atoms'] == '-725'
    assert participants_by_pid['bob']['net_sign'] == -1
    assert participants_by_pid['bob']['viz_color_key'] == 'debt-0'
    assert participants_by_pid['bob']['viz_size'] == {'w': 30, 'h': 30}
