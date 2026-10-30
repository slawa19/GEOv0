"""054-A-4: one `trustlines-list` row is one database state of its pair.

The listing used to take `used` from the run's snapshot and `reverse_used` from a later SELECT, so a reverse payment
committed between the two reads produced a row with a positive debt in BOTH directions (`used 300.00 /
reverse_used 100.00`) - a state the ledger never holds (a pair carries one directed debt). The row now takes both
directions from one read.

Real path: a real run (`POST /simulator/runs`), real HTTP actions, PostgreSQL mode B. The only intervention is a
scheduling barrier that HOLDS the listing right after its snapshot was really built (on the old code: between the two
reads); no result is substituted and no exception injected.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest

import app.api.v1.simulator as simulator_module
from app.config import settings
from app.core.simulator.runtime import runtime
from tests.conftest import MODE_B

pytestmark = MODE_B

SCENARIO = "riverside-town-50-realistic-v2"


def _admin() -> dict[str, str]:
    return {"X-Admin-Token": settings.ADMIN_TOKEN}


async def _start_paused(client) -> str:
    r = await client.post(
        "/api/v1/simulator/runs",
        headers=_admin(),
        json={"scenario_id": SCENARIO, "mode": "real", "intensity_percent": 0},
    )
    assert r.status_code == 200, r.text
    run_id = str(r.json()["run_id"])
    observer = await runtime.subscribe(run_id, equivalent="UAH")
    try:
        async with asyncio.timeout(120):
            while True:
                event = await observer.queue.get()
                if event.get("type") == "run_status" and event.get("state") == "running" and event.get("sim_time_ms", 0) >= 1000:
                    await runtime.pause(run_id)
                    break
    finally:
        await runtime.unsubscribe(run_id, observer)
    return run_id


def _pay(client, run_id, frm, to, amount):
    return client.post(
        f"/api/v1/simulator/runs/{run_id}/actions/payment-real",
        headers=_admin(),
        json={"from_pid": frm, "to_pid": to, "equivalent": "UAH", "amount": amount},
    )


def _row(listing, frm: str, to: str) -> dict:
    return next(i for i in listing.json()["items"] if i["from_pid"] == frm and i["to_pid"] == to)


@pytest.mark.asyncio
async def test_p054_A_4_trustlines_list_row_is_one_database_state(client, db_session, monkeypatch):
    monkeypatch.setattr("app.config.settings.SIMULATOR_ACTIONS_ENABLE", True)
    run_id = await _start_paused(client)
    p1, p6 = f"{run_id}:p1", f"{run_id}:p6"  # line p6 -> p1 (creditor p6), p1 owes p6 300
    r = await client.post(
        f"/api/v1/simulator/runs/{run_id}/actions/trustline-create",
        headers=_admin(),
        json={"from_pid": p1, "to_pid": p6, "equivalent": "UAH", "limit": "1000"},
    )
    assert r.status_code == 200 or (r.status_code == 409 and "TRUSTLINE_EXISTS" in r.text), r.text
    inside, release = asyncio.Event(), asyncio.Event()
    original = simulator_module.runtime.build_graph_snapshot
    calls = {"n": 0}

    async def held(*a, **k):
        res = await original(*a, **k)
        calls["n"] += 1
        if calls["n"] == 1:
            inside.set()
            await release.wait()  # the snapshot was really built; the listing's own debt read has not run yet
        return res

    monkeypatch.setattr(simulator_module.runtime, "build_graph_snapshot", held)
    try:
        listing = asyncio.create_task(
            client.get(f"/api/v1/simulator/runs/{run_id}/actions/trustlines-list", headers=_admin(),
                       params={"equivalent": "UAH"})
        )
        await asyncio.wait_for(inside.wait(), timeout=60)
        pay = await _pay(client, run_id, p6, p1, "400.00")  # nets p1's 300, then p6 owes p1 100
        assert pay.status_code == 200, pay.text
        release.set()
        listing = await asyncio.wait_for(listing, timeout=60)
    finally:
        release.set()
    assert listing.status_code == 200, listing.text
    row = _row(listing, p6, p1)
    again = await client.get(f"/api/v1/simulator/runs/{run_id}/actions/trustlines-list", headers=_admin(),
                             params={"equivalent": "UAH"})
    assert again.status_code == 200, again.text
    row2 = _row(again, p6, p1)
    held_state = (Decimal(row["used"]), Decimal(row["reverse_used"]))
    print("054-A-4 held listing row p6->p1:", {k: row[k] for k in ("used", "reverse_used", "available", "limit")})
    print("054-A-4 fresh listing row p6->p1:", {k: row2[k] for k in ("used", "reverse_used", "available", "limit")})

    assert not (held_state[0] > 0 and held_state[1] > 0), (
        f"held row {held_state} carries a positive debt in both directions - two database states in one row")
    assert held_state in {(Decimal("300"), Decimal("0")), (Decimal("0"), Decimal("100"))}, (
        f"held row {held_state} is neither the state before the payment nor the one after")
    assert Decimal(row["available"]) == Decimal(row["limit"]) - held_state[0], "available follows the row's own used"
    # Control: a listing after the payment shows the state after it.
    assert (Decimal(row2["used"]), Decimal(row2["reverse_used"])) == (Decimal("0"), Decimal("100")), "control"
