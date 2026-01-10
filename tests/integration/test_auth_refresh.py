import base64
from datetime import datetime, timedelta, timezone
import uuid

import pytest
from httpx import AsyncClient
from nacl.signing import SigningKey
import jwt

from app.core.auth.crypto import generate_keypair
from app.core.auth.canonical import canonical_json
from app.config import settings


async def _register_and_login_return_tokens(client: AsyncClient, name: str) -> dict:
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

    signature_b64 = base64.b64encode(signing_key.sign(challenge_str.encode("utf-8")).signature).decode("utf-8")
    login_data = {"pid": pid, "challenge": challenge_str, "signature": signature_b64}

    resp = await client.post("/api/v1/auth/login", json=login_data)
    assert resp.status_code == 200, resp.text
    tokens = resp.json()

    return {"pid": pid, "tokens": tokens}


@pytest.mark.asyncio
async def test_auth_refresh_rotates_and_reuse_is_rejected(client: AsyncClient):
    session = await _register_and_login_return_tokens(client, "Refresh Tester")
    tokens = session["tokens"]

    refresh_token_1 = tokens["refresh_token"]

    resp = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token_1})
    assert resp.status_code == 200, resp.text

    tokens2 = resp.json()
    assert tokens2["access_token"]
    assert tokens2["refresh_token"]
    assert tokens2["refresh_token"] != refresh_token_1

    # Reuse of old refresh token must fail (rotation).
    resp = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token_1})
    assert resp.status_code == 401, resp.text


@pytest.mark.asyncio
async def test_auth_refresh_rejects_access_token(client: AsyncClient):
    session = await _register_and_login_return_tokens(client, "Refresh Reject Access")
    tokens = session["tokens"]

    # Passing access token where refresh is expected must fail.
    resp = await client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["access_token"]})
    assert resp.status_code == 401, resp.text


@pytest.mark.asyncio
async def test_access_token_expiry_returns_401_and_refresh_recovers(client: AsyncClient):
    session = await _register_and_login_return_tokens(client, "Expiry Tester")
    pid = session["pid"]
    tokens = session["tokens"]

    expired_access = jwt.encode(
        {
            "exp": datetime.now(timezone.utc) - timedelta(minutes=1),
            "sub": pid,
            "type": "access",
            "jti": uuid.uuid4().hex,
        },
        settings.JWT_SECRET,
        algorithm=settings.JWT_ALGORITHM,
    )

    resp = await client.get(
        "/api/v1/participants/me",
        headers={"Authorization": f"Bearer {expired_access}"},
    )
    assert resp.status_code == 401, resp.text

    # Refresh should mint a new access token that works.
    resp = await client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert resp.status_code == 200, resp.text
    new_tokens = resp.json()

    resp = await client.get(
        "/api/v1/participants/me",
        headers={"Authorization": f"Bearer {new_tokens['access_token']}"},
    )
    assert resp.status_code == 200, resp.text
