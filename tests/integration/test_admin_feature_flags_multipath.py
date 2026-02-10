import base64
from decimal import Decimal
import uuid

import pytest
from httpx import AsyncClient
from nacl.signing import SigningKey
from sqlalchemy import select

from app.config import settings
from app.db.models.equivalent import Equivalent
from tests.integration.test_scenarios import (
    _sign_payment_request,
    _sign_trustline_create_request,
    register_and_login,
)


async def _seed_equivalent(db_session, code: str = "USD") -> None:
    result = await db_session.execute(select(Equivalent).where(Equivalent.code == code))
    eq = result.scalar_one_or_none()
    if not eq:
        db_session.add(Equivalent(code=code, description=code, precision=2))
        await db_session.commit()


async def _patch_feature_flags(client: AsyncClient, *, updates: dict, reason: str = "test") -> dict:
    resp = await client.patch(
        "/api/v1/admin/feature-flags",
        headers={"X-Admin-Token": settings.ADMIN_TOKEN},
        json={**updates, "reason": reason},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.mark.asyncio
async def test_feature_flag_multipath_enabled_gates_multi_route_payment(client: AsyncClient, db_session):
    await _seed_equivalent(db_session, "USD")

    original = settings.FEATURE_FLAGS_MULTIPATH_ENABLED
    try:
        alice = await register_and_login(client, "Alice_FF_Multi")
        bob = await register_and_login(client, "Bob_FF_Multi")
        carol = await register_and_login(client, "Carol_FF_Multi")
        dave = await register_and_login(client, "Dave_FF_Multi")

        bob_sk = SigningKey(base64.b64decode(bob["priv"]))
        carol_sk = SigningKey(base64.b64decode(carol["priv"]))
        dave_sk = SigningKey(base64.b64decode(dave["priv"]))

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

        # Two disjoint 2-hop paths from Alice to Dave, each 4.00:
        # A->B->D and A->C->D.
        # For X->Y capacity, Y must trust X (trustline Y -> X).
        await tl(bob["headers"], alice["pid"], "4.00", bob_sk)  # edge A->B
        await tl(dave["headers"], bob["pid"], "4.00", dave_sk)  # edge B->D

        await tl(carol["headers"], alice["pid"], "4.00", carol_sk)  # edge A->C
        await tl(dave["headers"], carol["pid"], "4.00", dave_sk)  # edge C->D

        alice_sk = SigningKey(base64.b64decode(alice["priv"]))

        # Disable multipath: should behave like max_paths=1 and fail for amount needing 2 paths.
        await _patch_feature_flags(client, updates={"multipath_enabled": False}, reason="test-disable-multipath")

        resp = await client.post(
            "/api/v1/payments",
            headers=alice["headers"],
            json={
                "tx_id": (tx_id := str(uuid.uuid4())),
                "to": dave["pid"],
                "equivalent": "USD",
                "amount": "6.00",
                "signature": _sign_payment_request(
                    signing_key=alice_sk,
                    tx_id=tx_id,
                    from_pid=alice["pid"],
                    to_pid=dave["pid"],
                    equivalent="USD",
                    amount="6.00",
                ),
            },
        )
        assert resp.status_code == 400, resp.text
        assert resp.json()["error"]["code"] == "E002"

        # Enable multipath: should succeed by splitting across 2 paths.
        await _patch_feature_flags(client, updates={"multipath_enabled": True}, reason="test-enable-multipath")

        resp = await client.post(
            "/api/v1/payments",
            headers=alice["headers"],
            json={
                "tx_id": (tx_id := str(uuid.uuid4())),
                "to": dave["pid"],
                "equivalent": "USD",
                "amount": "6.00",
                "signature": _sign_payment_request(
                    signing_key=alice_sk,
                    tx_id=tx_id,
                    from_pid=alice["pid"],
                    to_pid=dave["pid"],
                    equivalent="USD",
                    amount="6.00",
                ),
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "COMMITTED"
        routes = body.get("routes") or []
        assert len(routes) >= 1
        total = sum(Decimal(str(r["amount"])) for r in routes)
        assert total == Decimal("6.00")
    finally:
        settings.FEATURE_FLAGS_MULTIPATH_ENABLED = original


@pytest.mark.asyncio
async def test_feature_flag_full_multipath_gates_max_flow_metadata(client: AsyncClient, db_session):
    await _seed_equivalent(db_session, "USD")

    original = settings.FEATURE_FLAGS_FULL_MULTIPATH_ENABLED
    try:
        alice = await register_and_login(client, "Alice_FF_MaxFlow")
        bob = await register_and_login(client, "Bob_FF_MaxFlow")
        carol = await register_and_login(client, "Carol_FF_MaxFlow")
        dave = await register_and_login(client, "Dave_FF_MaxFlow")

        bob_sk = SigningKey(base64.b64decode(bob["priv"]))
        carol_sk = SigningKey(base64.b64decode(carol["priv"]))
        dave_sk = SigningKey(base64.b64decode(dave["priv"]))

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

        # Two disjoint 2-hop paths from Alice to Dave, each 5.00.
        await tl(bob["headers"], alice["pid"], "5.00", bob_sk)  # edge A->B
        await tl(dave["headers"], bob["pid"], "5.00", dave_sk)  # edge B->D

        await tl(carol["headers"], alice["pid"], "5.00", carol_sk)  # edge A->C
        await tl(dave["headers"], carol["pid"], "5.00", dave_sk)  # edge C->D

        # Metadata disabled: paths must be empty, but max_amount should still be correct.
        await _patch_feature_flags(client, updates={"full_multipath_enabled": False}, reason="test-disable-full")
        resp = await client.get(
            "/api/v1/payments/max-flow",
            headers=alice["headers"],
            params={"to": dave["pid"], "equivalent": "USD"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert Decimal(body["max_amount"]) == Decimal("10.00")
        assert body["paths"] == []

        # Metadata enabled: paths should include the augmenting paths and sum to max_amount.
        await _patch_feature_flags(client, updates={"full_multipath_enabled": True}, reason="test-enable-full")
        resp = await client.get(
            "/api/v1/payments/max-flow",
            headers=alice["headers"],
            params={"to": dave["pid"], "equivalent": "USD"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert Decimal(body["max_amount"]) == Decimal("10.00")
        paths = body["paths"]
        assert len(paths) >= 2
        total = sum(Decimal(p["capacity"]) for p in paths)
        assert total == Decimal("10.00")
    finally:
        settings.FEATURE_FLAGS_FULL_MULTIPATH_ENABLED = original
