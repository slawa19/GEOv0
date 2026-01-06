import base64

import pytest
from httpx import AsyncClient
from nacl.signing import SigningKey

from app.core.auth.crypto import generate_keypair
from app.core.auth.canonical import canonical_json


@pytest.mark.asyncio
async def test_register_participant_duplicate_public_key_returns_conflict(client: AsyncClient):
    pub, priv = generate_keypair()

    signing_key = SigningKey(base64.b64decode(priv))

    def sign(display_name: str, type_: str) -> str:
        msg = canonical_json(
            {
                "display_name": display_name,
                "type": type_,
                "public_key": pub,
                "profile": {},
            }
        )
        return base64.b64encode(signing_key.sign(msg).signature).decode("utf-8")

    # First registration
    resp = await client.post(
        "/api/v1/participants",
        json={
            "display_name": "DupUser",
            "type": "person",
            "public_key": pub,
            "signature": sign("DupUser", "person"),
            "profile": {},
        },
    )
    assert resp.status_code == 201

    # Second registration with same public_key should conflict
    resp = await client.post(
        "/api/v1/participants",
        json={
            "display_name": "DupUser2",
            "type": "person",
            "public_key": pub,
            "signature": sign("DupUser2", "person"),
            "profile": {},
        },
    )

    assert resp.status_code == 409
    body = resp.json()
    assert "error" in body
    assert body["error"]["code"] == "E008"
