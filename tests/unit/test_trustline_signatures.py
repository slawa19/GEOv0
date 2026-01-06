import base64
import uuid
from decimal import Decimal

import pytest
from nacl.signing import SigningKey

from app.core.auth.canonical import canonical_json
from app.core.auth.crypto import generate_keypair
from app.core.trustlines.service import TrustLineService
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.schemas.trustline import TrustLineCreateRequest
from app.utils.exceptions import InvalidSignatureException


@pytest.mark.asyncio
async def test_trustline_create_rejects_invalid_signature(db_session):
    eq = Equivalent(code="USD", precision=2, is_active=True)
    db_session.add(eq)

    # Sender + receiver
    sender_pub, sender_priv = generate_keypair()
    receiver = Participant(
        id=uuid.uuid4(),
        pid="bob",
        display_name="Bob",
        public_key="pk_bob",
        type="person",
        status="active",
        profile={},
    )
    sender = Participant(
        id=uuid.uuid4(),
        pid="alice",
        display_name="Alice",
        public_key=sender_pub,
        type="person",
        status="active",
        profile={},
    )
    db_session.add_all([receiver, sender])
    await db_session.commit()

    # Sign with a different key to ensure verification fails
    other_pub, other_priv = generate_keypair()
    signing_key = SigningKey(base64.b64decode(other_priv))

    payload = {"to": "bob", "equivalent": "USD", "limit": str(Decimal("10"))}
    bad_sig = base64.b64encode(signing_key.sign(canonical_json(payload)).signature).decode("utf-8")

    req = TrustLineCreateRequest(to="bob", equivalent="USD", limit=Decimal("10"), signature=bad_sig)

    service = TrustLineService(db_session)
    with pytest.raises(InvalidSignatureException) as exc:
        await service.create(sender.id, req)

    assert exc.value.status_code == 400
    assert exc.value.code == "E005"
    assert exc.value.message == "Invalid signature"
