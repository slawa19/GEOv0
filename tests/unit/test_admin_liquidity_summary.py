from __future__ import annotations

from decimal import Decimal

import pytest

from app.config import settings
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine

from tests.debt_setup import debt_fixture_setup


@pytest.mark.asyncio
async def test_admin_liquidity_summary_requires_admin_token(client):
    r = await client.get("/api/v1/admin/liquidity/summary")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_admin_liquidity_summary_smoke(client, db_session):
    # Arrange

    alice = Participant(pid="alice", display_name="Alice", public_key="A" * 64, type="person", status="active")
    bob = Participant(pid="bob", display_name="Bob", public_key="B" * 64, type="person", status="active")
    carol = Participant(pid="carol", display_name="Carol", public_key="C" * 64, type="person", status="active")
    db_session.add_all([alice, bob, carol])

    uah = Equivalent(code="UAH", symbol="₴", description="Hryvnia", precision=2, metadata_={}, is_active=True)
    db_session.add(uah)
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
        from_participant_id=carol.id,
        to_participant_id=alice.id,
        equivalent_id=uah.id,
        limit=Decimal("100.00"),
        policy={"auto_clearing": True, "can_be_intermediate": True},
        status="active",
    )
    db_session.add_all([tl1, tl2])
    await db_session.flush()

    # Debts:
    # - bob owes alice 95
    # - alice owes carol 1 (small usage on tl2)
    async with debt_fixture_setup(db_session, label="setup"):
        db_session.add_all(
            [
                Debt(debtor_id=bob.id, creditor_id=alice.id, equivalent_id=uah.id, amount=Decimal("95.00")),
                Debt(debtor_id=alice.id, creditor_id=carol.id, equivalent_id=uah.id, amount=Decimal("1.00")),
            ]
        )

    await db_session.commit()

    headers = {"X-Admin-Token": settings.ADMIN_TOKEN}

    # Act
    r = await client.get(
        "/api/v1/admin/liquidity/summary?equivalent=UAH",
        headers=headers,
    )
    assert r.status_code == 200
    payload = r.json()

    # Assert: totals
    assert payload["equivalent"] == "UAH"
    assert payload["active_trustlines"] == 2
    assert Decimal(payload["total_limit"]) == Decimal("200.00")
    assert Decimal(payload["total_used"]) == Decimal("96.00")
    assert Decimal(payload["total_available"]) == Decimal("104.00")

    # 032 S5 (F-2, owner decision 2026-10-07): the ranked net lists, the bottleneck count and edges and the
    # incidents counter left with the Liquidity screen; the six per-equivalent fields are the whole answer.
    assert set(payload) == {
        "equivalent", "updated_at", "active_trustlines", "total_limit", "total_used", "total_available",
    }
