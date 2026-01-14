from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.config import settings
from app.db.models.audit_log import AuditLog
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine
from app.db.models.transaction import Transaction


@pytest.mark.asyncio
async def test_admin_participant_metrics_requires_admin_token(client, db_session):
    resp = await client.get("/api/v1/admin/participants/alice/metrics")
    # App returns 403 for missing/invalid admin token.
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_admin_participant_metrics_balance_and_counterparties_and_capacity_and_rank(client, db_session):
    # Participants
    alice = Participant(pid="alice", display_name="Alice", public_key="A" * 64, type="person", status="active")
    bob = Participant(pid="bob", display_name="Bob", public_key="B" * 64, type="person", status="active")
    carol = Participant(pid="carol", display_name="Carol", public_key="C" * 64, type="person", status="active")

    # Equivalent
    usd = Equivalent(code="USD", precision=2)

    db_session.add_all([alice, bob, carol, usd])
    await db_session.commit()

    # Trustlines: from->to is creditor->debtor
    # Alice extends credit to Bob (limit 100), Bob extends credit to Alice (limit 50)
    tl_a_b = TrustLine(from_participant_id=alice.id, to_participant_id=bob.id, equivalent_id=usd.id, limit=Decimal("100"), status="active")
    tl_b_a = TrustLine(from_participant_id=bob.id, to_participant_id=alice.id, equivalent_id=usd.id, limit=Decimal("50"), status="active")

    db_session.add_all([tl_a_b, tl_b_a])
    await db_session.commit()

    # Debts: debtor owes creditor
    # Bob owes Alice 90 => used on trustline Alice->Bob is 90
    # Alice owes Bob 10 => used on trustline Bob->Alice is 10
    db_session.add_all(
        [
            Debt(debtor_id=bob.id, creditor_id=alice.id, equivalent_id=usd.id, amount=Decimal("90")),
            Debt(debtor_id=alice.id, creditor_id=bob.id, equivalent_id=usd.id, amount=Decimal("10")),
            Debt(debtor_id=carol.id, creditor_id=alice.id, equivalent_id=usd.id, amount=Decimal("5")),
        ]
    )
    await db_session.commit()

    headers = {"X-Admin-Token": settings.ADMIN_TOKEN}

    # Balance-only (equivalent omitted)
    resp = await client.get("/api/v1/admin/participants/alice/metrics", headers=headers)
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["pid"] == "alice"
    assert payload["equivalent"] is None
    assert isinstance(payload["balance_rows"], list)
    assert payload["counterparty"] is None

    # Equivalent-specific
    resp2 = await client.get(
        "/api/v1/admin/participants/alice/metrics?equivalent=USD&threshold=0.10",
        headers=headers,
    )
    assert resp2.status_code == 200
    p2 = resp2.json()

    assert p2["equivalent"] == "USD"

    def as_dec(v) -> Decimal:
        return Decimal(str(v))

    # Balance row for USD
    rows = p2["balance_rows"]
    assert len(rows) == 1
    r = rows[0]
    assert r["equivalent"] == "USD"

    # outgoing: Alice is creditor (from) on tl_a_b: limit 100, used 90
    assert as_dec(r["outgoing_limit"]) == Decimal("100")
    assert as_dec(r["outgoing_used"]) == Decimal("90")

    # incoming: Alice is debtor (to) on tl_b_a: limit 50, used 10
    assert as_dec(r["incoming_limit"]) == Decimal("50")
    assert as_dec(r["incoming_used"]) == Decimal("10")

    # total debt: Alice owes Bob 10
    assert as_dec(r["total_debt"]) == Decimal("10")

    # total credit: Bob owes Alice 90, Carol owes Alice 5
    assert as_dec(r["total_credit"]) == Decimal("95")

    # net = 95 - 10 = 85
    assert as_dec(r["net"]) == Decimal("85")

    # Counterparties
    cp = p2["counterparty"]
    assert cp["eq"] == "USD"
    assert as_dec(cp["totalDebt"]) == Decimal("10")
    assert as_dec(cp["totalCredit"]) == Decimal("95")

    creditors = cp["creditors"]
    debtors = cp["debtors"]

    # As debtor, Alice owes Bob
    assert len(creditors) == 1
    assert creditors[0]["pid"] == "bob"
    assert as_dec(creditors[0]["amount"]) == Decimal("10")

    # As creditor, Alice is owed by Bob and Carol (sorted by amount desc)
    assert [d["pid"] for d in debtors] == ["bob", "carol"]

    # Rank and distribution should exist for equivalent.
    assert p2["rank"]["eq"] == "USD"
    assert p2["rank"]["n"] == 3
    assert p2["distribution"]["eq"] == "USD"
    assert isinstance(p2["distribution"]["bins"], list)

    # Capacity and bottlenecks.
    cap = p2["capacity"]
    assert cap["eq"] == "USD"
    assert as_dec(cap["out"]["limit"]) == Decimal("100")
    assert as_dec(cap["out"]["used"]) == Decimal("90")
    assert as_dec(cap["inc"]["limit"]) == Decimal("50")
    assert as_dec(cap["inc"]["used"]) == Decimal("10")

    # With threshold=0.10, Alice->Bob available is 10/100 = 0.10, not < threshold => not bottleneck
    assert cap["bottlenecks"] == []

    resp3 = await client.get(
        "/api/v1/admin/participants/alice/metrics?equivalent=USD&threshold=0.11",
        headers=headers,
    )
    assert resp3.status_code == 200
    p3 = resp3.json()
    assert len(p3["capacity"]["bottlenecks"]) == 1
    b0 = p3["capacity"]["bottlenecks"][0]
    assert b0["dir"] == "out"
    assert b0["other"] == "bob"


@pytest.mark.asyncio
async def test_admin_participant_metrics_activity_counts(client, db_session):
    alice = Participant(pid="alice", display_name="Alice", public_key="A" * 64, type="person", status="active")
    bob = Participant(pid="bob", display_name="Bob", public_key="B" * 64, type="person", status="active")
    usd = Equivalent(code="USD", precision=2)
    db_session.add_all([alice, bob, usd])
    await db_session.commit()

    # Use a stable "now" by setting timestamps.
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)

    # Trustline created 5 days ago.
    tl = TrustLine(
        from_participant_id=alice.id,
        to_participant_id=bob.id,
        equivalent_id=usd.id,
        limit=Decimal("10"),
        status="active",
        created_at=now - timedelta(days=5),
        updated_at=now - timedelta(days=5),
    )
    db_session.add(tl)

    # Audit log participant op 10 days ago.
    db_session.add(
        AuditLog(
            timestamp=now - timedelta(days=10),
            actor_id=None,
            actor_role="admin",
            action="admin.participants.freeze",
            object_type="participant",
            object_id="alice",
            reason="test",
            before_state=None,
            after_state=None,
            request_id="r1",
            ip_address=None,
            user_agent=None,
        )
    )

    # Payment committed involving alice 2 days ago.
    tx = Transaction(
        tx_id="tx1",
        type="PAYMENT",
        initiator_id=alice.id,
        payload={"from": "alice", "to": "bob", "amount": "1", "equivalent": "USD"},
        state="COMMITTED",
        created_at=now - timedelta(days=2),
        updated_at=now - timedelta(days=2),
    )
    db_session.add(tx)

    await db_session.commit()

    headers = {"X-Admin-Token": settings.ADMIN_TOKEN}

    resp = await client.get("/api/v1/admin/participants/alice/metrics?equivalent=USD", headers=headers)
    assert resp.status_code == 200
    p = resp.json()

    act = p["activity"]
    assert act["windows"] == [7, 30, 90]

    # trustline created within 7/30/90
    assert act["trustline_created"]["7"] == 1
    assert act["trustline_created"]["30"] == 1
    assert act["trustline_created"]["90"] == 1

    # participant op at 10 days: not within 7, but within 30/90
    assert act["participant_ops"]["7"] == 0
    assert act["participant_ops"]["30"] == 1
    assert act["participant_ops"]["90"] == 1

    # payment committed at 2 days: within all windows
    assert act["payment_committed"]["7"] == 1
    assert act["payment_committed"]["30"] == 1
    assert act["payment_committed"]["90"] == 1
