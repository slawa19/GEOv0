"""Admin abort as the COMPATIBILITY SURFACE of programme 019 stage 4 (owner decision Q2).

There is no active payment work to abort since stage 4: a payment is one transaction inserted
`COMMITTED`/`ABORTED`, and migration 030 refuses any other `PAYMENT` state. The endpoint stays, without
the payment engine and without any money effect: unknown `tx_id` - 404; `COMMITTED` - 409; `ABORTED` -
the former idempotent answer `aborted`, an audit row, the metric `abort/already_aborted`, and the stored
error kept as it is (the payer's replay of a stored refusal, T1523 cell 2, is not rewritten by an
operator); any other state - only a non-`PAYMENT` type can hold one - 409 and nothing changes.

Removed with the live-payment abort (manifest t1901, 5.2, rows of this file): aborting a `WAITING`
payment, the tx-advisory-lock race between two aborts, and the bounded wait on the engine's owner locks
- none has a subject once no payment can be live.
"""

from __future__ import annotations

import pytest
from sqlalchemy import event

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


async def _audit_rows(db_session, tx_id: str):
    return (
        await db_session.execute(
            AuditLog.__table__.select().where(
                AuditLog.action == 'admin.transactions.abort',
                AuditLog.object_type == 'transaction',
                AuditLog.object_id == tx_id,
            )
        )
    ).all()


@pytest.mark.asyncio
async def test_admin_abort_tx_refuses_a_non_terminal_transaction_of_another_type(client, db_session):
    """No type filter used to exist: a non-terminal CLEARING was aborted through the payment engine.

    After stage 4 the endpoint never terminalises another writer's transaction: 409, row unchanged, no
    audit, no metric. (A non-terminal PAYMENT cannot be seeded at all - migration 030.)
    """

    alice = Participant(pid='abort-clearing-alice', display_name='Alice', public_key='W' * 64, type='person', status='active')
    db_session.add(alice)
    await db_session.flush()
    tx = Transaction(
        tx_id='TX_CLEARING_NEW',
        type='CLEARING',
        initiator_id=alice.id,
        payload={'cycle': [], 'amount': '1.00', 'equivalent': 'UAH', 'edges': []},
        state='NEW',
    )
    db_session.add(tx)
    await db_session.commit()
    success_before = _abort_metric_value('success')
    already_aborted_before = _abort_metric_value('already_aborted')

    response = await client.post(
        '/api/v1/admin/transactions/TX_CLEARING_NEW/abort',
        headers={'X-Admin-Token': settings.ADMIN_TOKEN},
        json={'reason': 'not a payment'},
    )

    assert response.status_code == 409
    await db_session.refresh(tx)
    assert (tx.state, tx.error) == ('NEW', None)
    assert _abort_metric_value('success') == success_before
    assert _abort_metric_value('already_aborted') == already_aborted_before
    assert await _audit_rows(db_session, 'TX_CLEARING_NEW') == []


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
    # Since stage 4 the compatibility answer writes nothing to the row: the engine used to fill a
    # missing error with `{E010, <operator reason>}`; now the row stays as it was.
    assert tx.error is None
    rows = await _audit_rows(db_session, 'TX_ALREADY_ABORTED')
    assert len(rows) == 1, rows
    assert rows[0].reason == 'repeat abort'
    assert rows[0].before_state == rows[0].after_state == {'state': 'ABORTED', 'error': None}


@pytest.mark.asyncio
async def test_admin_abort_tx_keeps_the_stored_refusal_of_an_aborted_payment(client, db_session):
    """The payer's stored refusal (here the terminal timeout) survives an operator's abort byte for byte.

    A replay of the same `tx_id` answers the stored `ABORTED` with its error (T1523 cell 2,
    `test_p015_t1523_replay_after_a_hold_or_an_abort.py`); an operator's click must not rewrite it.
    """

    alice = Participant(pid='abort-stored-alice', display_name='Alice', public_key='S' * 64, type='person', status='active')
    db_session.add(alice)
    await db_session.flush()
    stored_error = {'code': 'E007', 'message': 'Payment timeout', 'details': {'phase': 'commit'}}
    tx = Transaction(
        tx_id='TX_STORED_REFUSAL',
        type='PAYMENT',
        initiator_id=alice.id,
        payload={'from': 'alice', 'to': 'bob', 'amount': '1.00', 'equivalent': 'UAH', 'routes': []},
        state='ABORTED',
        error=stored_error,
    )
    db_session.add(tx)
    await db_session.commit()

    response = await client.post(
        '/api/v1/admin/transactions/TX_STORED_REFUSAL/abort',
        headers={'X-Admin-Token': settings.ADMIN_TOKEN},
        json={'reason': 'operator abort after the fact'},
    )

    assert response.status_code == 200
    assert response.json() == {'tx_id': 'TX_STORED_REFUSAL', 'status': 'aborted'}
    await db_session.refresh(tx)
    assert (tx.state, tx.error) == ('ABORTED', stored_error)
    rows = await _audit_rows(db_session, 'TX_STORED_REFUSAL')
    assert len(rows) == 1 and rows[0].after_state == {'state': 'ABORTED', 'error': stored_error}, rows


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
        state='ABORTED',
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
    assert tx.state == 'ABORTED'
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
        # The WAITING case left with the live-payment abort (migration 030 refuses the seed).
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
