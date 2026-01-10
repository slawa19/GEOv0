import base64

import pytest
from httpx import AsyncClient
from nacl.signing import SigningKey
from sqlalchemy import select

from app.config import settings
from app.db.models.equivalent import Equivalent
from tests.integration.test_scenarios import register_and_login, _sign_payment_request, _sign_trustline_create_request


async def _seed_equivalent(db_session, code: str):
    result = await db_session.execute(select(Equivalent).where(Equivalent.code == code))
    eq = result.scalar_one_or_none()
    if not eq:
        eq = Equivalent(code=code, description=code, precision=2)
        db_session.add(eq)
        await db_session.commit()
        await db_session.refresh(eq)
    return eq


async def _patch_admin_config(client: AsyncClient, *, updates: dict, reason: str = "test") -> None:
    resp = await client.patch(
        "/api/v1/admin/config",
        headers={"X-Admin-Token": settings.ADMIN_TOKEN},
        json={"updates": updates, "reason": reason},
    )
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_routing_max_paths_limits_multipath_payment(client: AsyncClient, db_session):
    await _seed_equivalent(db_session, "USD")

    original = settings.ROUTING_MAX_PATHS
    try:
        alice = await register_and_login(client, "Alice_MaxPaths")
        bob = await register_and_login(client, "Bob_MaxPaths")
        carol = await register_and_login(client, "Carol_MaxPaths")
        eve = await register_and_login(client, "Eve_MaxPaths")
        dave = await register_and_login(client, "Dave_MaxPaths")

        bob_sk = SigningKey(base64.b64decode(bob["priv"]))
        carol_sk = SigningKey(base64.b64decode(carol["priv"]))
        eve_sk = SigningKey(base64.b64decode(eve["priv"]))
        dave_sk = SigningKey(base64.b64decode(dave["priv"]))

        # Three disjoint 2-hop paths from Alice to Dave, each with capacity 4.00:
        # A->B->D, A->C->D, A->E->D.
        # For X->Y capacity, Y must trust X: trustline (Y -> X).
        async def tl(from_headers, to_pid, limit, signing_key):
            resp = await client.post(
                "/api/v1/trustlines",
                headers=from_headers,
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

        # B trusts A; D trusts B
        await tl(bob["headers"], alice["pid"], "4.00", bob_sk)
        await tl(dave["headers"], bob["pid"], "4.00", dave_sk)

        # C trusts A; D trusts C
        await tl(carol["headers"], alice["pid"], "4.00", carol_sk)
        await tl(dave["headers"], carol["pid"], "4.00", dave_sk)

        # E trusts A; D trusts E
        await tl(eve["headers"], alice["pid"], "4.00", eve_sk)
        await tl(dave["headers"], eve["pid"], "4.00", dave_sk)

        alice_sk = SigningKey(base64.b64decode(alice["priv"]))

        # Limit to 2 paths -> total possible = 8.00, so amount 10.00 must fail.
        await _patch_admin_config(client, updates={"ROUTING_MAX_PATHS": 2}, reason="test-max-paths-2")

        resp = await client.post(
            "/api/v1/payments",
            headers=alice["headers"],
            json={
                "to": dave["pid"],
                "equivalent": "USD",
                "amount": "10.00",
                "signature": _sign_payment_request(
                    signing_key=alice_sk,
                    from_pid=alice["pid"],
                    to_pid=dave["pid"],
                    equivalent="USD",
                    amount="10.00",
                ),
            },
        )
        assert resp.status_code == 400, resp.text
        assert resp.json()["error"]["code"] == "E002"

        # Restore to 3 paths -> 10.00 should succeed with 3 routes.
        await _patch_admin_config(client, updates={"ROUTING_MAX_PATHS": 3}, reason="test-max-paths-3")

        resp = await client.post(
            "/api/v1/payments",
            headers=alice["headers"],
            json={
                "to": dave["pid"],
                "equivalent": "USD",
                "amount": "10.00",
                "signature": _sign_payment_request(
                    signing_key=alice_sk,
                    from_pid=alice["pid"],
                    to_pid=dave["pid"],
                    equivalent="USD",
                    amount="10.00",
                ),
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "COMMITTED"
        routes = body.get("routes") or []
        assert len(routes) == 3
        total = sum(float(r["amount"]) for r in routes)
        assert round(total, 2) == 10.00
    finally:
        # Ensure we don't leak settings between tests.
        settings.ROUTING_MAX_PATHS = original
