import base64

import pytest
from nacl.signing import SigningKey

from app.core.auth.canonical import canonical_json


@pytest.mark.asyncio
async def test_participant_type_defaults_to_person_when_omitted(client, test_user_keys):
    signing_key_bytes = base64.b64decode(test_user_keys["private"])
    signing_key = SigningKey(signing_key_bytes)

    # Even if the request omits `type`, the server defaults it to "person".
    # The signature must be computed over the canonical payload including the effective type.
    payload = {
        "display_name": "Type Default Tester",
        "type": "person",
        "public_key": test_user_keys["public"],
        "profile": {},
    }
    message = canonical_json(payload)
    signature_b64 = base64.b64encode(signing_key.sign(message).signature).decode("utf-8")

    resp = await client.post(
        "/api/v1/participants",
        json={
            "display_name": payload["display_name"],
            "public_key": payload["public_key"],
            "profile": payload["profile"],
            "signature": signature_b64,
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["type"] == "person"
