import base64

import pytest
from httpx import AsyncClient
from nacl.signing import SigningKey

from app.core.auth.canonical import canonical_json
from app.core.auth.crypto import generate_keypair
from tests.integration.test_scenarios import register_and_login


async def _register_participant(client: AsyncClient, *, display_name: str, type_: str) -> dict:
    pub, priv = generate_keypair()
    signing_key = SigningKey(base64.b64decode(priv))

    payload = {
        "display_name": display_name,
        "type": type_,
        "public_key": pub,
        "profile": {},
    }
    signature_b64 = base64.b64encode(signing_key.sign(canonical_json(payload)).signature).decode("utf-8")

    resp = await client.post(
        "/api/v1/participants",
        json={
            "display_name": display_name,
            "type": type_,
            "public_key": pub,
            "signature": signature_b64,
            "profile": {},
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    return {"pid": body["pid"], "type": body["type"], "display_name": body["display_name"]}


@pytest.mark.asyncio
async def test_participants_search_by_q_type_and_limit(client: AsyncClient):
    # Create a requester (needs auth to call search endpoints)
    requester = await register_and_login(client, "Requester_Search")

    alice_person = await _register_participant(client, display_name="Alice Alpha", type_="person")
    alice_org = await _register_participant(client, display_name="Alice Org", type_="business")
    _bob = await _register_participant(client, display_name="Bob Beta", type_="person")

    # Search by partial name
    resp = await client.get(
        "/api/v1/participants/search",
        headers=requester["headers"],
        params={"q": "Alice"},
    )
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    pids = {i["pid"] for i in items}
    assert alice_person["pid"] in pids
    assert alice_org["pid"] in pids

    # Filter by type
    resp = await client.get(
        "/api/v1/participants/search",
        headers=requester["headers"],
        params={"q": "Alice", "type": "business"},
    )
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert len(items) >= 1
    assert all(i["type"] == "business" for i in items)

    # Limit
    resp = await client.get(
        "/api/v1/participants/search",
        headers=requester["headers"],
        params={"q": "Alice", "limit": 1},
    )
    assert resp.status_code == 200, resp.text
    assert len(resp.json()["items"]) == 1


@pytest.mark.asyncio
async def test_participants_search_pagination_is_consistent(client: AsyncClient):
    requester = await register_and_login(client, "Requester_Pagination")

    created = []
    for i in range(5):
        created.append(await _register_participant(client, display_name=f"User{i} Pag", type_="person"))

    resp1 = await client.get(
        "/api/v1/participants/search",
        headers=requester["headers"],
        params={"q": "User", "page": 1, "per_page": 2},
    )
    assert resp1.status_code == 200, resp1.text
    items1 = resp1.json()["items"]
    assert len(items1) == 2

    resp2 = await client.get(
        "/api/v1/participants/search",
        headers=requester["headers"],
        params={"q": "User", "page": 2, "per_page": 2},
    )
    assert resp2.status_code == 200, resp2.text
    items2 = resp2.json()["items"]
    assert len(items2) == 2

    pids1 = {i["pid"] for i in items1}
    pids2 = {i["pid"] for i in items2}
    assert pids1.isdisjoint(pids2)
