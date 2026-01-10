from __future__ import annotations

from decimal import Decimal

import pytest

from app.config import settings
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine


@pytest.mark.asyncio
async def test_admin_trustlines_requires_admin_token(client, db_session):
    r = await client.get("/api/v1/admin/trustlines")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_admin_trustlines_list_filters_and_pagination(client, db_session):
    # Arrange: participants + equivalent
    alice = Participant(pid="alice", display_name="Alice", public_key="A" * 64, type="person", status="active")
    bob = Participant(pid="bob", display_name="Bob", public_key="B" * 64, type="person", status="active")
    charlie = Participant(pid="charlie", display_name="Charlie", public_key="C" * 64, type="person", status="active")
    db_session.add_all([alice, bob, charlie])

    uah = Equivalent(code="UAH", symbol="₴", description="Hryvnia", precision=2, metadata_={}, is_active=True)
    usd = Equivalent(code="USD", symbol="$", description="Dollar", precision=2, metadata_={}, is_active=True)
    db_session.add_all([uah, usd])

    await db_session.flush()

    tl1 = TrustLine(
        from_participant_id=alice.id,
        to_participant_id=bob.id,
        equivalent_id=uah.id,
        limit=Decimal("100.00"),
        policy={"auto_clearing": True, "can_be_intermediate": True},
        status="active",
    )
    tl2 = TrustLine(
        from_participant_id=charlie.id,
        to_participant_id=bob.id,
        equivalent_id=usd.id,
        limit=Decimal("50.00"),
        policy={"auto_clearing": True, "can_be_intermediate": True},
        status="frozen",
    )
    db_session.add_all([tl1, tl2])

    await db_session.flush()

    # Debt for tl1: debtor=bob (to), creditor=alice (from)
    db_session.add(
        Debt(
            debtor_id=bob.id,
            creditor_id=alice.id,
            equivalent_id=uah.id,
            amount=Decimal("7.25"),
        )
    )

    await db_session.commit()

    headers = {"X-Admin-Token": settings.ADMIN_TOKEN}

    # Act: list all
    r = await client.get("/api/v1/admin/trustlines", headers=headers)
    assert r.status_code == 200
    data = r.json()
    assert "items" in data
    assert len(data["items"]) == 2

    # Assert: schema fields are hydrated
    by_pair = {(i["from"], i["to"], i["equivalent"]): i for i in data["items"]}
    assert ("alice", "bob", "UAH") in by_pair
    assert ("charlie", "bob", "USD") in by_pair

    item1 = by_pair[("alice", "bob", "UAH")]
    assert item1["from_display_name"] == "Alice"
    assert item1["to_display_name"] == "Bob"
    assert item1["limit"] == "100.00000000" or item1["limit"] == "100.00"
    assert item1["used"] in ("7.25000000", "7.25")

    # Filter by equivalent
    r = await client.get("/api/v1/admin/trustlines?equivalent=UAH", headers=headers)
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["equivalent"] == "UAH"

    # Filter by status
    r = await client.get("/api/v1/admin/trustlines?status=frozen", headers=headers)
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["status"] == "frozen"

    # Filter by creditor/debtor
    r = await client.get("/api/v1/admin/trustlines?creditor=alice", headers=headers)
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["from"] == "alice"

    r = await client.get("/api/v1/admin/trustlines?debtor=bob", headers=headers)
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 2

    # Pagination
    r = await client.get("/api/v1/admin/trustlines?per_page=1&page=1", headers=headers)
    assert r.status_code == 200
    assert len(r.json()["items"]) == 1

    r = await client.get("/api/v1/admin/trustlines?per_page=1&page=2", headers=headers)
    assert r.status_code == 200
    assert len(r.json()["items"]) == 1
