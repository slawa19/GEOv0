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
async def test_payments_tx_id_returns_same_result(client: AsyncClient, db_session):
    await _seed_equivalent(db_session, "USD")

    alice = await register_and_login(client, "Alice_Idempotency")
    bob = await register_and_login(client, "Bob_Idempotency")

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

    body = {
        "tx_id": tx_id,
        "to": bob["pid"],
        "equivalent": "USD",
        "amount": "10.00",
        "signature": _sign_payment_request(
            signing_key=signing_key,
            tx_id=tx_id,
            from_pid=alice["pid"],
            to_pid=bob["pid"],
            equivalent="USD",
            amount="10.00",
        ),
    }

    headers = dict(alice["headers"])

    resp1 = await client.post("/api/v1/payments", json=body, headers=headers)
    assert resp1.status_code == 200
    p1 = resp1.json()
    assert p1["tx_id"] == tx_id
    assert p1["status"] == "COMMITTED"

    resp2 = await client.post("/api/v1/payments", json=body, headers=headers)
    assert resp2.status_code == 200
    p2 = resp2.json()
    assert p2["tx_id"] == p1["tx_id"]
    assert p2["status"] == p1["status"]


@pytest.mark.asyncio
async def test_payments_tx_id_reuse_with_different_payload_conflicts(client: AsyncClient, db_session):
    await _seed_equivalent(db_session, "USD")

    alice = await register_and_login(client, "Alice_Idempotency_Conflict")
    bob = await register_and_login(client, "Bob_Idempotency_Conflict")

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

    headers = dict(alice["headers"])

    tx_id = str(uuid.uuid4())

    body1 = {
        "tx_id": tx_id,
        "to": bob["pid"],
        "equivalent": "USD",
        "amount": "10.00",
        "signature": _sign_payment_request(
            signing_key=signing_key,
            tx_id=tx_id,
            from_pid=alice["pid"],
            to_pid=bob["pid"],
            equivalent="USD",
            amount="10.00",
        ),
    }

    body2 = {
        "tx_id": tx_id,
        "to": bob["pid"],
        "equivalent": "USD",
        "amount": "11.00",
        "signature": _sign_payment_request(
            signing_key=signing_key,
            tx_id=tx_id,
            from_pid=alice["pid"],
            to_pid=bob["pid"],
            equivalent="USD",
            amount="11.00",
        ),
    }

    resp1 = await client.post("/api/v1/payments", json=body1, headers=headers)
    assert resp1.status_code == 200

    resp2 = await client.post("/api/v1/payments", json=body2, headers=headers)
    assert resp2.status_code == 409
    payload = resp2.json()
    assert payload["error"]["code"] == "E008"


@pytest.mark.asyncio
async def test_payments_missing_tx_id_is_bad_request(client: AsyncClient, db_session):
    await _seed_equivalent(db_session, "USD")

    alice = await register_and_login(client, "Alice_MissingTxId")
    bob = await register_and_login(client, "Bob_MissingTxId")

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

    # No tx_id (and no Idempotency-Key fallback): should fail at API boundary.
    resp = await client.post(
        "/api/v1/payments",
        json={
            "to": bob["pid"],
            "equivalent": "USD",
            "amount": "10.00",
            "signature": "x",
        },
        headers=alice["headers"],
    )
    assert resp.status_code == 400
    payload = resp.json()
    assert payload["error"]["code"] == "E009"
