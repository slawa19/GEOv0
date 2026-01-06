import base64

import pytest
from httpx import AsyncClient
from nacl.signing import SigningKey
from sqlalchemy import select

from app.core.auth.canonical import canonical_json
from app.core.auth.crypto import generate_keypair
from app.db.models.equivalent import Equivalent


def _sign_trustline_create_request(
    *,
    signing_key: SigningKey,
    to_pid: str,
    equivalent: str,
    limit: str,
    policy: dict | None = None,
) -> str:
    payload: dict = {"to": to_pid, "equivalent": equivalent, "limit": limit}
    if policy is not None:
        payload["policy"] = policy
    message = canonical_json(payload)
    return base64.b64encode(signing_key.sign(message).signature).decode("utf-8")


def _sign_payment_request(
    *,
    signing_key: SigningKey,
    to_pid: str,
    equivalent: str,
    amount: str,
) -> str:
    payload = {"to": to_pid, "equivalent": equivalent, "amount": amount}
    message = canonical_json(payload)
    return base64.b64encode(signing_key.sign(message).signature).decode("utf-8")


async def _register_and_login(client: AsyncClient, name: str) -> dict:
    pub, priv = generate_keypair()

    signing_key = SigningKey(base64.b64decode(priv))
    reg_message = canonical_json(
        {
            "display_name": name,
            "type": "person",
            "public_key": pub,
            "profile": {},
        }
    )
    reg_sig_b64 = base64.b64encode(signing_key.sign(reg_message).signature).decode("utf-8")

    reg_data = {
        "display_name": name,
        "type": "person",
        "public_key": pub,
        "signature": reg_sig_b64,
        "profile": {},
    }
    resp = await client.post("/api/v1/participants", json=reg_data)
    assert resp.status_code == 201, resp.text

    pid = resp.json()["pid"]

    resp = await client.post("/api/v1/auth/challenge", json={"pid": pid})
    assert resp.status_code == 200
    challenge_str = resp.json()["challenge"]

    signature_b64 = base64.b64encode(signing_key.sign(challenge_str.encode("utf-8")).signature).decode(
        "utf-8"
    )

    login_data = {"pid": pid, "challenge": challenge_str, "signature": signature_b64}
    resp = await client.post("/api/v1/auth/login", json=login_data)
    assert resp.status_code == 200
    tokens = resp.json()

    return {
        "pid": pid,
        "pub": pub,
        "priv": priv,
        "headers": {"Authorization": f"Bearer {tokens['access_token']}"},
    }


@pytest.mark.asyncio
async def test_daily_limit_is_informational_only(client: AsyncClient, db_session):
    # Ensure USD exists.
    result = await db_session.execute(select(Equivalent).where(Equivalent.code == "USD"))
    usd = result.scalar_one_or_none()
    if not usd:
        usd = Equivalent(code="USD", description="US Dollar", precision=2)
        db_session.add(usd)
        await db_session.commit()
        await db_session.refresh(usd)

    alice = await _register_and_login(client, "Alice_DailyLimit")
    bob = await _register_and_login(client, "Bob_DailyLimit")

    # For Alice -> Bob payment, Bob must trust Alice.
    bob_signing_key = SigningKey(base64.b64decode(bob["priv"]))
    policy = {
        "auto_clearing": True,
        "can_be_intermediate": True,
        "daily_limit": "0",
    }
    tl_data = {
        "to": alice["pid"],
        "equivalent": "USD",
        "limit": "100.00",
        "policy": policy,
        "signature": _sign_trustline_create_request(
            signing_key=bob_signing_key,
            to_pid=alice["pid"],
            equivalent="USD",
            limit="100.00",
            policy=policy,
        ),
    }
    resp = await client.post("/api/v1/trustlines", json=tl_data, headers=bob["headers"])
    assert resp.status_code == 201, resp.text

    # Even with daily_limit=0, MVP does not enforce it; payment should succeed.
    alice_signing_key = SigningKey(base64.b64decode(alice["priv"]))
    pay_data = {
        "to": bob["pid"],
        "equivalent": "USD",
        "amount": "10.00",
        "signature": _sign_payment_request(
            signing_key=alice_signing_key,
            to_pid=bob["pid"],
            equivalent="USD",
            amount="10.00",
        ),
    }
    resp = await client.post("/api/v1/payments", json=pay_data, headers=alice["headers"])
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "COMMITTED"
