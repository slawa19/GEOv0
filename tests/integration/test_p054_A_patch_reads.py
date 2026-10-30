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

