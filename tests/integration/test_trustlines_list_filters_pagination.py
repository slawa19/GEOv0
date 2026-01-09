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


def _sign_trustline_close_request(*, signing_key: SigningKey, trustline_id: str) -> str:
    payload: dict = {"id": str(trustline_id)}
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
    assert resp.status_code == 200, resp.text
    challenge_str = resp.json()["challenge"]

    signature_b64 = base64.b64encode(signing_key.sign(challenge_str.encode("utf-8")).signature).decode(
        "utf-8"
    )

    login_data = {"pid": pid, "challenge": challenge_str, "signature": signature_b64}
    resp = await client.post("/api/v1/auth/login", json=login_data)
    assert resp.status_code == 200, resp.text
    tokens = resp.json()

    return {
        "pid": pid,
        "priv": priv,
        "headers": {"Authorization": f"Bearer {tokens['access_token']}"},
        "display_name": name,
    }


@pytest.mark.asyncio
async def test_trustlines_list_status_filter_and_pagination(client: AsyncClient, db_session):
    # Ensure USD exists.
    result = await db_session.execute(select(Equivalent).where(Equivalent.code == "USD"))
    usd = result.scalar_one_or_none()
    if not usd:
        usd = Equivalent(code="USD", description="US Dollar", precision=2)
        db_session.add(usd)
        await db_session.commit()
        await db_session.refresh(usd)

    alice = await _register_and_login(client, "Alice_TL_List")
    bob = await _register_and_login(client, "Bob_TL_List")
    charlie = await _register_and_login(client, "Charlie_TL_List")

    alice_signing_key = SigningKey(base64.b64decode(alice["priv"]))

    # Create 2 outgoing trustlines.
    tl_ids: list[str] = []
    for target in (bob, charlie):
        tl_data = {
            "to": target["pid"],
            "equivalent": "USD",
            "limit": "100.00",
            "signature": _sign_trustline_create_request(
                signing_key=alice_signing_key,
                to_pid=target["pid"],
                equivalent="USD",
                limit="100.00",
            ),
        }
        resp = await client.post("/api/v1/trustlines", json=tl_data, headers=alice["headers"])
        assert resp.status_code == 201, resp.text
        tl_ids.append(resp.json()["id"])

    active_id, to_close_id = tl_ids[0], tl_ids[1]

    # Close one trustline so we can validate the status filter.
    resp = await client.request(
        "DELETE",
        f"/api/v1/trustlines/{to_close_id}",
        headers=alice["headers"],
        json={"signature": _sign_trustline_close_request(signing_key=alice_signing_key, trustline_id=to_close_id)},
    )
    assert resp.status_code == 200, resp.text

    # Default status is active when query param is omitted.
    resp = await client.get("/api/v1/trustlines?direction=outgoing", headers=alice["headers"])
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert all(item["status"] == "active" for item in items)
    assert all(item["id"] != to_close_id for item in items)

    # Explicit active filter + pagination.
    resp1 = await client.get(
        "/api/v1/trustlines?direction=outgoing&status=active&page=1&per_page=1",
        headers=alice["headers"],
    )
    assert resp1.status_code == 200, resp1.text
    page1_items = resp1.json()["items"]
    assert len(page1_items) == 1

    resp2 = await client.get(
        "/api/v1/trustlines?direction=outgoing&status=active&page=2&per_page=1",
        headers=alice["headers"],
    )
    assert resp2.status_code == 200, resp2.text
    page2_items = resp2.json()["items"]
    # Only 1 active outgoing trustline exists after closing one.
    assert len(page2_items) == 0

    # Closed filter returns the closed trustline.
    resp = await client.get(
        "/api/v1/trustlines?direction=outgoing&status=closed",
        headers=alice["headers"],
    )
    assert resp.status_code == 200, resp.text
    closed_items = resp.json()["items"]
    assert len(closed_items) == 1
    assert closed_items[0]["id"] == to_close_id

    # Hydrated display names are present.
    assert closed_items[0]["from_display_name"] == alice["display_name"]
    assert closed_items[0]["to_display_name"] in {bob["display_name"], charlie["display_name"]}
