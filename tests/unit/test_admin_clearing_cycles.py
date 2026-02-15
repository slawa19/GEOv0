from __future__ import annotations

from decimal import Decimal

import pytest

from app.config import settings
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine


@pytest.mark.asyncio
async def test_admin_clearing_cycles_requires_admin_token(client, db_session):
    r = await client.get('/api/v1/admin/clearing/cycles')
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_admin_clearing_cycles_returns_equivalents_and_cycles(client, db_session):
    alice = Participant(pid='alice', display_name='Alice', public_key='A' * 64, type='person', status='active')
    bob = Participant(pid='bob', display_name='Bob', public_key='B' * 64, type='person', status='active')
    carol = Participant(pid='carol', display_name='Carol', public_key='C' * 64, type='person', status='active')
    db_session.add_all([alice, bob, carol])

    eur = Equivalent(code='EUR', symbol='€', description='Euro', precision=2, metadata_={}, is_active=True)
    uah = Equivalent(code='UAH', symbol='₴', description='Hryvnia', precision=2, metadata_={}, is_active=True)
    db_session.add_all([eur, uah])
    await db_session.flush()

    # Create a 3-edge debt cycle for UAH: alice -> carol -> bob -> alice
    # NOTE: cycle discovery filters by auto-clearing consent; provide controlling
    # trustlines so the cycle is executable.
    db_session.add_all(
        [
            # Controlling trustline for debt(alice->carol) is TL(carol->alice)
            TrustLine(
                from_participant_id=carol.id,
                to_participant_id=alice.id,
                equivalent_id=uah.id,
                limit=Decimal('100.00'),
                status='active',
            ),
            # Controlling trustline for debt(carol->bob) is TL(bob->carol)
            TrustLine(
                from_participant_id=bob.id,
                to_participant_id=carol.id,
                equivalent_id=uah.id,
                limit=Decimal('100.00'),
                status='active',
            ),
            # Controlling trustline for debt(bob->alice) is TL(alice->bob)
            TrustLine(
                from_participant_id=alice.id,
                to_participant_id=bob.id,
                equivalent_id=uah.id,
                limit=Decimal('100.00'),
                status='active',
            ),
        ]
    )
    db_session.add_all(
        [
            Debt(debtor_id=alice.id, creditor_id=carol.id, equivalent_id=uah.id, amount=Decimal('10.00')),
            Debt(debtor_id=carol.id, creditor_id=bob.id, equivalent_id=uah.id, amount=Decimal('10.00')),
            Debt(debtor_id=bob.id, creditor_id=alice.id, equivalent_id=uah.id, amount=Decimal('10.00')),
        ]
    )

    await db_session.commit()

    headers = {'X-Admin-Token': settings.ADMIN_TOKEN}

    r = await client.get('/api/v1/admin/clearing/cycles', headers=headers)
    assert r.status_code == 200
    payload = r.json()

    assert 'equivalents' in payload
    assert set(payload['equivalents'].keys()) == {'EUR', 'UAH'}

    # EUR has no debts => no cycles
    assert payload['equivalents']['EUR']['cycles'] == []

    uah_cycles = payload['equivalents']['UAH']['cycles']
    assert isinstance(uah_cycles, list)
    assert len(uah_cycles) >= 1

    # At least one cycle contains the UAH edges with the equivalent field populated
    cycle0 = uah_cycles[0]
    assert len(cycle0) == 3
    assert all(e['equivalent'] == 'UAH' for e in cycle0)

    # Optional participant filter should keep only cycles that touch that PID.
    r2 = await client.get('/api/v1/admin/clearing/cycles', headers=headers, params={'participant_pid': 'alice'})
    assert r2.status_code == 200
    p2 = r2.json()
    uah_cycles2 = p2['equivalents']['UAH']['cycles']
    assert len(uah_cycles2) >= 1
    assert all(
        any(edge['debtor'] == 'alice' or edge['creditor'] == 'alice' for edge in c) for c in uah_cycles2
    )
