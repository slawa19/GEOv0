from __future__ import annotations

from decimal import Decimal

import pytest

from app.config import settings
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine


@pytest.mark.asyncio
async def test_admin_trustlines_bottlenecks_requires_admin_token(client):
    r = await client.get("/api/v1/admin/trustlines/bottlenecks")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_admin_trustlines_bottlenecks_filters_and_sorts(client, db_session):
    # Arrange
    alice = Participant(pid="alice", display_name="Alice", public_key="A" * 64, type="person", status="active")
    bob = Participant(pid="bob", display_name="Bob", public_key="B" * 64, type="person", status="active")
    charlie = Participant(pid="charlie", display_name="Charlie", public_key="C" * 64, type="person", status="active")
    db_session.add_all([alice, bob, charlie])

    uah = Equivalent(code="UAH", symbol="₴", description="Hryvnia", precision=2, metadata_={}, is_active=True)
    usd = Equivalent(code="USD", symbol="$", description="Dollar", precision=2, metadata_={}, is_active=True)
    db_session.add_all([uah, usd])
    await db_session.flush()

    # Two bottlenecks (available/limit below 0.10), different available for sorting.
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
        equivalent_id=uah.id,
        limit=Decimal("100.00"),
        policy={"auto_clearing": True, "can_be_intermediate": True},
        status="active",
    )
    # Non-matching rows (status != active / different equivalent)
    tl3 = TrustLine(
        from_participant_id=alice.id,
        to_participant_id=charlie.id,
        equivalent_id=usd.id,
        limit=Decimal("100.00"),
        policy=None,
        status="frozen",
    )
    db_session.add_all([tl1, tl2, tl3])
    await db_session.flush()

    db_session.add_all(
        [
            Debt(debtor_id=bob.id, creditor_id=alice.id, equivalent_id=uah.id, amount=Decimal("95.00")),
            Debt(debtor_id=bob.id, creditor_id=charlie.id, equivalent_id=uah.id, amount=Decimal("98.00")),
        ]
    )
    await db_session.commit()

    headers = {"X-Admin-Token": settings.ADMIN_TOKEN}

    # Act
    r = await client.get(
        "/api/v1/admin/trustlines/bottlenecks?threshold=0.10&limit=10",
        headers=headers,
    )
    assert r.status_code == 200
    payload = r.json()

    assert payload["threshold"] == 0.10
    items = payload["items"]
    assert len(items) == 2

    # Sorted by available asc: tl2 (available=2) then tl1 (available=5)
    assert items[0]["from"] == "charlie"
    assert items[0]["to"] == "bob"
    assert items[0]["equivalent"] == "UAH"
    assert Decimal(items[0]["used"]) == Decimal("98.00")
    assert Decimal(items[0]["available"]) == Decimal("2.00")

    assert items[1]["from"] == "alice"
    assert items[1]["to"] == "bob"
    assert items[1]["equivalent"] == "UAH"
    assert Decimal(items[1]["used"]) == Decimal("95.00")
    assert Decimal(items[1]["available"]) == Decimal("5.00")

    # Equivalent filter
    r2 = await client.get(
        "/api/v1/admin/trustlines/bottlenecks?threshold=0.10&equivalent=USD",
        headers=headers,
    )
    assert r2.status_code == 200
    assert r2.json()["items"] == []
