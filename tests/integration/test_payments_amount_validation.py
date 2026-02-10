import base64
import uuid

import pytest
from httpx import AsyncClient
from nacl.signing import SigningKey
from sqlalchemy import select

from app.db.models.equivalent import Equivalent
from tests.integration.test_scenarios import (
    register_and_login,
    _sign_payment_request,
    _sign_trustline_create_request,
)


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
    bob_signing_key = SigningKey(base64.b64decode(bob["priv"]))
    resp = await client.post(
        "/api/v1/trustlines",
        json={
            "to": alice["pid"],
            "equivalent": "USD",
            "limit": "100.00",
            "signature": _sign_trustline_create_request(
                signing_key=bob_signing_key,
                to_pid=alice["pid"],
                equivalent="USD",
                limit="100.00",
            ),
        },
        headers=bob["headers"],
    )
    assert resp.status_code == 201

    signing_key = SigningKey(base64.b64decode(alice["priv"]))
    tx_id = str(uuid.uuid4())

    cases = [
        ("not-a-decimal", "Invalid amount format"),
        ("NaN", "Invalid amount format"),
        ("Infinity", "Invalid amount format"),
        ("-Infinity", "Invalid amount format"),
        ("1e3", "Invalid amount format"),
        ("1E-3", "Invalid amount format"),
        ("0." + ("0" * 30), "Invalid amount format"),
        (" 1", "Invalid amount format"),
        ("+1", "Invalid amount format"),
        ("-1", "Amount must be positive"),
    ]

    for amount, expected_message in cases:
        tx_id = str(uuid.uuid4())
        resp = await client.post(
            "/api/v1/payments",
            json={
                "tx_id": tx_id,
                "to": bob["pid"],
                "equivalent": "USD",
                "amount": amount,
                "signature": _sign_payment_request(
                    signing_key=signing_key,
                    tx_id=tx_id,
                    from_pid=alice["pid"],
                    to_pid=bob["pid"],
                    equivalent="USD",
                    amount=amount,
                ),
            },
            headers=alice["headers"],
        )

        assert resp.status_code == 400, (amount, resp.text)
        body = resp.json()
        assert "error" in body
        assert body["error"]["message"] == expected_message
