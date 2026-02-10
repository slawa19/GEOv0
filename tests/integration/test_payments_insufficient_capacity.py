import base64
import uuid

import pytest
from httpx import AsyncClient
from nacl.signing import SigningKey

from tests.integration.test_scenarios import (
    register_and_login,
    _sign_payment_request,
    _sign_trustline_create_request,
)


@pytest.mark.asyncio
async def test_payment_insufficient_capacity_returns_400_e002(client: AsyncClient, db_session):
    # Seed USD
    from app.db.models.equivalent import Equivalent
    from sqlalchemy import select

    result = await db_session.execute(select(Equivalent).where(Equivalent.code == "USD"))
    usd = result.scalar_one_or_none()
    if not usd:
        usd = Equivalent(code="USD", description="US Dollar", precision=2)
        db_session.add(usd)
        await db_session.commit()
        await db_session.refresh(usd)

    alice = await register_and_login(client, "Alice_IC")
    bob = await register_and_login(client, "Bob_IC")

    # For payment Alice -> Bob to have capacity, Bob must trust Alice.
    bob_signing_key = SigningKey(base64.b64decode(bob["priv"]))
    resp = await client.post(
        "/api/v1/trustlines",
        json={
            "to": alice["pid"],
            "equivalent": "USD",
            "limit": "5.00",
            "signature": _sign_trustline_create_request(
                signing_key=bob_signing_key,
                to_pid=alice["pid"],
                equivalent="USD",
                limit="5.00",
            ),
        },
        headers=bob["headers"],
    )
    assert resp.status_code == 201, resp.text

    alice_signing_key = SigningKey(base64.b64decode(alice["priv"]))
    pay_amount = "10.00"
    tx_id = str(uuid.uuid4())
    resp = await client.post(
        "/api/v1/payments",
        json={
            "tx_id": tx_id,
            "to": bob["pid"],
            "equivalent": "USD",
            "amount": pay_amount,
            "signature": _sign_payment_request(
                signing_key=alice_signing_key,
                tx_id=tx_id,
                from_pid=alice["pid"],
                to_pid=bob["pid"],
                equivalent="USD",
                amount=pay_amount,
            ),
        },
        headers=alice["headers"],
    )

    assert resp.status_code == 400, resp.text
    assert resp.json()["error"]["code"] == "E002"
