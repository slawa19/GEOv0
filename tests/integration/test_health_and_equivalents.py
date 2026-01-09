import base64

import pytest
from httpx import AsyncClient
from nacl.signing import SigningKey
from sqlalchemy import select

from app.core.auth.canonical import canonical_json
from app.core.auth.crypto import generate_keypair
from app.db.models.equivalent import Equivalent


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
    assert resp.status_code == 200, resp.text
    challenge_str = resp.json()["challenge"]

    signature_b64 = base64.b64encode(signing_key.sign(challenge_str.encode("utf-8")).signature).decode(
        "utf-8"
    )

    login_data = {"pid": pid, "challenge": challenge_str, "signature": signature_b64}
    resp = await client.post("/api/v1/auth/login", json=login_data)
    assert resp.status_code == 200, resp.text
    tokens = resp.json()

    return {"headers": {"Authorization": f"Bearer {tokens['access_token']}"}}


@pytest.mark.asyncio
async def test_health_endpoints(client: AsyncClient):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"

    resp = await client.get("/healthz")
    assert resp.status_code == 200

    # /api/v1 health aliases exist for clients that use the API base URL.
    resp = await client.get("/api/v1/health")
    assert resp.status_code == 200

    resp = await client.get("/api/v1/healthz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"

    resp = await client.get("/api/v1/health/db")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_equivalents_list(client: AsyncClient, db_session):
    # Ensure USD exists.
    result = await db_session.execute(select(Equivalent).where(Equivalent.code == "USD"))
    usd = result.scalar_one_or_none()
    if not usd:
        usd = Equivalent(code="USD", description="US Dollar", precision=2)
        db_session.add(usd)
        await db_session.commit()
        await db_session.refresh(usd)

    auth = await _register_and_login(client, "Equivalents_User")

    resp = await client.get("/api/v1/equivalents", headers=auth["headers"])
    assert resp.status_code == 200, resp.text

    payload = resp.json()
    assert "items" in payload
    codes = {item["code"] for item in payload["items"]}
    assert "USD" in codes
