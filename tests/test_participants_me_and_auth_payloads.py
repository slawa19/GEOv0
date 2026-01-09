from __future__ import annotations

import base64

import pytest

from nacl.signing import SigningKey

from app.core.auth.canonical import canonical_json
from app.core.auth.crypto import generate_keypair


@pytest.mark.asyncio
async def test_auth_login_returns_expires_in_and_participant(client):
    public_key, private_key = generate_keypair()

    # Register participant
    message = canonical_json(
        {
            "display_name": "Test User",
            "type": "person",
            "public_key": public_key,
            "profile": {},
        }
    )
    signing_key = SigningKey(base64.b64decode(private_key))
    reg_signature_b64 = base64.b64encode(signing_key.sign(message).signature).decode("utf-8")
    r = await client.post(
        "/api/v1/participants",
        json={
            "display_name": "Test User",
            "type": "person",
            "public_key": public_key,
            "profile": {},
            "signature": reg_signature_b64,
        },
    )
    assert r.status_code == 201
    pid = r.json()["pid"]

    # Challenge
    r = await client.post("/api/v1/auth/challenge", json={"pid": pid})
    assert r.status_code == 200
    challenge = r.json()["challenge"]

    # Login
    login_sig_b64 = base64.b64encode(signing_key.sign(challenge.encode("utf-8")).signature).decode("utf-8")
    r = await client.post(
        "/api/v1/auth/login",
        json={
            "pid": pid,
            "challenge": challenge,
            "signature": login_sig_b64,
            "device_info": {"platform": "pytest", "app_version": "0"},
        },
    )
    assert r.status_code == 200
    data = r.json()

    assert isinstance(data.get("access_token"), str) and data["access_token"]
    assert isinstance(data.get("refresh_token"), str) and data["refresh_token"]
    assert isinstance(data.get("expires_in"), int) and data["expires_in"] > 0
    assert isinstance(data.get("participant"), dict)
    assert data["participant"]["pid"] == pid


@pytest.mark.asyncio
async def test_participants_me_get_and_patch(client, auth_user):
    # GET /participants/me
    r = await client.get("/api/v1/participants/me", headers=auth_user["headers"])
    assert r.status_code == 200
    me = r.json()
    assert me["pid"] == auth_user["pid"]
    assert "stats" in me
    assert "total_incoming_trust" in me["stats"]
    assert "total_outgoing_trust" in me["stats"]
    assert "total_debt" in me["stats"]
    assert "total_credit" in me["stats"]
    assert "net_balance" in me["stats"]

    # PATCH /participants/me (signed changes)
    update_payload = {"display_name": "Alice Smith"}
    signing_key = SigningKey(base64.b64decode(auth_user["private_key"]))
    update_sig_b64 = base64.b64encode(signing_key.sign(canonical_json(update_payload)).signature).decode("utf-8")
    r = await client.patch(
        "/api/v1/participants/me",
        headers=auth_user["headers"],
        json={**update_payload, "signature": update_sig_b64},
    )
    assert r.status_code == 200
    updated = r.json()
    assert updated["display_name"] == "Alice Smith"