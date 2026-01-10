import base64

import pytest
from httpx import AsyncClient
from nacl.signing import SigningKey

from app.config import settings
from tests.integration.test_scenarios import register_and_login, _sign_trustline_create_request


@pytest.mark.asyncio
async def test_admin_freeze_blocks_participant_and_unfreeze_restores_access(client: AsyncClient):
    alice = await register_and_login(client, "Alice_Freeze")
    bob = await register_and_login(client, "Bob_Freeze")

    # Bob can access protected endpoints while active.
    resp = await client.get("/api/v1/participants/me", headers=bob["headers"])
    assert resp.status_code == 200

    admin_headers = {"X-Admin-Token": settings.ADMIN_TOKEN}

    # Freeze Bob
    resp = await client.post(
        f"/api/v1/admin/participants/{bob['pid']}/freeze",
        headers=admin_headers,
        json={"reason": "test-freeze"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "suspended"

    # Any authenticated action should now be rejected.
    resp = await client.get("/api/v1/participants/me", headers=bob["headers"])
    assert resp.status_code == 403, resp.text

    # Example of a mutating operation also blocked.
    bob_signing_key = SigningKey(base64.b64decode(bob["priv"]))
    resp = await client.post(
        "/api/v1/trustlines",
        headers=bob["headers"],
        json={
            "to": alice["pid"],
            "equivalent": "USD",
            "limit": "10.00",
            "signature": _sign_trustline_create_request(
                signing_key=bob_signing_key,
                to_pid=alice["pid"],
                equivalent="USD",
                limit="10.00",
            ),
        },
    )
    assert resp.status_code == 403, resp.text

    # Unfreeze Bob
    resp = await client.post(
        f"/api/v1/admin/participants/{bob['pid']}/unfreeze",
        headers=admin_headers,
        json={"reason": "test-unfreeze"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "active"

    resp = await client.get("/api/v1/participants/me", headers=bob["headers"])
    assert resp.status_code == 200, resp.text
