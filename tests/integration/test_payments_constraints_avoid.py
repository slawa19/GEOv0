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


@pytest.mark.asyncio
async def test_payment_routing_constraints_avoid_filters_intermediate_pid(
    client: AsyncClient,
    db_session,
):
    # Seed USD
    result = await db_session.execute(select(Equivalent).where(Equivalent.code == "USD"))
    usd = result.scalar_one_or_none()
    if not usd:
        usd = Equivalent(code="USD", description="US Dollar", precision=2)
        db_session.add(usd)
        await db_session.commit()
        await db_session.refresh(usd)

    alice = await register_and_login(client, "Alice_Avoid")
    bob = await register_and_login(client, "Bob_Avoid")
    carol = await register_and_login(client, "Carol_Avoid")
    eve = await register_and_login(client, "Eve_Avoid")
    dave = await register_and_login(client, "Dave_Avoid")

    alice_sk = SigningKey(base64.b64decode(alice["priv"]))
    bob_sk = SigningKey(base64.b64decode(bob["priv"]))
    carol_sk = SigningKey(base64.b64decode(carol["priv"]))
    eve_sk = SigningKey(base64.b64decode(eve["priv"]))
    dave_sk = SigningKey(base64.b64decode(dave["priv"]))

    async def tl(*, headers, signing_key, to_pid: str, limit: str) -> None:
        resp = await client.post(
            "/api/v1/trustlines",
            headers=headers,
            json={
                "to": to_pid,
                "equivalent": "USD",
                "limit": limit,
                "signature": _sign_trustline_create_request(
                    signing_key=signing_key,
                    to_pid=to_pid,
                    equivalent="USD",
                    limit=limit,
                ),
            },
        )
        assert resp.status_code == 201, resp.text

    # Build two candidate paths from Alice -> Dave:
    # - short path (2 hops): Alice -> Bob -> Dave
    # - alternative path (3 hops): Alice -> Carol -> Eve -> Dave
    # For X->Y capacity, Y must trust X (trustline Y -> X).

    # Alice -> Bob -> Dave
    await tl(headers=bob["headers"], signing_key=bob_sk, to_pid=alice["pid"], limit="10.00")
    await tl(headers=dave["headers"], signing_key=dave_sk, to_pid=bob["pid"], limit="10.00")

    # Alice -> Carol -> Eve -> Dave
    await tl(headers=carol["headers"], signing_key=carol_sk, to_pid=alice["pid"], limit="10.00")
    await tl(headers=eve["headers"], signing_key=eve_sk, to_pid=carol["pid"], limit="10.00")
    await tl(headers=dave["headers"], signing_key=dave_sk, to_pid=eve["pid"], limit="10.00")

    amount = "1.00"
    tx_id = str(uuid.uuid4())
    constraints = {"avoid": [bob["pid"]]}

    resp = await client.post(
        "/api/v1/payments",
        headers=alice["headers"],
        json={
            "tx_id": tx_id,
            "to": dave["pid"],
            "equivalent": "USD",
            "amount": amount,
            "constraints": constraints,
            "signature": _sign_payment_request(
                signing_key=alice_sk,
                tx_id=tx_id,
                from_pid=alice["pid"],
                to_pid=dave["pid"],
                equivalent="USD",
                amount=amount,
                constraints=constraints,
            ),
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "COMMITTED"

    routes = body.get("routes") or []
    assert len(routes) == 1
    path = routes[0]["path"]
    assert bob["pid"] not in path
    assert path == [alice["pid"], carol["pid"], eve["pid"], dave["pid"]]

