import base64

import pytest
from httpx import AsyncClient
from nacl.signing import SigningKey

from tests.integration.test_scenarios import register_and_login, _sign_payment_request


@pytest.mark.asyncio
async def test_payment_multipath_split_two_routes(client: AsyncClient, db_session):
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

    alice = await register_and_login(client, "Alice_MP")
    bob = await register_and_login(client, "Bob_MP")
    carol = await register_and_login(client, "Carol_MP")
    dave = await register_and_login(client, "Dave_MP")

    # Create two disjoint 2-hop routes Alice -> Bob -> Dave and Alice -> Carol -> Dave.
    # For payment X -> Y to have capacity, Y must trust X (trustline Y -> X).
    # Route 1 capacities: A->B (B->A limit 6), B->D (D->B limit 6)
    resp = await client.post(
        "/api/v1/trustlines",
        json={"to": alice["pid"], "equivalent": "USD", "limit": "6.00"},
        headers=bob["headers"],
    )
    assert resp.status_code == 201

    resp = await client.post(
        "/api/v1/trustlines",
        json={"to": bob["pid"], "equivalent": "USD", "limit": "6.00"},
        headers=dave["headers"],
    )
    assert resp.status_code == 201

    # Route 2 capacities: A->C (C->A limit 5), C->D (D->C limit 5)
    resp = await client.post(
        "/api/v1/trustlines",
        json={"to": alice["pid"], "equivalent": "USD", "limit": "5.00"},
        headers=carol["headers"],
    )
    assert resp.status_code == 201

    resp = await client.post(
        "/api/v1/trustlines",
        json={"to": carol["pid"], "equivalent": "USD", "limit": "5.00"},
        headers=dave["headers"],
    )
    assert resp.status_code == 201

    # Multipath payment amount requires split across two routes.
    alice_signing_key = SigningKey(base64.b64decode(alice["priv"]))
    pay_amount = "10.00"
    pay_data = {
        "to": dave["pid"],
        "equivalent": "USD",
        "amount": pay_amount,
        "signature": _sign_payment_request(
            signing_key=alice_signing_key,
            from_pid=alice["pid"],
            to_pid=dave["pid"],
            equivalent="USD",
            amount=pay_amount,
        ),
    }

    resp = await client.post("/api/v1/payments", json=pay_data, headers=alice["headers"])
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "COMMITTED"

    routes = body.get("routes") or []
    assert len(routes) == 2

    # Ensure the split sums to the requested amount.
    total = sum(float(r["amount"]) for r in routes)
    assert round(total, 2) == 10.00
