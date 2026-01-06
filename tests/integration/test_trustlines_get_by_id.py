from decimal import Decimal
import base64

import pytest
from nacl.signing import SigningKey

from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.core.auth.canonical import canonical_json


@pytest.mark.asyncio
async def test_get_trustline_by_id(client, db_session, auth_user):
    usd = Equivalent(code="USD", description="US Dollar", precision=2)
    db_session.add(usd)

    bob = Participant(pid="bob", type="person", display_name="Bob", public_key="pk_bob", status="active")
    db_session.add(bob)

    await db_session.commit()

    signing_key = SigningKey(base64.b64decode(auth_user["private_key"]))
    payload = {
        "to": "bob",
        "equivalent": "USD",
        "limit": str(Decimal("100")),
        "policy": {},
    }
    signature_b64 = base64.b64encode(signing_key.sign(canonical_json(payload)).signature).decode("utf-8")

    create_resp = await client.post(
        "/api/v1/trustlines",
        headers=auth_user["headers"],
        json={
            "to": "bob",
            "equivalent": "USD",
            "limit": str(Decimal("100")),
            "policy": {},
            "signature": signature_b64,
        },
    )
    assert create_resp.status_code == 201
    trustline_id = create_resp.json()["id"]

    get_resp = await client.get(f"/api/v1/trustlines/{trustline_id}", headers=auth_user["headers"])
    assert get_resp.status_code == 200
    body = get_resp.json()

    assert body["id"] == trustline_id
    assert body["to"] == "bob"
    assert body["equivalent"] == "USD"
