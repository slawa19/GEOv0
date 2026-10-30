"""054-A-3 and 054-A-5: the SSE patches describe a state that exists, computed over the run's own lines.

054-A-3 (witness W-A3-1, 2026-10-30): `VizPatchHelper.compute_node_patches` read the credit and the debit sums in two
SELECTs under READ COMMITTED; a payment committed between them made the published node patch a combination that never
existed in the database ((-2, +1) where the database held (-1, +1) and then (-2, +2)).

054-A-5 (witness W-A3-3): `EdgePatchBuilder.build_edge_patch_for_equivalent` computed the width quantiles over every
live line of the equivalent, the lines of other runs included, while the snapshot uses the run's own lines; the same
line got a different `viz_width_key` in the patch and in the snapshot.

Real path: real runs (`POST /simulator/runs`, own heartbeat), real HTTP Interact actions, PostgreSQL mode B. The only
intervention is a scheduling barrier that HOLDS a real read after it returned; no result is substituted, no exception
is injected.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest

from app.config import settings
from app.core.simulator.runtime import runtime
from app.core.simulator.viz_patch_helper import VizPatchHelper
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
                if (
                    event.get("type") == "run_status"
                    and event.get("state") == "running"
                    and event.get("sim_time_ms", 0) >= 1000
                ):
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


def _node(event: dict, pid: str):
    for n in event.get("node_patch") or []:
        if n.get("id") == pid:
            return n.get("net_balance")
    return None


@pytest.mark.parametrize("hold", [True, False], ids=["payment_commits_after_the_first_read", "control_sequential"])
@pytest.mark.asyncio
async def test_node_patch_is_a_state_the_database_held(client, db_session, monkeypatch, hold):
    monkeypatch.setattr("app.config.settings.SIMULATOR_ACTIONS_ENABLE", True)
    run_id = await _start_paused(client)
    p1, p6 = f"{run_id}:p1", f"{run_id}:p6"  # opening: p1 owes p6 300, p45 owes p1 300, p6 owes p24 300 -> nets 0
    inside, release = asyncio.Event(), asyncio.Event()
    original = VizPatchHelper.compute_node_patches
    calls = {"n": 0}
    held_reads = {"n": 0}

    class _HoldAfterFirstRead:
        """Holds right after the FIRST statement of `compute_node_patches` returned, while a second payment commits.

        Before 054-A-3 that statement was the credit SELECT and the debit SELECT ran after the hold (the mixed
        state); with one statement over both roles nothing is read after the hold.
        """

        def __init__(self, s):
            self._s = s

        async def execute(self, *a, **k):
            result = await self._s.execute(*a, **k)
            held_reads["n"] += 1
            if held_reads["n"] == 1:
                inside.set()
                await release.wait()
            return result

        def __getattr__(self, name):
            return getattr(self._s, name)

    async def held(self, session, **kw):
        calls["n"] += 1
        if hold and calls["n"] == 1:
            return await original(self, _HoldAfterFirstRead(session), **kw)
        return await original(self, session, **kw)

    monkeypatch.setattr(VizPatchHelper, "compute_node_patches", held)
    observer = await runtime.subscribe(run_id, equivalent="UAH")
    try:
        if hold:
            first = asyncio.create_task(_pay(client, run_id, p1, p6, "1.00"))
            await asyncio.wait_for(inside.wait(), timeout=60)
            second = await _pay(client, run_id, p1, p6, "1.00")
            release.set()
            first = await asyncio.wait_for(first, timeout=60)
        else:
            first = await _pay(client, run_id, p1, p6, "1.00")
            second = await _pay(client, run_id, p1, p6, "1.00")
        assert first.status_code == 200 and second.status_code == 200, (first.text, second.text)
        events = []
        while not observer.queue.empty():
            events.append(observer.queue.get_nowait())
    finally:
        release.set()
        await runtime.unsubscribe(run_id, observer)
    # Anti-vacuum: in the held case the second payment really committed while the first read was held.
    assert not hold or (inside.is_set() and calls["n"] >= 2), "the barrier did not hold a read"
    tx = [e for e in events if e.get("type") == "tx.updated"]
    seen = [(e["event_id"], _node(e, p1), _node(e, p6)) for e in tx]
    print("054-A-3 hold=", hold, "tx.updated (event_id, p1, p6):", seen)
    assert len(seen) == 2 and all(a is not None and b is not None for _, a, b in seen), seen
    db_states = {(Decimal("-1"), Decimal("1")), (Decimal("-2"), Decimal("2"))}
    observed = [(Decimal(a), Decimal(b)) for _, a, b in seen]
    assert all(state in db_states for state in observed), f"a node patch the database never held: {observed}"


@pytest.mark.asyncio
async def test_edge_patch_width_does_not_depend_on_another_runs_lines(client, db_session, monkeypatch):
    monkeypatch.setattr("app.config.settings.SIMULATOR_ACTIONS_ENABLE", True)
    # Visitor run B raises its own lines (a real Interact action), then stops; its lines stay in the database.
    run_b = await _start_paused(client)
    lines_b = runtime.get_run(run_b)._scenario_raw["trustlines"]
    for t in lines_b[:160]:
        r = await client.post(
            f"/api/v1/simulator/runs/{run_b}/actions/trustline-update",
            headers=_admin(),
            json={"from_pid": t["from"], "to_pid": t["to"], "equivalent": "UAH", "new_limit": "50000"},
        )
        assert r.status_code == 200, r.text
    await runtime.stop(run_b)
    # Run A is untouched by B; update its largest line to the value it already has.
    run_a = await _start_paused(client)
    lines_a = runtime.get_run(run_a)._scenario_raw["trustlines"]
    top = max(lines_a, key=lambda t: Decimal(str(t["limit"])))
    observer = await runtime.subscribe(run_a, equivalent="UAH")
    events: list[dict] = []

    def _hit():
        for e in events:
            body = e.get("payload") or {}
            for p in (body.get("edge_patch") or []) if e.get("type") == "topology.changed" else []:
                if p.get("source") == top["from"] and p.get("target") == top["to"]:
                    return p
        return None

    try:
        r = await client.post(
            f"/api/v1/simulator/runs/{run_a}/actions/trustline-update",
            headers=_admin(),
            json={"from_pid": top["from"], "to_pid": top["to"], "equivalent": "UAH", "new_limit": str(top["limit"])},
        )
        assert r.status_code == 200, r.text
        try:
            async with asyncio.timeout(15):
                while _hit() is None:
                    events.append(await observer.queue.get())
        except TimeoutError:
            pass
    finally:
        await runtime.unsubscribe(run_a, observer)
    hit = _hit()
    assert hit is not None, "no edge_patch for the updated line"
    p_key = hit["viz_width_key"]
    snap = await client.get(
        f"/api/v1/simulator/runs/{run_a}/graph/snapshot", headers=_admin(), params={"equivalent": "UAH"}
    )
    assert snap.status_code == 200, snap.text
    s_key = next(
        link["viz_width_key"]
        for link in snap.json()["links"]
        if link["source"] == top["from"] and link["target"] == top["to"]
    )
    print("054-A-5 line", top["from"], "->", top["to"], "limit", top["limit"], "patch:", p_key, "snapshot:", s_key)
    assert p_key == s_key
