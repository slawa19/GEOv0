"""054-A review FD4 (brother of 054-A-1): `stop()` publishes `run_status(stopped)` after the run's pending closed-line
publications.

Defect (2026-10-30): a committed tick payment that settles the debt of a line whose close was requested closes the line
(026 S3) and, from the synchronous post-commit callback, SCHEDULES `publish_closed_trustlines` as a task of its own
(`schedule_closed_trustlines_publication`): one re-read of the live lines, then `topology.changed(removed_edges)`. The
tick does not await it (`DeferredRealPaymentEffects.closed_publications` has no awaiting reader), so the heartbeat
drain of 054-A-1 does not cover it: `stopped` could be published first, and the live stream (which returns on
`stopped`) never got the line's removal.

Real path: real PostgreSQL (mode B), the close requested by the signed `DELETE`, the debt settled by the production
`RealPaymentsExecutor` on the runtime's own SSE broadcast, its commit and the production post-commit resolution, and the
runtime's real `stop()`. The only intervention holds the publication's re-read (`_live_pairs`) behind a barrier, as a
slow SELECT would; nothing is injected or substituted.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

import pytest

import app.core.simulator.sse_broadcast as sse_broadcast
from app.core.simulator.edge_patch_builder import EdgePatchBuilder
from app.core.simulator.models import RunRecord
from app.core.simulator.real_payments_executor import RealPaymentsExecutor
from app.core.simulator.runtime import runtime
from tests.conftest import MODE_B
from tests.integration.test_p026_s2_limit_below_used_postgres import _pay, _world
from tests.integration.test_p026_s3_close_request_postgres import _close, _line

pytestmark = MODE_B
_LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Planned:
    seq: int
    equivalent: str
    sender_pid: str
    receiver_pid: str
    amount: str


def _seq(event_id: str) -> int:
    return int(str(event_id).rsplit("_", 1)[-1])


@pytest.mark.asyncio
async def test_stopped_follows_the_pending_closed_line_publication(client, db_session, monkeypatch) -> None:
    code, p, lines, factory = await _world(client, db_session)
    assert await _pay(factory, p["B"], p["A"], code, "50")  # B owes A 50
    r = await _close(client, p["A"], lines["AB"])  # A asks to close A -> B: it stands while B owes
    assert r.status_code == 200 and r.json()["trustline"]["status"] == "active", r.text

    a, b = p["A"]["pid"], p["B"]["pid"]
    pairs = [(p[x]["pid"], p[y]["pid"]) for x, y in ("AB", "BC", "CA")]
    run = RunRecord(run_id="fd4-" + uuid.uuid4().hex[:8], scenario_id="fd4", mode="real", state="running")
    run._scenario_raw = {
        "participants": [{"id": p[k]["pid"], "name": k, "type": "person", "status": "active"} for k in "ABC"],
        "trustlines": [{"equivalent": code, "from": x, "to": y, "limit": "100"} for x, y in pairs],
    }
    run._edges_by_equivalent = {code: list(pairs)}
    run._real_participants = [(p[k]["id"], p[k]["pid"]) for k in "ABC"]
    run._real_seeded = True
    monkeypatch.setitem(runtime._runs, run.run_id, run)

    inside, release = asyncio.Event(), asyncio.Event()
    original_live_pairs = sse_broadcast._live_pairs

    async def held_live_pairs(eq, gone):
        inside.set()
        await release.wait()  # the publication's re-read is slow
        return await original_live_pairs(eq, gone)

    monkeypatch.setattr(sse_broadcast, "_live_pairs", held_live_pairs)
    executor = RealPaymentsExecutor(
        lock=runtime._lock, sse=runtime._sse, utc_now=lambda: datetime.now(timezone.utc), logger=_LOG,
        edge_patch_builder=EdgePatchBuilder(logger=_LOG), should_warn_this_tick=lambda *_: False,
        sim_idempotency_key=lambda **_kw: "fd4-" + uuid.uuid4().hex)
    stop = None
    try:
        async with factory() as session:  # B pays A 50 in a tick: the debt is settled, the close completes
            result = await executor.execute_planned_payments(
                session=session, run_id=run.run_id, run=run, planned=[_Planned(0, code, a, b, "50")],
                equivalents=[code], sender_id_by_pid={a: p["A"]["id"]}, max_in_flight=1, max_timeouts_per_tick=0,
                fail_run=lambda *_: None)
            assert result.committed == 1, result
            await session.commit()
        effects = result.deferred_effects
        await effects.build_post_commit_patches(factory)
        assert effects.apply_after_commit()
        assert effects.closed_publications, "premise: the committed payment scheduled the closed-line publication"
        assert (await _line(client, db_session, p["A"], lines["AB"]))["status"] == "closed"
        await asyncio.wait_for(inside.wait(), timeout=30)

        stop = asyncio.create_task(runtime.stop(run.run_id))
        await asyncio.sleep(0.3)
        stopped_while_held = [e[3] for e in run._event_buffer
                              if e[3].get("type") == "run_status" and e[3].get("state") == "stopped"]
        release.set()
        await asyncio.wait_for(stop, timeout=30)
    finally:
        release.set()
        if stop is not None and not stop.done():
            await asyncio.wait_for(stop, timeout=30)

    buffered = [e[3] for e in run._event_buffer]
    print("054-A FD4 events:", [(e.get("type"), e.get("state")) for e in buffered])
    removals = [e for e in buffered if e.get("type") == "topology.changed"]
    stopped = [e for e in buffered if e.get("type") == "run_status" and e.get("state") == "stopped"]
    assert not stopped_while_held, "stopped was published while the closed-line publication was pending"
    assert removals, "premise: the closed line's removal was published"
    assert [x["from_pid"] for x in removals[0]["payload"]["removed_edges"]] == [a]
    assert stopped and _seq(removals[-1]["event_id"]) < _seq(stopped[0]["event_id"]), (removals, stopped)


@pytest.mark.asyncio
async def test_a_stuck_publication_delays_stopped_only_for_the_bound(db_session, monkeypatch, caplog) -> None:
    """Counter-check of the bound: a publication that never ends does not hold the stop for ever - it is waited for
    the bound and named in a warning, then `stopped` is published."""

    run = RunRecord(run_id="fd4b-" + uuid.uuid4().hex[:8], scenario_id="fd4", mode="real", state="running")
    monkeypatch.setitem(runtime._runs, run.run_id, run)
    monkeypatch.setattr(sse_broadcast, "CLOSED_TRUSTLINES_PUBLICATION_DRAIN_SEC", 0.2)
    never = asyncio.Event()

    async def stuck(**_kw):
        await never.wait()

    monkeypatch.setattr(sse_broadcast, "publish_closed_trustlines", stuck)
    task = sse_broadcast.schedule_closed_trustlines_publication(run_id=run.run_id, pairs=[("x", "y")])
    assert task is not None
    try:
        with caplog.at_level(logging.WARNING):
            status = await asyncio.wait_for(runtime.stop(run.run_id), timeout=10)
        assert status.state == "stopped"
        assert any("trustline_closed_publication_drain_timeout" in r.getMessage() and run.run_id in r.getMessage()
                   for r in caplog.records), [r.getMessage() for r in caplog.records]
        assert not task.done(), "the bound waits; it does not cancel the publication"
    finally:
        never.set()
        await asyncio.wait_for(task, timeout=5)
