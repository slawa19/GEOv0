import asyncio
from decimal import Decimal, ROUND_DOWN

import pytest
from sqlalchemy import select

from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine


@pytest.mark.asyncio
async def test_real_mode_graph_snapshot_enriches_used_and_net_sign(
    client, auth_headers, db_session
):
    # Start a real-mode run from fixture scenario.
    resp = await client.post(
        "/api/v1/simulator/runs",
        headers=auth_headers,
        json={"scenario_id": "greenfield-village-100-realistic-v2", "mode": "real", "intensity_percent": 0},
    )
    assert resp.status_code == 200, resp.text
    run_id = resp.json()["run_id"]

    # Seeding occurs on the first real tick (heartbeat loop). Poll for it to land.
    eq = None
    for _ in range(15):
        eq = (
            await db_session.execute(select(Equivalent).where(Equivalent.code == "UAH"))
        ).scalar_one_or_none()
        if eq is not None:
            break
        await asyncio.sleep(0.2)
    assert eq is not None

    tl = (
        await db_session.execute(select(TrustLine).where(TrustLine.equivalent_id == eq.id))
    ).scalars().first()
    assert tl is not None

    creditor = await db_session.get(Participant, tl.from_participant_id)
    debtor = await db_session.get(Participant, tl.to_participant_id)
    assert creditor is not None
    assert debtor is not None

    # Ensure a deterministic debt value for this edge.
    existing = (
        await db_session.execute(
            select(Debt).where(
                Debt.equivalent_id == eq.id,
                Debt.creditor_id == creditor.id,
                Debt.debtor_id == debtor.id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        await db_session.delete(existing)
        await db_session.flush()

    amount = Decimal("12.34")
    db_session.add(
        Debt(
            equivalent_id=eq.id,
            creditor_id=creditor.id,
            debtor_id=debtor.id,
            amount=amount,
        )
    )
    await db_session.commit()

    snap = await client.get(
        f"/api/v1/simulator/runs/{run_id}/graph/snapshot",
        headers=auth_headers,
        params={"equivalent": "UAH"},
    )
    assert snap.status_code == 200, snap.text
    data = snap.json()

    nodes = {n["id"]: n for n in (data.get("nodes") or [])}
    links = data.get("links") or []

    link = next(
        l
        for l in links
        if l.get("source") == creditor.pid and l.get("target") == debtor.pid
    )

    assert link.get("used") == "12.34"

    expected_available = (tl.limit - amount).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
    if expected_available < 0:
        expected_available = Decimal("0.00")
    assert link.get("available") == format(expected_available, "f")

    # Net sign is derived from debts: creditor positive, debtor negative.
    assert nodes[creditor.pid].get("net_sign") == 1
    assert nodes[debtor.pid].get("net_sign") == -1

    # UI node appearance is driven by viz_color_key (see simulator-ui/v2/src/vizMapping.ts).
    # For debtors (negative net), backend should assign a debt bin key.
    assert isinstance(nodes[debtor.pid].get("viz_color_key"), str)
    assert str(nodes[debtor.pid].get("viz_color_key")).startswith("debt-")

    # For creditors/neutral, backend should keep person/business semantics.
    assert nodes[creditor.pid].get("viz_color_key") in ("person", "business")

    # Node sizing must be present (fixtures semantics).
    assert nodes[creditor.pid].get("viz_size") is not None
    assert nodes[debtor.pid].get("viz_size") is not None
    assert nodes[creditor.pid]["viz_size"]["w"] > 0
    assert nodes[creditor.pid]["viz_size"]["h"] > 0

    # Link viz keys should be computed like fixtures.
    assert link.get("viz_width_key") in ("hairline", "thin", "mid", "thick")
    assert link.get("viz_alpha_key") in ("bg", "muted", "active", "hi")
