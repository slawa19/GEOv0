import base64
from decimal import Decimal

import pytest
from httpx import AsyncClient
from nacl.signing import SigningKey
from sqlalchemy import select

from app.db.models.equivalent import Equivalent
from tests.integration.test_scenarios import (
    _sign_payment_request,
    _sign_trustline_close_request,
    _sign_trustline_create_request,
    _sign_trustline_update_request,
    register_and_login,
)


async def _seed_equivalent(db_session, code: str = "USD") -> None:
    result = await db_session.execute(select(Equivalent).where(Equivalent.code == code))
    eq = result.scalar_one_or_none()
    if not eq:
        db_session.add(Equivalent(code=code, description=code, precision=2))
        await db_session.commit()


@pytest.mark.asyncio
async def test_trustline_update_rejects_limit_below_used(client: AsyncClient, db_session):
    await _seed_equivalent(db_session, "USD")

    alice = await register_and_login(client, "Alice_TS07")
    bob = await register_and_login(client, "Bob_TS07")

    # Bob trusts Alice: creates edge Alice -> Bob with 100.00 capacity.
    bob_sk = SigningKey(base64.b64decode(bob["priv"]))
    resp = await client.post(
        "/api/v1/trustlines",
        headers=bob["headers"],
        json={
            "to": alice["pid"],
            "equivalent": "USD",
            "limit": "100.00",
            "signature": _sign_trustline_create_request(
                signing_key=bob_sk,
                to_pid=alice["pid"],
                equivalent="USD",
                limit="100.00",
            ),
        },
    )
    assert resp.status_code == 201, resp.text
    tl_id = resp.json()["id"]

    # Alice pays Bob 10.00 -> debt(Alice -> Bob) becomes 10.00, i.e. used on trustline (Bob -> Alice) is 10.00.
    alice_sk = SigningKey(base64.b64decode(alice["priv"]))
    pay = await client.post(
        "/api/v1/payments",
        headers=alice["headers"],
        json={
            "to": bob["pid"],
            "equivalent": "USD",
            "amount": "10.00",
            "signature": _sign_payment_request(
                signing_key=alice_sk,
                from_pid=alice["pid"],
                to_pid=bob["pid"],
                equivalent="USD",
                amount="10.00",
            ),
        },
    )
    assert pay.status_code == 200, pay.text
    assert pay.json()["status"] == "COMMITTED"

    # Attempt to reduce limit below used (10.00) must be rejected.
    update = await client.patch(
        f"/api/v1/trustlines/{tl_id}",
        headers=bob["headers"],
        json={
            "limit": "5.00",
            "signature": _sign_trustline_update_request(
                signing_key=bob_sk,
                trustline_id=tl_id,
                limit="5.00",
            ),
        },
    )
    assert update.status_code == 400, update.text
    body = update.json()
    assert body["error"]["code"] == "E009"
    assert Decimal(body["error"]["details"]["used"]) >= Decimal("10.00")


@pytest.mark.asyncio
async def test_trustline_close_rejects_non_zero_debt(client: AsyncClient, db_session):
    await _seed_equivalent(db_session, "USD")

    alice = await register_and_login(client, "Alice_TS09")
    bob = await register_and_login(client, "Bob_TS09")

    bob_sk = SigningKey(base64.b64decode(bob["priv"]))
    resp = await client.post(
        "/api/v1/trustlines",
        headers=bob["headers"],
        json={
            "to": alice["pid"],
            "equivalent": "USD",
            "limit": "100.00",
            "signature": _sign_trustline_create_request(
                signing_key=bob_sk,
                to_pid=alice["pid"],
                equivalent="USD",
                limit="100.00",
            ),
        },
    )
    assert resp.status_code == 201, resp.text
    tl_id = resp.json()["id"]

    alice_sk = SigningKey(base64.b64decode(alice["priv"]))
    pay = await client.post(
        "/api/v1/payments",
        headers=alice["headers"],
        json={
            "to": bob["pid"],
            "equivalent": "USD",
            "amount": "10.00",
            "signature": _sign_payment_request(
                signing_key=alice_sk,
                from_pid=alice["pid"],
                to_pid=bob["pid"],
                equivalent="USD",
                amount="10.00",
            ),
        },
    )
    assert pay.status_code == 200, pay.text
    assert pay.json()["status"] == "COMMITTED"

    # Cannot close while there is outstanding debt in either direction.
    close = await client.request(
        "DELETE",
        f"/api/v1/trustlines/{tl_id}",
        headers=bob["headers"],
        json={
            "signature": _sign_trustline_close_request(
                signing_key=bob_sk,
                trustline_id=tl_id,
            )
        },
    )
    assert close.status_code == 400, close.text
    assert close.json()["error"]["code"] == "E009"
