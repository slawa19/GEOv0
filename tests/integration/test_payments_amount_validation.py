import base64

import pytest
from httpx import AsyncClient
from nacl.signing import SigningKey
from sqlalchemy import select

from app.db.models.equivalent import Equivalent
from tests.integration.test_scenarios import register_and_login, _sign_payment_request


async def _seed_equivalent(db_session, code: str):
    result = await db_session.execute(select(Equivalent).where(Equivalent.code == code))
    eq = result.scalar_one_or_none()
    if not eq:
        eq = Equivalent(code=code, description=code, precision=2)
        db_session.add(eq)
        await db_session.commit()
        await db_session.refresh(eq)
    return eq


@pytest.mark.asyncio
async def test_create_payment_rejects_invalid_amount(client: AsyncClient, db_session):
    await _seed_equivalent(db_session, "USD")

    alice = await register_and_login(client, "Alice_InvalidAmount")
    bob = await register_and_login(client, "Bob_InvalidAmount")

    # Bob must trust Alice for Alice -> Bob payments.
    resp = await client.post(
        "/api/v1/trustlines",
        json={"to": alice["pid"], "equivalent": "USD", "limit": "100.00"},
        headers=bob["headers"],
    )
    assert resp.status_code == 201

    signing_key = SigningKey(base64.b64decode(alice["priv"]))

    resp = await client.post(
        "/api/v1/payments",
        json={
            "to": bob["pid"],
            "equivalent": "USD",
            "amount": "not-a-decimal",
            "signature": _sign_payment_request(
                signing_key=signing_key,
                from_pid=alice["pid"],
                to_pid=bob["pid"],
                equivalent="USD",
                amount="not-a-decimal",
            ),
        },
        headers=alice["headers"],
    )

    assert resp.status_code == 400
    body = resp.json()
    assert "error" in body
    assert body["error"]["message"] == "Invalid amount format"
