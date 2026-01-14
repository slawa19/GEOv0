from __future__ import annotations

from decimal import Decimal

import pytest

from app.config import settings
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine


@pytest.mark.asyncio
async def test_admin_graph_ego_requires_admin_token(client, db_session):
    r = await client.get('/api/v1/admin/graph/ego', params={'pid': 'alice'})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_admin_graph_ego_depth_1_and_2(client, db_session):
    alice = Participant(pid='alice', display_name='Alice', public_key='A' * 64, type='person', status='active')
    bob = Participant(pid='bob', display_name='Bob', public_key='B' * 64, type='person', status='active')
    carol = Participant(pid='carol', display_name='Carol', public_key='C' * 64, type='person', status='active')
    dave = Participant(pid='dave', display_name='Dave', public_key='D' * 64, type='person', status='active')
    db_session.add_all([alice, bob, carol, dave])

    eur = Equivalent(code='EUR', symbol='€', description='Euro', precision=2, metadata_={}, is_active=True)
    uah = Equivalent(code='UAH', symbol='₴', description='Hryvnia', precision=2, metadata_={}, is_active=True)
    db_session.add_all([eur, uah])
    await db_session.flush()

    # Trustlines: alice -> bob -> carol (UAH)
    tl_ab = TrustLine(
        from_participant_id=alice.id,
        to_participant_id=bob.id,
        equivalent_id=uah.id,
        limit=Decimal('100.00'),
        policy={'auto_clearing': True, 'can_be_intermediate': True},
        status='active',
    )
    tl_bc = TrustLine(
        from_participant_id=bob.id,
        to_participant_id=carol.id,
        equivalent_id=uah.id,
        limit=Decimal('50.00'),
        policy={'auto_clearing': True, 'can_be_intermediate': True},
        status='active',
    )
    db_session.add_all([tl_ab, tl_bc])
    await db_session.flush()

    # 2-hop neighbor via bob
    db_session.add(
        TrustLine(
            from_participant_id=bob.id,
            to_participant_id=dave.id,
            equivalent_id=uah.id,
            limit=Decimal('25.00'),
            policy={'auto_clearing': True, 'can_be_intermediate': True},
            status='active',
        )
    )

    # Extra equivalent edge (EUR) that should be excluded when filtering by equivalent/status.
    db_session.add(
        TrustLine(
            from_participant_id=alice.id,
            to_participant_id=carol.id,
            equivalent_id=eur.id,
            limit=Decimal('10.00'),
            policy={'auto_clearing': True, 'can_be_intermediate': True},
            status='closed',
        )
    )

    # Debts follow trustline direction: debtor=to, creditor=from
    db_session.add_all(
        [
            Debt(debtor_id=bob.id, creditor_id=alice.id, equivalent_id=uah.id, amount=Decimal('5.00')),
            Debt(debtor_id=carol.id, creditor_id=bob.id, equivalent_id=uah.id, amount=Decimal('2.00')),
            Debt(debtor_id=dave.id, creditor_id=bob.id, equivalent_id=uah.id, amount=Decimal('1.00')),
        ]
    )

    await db_session.commit()

    headers = {'X-Admin-Token': settings.ADMIN_TOKEN}

    r1 = await client.get('/api/v1/admin/graph/ego', headers=headers, params={'pid': 'alice', 'depth': 1})
    assert r1.status_code == 200
    p1 = r1.json()
    assert {p['pid'] for p in p1['participants']} == {'alice', 'bob', 'carol'}
    assert ('alice', 'bob') in {(t['from'], t['to']) for t in p1['trustlines']}
    assert all(d['debtor'] != 'dave' and d['creditor'] != 'dave' for d in p1['debts'])

    r2 = await client.get('/api/v1/admin/graph/ego', headers=headers, params={'pid': 'alice', 'depth': 2})
    assert r2.status_code == 200
    p2 = r2.json()
    assert {p['pid'] for p in p2['participants']} == {'alice', 'bob', 'carol', 'dave'}
    assert ('bob', 'dave') in {(t['from'], t['to']) for t in p2['trustlines']}
    assert any(d['debtor'] == 'dave' or d['creditor'] == 'dave' for d in p2['debts'])

    # Equivalent/status filters: only UAH + active should remain.
    rf = await client.get(
        '/api/v1/admin/graph/ego',
        headers=headers,
        params={'pid': 'alice', 'depth': 2, 'equivalent': 'UAH', 'status': ['active']},
    )
    assert rf.status_code == 200
    pf = rf.json()
    assert all(t['equivalent'] == 'UAH' for t in pf['trustlines'])
    assert all(t['status'] == 'active' for t in pf['trustlines'])
