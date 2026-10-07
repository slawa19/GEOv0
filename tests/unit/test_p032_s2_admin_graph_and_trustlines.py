"""032 / S2 (B-3, B-4, B-9): the admin graph and the trustline list read one projection.

What each test pins, and why it is red on the code before the slice:

* ``test_a_closed_line_reports_no_debt_on_every_admin_read`` (B-3).  Since migration 019 a closed
  incarnation may share (from, to, equivalent) with the live one.  The graph joined the pair's debt to
  EVERY row of the pair, so a closed line showed the live line's debt as its own ``used`` (and a negative
  ``available`` when the debt exceeded the closed line's old limit), while ``GET /admin/trustlines``
  already said ``used = 0``.  One projection now: a closed line is history, ``used = 0`` and
  ``available = limit``; the formula ``available = limit - used`` is unchanged.
* ``test_the_trustline_list_costs_a_constant_number_of_statements`` (B-4).  The list hydrated each row with
  up to four extra statements; the page is now one JOIN query and one count.
* ``test_ego_status_filter_accepts_only_the_declared_statuses`` and the 400/404 tests (B-9): the
  refusals the routes really give are the ones the canon declares.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
import yaml
from sqlalchemy import event

from app.config import settings
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine

from tests.debt_setup import debt_fixture_setup

HEADERS = {"X-Admin-Token": settings.ADMIN_TOKEN}
ROOT = Path(__file__).resolve().parents[2]


def _line(frm, to, eq, limit: str, status: str) -> TrustLine:
    return TrustLine(
        from_participant_id=frm.id,
        to_participant_id=to.id,
        equivalent_id=eq.id,
        limit=Decimal(limit),
        policy={"auto_clearing": True, "can_be_intermediate": True},
        status=status,
    )


async def _world_with_a_reopened_pair(db_session):
    """alice -> bob (UAH): a CLOSED line (limit 50), then a LIVE one (limit 100) carrying a debt of 80."""
    alice = Participant(pid="alice", display_name="Alice", public_key="A" * 64, type="person", status="active")
    bob = Participant(pid="bob", display_name="Bob", public_key="B" * 64, type="person", status="active")
    db_session.add_all([alice, bob])
    uah = Equivalent(code="UAH", symbol="₴", description="Hryvnia", precision=2, metadata_={}, is_active=True)
    db_session.add(uah)
    await db_session.flush()
    db_session.add(_line(alice, bob, uah, "50.00", "closed"))
    await db_session.flush()
    db_session.add(_line(alice, bob, uah, "100.00", "active"))
    await db_session.flush()
    async with debt_fixture_setup(db_session, label="setup"):
        db_session.add(
            Debt(debtor_id=bob.id, creditor_id=alice.id, equivalent_id=uah.id, amount=Decimal("80.00"))
        )
    await db_session.commit()


@pytest.mark.asyncio
async def test_a_closed_line_reports_no_debt_on_every_admin_read(client, db_session):
    await _world_with_a_reopened_pair(db_session)

    ego = await client.get(
        "/api/v1/admin/graph/ego", headers=HEADERS, params={"pid": "alice", "status": ["closed"]}
    )
    assert ego.status_code == 200, ego.text
    (closed,) = ego.json()["trustlines"]
    assert closed["status"] == "closed"
    assert Decimal(closed["used"]) == 0, closed
    assert Decimal(closed["available"]) == Decimal(closed["limit"]) == Decimal("50.00"), closed

    listed = await client.get("/api/v1/admin/trustlines", headers=HEADERS, params={"status": "closed"})
    assert listed.status_code == 200, listed.text
    (listed_closed,) = listed.json()["items"]
    assert (listed_closed["used"], listed_closed["available"]) == (closed["used"], closed["available"])

    # The live line of the same pair keeps the debt, in the snapshot and in the unfiltered ego.
    snap = await client.get("/api/v1/admin/graph/snapshot", headers=HEADERS)
    (live,) = snap.json()["trustlines"]
    assert live["status"] == "active" and Decimal(live["used"]) == Decimal("80.00")
    assert Decimal(live["available"]) == Decimal("20.00")
    ego_all = await client.get("/api/v1/admin/graph/ego", headers=HEADERS, params={"pid": "alice"})
    (live_ego,) = ego_all.json()["trustlines"]
    assert live_ego == live


@pytest.mark.asyncio
async def test_the_trustline_list_costs_a_constant_number_of_statements(client, db_session):
    from tests.conftest import engine

    people = [
        Participant(pid=f"p{i}", display_name=f"P{i}", public_key=f"{i:064d}", type="person", status="active")
        for i in range(8)
    ]
    db_session.add_all(people)
    uah = Equivalent(code="UAH", symbol="₴", description="Hryvnia", precision=2, metadata_={}, is_active=True)
    db_session.add(uah)
    await db_session.flush()
    for i in range(7):
        db_session.add(_line(people[i], people[i + 1], uah, "10.00", "active"))
    await db_session.flush()
    await db_session.commit()

    async def statements_for(per_page: int) -> tuple[int, int]:
        seen: list[str] = []

        def _record(conn, cursor, statement, parameters, context, executemany) -> None:
            seen.append(" ".join(str(statement).split()).upper())

        event.listen(engine.sync_engine, "before_cursor_execute", _record)
        try:
            r = await client.get(
                "/api/v1/admin/trustlines", headers=HEADERS, params={"per_page": per_page, "equivalent": "UAH"}
            )
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", _record)
        assert r.status_code == 200, r.text
        body = r.json()
        assert len(body["items"]) == min(per_page, 7) and body["total"] == 7
        return len(seen), len(body["items"])

    await statements_for(1)  # warm-up: the first request on a connection pays one-off driver statements
    few, few_rows = await statements_for(2)
    many, many_rows = await statements_for(7)
    assert (few_rows, many_rows) == (2, 7)
    # One page query and one count - not a function of the number of rows on the page.
    assert few == many == 2, (few, many)


@pytest.mark.asyncio
async def test_ego_status_filter_accepts_only_the_declared_statuses(client, db_session):
    await _world_with_a_reopened_pair(db_session)
    bad = await client.get(
        "/api/v1/admin/graph/ego", headers=HEADERS, params={"pid": "alice", "status": ["active", "bogus"]}
    )
    assert bad.status_code == 422, bad.text
    ok = await client.get(
        "/api/v1/admin/graph/ego", headers=HEADERS, params={"pid": "alice", "status": ["active", "closed"]}
    )
    assert ok.status_code == 200, ok.text


@pytest.mark.asyncio
async def test_the_graph_refusals_are_the_declared_ones(client, db_session):
    await _world_with_a_reopened_pair(db_session)
    assert (await client.get("/api/v1/admin/graph/snapshot", headers=HEADERS, params={"equivalent": "uah!"})).status_code == 400
    assert (await client.get("/api/v1/admin/graph/ego", headers=HEADERS, params={"pid": "  "})).status_code == 400
    assert (
        await client.get("/api/v1/admin/graph/ego", headers=HEADERS, params={"pid": "alice", "equivalent": "uah!"})
    ).status_code == 400
    assert (await client.get("/api/v1/admin/graph/ego", headers=HEADERS, params={"pid": "nobody"})).status_code == 404

    canon = yaml.safe_load((ROOT / "api" / "openapi.yaml").read_text(encoding="utf-8"))["paths"]
    snapshot = canon["/admin/graph/snapshot"]["get"]["responses"]
    ego = canon["/admin/graph/ego"]["get"]["responses"]
    assert "400" in snapshot and "404" not in snapshot, sorted(snapshot)
    assert {"400", "404"} <= set(ego), sorted(ego)
    ego_params = {p["name"]: p for p in canon["/admin/graph/ego"]["get"]["parameters"]}
    assert ego_params["status"]["schema"]["items"]["enum"] == ["active", "closed"]
