import base64
import uuid
import asyncio
from nacl.signing import SigningKey

import pytest
from sqlalchemy import select

from app.config import settings
from app.core.auth.canonical import canonical_json
from app.core.auth.crypto import generate_keypair, get_pid_from_public_key
from app.core.payments.service import PaymentService
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.transaction import Transaction
from app.schemas.payment import PaymentCreateRequest
from app.utils.exceptions import GeoException


@pytest.mark.asyncio
async def test_payment_prepare_timeout_aborts_transaction(db_session, monkeypatch):
    # Tighten timeouts for the test
    monkeypatch.setattr(settings, "PREPARE_TIMEOUT_SECONDS", 0.01, raising=False)
    monkeypatch.setattr(settings, "COMMIT_TIMEOUT_SECONDS", 1, raising=False)
    monkeypatch.setattr(settings, "PAYMENT_TOTAL_TIMEOUT_SECONDS", 1, raising=False)
    monkeypatch.setattr(settings, "ROUTING_PATH_FINDING_TIMEOUT_MS", 500, raising=False)

    # Setup participants + equivalent
    sender_pub, sender_priv = generate_keypair()
    receiver_pub, receiver_priv = generate_keypair()

    sender_pid = get_pid_from_public_key(sender_pub)
    receiver_pid = get_pid_from_public_key(receiver_pub)

    sender = Participant(
        id=uuid.uuid4(),
        pid=sender_pid,
        display_name="Sender",
        public_key=sender_pub,
        type="person",
        status="active",
        profile={},
    )
    receiver = Participant(
        id=uuid.uuid4(),
        pid=receiver_pid,
        display_name="Receiver",
        public_key=receiver_pub,
        type="person",
        status="active",
        profile={},
    )
    eq = Equivalent(code="USD", precision=2, is_active=True)

    db_session.add_all([sender, receiver, eq])
    await db_session.commit()

    # Build a valid signed request
    payload = {"to": receiver_pid, "equivalent": "USD", "amount": "1"}
    msg = canonical_json(payload)
    signing_key = SigningKey(base64.b64decode(sender_priv))
    sig_b64 = base64.b64encode(signing_key.sign(msg).signature).decode("utf-8")

    req = PaymentCreateRequest(to=receiver_pid, equivalent="USD", amount="1", signature=sig_b64)

    service = PaymentService(db_session)

    async def _build_graph(_code: str) -> None:
        return None

    def _find_flow_routes(_from: str, _to: str, _amount, *, max_hops: int, max_paths: int):
        return [([sender_pid, receiver_pid], _amount)]

    async def _slow_prepare(*_args, **_kwargs):
        # Block long enough to exceed PREPARE_TIMEOUT_SECONDS
        await asyncio.sleep(0.05)

    monkeypatch.setattr(service.router, "build_graph", _build_graph)
    monkeypatch.setattr(service.router, "find_flow_routes", _find_flow_routes)
    monkeypatch.setattr(service.engine, "prepare", _slow_prepare)

    with pytest.raises(GeoException) as exc:
        await service.create_payment(sender.id, req)

    assert exc.value.message == "Payment timed out"
    assert exc.value.status_code == 504

    tx_obj = (
        await db_session.execute(select(Transaction).where(Transaction.initiator_id == sender.id))
    ).scalar_one()
    assert tx_obj.state == "ABORTED"
    assert (tx_obj.error or {}).get("message") == "Payment timeout"
