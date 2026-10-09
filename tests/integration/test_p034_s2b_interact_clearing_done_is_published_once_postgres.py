"""034 S2b (F-034-3, divergence 3): an interactive clearing publishes its `clearing.done` once, cancelled or not.

The action publishes `clearing.done` and THEN awaits the publication of the lines the clearing closed
(`app/api/v1/simulator.py`, `_emit_interact_clearing_done_best_effort`: the emit, then
`await _publish_closed_best_effort`). A cancellation arriving on that await - the client went away while the
liveness re-read of the closed pair was running - left through the handler's `except asyncio.CancelledError`, which
exists for a cancellation BEFORE the emit and publishes the durable progress without patches: a second
`clearing.done` for the same committed cycle, under a new `plan_id`. The tick's copy of this ending is guarded by a
"done already published" flag (`RealTick._run_clearing`, `done_emitted`).

THE STAND is 026 S4's (`test_an_interact_clearing_that_completes_a_close_removes_the_edge_once`): real PostgreSQL,
debts by real payments, a close request that the clearing completes - so there is a closed pair and the re-read really
awaits. The cancellation is a real one: the request task is cancelled while it stands inside that re-read
(`sse_broadcast._live_pairs`, held by a barrier), nothing is raised by hand.

Not checked: what the client receives (it is gone), and the tick's path (its own tests).
"""

from __future__ import annotations

import asyncio

import pytest

import app.api.v1.simulator as simulator_module
import app.core.simulator.sse_broadcast as sse_broadcast
from app.config import settings
from tests.conftest import MODE_B
from tests.integration.test_p026_s2_limit_below_used_postgres import _pay
from tests.integration.test_p026_s3_close_request_postgres import _line
from tests.integration.test_p026_s4_tick_close_publication_postgres import _requested, _run
from tests.simulator_tick_stand import RecordingSse


async def _stand(client, db_session, monkeypatch):
    code, p, lines, factory = await _requested(client, db_session)
    # A owes C 50 and C owes B 50: with B owing A 50 the cycle clears 50 and completes the close of A -> B.
    assert await _pay(factory, p["A"], p["C"], code, "50") and await _pay(factory, p["C"], p["B"], code, "50")
    run, sse = _run(code, p), RecordingSse()
    monkeypatch.setattr("app.config.settings.SIMULATOR_ACTIONS_ENABLE", True)
    monkeypatch.setitem(simulator_module.runtime._runs, run.run_id, run)
    monkeypatch.setattr(simulator_module.runtime, "_sse", sse)
    return code, p, lines, run, sse


def _post(client, run, code):
    return client.post(f"/api/v1/simulator/runs/{run.run_id}/actions/clearing-real",
                       headers={"X-Admin-Token": settings.ADMIN_TOKEN}, json={"equivalent": code})


def _done(sse) -> list[dict]:
    return [e for e in sse.events if e.get("type") == "clearing.done"]


@MODE_B
@pytest.mark.asyncio
async def test_control_an_uncancelled_clearing_publishes_one_done_and_reaches_the_re_read(
    client, db_session, monkeypatch
) -> None:
    code, p, lines, run, sse = await _stand(client, db_session, monkeypatch)
    read_live, reached = sse_broadcast._live_pairs, []

    async def _seen(eq, pairs):
        reached.append(set(pairs))
        return await read_live(eq, pairs)

    monkeypatch.setattr(sse_broadcast, "_live_pairs", _seen)
    r = await _post(client, run, code)
    assert r.status_code == 200 and r.json()["cleared_cycles"] == 1, r.text
    assert reached == [{(p["A"]["pid"], p["B"]["pid"])}], reached  # the await the cancellation below lands on
    assert len(_done(sse)) == 1, _done(sse)


@MODE_B
@pytest.mark.asyncio
async def test_a_clearing_cancelled_while_its_closed_line_is_published_says_done_once(
    client, db_session, monkeypatch
) -> None:
    code, p, lines, run, sse = await _stand(client, db_session, monkeypatch)
    inside, release = asyncio.Event(), asyncio.Event()

    async def _held(_eq, _pairs):
        inside.set()
        await release.wait()  # the request stands here, as it would on a slow SELECT
        return set()

    monkeypatch.setattr(sse_broadcast, "_live_pairs", _held)
    request = asyncio.create_task(_post(client, run, code))
    await asyncio.wait_for(inside.wait(), timeout=30)
    # Controls: the clearing is committed and its `clearing.done` is out; only the closed-line publication remains.
    assert len(_done(sse)) == 1 and _done(sse)[0]["cleared_cycles"] == 1, _done(sse)
    assert (await _line(client, db_session, p["A"], lines["AB"]))["status"] == "closed"

    request.cancel()
    with pytest.raises(asyncio.CancelledError):
        await request

    done = _done(sse)
    assert len(done) == 1, (
        f"one committed cycle, {len(done)} clearing.done event(s): plan_ids {[d.get('plan_id') for d in done]}, "
        f"patches {[(d.get('node_patch') is not None, d.get('edge_patch') is not None) for d in done]}")
