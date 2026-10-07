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
async def test_admin_participant_metrics_requires_admin_token(client, db_session):
    resp = await client.get("/api/v1/admin/participants/alice/metrics")
    # App returns 403 for missing/invalid admin token.
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_admin_participant_metrics_balance_rows(client, db_session):
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
    async with debt_fixture_setup(db_session, label="setup"):
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
    assert set(payload) == {"pid", "equivalent", "balance_rows"}

    # Equivalent-specific
    resp2 = await client.get(
        "/api/v1/admin/participants/alice/metrics?equivalent=USD",
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

    # 032 S5 (F-1, owner decision 2026-10-07): the counterparty split, rank, distribution, capacity and activity
    # were removed; the balance rows are the whole answer.
    assert set(p2) == {"pid", "equivalent", "balance_rows"}
