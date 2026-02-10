import pytest
from httpx import AsyncClient
import base64
import json
import uuid
from app.core.auth.crypto import generate_keypair
from app.core.auth.canonical import canonical_json
from nacl.signing import SigningKey


def _sign_payment_request(
    *,
    signing_key: SigningKey,
    tx_id: str,
    from_pid: str,
    to_pid: str,
    equivalent: str,
    amount: str,
    description=None,
    constraints=None,
) -> str:
    payload = {
        "tx_id": tx_id,
        "to": to_pid,
        "equivalent": equivalent,
        "amount": amount,
    }
    if description is not None:
        payload["description"] = description
    if constraints is not None:
        payload["constraints"] = constraints

    message = canonical_json(payload)
    return base64.b64encode(signing_key.sign(message).signature).decode("utf-8")


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


def _sign_trustline_update_request(
    *,
    signing_key: SigningKey,
    trustline_id: str,
    limit: str | None = None,
    policy: dict | None = None,
) -> str:
    payload: dict = {"id": str(trustline_id)}
    if limit is not None:
        payload["limit"] = limit
    if policy is not None:
        payload["policy"] = policy
    message = canonical_json(payload)
    return base64.b64encode(signing_key.sign(message).signature).decode("utf-8")


def _sign_trustline_close_request(
    *,
    signing_key: SigningKey,
    trustline_id: str,
) -> str:
    payload: dict = {"id": str(trustline_id)}
    message = canonical_json(payload)
    return base64.b64encode(signing_key.sign(message).signature).decode("utf-8")

async def register_and_login(client: AsyncClient, name: str) -> dict:
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
    if resp.status_code != 201:
        raise Exception(f"Failed to register {name}: {resp.text}")

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
async def test_register_and_auth(client: AsyncClient):
    """
    Test flow:
    1. Register new participant
    2. Request challenge
    3. Sign challenge
    4. Login and get tokens
    5. Access protected endpoint (e.g. get self)
    """
    pub, priv = generate_keypair()

    signing_key = SigningKey(base64.b64decode(priv))
    reg_message = canonical_json(
        {
            "display_name": "Auth Tester",
            "type": "person",
            "public_key": pub,
            "profile": {},
        }
    )
    reg_sig = base64.b64encode(signing_key.sign(reg_message).signature).decode("utf-8")

    reg_response = await client.post(
        "/api/v1/participants",
        json={
            "display_name": "Auth Tester",
            "type": "person",
            "public_key": pub,
            "signature": reg_sig,
            "profile": {},
        },
    )
    assert reg_response.status_code == 201
    pid = reg_response.json()["pid"]
    
    # 2. Challenge
    chal_response = await client.post("/api/v1/auth/challenge", json={"pid": pid})
    assert chal_response.status_code == 200
    challenge = chal_response.json()["challenge"]
    
    # 3. Sign
    sig = base64.b64encode(signing_key.sign(challenge.encode('utf-8')).signature).decode('utf-8')
    
    # 4. Login
    login_response = await client.post("/api/v1/auth/login", json={
        "pid": pid,
        "challenge": challenge,
        "signature": sig
    })
    assert login_response.status_code == 200
    tokens = login_response.json()
    assert "access_token" in tokens
    
    # 5. Access Protected
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    me_response = await client.get(f"/api/v1/participants/{pid}", headers=headers)
    assert me_response.status_code == 200
    assert me_response.json()["pid"] == pid


@pytest.mark.asyncio
async def test_trustlines_crud(client: AsyncClient, db_session):
    """
    Test flow:
    1. Create two users (Alice, Bob)
    2. Alice creates TrustLine to Bob
    3. Alice checks her trustlines
    4. Alice updates TrustLine limit
    5. Alice closes TrustLine
    """
    from app.db.models.equivalent import Equivalent
    from sqlalchemy import select
    
    # Seed Equivalent
    result = await db_session.execute(select(Equivalent).where(Equivalent.code == "USD"))
    usd = result.scalar_one_or_none()
    if not usd:
        usd = Equivalent(code="USD", description="US Dollar", precision=2)
        db_session.add(usd)
        await db_session.commit()
        await db_session.refresh(usd)

    alice = await register_and_login(client, "Alice_TL")
    bob = await register_and_login(client, "Bob_TL")
    
    # 2. Create TrustLine (Alice trusts Bob)
    alice_signing_key = SigningKey(base64.b64decode(alice["priv"]))
    tl_data = {
        "to": bob["pid"],
        "equivalent": "USD",
        "limit": "100.00",
        "signature": _sign_trustline_create_request(
            signing_key=alice_signing_key,
            to_pid=bob["pid"],
            equivalent="USD",
            limit="100.00",
        ),
    }

    resp = await client.post("/api/v1/trustlines", json=tl_data, headers=alice["headers"])
    assert resp.status_code == 201
    tl_id = resp.json()["id"]
    
    # 3. List
    resp = await client.get("/api/v1/trustlines?direction=outgoing", headers=alice["headers"])
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) >= 1
    # Check if our created TL is in list
    found = False
    for item in items:
        if item["id"] == tl_id:
            found = True
            break
    assert found
    
    # 4. Update
    update_data = {
        "limit": "200.00",
        "signature": _sign_trustline_update_request(
            signing_key=alice_signing_key,
            trustline_id=tl_id,
            limit="200.00",
        ),
    }
    resp = await client.patch(f"/api/v1/trustlines/{tl_id}", json=update_data, headers=alice["headers"])
    assert resp.status_code == 200
    assert float(resp.json()["limit"]) == 200.0
    
    # 5. Close
    resp = await client.request(
        "DELETE",
        f"/api/v1/trustlines/{tl_id}",
        headers=alice["headers"],
        json={
            "signature": _sign_trustline_close_request(
                signing_key=alice_signing_key,
                trustline_id=tl_id,
            )
        },
    )
    assert resp.status_code == 200
    
    # No GET-by-id endpoint in MVP; closing success is sufficient


@pytest.mark.asyncio
async def test_direct_payment(client: AsyncClient, db_session):
    # Seed USD
    from app.db.models.equivalent import Equivalent
    from sqlalchemy import select
    result = await db_session.execute(select(Equivalent).where(Equivalent.code == "USD"))
    usd = result.scalar_one_or_none()
    if not usd:
        usd = Equivalent(code="USD", description="US Dollar", precision=2)
        db_session.add(usd)
        await db_session.commit()
        await db_session.refresh(usd)

    # Alice -> Bob
    # Alice needs to trust Bob? No, for Alice to PAY Bob, Bob must trust Alice (accept IOU).
    # Direction: Payment A -> B means B holds A's debt.
    # So B must have TrustLine to A (Incoming for B, Outgoing for A? No.)
    # If A pays B, A becomes Debtor, B becomes Creditor.
    # Creditor (B) must trust Debtor (A).
    # So B creates TrustLine to A.
    
    alice = await register_and_login(client, "Alice_Pay")
    bob = await register_and_login(client, "Bob_Pay")
    
    # Bob trusts Alice for 100 USD (required for Alice -> Bob payment)
    bob_signing_key = SigningKey(base64.b64decode(bob["priv"]))
    tl_data = {
        "to": alice["pid"],
        "equivalent": "USD",
        "limit": "100.00",
        "signature": _sign_trustline_create_request(
            signing_key=bob_signing_key,
            to_pid=alice["pid"],
            equivalent="USD",
            limit="100.00",
        ),
    }
    resp = await client.post("/api/v1/trustlines", json=tl_data, headers=bob["headers"])
    assert resp.status_code == 201
    
    # Check Capacity (Alice checking if she can pay Bob)
    # A -> B
    resp = await client.get(
        "/api/v1/payments/capacity",
        headers=alice["headers"],
        params={"to": bob["pid"], "equivalent": "USD", "amount": "10"},
    )
    assert resp.status_code == 200
    assert resp.json()["can_pay"] is True
    
    # Execute Payment
    alice_signing_key = SigningKey(base64.b64decode(alice["priv"]))
    tx_id = str(uuid.uuid4())
    pay_data = {
        "tx_id": tx_id,
        "to": bob["pid"],
        "equivalent": "USD",
        "amount": "10.00",
        "signature": _sign_payment_request(
            signing_key=alice_signing_key,
            tx_id=tx_id,
            from_pid=alice["pid"],
            to_pid=bob["pid"],
            equivalent="USD",
            amount="10.00",
        ),
    }
    resp = await client.post("/api/v1/payments", json=pay_data, headers=alice["headers"])
    assert resp.status_code == 200
    result = resp.json()
    assert result["status"] == "COMMITTED"
    
    # Verify Balance/Debt
    # Alice should see balance -10
    # Bob should see balance +10
    # Currently `balance` endpoint gives aggregated or per trustline?
    # Let's check debts via trustlines or debt endpoint?
    # Since we didn't inspect balance API, we skip exact balance check unless we want to assume its structure.
    # But we can check max-flow or capacity again.
    
    resp = await client.get(
        f"/api/v1/payments/capacity?to={bob['pid']}&equivalent=USD&amount=90",
        headers=alice["headers"],
    )
    assert resp.json()["can_pay"] is True

    resp = await client.get(
        f"/api/v1/payments/capacity?to={bob['pid']}&equivalent=USD&amount=91",
        headers=alice["headers"],
    )
    assert resp.json()["can_pay"] is False  # 100 limit - 10 used = 90 remaining


@pytest.mark.asyncio
async def test_multihop_payment(client: AsyncClient, db_session):
    # A -> B -> C
    # C trusts B, B trusts A.
    # A pays C. Path: A -> B -> C.
    
    # Seed USD
    from app.db.models.equivalent import Equivalent
    from sqlalchemy import select
    result = await db_session.execute(select(Equivalent).where(Equivalent.code == "USD"))
    usd = result.scalar_one_or_none()
    if not usd:
        usd = Equivalent(code="USD", description="US Dollar", precision=2)
        db_session.add(usd)
        await db_session.commit()
        await db_session.refresh(usd)

    alice = await register_and_login(client, "Alice_Hop")
    bob = await register_and_login(client, "Bob_Hop")
    carol = await register_and_login(client, "Carol_Hop")
    
    # B trusts A; C trusts B
    bob_signing_key = SigningKey(base64.b64decode(bob["priv"]))
    carol_signing_key = SigningKey(base64.b64decode(carol["priv"]))
    await client.post(
        "/api/v1/trustlines",
        headers=bob["headers"],
        json={
            "to": alice["pid"],
            "equivalent": "USD",
            "limit": "100.00",
            "signature": _sign_trustline_create_request(
                signing_key=bob_signing_key,
                to_pid=alice["pid"],
                equivalent="USD",
                limit="100.00",
            ),
        },
    )
    await client.post(
        "/api/v1/trustlines",
        headers=carol["headers"],
        json={
            "to": bob["pid"],
            "equivalent": "USD",
            "limit": "100.00",
            "signature": _sign_trustline_create_request(
                signing_key=carol_signing_key,
                to_pid=bob["pid"],
                equivalent="USD",
                limit="100.00",
            ),
        },
    )
    
    # A pays C
    alice_signing_key = SigningKey(base64.b64decode(alice["priv"]))
    tx_id = str(uuid.uuid4())
    pay_data = {
        "tx_id": tx_id,
        "to": carol["pid"],
        "equivalent": "USD",
        "amount": "50.00",
        "signature": _sign_payment_request(
            signing_key=alice_signing_key,
            tx_id=tx_id,
            from_pid=alice["pid"],
            to_pid=carol["pid"],
            equivalent="USD",
            amount="50.00",
        ),
    }
    resp = await client.post("/api/v1/payments", json=pay_data, headers=alice["headers"])
    assert resp.status_code == 200
    assert resp.json()["status"] == "COMMITTED"
    
    # Check paths logic if possible, but status COMPLETED confirms it worked.


@pytest.mark.asyncio
async def test_multipath_payment(client: AsyncClient, db_session):
    # A pays D using 2 disjoint paths:
    #   A -> B -> D (capacity 30)
    #   A -> C -> D (capacity 30)
    # Single-path 50 would fail; multipath 50 should succeed (30 + 20).

    # Seed USD
    from app.db.models.equivalent import Equivalent
    from sqlalchemy import select

    result = await db_session.execute(select(Equivalent).where(Equivalent.code == "USD"))
    usd = result.scalar_one_or_none()
    if not usd:
        usd = Equivalent(code="USD", description="US Dollar", precision=2)
        db_session.add(usd)
        await db_session.commit()
        await db_session.refresh(usd)

    a = await register_and_login(client, "A_Multi")
    b = await register_and_login(client, "B_Multi")
    c = await register_and_login(client, "C_Multi")
    d = await register_and_login(client, "D_Multi")

    # Trustlines enabling payments along edges:
    # For X -> Y, need trustline Y trusts X (Y -> X)
    b_signing_key = SigningKey(base64.b64decode(b["priv"]))
    c_signing_key = SigningKey(base64.b64decode(c["priv"]))
    d_signing_key = SigningKey(base64.b64decode(d["priv"]))

    await client.post(
        "/api/v1/trustlines",
        headers=b["headers"],
        json={
            "to": a["pid"],
            "equivalent": "USD",
            "limit": "30.00",
            "signature": _sign_trustline_create_request(
                signing_key=b_signing_key,
                to_pid=a["pid"],
                equivalent="USD",
                limit="30.00",
            ),
        },
    )
    await client.post(
        "/api/v1/trustlines",
        headers=d["headers"],
        json={
            "to": b["pid"],
            "equivalent": "USD",
            "limit": "30.00",
            "signature": _sign_trustline_create_request(
                signing_key=d_signing_key,
                to_pid=b["pid"],
                equivalent="USD",
                limit="30.00",
            ),
        },
    )

    await client.post(
        "/api/v1/trustlines",
        headers=c["headers"],
        json={
            "to": a["pid"],
            "equivalent": "USD",
            "limit": "30.00",
            "signature": _sign_trustline_create_request(
                signing_key=c_signing_key,
                to_pid=a["pid"],
                equivalent="USD",
                limit="30.00",
            ),
        },
    )
    await client.post(
        "/api/v1/trustlines",
        headers=d["headers"],
        json={
            "to": c["pid"],
            "equivalent": "USD",
            "limit": "30.00",
            "signature": _sign_trustline_create_request(
                signing_key=d_signing_key,
                to_pid=c["pid"],
                equivalent="USD",
                limit="30.00",
            ),
        },
    )

    # Payment should succeed via multipath.
    a_signing_key = SigningKey(base64.b64decode(a["priv"]))
    tx_id = str(uuid.uuid4())
    pay_data = {
        "tx_id": tx_id,
        "to": d["pid"],
        "equivalent": "USD",
        "amount": "50.00",
        "signature": _sign_payment_request(
            signing_key=a_signing_key,
            tx_id=tx_id,
            from_pid=a["pid"],
            to_pid=d["pid"],
            equivalent="USD",
            amount="50.00",
        ),
    }
    resp = await client.post("/api/v1/payments", json=pay_data, headers=a["headers"])
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "COMMITTED"
    assert float(body["amount"]) == 50.0
    assert body.get("routes") is not None
    assert len(body["routes"]) == 2


@pytest.mark.asyncio
async def test_clearing(client: AsyncClient, db_session):
    """
    Test Clearing Cycle:
    A -> B -> C -> A
    1. A pays B 10
    2. B pays C 10
    3. C pays A 10
    System should detect cycle and clear it (reduce debts to 0).
    """
    # Seed USD
    from app.db.models.equivalent import Equivalent
    from sqlalchemy import select
    result = await db_session.execute(select(Equivalent).where(Equivalent.code == "USD"))
    usd = result.scalar_one_or_none()
    if not usd:
        usd = Equivalent(code="USD", description="US Dollar", precision=2)
        db_session.add(usd)
        await db_session.commit()
        await db_session.refresh(usd)

    alice = await register_and_login(client, "Alice_Clear")
    bob = await register_and_login(client, "Bob_Clear")
    carol = await register_and_login(client, "Carol_Clear")
    
    # Setup Ring Trust
    # Ring trust: B trusts A; C trusts B; A trusts C
    alice_tl_signing_key = SigningKey(base64.b64decode(alice["priv"]))
    bob_tl_signing_key = SigningKey(base64.b64decode(bob["priv"]))
    carol_tl_signing_key = SigningKey(base64.b64decode(carol["priv"]))
    await client.post(
        "/api/v1/trustlines",
        headers=bob["headers"],
        json={
            "to": alice["pid"],
            "equivalent": "USD",
            "limit": "100.00",
            "signature": _sign_trustline_create_request(
                signing_key=bob_tl_signing_key,
                to_pid=alice["pid"],
                equivalent="USD",
                limit="100.00",
            ),
        },
    )
    await client.post(
        "/api/v1/trustlines",
        headers=carol["headers"],
        json={
            "to": bob["pid"],
            "equivalent": "USD",
            "limit": "100.00",
            "signature": _sign_trustline_create_request(
                signing_key=carol_tl_signing_key,
                to_pid=bob["pid"],
                equivalent="USD",
                limit="100.00",
            ),
        },
    )
    await client.post(
        "/api/v1/trustlines",
        headers=alice["headers"],
        json={
            "to": carol["pid"],
            "equivalent": "USD",
            "limit": "100.00",
            "signature": _sign_trustline_create_request(
                signing_key=alice_tl_signing_key,
                to_pid=carol["pid"],
                equivalent="USD",
                limit="100.00",
            ),
        },
    )
    
    # Create Cycle of Debts
    # A pays B 10
    alice_signing_key = SigningKey(base64.b64decode(alice["priv"]))
    tx_id = str(uuid.uuid4())
    await client.post(
        "/api/v1/payments",
        headers=alice["headers"],
        json={
            "tx_id": tx_id,
            "to": bob["pid"],
            "equivalent": "USD",
            "amount": "10.00",
            "signature": _sign_payment_request(
                signing_key=alice_signing_key,
                tx_id=tx_id,
                from_pid=alice["pid"],
                to_pid=bob["pid"],
                equivalent="USD",
                amount="10.00",
            ),
        },
    )
    # B pays C 10
    bob_signing_key = SigningKey(base64.b64decode(bob["priv"]))
    tx_id = str(uuid.uuid4())
    await client.post(
        "/api/v1/payments",
        headers=bob["headers"],
        json={
            "tx_id": tx_id,
            "to": carol["pid"],
            "equivalent": "USD",
            "amount": "10.00",
            "signature": _sign_payment_request(
                signing_key=bob_signing_key,
                tx_id=tx_id,
                from_pid=bob["pid"],
                to_pid=carol["pid"],
                equivalent="USD",
                amount="10.00",
            ),
        },
    )
    # C pays A 10
    carol_signing_key = SigningKey(base64.b64decode(carol["priv"]))
    tx_id = str(uuid.uuid4())
    await client.post(
        "/api/v1/payments",
        headers=carol["headers"],
        json={
            "tx_id": tx_id,
            "to": alice["pid"],
            "equivalent": "USD",
            "amount": "10.00",
            "signature": _sign_payment_request(
                signing_key=carol_signing_key,
                tx_id=tx_id,
                from_pid=carol["pid"],
                to_pid=alice["pid"],
                equivalent="USD",
                amount="10.00",
            ),
        },
    )
    
    # Now we have a cycle A->B->C->A of 10 USD.
    
    # Trigger clearing via API
    resp = await client.get("/api/v1/clearing/cycles?equivalent=USD", headers=alice["headers"])
    assert resp.status_code == 200
    assert len(resp.json()["cycles"]) > 0

    resp = await client.post("/api/v1/clearing/auto?equivalent=USD", headers=alice["headers"])
    assert resp.status_code == 200
    assert resp.json()["cleared_cycles"] >= 1
    
    # Verify debts are gone (or reduced)
    # We can check capacity. If debt is cleared, capacity should be fully restored.
    # A -> B capacity should be 100 (if debt cleared) vs 90 (if debt remains).
    
    resp = await client.get(
        f"/api/v1/payments/capacity?to={bob['pid']}&equivalent=USD&amount=95",
        headers=alice["headers"],
    )
    assert resp.json()["can_pay"] is True
    
