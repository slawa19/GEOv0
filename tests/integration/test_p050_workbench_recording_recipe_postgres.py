"""050 T5004: the recorder's fixed story still executes on the regenerated economy.

Real HTTP Interact payments and clearing, real router/ledger, on the tier's disposable
PostgreSQL clone. This checks the recipe's effect, independently of its static preflight
and the unchanged canned-stream recorder tests. No tick, opening payment or automatic
clearing may get in ahead of the immediately paused run. SSE transport, restart and
concurrent visitors belong to their existing selectors, not this recipe witness.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.config import settings
from app.core.simulator.runtime import runtime
from app.db.models.debt import Debt
from app.db.models.participant import Participant
from scripts import record_workbench_playlists as recorder
from tests.conftest import MODE_B, sessionmaker_of

pytestmark = MODE_B
API = "/api/v1/simulator"


async def _debts(db_session, run_id: str) -> dict[tuple[int, int], Decimal]:
    """Read actual positive debts, not the recipe's expected trust-line direction."""
    async with sessionmaker_of(db_session)() as session:
        participants = (await session.execute(select(Participant.id, Participant.pid).where(
            Participant.pid.in_({f"{run_id}:p{i}" for i in range(1, 101)})
        ))).all()
        ordinal = {row.id: int(row.pid.rsplit(":p", 1)[1]) for row in participants}
        rows = (await session.execute(select(Debt).where(
            Debt.debtor_id.in_(ordinal), Debt.creditor_id.in_(ordinal)
        ))).scalars()
        return {(ordinal[d.debtor_id], ordinal[d.creditor_id]): Decimal(d.amount) for d in rows if d.amount > 0}


def _overlapping_cycles(debts: dict[tuple[int, int], Decimal]) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """Find two actual 3/4-edge debt cycles sharing an oriented edge, without prescribing router tie breaks."""
    cycles: list[tuple[int, ...]] = []

    def visit(path: tuple[int, ...]) -> None:
        for a, b in debts:
            if a != path[-1]:
                continue
            if b == path[0] and len(path) >= 3:
                cycles.append(path)
            elif b > path[0] and b not in path and len(path) < 4:
                visit((*path, b))

    for first in sorted({a for a, _ in debts}):
        visit((first,))
    for i, left in enumerate(cycles):
        edges = set(zip(left, (*left[1:], left[0])))
        for right in cycles[i + 1:]:
            if edges & set(zip(right, (*right[1:], right[0]))):
                return left, right
    raise AssertionError(f"no two overlapping debt cycles before clearing: {debts}")


@pytest.mark.asyncio
async def test_the_twenty_payment_recipe_routes_and_leaves_overlapping_clearable_debts(
    client: AsyncClient, db_session, monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(settings, "SIMULATOR_ACTIONS_ENABLE", True)
    headers = {"X-Admin-Token": settings.ADMIN_TOKEN}
    scenario = json.loads(recorder.SCENARIO_PATH.read_text(encoding="utf-8"))
    assert runtime.get_scenario(recorder.SCENARIO_ID).raw == scenario, "the actual run must use the current shipped graph"
    assert len(recorder.PLAN) == 20 and recorder.DIRECT
    assert all(Decimal(amount).as_tuple().exponent == -2 for _, _, amount in recorder.PLAN)
    started = await client.post(f"{API}/runs", headers=headers, json={
        "scenario_id": recorder.SCENARIO_ID, "intensity_percent": 0,
    })
    assert started.status_code == 200, started.text
    run_id = started.json()["run_id"]
    try:
        paused = await client.post(f"{API}/runs/{run_id}/pause", headers=headers)
        assert paused.status_code == 200, paused.text
        run = runtime.get_run(run_id)
        assert (run.state, run.tick_index, run.intensity_percent) == ("paused", 0, 0)
        seeded = await client.get(f"{API}/runs/{run_id}/actions/trustlines-list", headers=headers,
                                  params={"equivalent": recorder.EQUIVALENT})
        assert seeded.status_code == 200, seeded.text
        assert await _debts(db_session, run_id) == {}, "no scripted opening or automatic payment preceded the recipe"

        routes: list[list[int]] = []
        for n, (payer, payee, amount) in enumerate(recorder.PLAN):
            paid = await client.post(f"{API}/runs/{run_id}/actions/payment-real", headers=headers, json={
                "from_pid": f"{run_id}:p{payer}", "to_pid": f"{run_id}:p{payee}",
                "equivalent": recorder.EQUIVALENT, "amount": amount,
            })
            assert paid.status_code == 200, f"PLAN[{n}] {payer}->{payee}: {paid.text}"
            body = paid.json()
            assert body["status"].upper() == "COMMITTED" and len(body["routes"]) == 1, (n, body)
            hops = body["routes"][0]["hops"]
            path = [int(hops[0]["from"].rsplit(":p", 1)[1])]
            for hop in hops:
                assert int(hop["from"].rsplit(":p", 1)[1]) == path[-1], (n, hops)
                assert Decimal(hop["amount"]) == Decimal(amount), (n, hops)
                path.append(int(hop["to"].rsplit(":p", 1)[1]))
            assert (path[0], path[-1]) == (payer, payee), (n, path)
            if (payer, payee) in recorder.DIRECT:
                assert len(hops) == 1, (n, path)
            routes.append(path)
        assert {len(path) - 1 for path in routes} >= {1, 2, 3, 4}, routes
        before = await _debts(db_session, run_id)
        cycles = _overlapping_cycles(before)
        cleared = await client.post(f"{API}/runs/{run_id}/actions/clearing-real", headers=headers,
                                    json={"equivalent": recorder.EQUIVALENT})
        assert cleared.status_code == 200, cleared.text
        clearing = cleared.json()
        assert clearing["cleared_cycles"] > 0 and Decimal(clearing["total_cleared_amount"]) > 0, clearing
        after = await _debts(db_session, run_id)
        assert sum(after.values()) < sum(before.values()), (before, after, clearing)
        assert (run.state, run.tick_index) == ("paused", 0), "all observed money came from the manual recipe"
        (tmp_path / "recipe-observation.json").write_text(json.dumps({
            "run_id": run_id, "seed": run.seed, "tick_index": run.tick_index,
            "state": run.state, "intensity_percent": run.intensity_percent,
            "routes": routes, "overlapping_cycles": cycles, "clearing": clearing,
            "debt_before": str(sum(before.values())), "debt_after": str(sum(after.values())),
        }, indent=2) + "\n", encoding="utf-8")
    finally:
        await runtime.stop(run_id)
