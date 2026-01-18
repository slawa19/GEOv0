from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.config import settings
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.transaction import Transaction
from app.db.models.trustline import TrustLine


@pytest.mark.asyncio
async def test_admin_liquidity_summary_requires_admin_token(client):
    r = await client.get("/api/v1/admin/liquidity/summary")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_admin_liquidity_summary_smoke(client, db_session, monkeypatch):
    # Arrange
    monkeypatch.setattr(settings, "PAYMENT_TX_STUCK_TIMEOUT_SECONDS", 120, raising=False)

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
    # - bob owes alice 95 (bottleneck on tl1)
    # - alice owes carol 1 (small usage on tl2)
    db_session.add_all(
        [
            Debt(debtor_id=bob.id, creditor_id=alice.id, equivalent_id=uah.id, amount=Decimal("95.00")),
            Debt(debtor_id=alice.id, creditor_id=carol.id, equivalent_id=uah.id, amount=Decimal("1.00")),
        ]
    )

    now = datetime.now(timezone.utc)
    old = now - timedelta(seconds=121)
    db_session.add(
        Transaction(
            tx_id="tx_stuck_1",
            idempotency_key=None,
            type="PAYMENT",
            initiator_id=alice.id,
            payload={"equivalent": "UAH"},
            signatures=[],
            state="PREPARED",
            error=None,
            created_at=old,
            updated_at=old,
        )
    )

    await db_session.commit()

    headers = {"X-Admin-Token": settings.ADMIN_TOKEN}

    # Act
    r = await client.get(
        "/api/v1/admin/liquidity/summary?equivalent=UAH&threshold=0.10&limit=10",
        headers=headers,
    )
    assert r.status_code == 200
    payload = r.json()

    # Assert: totals
    assert payload["equivalent"] == "UAH"
    assert payload["active_trustlines"] == 2
    assert payload["bottlenecks"] == 1
    assert payload["incidents_over_sla"] == 1

    assert Decimal(payload["total_limit"]) == Decimal("200.00")
    assert Decimal(payload["total_used"]) == Decimal("96.00")
    assert Decimal(payload["total_available"]) == Decimal("104.00")

    # Assert: net positions (Debt direction: debtor -> creditor)
    # alice: +95 (creditor) -1 (debtor) = +94
    # bob: -95
    # carol: +1
    top_creditors = payload["top_creditors"]
    assert {r["pid"] for r in top_creditors} >= {"alice", "carol"}

    by_pid = {r["pid"]: r for r in payload["top_by_abs_net"]}
    assert Decimal(by_pid["alice"]["net"]) == Decimal("94.00")
    assert Decimal(by_pid["bob"]["net"]) == Decimal("-95.00")
    assert Decimal(by_pid["carol"]["net"]) == Decimal("1.00")

    # Assert: bottleneck edges list is present and matches threshold
    assert isinstance(payload["top_bottleneck_edges"], list)
    assert len(payload["top_bottleneck_edges"]) == 1
    edge = payload["top_bottleneck_edges"][0]
    assert edge["from"] == "alice"
    assert edge["to"] == "bob"
    assert edge["equivalent"] == "UAH"
