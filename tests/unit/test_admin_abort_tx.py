from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event, update

from app.config import settings
from app.db.models.audit_log import AuditLog
from app.db.models.participant import Participant
from app.db.models.transaction import Transaction
from app.utils.metrics import PAYMENT_EVENTS_TOTAL


def _abort_metric_value(result: str) -> float:
    return PAYMENT_EVENTS_TOTAL.labels(event='abort', result=result)._value.get()


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
    success_before = _abort_metric_value('success')
    already_aborted_before = _abort_metric_value('already_aborted')

    reason = 'manual abort in test'
    r = await client.post(
        '/api/v1/admin/transactions/TX_ABORT_ME/abort',
        headers=headers,
        json={'reason': reason},
    )
    assert r.status_code == 200
    payload = r.json()
    assert payload == {'tx_id': 'TX_ABORT_ME', 'status': 'aborted'}
    assert _abort_metric_value('success') - success_before == 1
    assert _abort_metric_value('already_aborted') == already_aborted_before

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


@pytest.mark.asyncio
async def test_admin_abort_tx_repeats_aborted_transaction_idempotently(
    client,
    db_session,
):
    alice = Participant(pid='aborted-alice', display_name='Alice', public_key='B' * 64, type='person', status='active')
    db_session.add(alice)
    await db_session.flush()

    tx = Transaction(
        tx_id='TX_ALREADY_ABORTED',
        type='PAYMENT',
        initiator_id=alice.id,
        payload={'from': 'alice', 'to': 'bob', 'amount': '1.00', 'equivalent': 'UAH', 'routes': []},
        state='ABORTED',
    )
    db_session.add(tx)
    await db_session.commit()

    success_before = _abort_metric_value('success')
    already_aborted_before = _abort_metric_value('already_aborted')
    response = await client.post(
        '/api/v1/admin/transactions/TX_ALREADY_ABORTED/abort',
        headers={'X-Admin-Token': settings.ADMIN_TOKEN},
        json={'reason': 'repeat abort'},
    )

    assert response.status_code == 200
    assert response.json() == {'tx_id': 'TX_ALREADY_ABORTED', 'status': 'aborted'}
    assert _abort_metric_value('success') == success_before
    assert _abort_metric_value('already_aborted') - already_aborted_before == 1
    await db_session.refresh(tx)
    assert tx.state == 'ABORTED'
    assert tx.error == {
        'code': 'E010',
        'message': 'repeat abort',
        'details': {},
    }


@pytest.mark.asyncio
async def test_admin_abort_tx_uses_lock_protected_already_aborted_metric(
    client,
    db_session,
    monkeypatch,
):
    alice = Participant(pid='abort-race-alice', display_name='Alice', public_key='R' * 64, type='person', status='active')
    db_session.add(alice)
    await db_session.flush()

    tx = Transaction(
        tx_id='TX_ABORT_RACE',
        type='PAYMENT',
        initiator_id=alice.id,
        payload={'from': 'alice', 'to': 'bob', 'amount': '1.00', 'equivalent': 'UAH', 'routes': []},
        state='WAITING',
    )
    db_session.add(tx)
    await db_session.commit()

    pre_lock_states = []

    async def _concurrent_abort_wins(engine, tx_id):
        pre_lock_states.append(tx.state)
        await engine.session.execute(
            update(Transaction)
            .where(Transaction.tx_id == tx_id)
            .values(state='ABORTED')
        )
        await engine.session.flush()

    monkeypatch.setattr(
        'app.core.payments.engine.PaymentEngine._acquire_tx_advisory_lock',
        _concurrent_abort_wins,
    )
    success_before = _abort_metric_value('success')
    already_aborted_before = _abort_metric_value('already_aborted')

    response = await client.post(
        '/api/v1/admin/transactions/TX_ABORT_RACE/abort',
        headers={'X-Admin-Token': settings.ADMIN_TOKEN},
        json={'reason': 'race loser observes abort'},
    )

    assert pre_lock_states == ['WAITING']
    assert response.status_code == 200
    assert response.json() == {'tx_id': 'TX_ABORT_RACE', 'status': 'aborted'}
    assert _abort_metric_value('success') == success_before
    assert _abort_metric_value('already_aborted') - already_aborted_before == 1
    await db_session.refresh(tx)
    assert tx.state == 'ABORTED'


@pytest.mark.asyncio
async def test_admin_abort_tx_bounds_staged_owner_wait_and_rolls_back(
    client,
    db_session,
    monkeypatch,
):
    alice = Participant(
        pid='abort-timeout-alice',
        display_name='Alice',
        public_key='T' * 64,
        type='person',
        status='active',
    )
    db_session.add(alice)
    await db_session.flush()
    tx = Transaction(
        tx_id='TX_ABORT_OWNER_TIMEOUT',
        type='PAYMENT',
        initiator_id=alice.id,
        payload={'from': 'alice', 'to': 'bob', 'amount': '1.00', 'equivalent': 'UAH', 'routes': []},
        state='WAITING',
    )
    db_session.add(tx)
    await db_session.commit()

    abort_cancelled = asyncio.Event()

    async def _block_abort(*_args, **_kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            abort_cancelled.set()

    monkeypatch.setattr(
        'app.api.v1.admin.PaymentEngine.abort',
        _block_abort,
    )
    monkeypatch.setattr(settings, 'PAYMENT_TOTAL_TIMEOUT_SECONDS', 0.02)
    monkeypatch.setattr(settings, 'COMMIT_TIMEOUT_SECONDS', 0.01)

    response = await client.post(
        '/api/v1/admin/transactions/TX_ABORT_OWNER_TIMEOUT/abort',
        headers={'X-Admin-Token': settings.ADMIN_TOKEN},
        json={'reason': 'bounded owner wait'},
    )

    assert response.status_code == 504
    assert response.json()['error']['code'] == 'E007'
    assert abort_cancelled.is_set()
    await db_session.refresh(tx)
    assert tx.state == 'WAITING'
    assert tx.error is None
    row = (
        await db_session.execute(
            AuditLog.__table__.select().where(
                AuditLog.action == 'admin.transactions.abort',
                AuditLog.object_id == 'TX_ABORT_OWNER_TIMEOUT',
            )
        )
    ).first()
    assert row is None


@pytest.mark.asyncio
async def test_admin_abort_tx_rejects_committed_transaction_without_audit(
    client,
    db_session,
):
    alice = Participant(pid='committed-alice', display_name='Alice', public_key='C' * 64, type='person', status='active')
    db_session.add(alice)
    await db_session.flush()

    tx = Transaction(
        tx_id='TX_ALREADY_COMMITTED',
        type='PAYMENT',
        initiator_id=alice.id,
        payload={'from': 'alice', 'to': 'bob', 'amount': '1.00', 'equivalent': 'UAH', 'routes': []},
        state='COMMITTED',
    )
    db_session.add(tx)
    await db_session.commit()

    response = await client.post(
        '/api/v1/admin/transactions/TX_ALREADY_COMMITTED/abort',
        headers={'X-Admin-Token': settings.ADMIN_TOKEN},
        json={'reason': 'cannot abort committed'},
    )

    assert response.status_code == 409
    await db_session.refresh(tx)
    assert tx.state == 'COMMITTED'
    row = (
        await db_session.execute(
            AuditLog.__table__.select().where(
                AuditLog.action == 'admin.transactions.abort',
                AuditLog.object_id == 'TX_ALREADY_COMMITTED',
            )
        )
    ).first()
    assert row is None


@pytest.mark.asyncio
async def test_admin_abort_tx_rolls_back_when_audit_flush_fails(
    client,
    db_session,
):
    alice = Participant(pid='abort-audit-alice', display_name='Alice', public_key='D' * 64, type='person', status='active')
    db_session.add(alice)
    await db_session.flush()

    tx = Transaction(
        tx_id='TX_ABORT_AUDIT_FAILURE',
        type='PAYMENT',
        initiator_id=alice.id,
        payload={'from': 'alice', 'to': 'bob', 'amount': '1.00', 'equivalent': 'UAH', 'routes': []},
        state='WAITING',
    )
    db_session.add(tx)
    await db_session.commit()

    def fail_when_audit_is_flushed(session, _flush_context, _instances):
        if any(isinstance(item, AuditLog) for item in session.new):
            raise RuntimeError('required abort audit flush failed')

    event.listen(db_session.sync_session, 'before_flush', fail_when_audit_is_flushed)
    try:
        with pytest.raises(RuntimeError, match='required abort audit flush failed'):
            await client.post(
                '/api/v1/admin/transactions/TX_ABORT_AUDIT_FAILURE/abort',
                headers={'X-Admin-Token': settings.ADMIN_TOKEN},
                json={'reason': 'must rollback abort'},
            )
    finally:
        event.remove(db_session.sync_session, 'before_flush', fail_when_audit_is_flushed)

    await db_session.refresh(tx)
    assert tx.state == 'WAITING'
    assert tx.error is None
    row = (
        await db_session.execute(
            AuditLog.__table__.select().where(
                AuditLog.action == 'admin.transactions.abort',
                AuditLog.object_id == 'TX_ABORT_AUDIT_FAILURE',
            )
        )
    ).first()
    assert row is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('initial_state', 'tx_id'),
    [
        ('WAITING', 'TX_ABORT_COMMIT_FAILURE'),
        ('ABORTED', 'TX_ABORTED_COMMIT_FAILURE'),
    ],
)
async def test_admin_abort_tx_rolls_back_when_outer_commit_fails(
    client,
    db_session,
    monkeypatch,
    initial_state,
    tx_id,
):
    alice = Participant(pid='abort-commit-alice', display_name='Alice', public_key='E' * 64, type='person', status='active')
    db_session.add(alice)
    await db_session.flush()

    tx = Transaction(
        tx_id=tx_id,
        type='PAYMENT',
        initiator_id=alice.id,
        payload={'from': 'alice', 'to': 'bob', 'amount': '1.00', 'equivalent': 'UAH', 'routes': []},
        state=initial_state,
    )
    db_session.add(tx)
    await db_session.commit()
    success_before = _abort_metric_value('success')
    already_aborted_before = _abort_metric_value('already_aborted')

    async def fail_commit():
        raise RuntimeError('required abort commit failed')

    with monkeypatch.context() as patch:
        patch.setattr(db_session, 'commit', fail_commit)
        with pytest.raises(RuntimeError, match='required abort commit failed'):
            await client.post(
                f'/api/v1/admin/transactions/{tx_id}/abort',
                headers={'X-Admin-Token': settings.ADMIN_TOKEN},
                json={'reason': 'must rollback outer commit'},
            )

    await db_session.refresh(tx)
    assert tx.state == initial_state
    assert tx.error is None
    assert _abort_metric_value('success') == success_before
    assert _abort_metric_value('already_aborted') == already_aborted_before
    row = (
        await db_session.execute(
            AuditLog.__table__.select().where(
                AuditLog.action == 'admin.transactions.abort',
                AuditLog.object_id == tx_id,
            )
        )
    ).first()
    assert row is None
