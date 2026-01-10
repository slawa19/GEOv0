import pytest
from decimal import Decimal

from httpx import AsyncClient

from tests.integration.test_scenarios import register_and_login


@pytest.mark.asyncio
async def test_clearing_max_depth_blocks_and_allows_length_5_cycle(client: AsyncClient, db_session):
    # Create an auth user to call clearing endpoints.
    user = await register_and_login(client, "Clearing Depth Tester")

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

    # Create 5 participants and a length-5 debt cycle: A->B->C->D->E->A.
    from app.db.models.participant import Participant
    from app.db.models.trustline import TrustLine
    from app.db.models.debt import Debt

    pids = ["A5", "B5", "C5", "D5", "E5"]
    participants = []
    for pid in pids:
        p = Participant(pid=pid, display_name=pid, public_key=f"pk_{pid}", type="person", status="active")
        db_session.add(p)
        participants.append(p)
    await db_session.commit()
    for p in participants:
        await db_session.refresh(p)

    id_by_pid = {p.pid: p.id for p in participants}

    # For a debt edge debtor->creditor, the controlling trustline is creditor->debtor.
    edges = [("A5", "B5"), ("B5", "C5"), ("C5", "D5"), ("D5", "E5"), ("E5", "A5")]
    for debtor, creditor in edges:
        db_session.add(
            TrustLine(
                from_participant_id=id_by_pid[creditor],
                to_participant_id=id_by_pid[debtor],
                equivalent_id=usd.id,
                limit=Decimal("100.00"),
                policy={
                    "auto_clearing": True,
                    "can_be_intermediate": True,
                    "max_hop_usage": None,
                    "daily_limit": None,
                    "blocked_participants": [],
                },
                status="active",
            )
        )
    await db_session.commit()

    for debtor, creditor in edges:
        db_session.add(
            Debt(
                debtor_id=id_by_pid[debtor],
                creditor_id=id_by_pid[creditor],
                equivalent_id=usd.id,
                amount=Decimal("1.00"),
            )
        )
    await db_session.commit()

    # With max_depth=4, the length-5 cycle should not be detected.
    resp = await client.get(
        "/api/v1/clearing/cycles",
        params={"equivalent": "USD", "max_depth": 4},
        headers=user["headers"],
    )
    assert resp.status_code == 200, resp.text
    assert resp.json().get("cycles") == []

    # With max_depth=4, auto_clear should not clear the cycle.
    resp = await client.post(
        "/api/v1/clearing/auto",
        params={"equivalent": "USD", "max_depth": 4},
        headers=user["headers"],
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["cleared_cycles"] == 0

    # With max_depth=5, auto_clear should clear the cycle.
    resp = await client.post(
        "/api/v1/clearing/auto",
        params={"equivalent": "USD", "max_depth": 5},
        headers=user["headers"],
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["cleared_cycles"] >= 1

    resp = await client.get(
        "/api/v1/clearing/cycles",
        params={"equivalent": "USD", "max_depth": 5},
        headers=user["headers"],
    )
    assert resp.status_code == 200, resp.text
    assert resp.json().get("cycles") == []
