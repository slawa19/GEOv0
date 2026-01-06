import asyncio
from datetime import datetime

import base64
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
async def test_list_payments_filters(client: AsyncClient, db_session):
    await _seed_equivalent(db_session, "USD")
    await _seed_equivalent(db_session, "EUR")

    alice = await register_and_login(client, "Alice_List")
    bob = await register_and_login(client, "Bob_List")

    alice_tl_signing_key = SigningKey(base64.b64decode(alice["priv"]))
    bob_tl_signing_key = SigningKey(base64.b64decode(bob["priv"]))

    # Allow Alice -> Bob payments (Bob trusts Alice)
    resp = await client.post(
        "/api/v1/trustlines",
        json={
            "to": alice["pid"],
            "equivalent": "USD",
            "limit": "100.00",
            "signature": _sign_trustline_create_request(
                signing_key=bob_tl_signing_key,
                to_pid=alice["pid"],
                equivalent="USD",
                limit="100.00",
            ),
        },
        headers=bob["headers"],
    )
    assert resp.status_code == 201

    resp = await client.post(
        "/api/v1/trustlines",
        json={
            "to": alice["pid"],
            "equivalent": "EUR",
            "limit": "100.00",
            "signature": _sign_trustline_create_request(
                signing_key=bob_tl_signing_key,
                to_pid=alice["pid"],
                equivalent="EUR",
                limit="100.00",
            ),
        },
        headers=bob["headers"],
    )
    assert resp.status_code == 201

    # Allow Bob -> Alice payments (Alice trusts Bob)
    resp = await client.post(
        "/api/v1/trustlines",
        json={
            "to": bob["pid"],
            "equivalent": "USD",
            "limit": "100.00",
            "signature": _sign_trustline_create_request(
                signing_key=alice_tl_signing_key,
                to_pid=bob["pid"],
                equivalent="USD",
                limit="100.00",
            ),
        },
        headers=alice["headers"],
    )
    assert resp.status_code == 201

    alice_signing_key = SigningKey(base64.b64decode(alice["priv"]))
    bob_signing_key = SigningKey(base64.b64decode(bob["priv"]))

    # Payment 1: Alice -> Bob (USD)
    resp = await client.post(
        "/api/v1/payments",
        json={
            "to": bob["pid"],
            "equivalent": "USD",
            "amount": "10.00",
            "signature": _sign_payment_request(
                signing_key=alice_signing_key,
                from_pid=alice["pid"],
                to_pid=bob["pid"],
                equivalent="USD",
                amount="10.00",
            ),
        },
        headers=alice["headers"],
    )
    assert resp.status_code == 200
    p1 = resp.json()
    assert p1["status"] == "COMMITTED"

    await asyncio.sleep(1.1)

    # Payment 2: Alice -> Bob (EUR)
    resp = await client.post(
        "/api/v1/payments",
        json={
            "to": bob["pid"],
            "equivalent": "EUR",
            "amount": "2.00",
            "signature": _sign_payment_request(
                signing_key=alice_signing_key,
                from_pid=alice["pid"],
                to_pid=bob["pid"],
                equivalent="EUR",
                amount="2.00",
            ),
        },
        headers=alice["headers"],
    )
    assert resp.status_code == 200
    p2 = resp.json()
    assert p2["status"] == "COMMITTED"

    await asyncio.sleep(1.1)

    # Payment 3: Bob -> Alice (USD)
    resp = await client.post(
        "/api/v1/payments",
        json={
            "to": alice["pid"],
            "equivalent": "USD",
            "amount": "5.00",
            "signature": _sign_payment_request(
                signing_key=bob_signing_key,
                from_pid=bob["pid"],
                to_pid=alice["pid"],
                equivalent="USD",
                amount="5.00",
            ),
        },
        headers=bob["headers"],
    )
    assert resp.status_code == 200
    p3 = resp.json()
    assert p3["status"] == "COMMITTED"

    # Alice: sent USD only
    resp = await client.get(
        "/api/v1/payments",
        headers=alice["headers"],
        params={"direction": "sent", "equivalent": "USD"},
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert {i["tx_id"] for i in items} == {p1["tx_id"]}

    # Alice: received USD only
    resp = await client.get(
        "/api/v1/payments",
        headers=alice["headers"],
        params={"direction": "received", "equivalent": "USD"},
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert {i["tx_id"] for i in items} == {p3["tx_id"]}

    # Bob: received EUR only
    resp = await client.get(
        "/api/v1/payments",
        headers=bob["headers"],
        params={"direction": "received", "equivalent": "EUR"},
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert {i["tx_id"] for i in items} == {p2["tx_id"]}

    # Pagination for Alice sent (2 items: p2 newest, then p1)
    resp = await client.get(
        "/api/v1/payments",
        headers=alice["headers"],
        params={"direction": "sent", "per_page": 1, "page": 1},
    )
    assert resp.status_code == 200
    page1 = resp.json()["items"]
    assert len(page1) == 1

    resp = await client.get(
        "/api/v1/payments",
        headers=alice["headers"],
        params={"direction": "sent", "per_page": 1, "page": 2},
    )
    assert resp.status_code == 200
    page2 = resp.json()["items"]
    assert len(page2) == 1

    assert {page1[0]["tx_id"], page2[0]["tx_id"]} == {p1["tx_id"], p2["tx_id"]}

    # from_date filter: should exclude p1 and include p2 for Alice sent
    from_date = datetime.fromisoformat(p2["created_at"]).isoformat()
    resp = await client.get(
        "/api/v1/payments",
        headers=alice["headers"],
        params={"direction": "sent", "from_date": from_date},
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert {i["tx_id"] for i in items} == {p2["tx_id"]}
