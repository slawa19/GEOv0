import base64

import pytest
from nacl.signing import SigningKey


@pytest.mark.asyncio
async def test_refresh_token_rejected_for_protected_endpoints(client, test_user_keys):
    message = f"geo:participant:create:Test User:person:{test_user_keys['public']}".encode("utf-8")
    signing_key_bytes = base64.b64decode(test_user_keys["private"])
    signing_key = SigningKey(signing_key_bytes)
    signature_b64 = base64.b64encode(signing_key.sign(message).signature).decode("utf-8")

    register_resp = await client.post(
        "/api/v1/participants",
        json={
            "display_name": "Test User",
            "type": "person",
            "public_key": test_user_keys["public"],
            "signature": signature_b64,
            "profile": {},
        },
    )
    assert register_resp.status_code == 201
    pid = register_resp.json()["pid"]

    challenge_resp = await client.post("/api/v1/auth/challenge", json={"pid": pid})
    assert challenge_resp.status_code == 200
    challenge = challenge_resp.json()["challenge"]

    signature_bytes = signing_key.sign(challenge.encode("utf-8")).signature
    signature_b64 = base64.b64encode(signature_bytes).decode("utf-8")

    login_resp = await client.post(
        "/api/v1/auth/login",
        json={
            "pid": pid,
            "challenge": challenge,
            "signature": signature_b64,
        },
    )
    assert login_resp.status_code == 200
    tokens = login_resp.json()

    access_token = tokens["access_token"]
    refresh_token = tokens["refresh_token"]

    ok_resp = await client.get(
        "/api/v1/trustlines",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert ok_resp.status_code == 200

    bad_resp = await client.get(
        "/api/v1/trustlines",
        headers={"Authorization": f"Bearer {refresh_token}"},
    )
    assert bad_resp.status_code == 401
