import hashlib
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.simulator.post_tick_audit import audit_tick_balance
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.transaction import Transaction


def _sim_idempotency_key(
    *,
    run_id: str,
    tick_ms: int,
    sender_pid: str,
    receiver_pid: str,
    equivalent: str,
    amount: str,
    seq: int,
) -> str:
    material = f"{run_id}|{tick_ms}|{sender_pid}|{receiver_pid}|{equivalent}|{amount}|{seq}"
    return "sim:" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


@pytest.mark.asyncio
async def test_post_tick_audit_detects_drift(db_session: AsyncSession) -> None:
    nonce = hashlib.sha256(b"post_tick_audit").hexdigest()[:10]
    run_id = f"run-{nonce}"

    eq = Equivalent(
        code=("AUD" + nonce[:13]).upper(),
        symbol="AUD",
        description=None,
        precision=2,
        metadata_={},
        is_active=True,
    )
    sender = Participant(
        pid="p_sender_" + nonce,
        display_name="Sender",
        public_key="pk_sender_" + nonce,
        type="person",
        status="active",
        profile={},
    )
    receiver = Participant(
        pid="p_receiver_" + nonce,
        display_name="Receiver",
        public_key="pk_receiver_" + nonce,
        type="person",
        status="active",
        profile={},
    )
    db_session.add_all([eq, sender, receiver])
    await db_session.flush()

    # Planned tick intends a 50 payment sender -> receiver.
    tick_index = 7
    planned_action = {
        "seq": 1,
        "equivalent": eq.code,
        "sender_pid": sender.pid,
        "receiver_pid": receiver.pid,
        "amount": "50",
    }
    tx_id = _sim_idempotency_key(
        run_id=run_id,
        tick_ms=tick_index,
        sender_pid=sender.pid,
        receiver_pid=receiver.pid,
        equivalent=eq.code,
        amount="50",
        seq=1,
    )
    tx = Transaction(
        tx_id=tx_id,
        idempotency_key=tx_id,
        type="PAYMENT",
        initiator_id=sender.id,
        payload={
            "from": sender.pid,
            "to": receiver.pid,
            "equivalent": eq.code,
            "amount": "50",
        },
        state="COMMITTED",
        error=None,
    )
    db_session.add(tx)

    # Corrupt post-tick debt state: only 20 instead of 50.
    debt = Debt(
        debtor_id=sender.id,
        creditor_id=receiver.id,
        equivalent_id=eq.id,
        amount=Decimal("20"),
    )
    db_session.add(debt)
    await db_session.commit()

    payments_result = SimpleNamespace(planned=[planned_action], debt_snapshot={})

    res = await audit_tick_balance(
        session=db_session,
        equivalent_code=eq.code,
        tick_index=tick_index,
        payments_result=payments_result,
        clearing_volume_by_eq={eq.code: "100"},
        run_id=run_id,
        sim_idempotency_key=_sim_idempotency_key,
    )

    assert res.ok is False
    assert res.tick_index == tick_index
    assert res.total_drift == Decimal("30")
    assert res.tick_volume == Decimal("150")
    assert isinstance(res.drifts, list)
    assert {d.get("participant_id") for d in res.drifts} == {sender.pid, receiver.pid}
