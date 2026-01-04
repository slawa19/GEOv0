from decimal import Decimal

import pytest

from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant


@pytest.mark.asyncio
async def test_get_trustline_by_id(client, db_session, auth_headers):
    usd = Equivalent(code="USD", description="US Dollar", precision=2)
    db_session.add(usd)

    bob = Participant(pid="bob", type="person", display_name="Bob", public_key="pk_bob", status="active")
    db_session.add(bob)

    await db_session.commit()

    create_resp = await client.post(
        "/api/v1/trustlines",
        headers=auth_headers,
        json={
            "to": "bob",
            "equivalent": "USD",
            "limit": str(Decimal("100")),
            "policy": {},
        },
    )
    assert create_resp.status_code == 201
    trustline_id = create_resp.json()["id"]

    get_resp = await client.get(f"/api/v1/trustlines/{trustline_id}", headers=auth_headers)
    assert get_resp.status_code == 200
    body = get_resp.json()

    assert body["id"] == trustline_id
    assert body["to"] == "bob"
    assert body["equivalent"] == "USD"
