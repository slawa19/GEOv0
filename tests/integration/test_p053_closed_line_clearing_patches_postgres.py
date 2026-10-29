"""053 F: a committed clearing accounts for closed lines before removing them.

Manual uses signed close and real payments; periodic/scripted/recovery use the
existing real runner stand. Only pauses/read faults are injected, never commits.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select, update

import app.api.v1.simulator as simulator_module
from app.config import settings
from app.core.clearing.service import ClearingService
from app.db.models.trustline import TrustLine
from tests.conftest import MODE_B
from tests.integration.test_p026_s2_limit_below_used_postgres import _debts, _pay
from tests.integration.test_p026_s3_close_request_postgres import _line
from tests.integration.test_p026_s4_tick_close_publication_postgres import _requested, _run
from tests.integration.test_p023_d_tick_driver_through_runner_postgres import CODE, T1, T2, _stand
from tests.integration.test_p052_complete_clearing_pass_postgres import factory  # noqa: F401
from tests.simulator_tick_stand import RecordingSse


def _assert_complete(done, pairs):
    patches = {(p["source"], p["target"]): p for p in done.get("edge_patch") or []}
    assert set(patches) == pairs, f"committed clearing must patch all touched lines: actual={set(patches)}, expected={pairs}"
    assert all(Decimal(p["used"]) == 0 for p in patches.values()), patches


@MODE_B
@pytest.mark.asyncio
async def test_signed_close_manual_clearing_accounts_for_all_150_before_removal(client, db_session, monkeypatch):
    code, people, lines, factory = await _requested(client, db_session)
    assert await _pay(factory, people["A"], people["C"], code, "50")
    assert await _pay(factory, people["C"], people["B"], code, "50")
    assert sum((await _debts(factory, code)).values()) == Decimal("150")
    run, sse = _run(code, people), RecordingSse()
    monkeypatch.setattr(settings, "SIMULATOR_ACTIONS_ENABLE", True)
    monkeypatch.setitem(simulator_module.runtime._runs, run.run_id, run)
    original_dispatch = simulator_module.runtime._sse._dispatch_locked

    def record_dispatch(*, run, payload):
        sse.broadcast(run.run_id, payload)
        original_dispatch(run=run, payload=payload)

    monkeypatch.setattr(simulator_module.runtime._sse, "_dispatch_locked", record_dispatch)
    response = await client.post(f"/api/v1/simulator/runs/{run.run_id}/actions/clearing-real",
        headers={"X-Admin-Token": settings.ADMIN_TOKEN}, json={"equivalent": code})
    assert response.status_code == 200, response.text
    assert response.json()["cleared_cycles"] == 1
    assert await _debts(factory, code) == {}
    assert (await _line(client, db_session, people["A"], lines["AB"]))["status"] == "closed"
    done, removed = sse.events
    assert [done["type"], removed["type"]] == ["clearing.done", "topology.changed"]
    assert removed["payload"]["removed_edges"] == [{
        "from_pid": people["A"]["pid"], "to_pid": people["B"]["pid"], "equivalent_code": code, "limit": None,
    }]
    pairs = {(people[a]["pid"], people[b]["pid"]) for a, b in ("AB", "BC", "CA")}
    # A fresh actual producer artifact can be consumed by the focused UI test via
    # P053_CLEARING_WIRE; it is evidence under .local-run, never a fixture copy.
    output = Path(os.environ.get("GEO_TEST_ARTIFACT_ROOT") or ".local-run/test-runs/direct-pytest/artifacts") / "p053/manual.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"equivalent": code, "pairs": sorted(pairs), "events": sse.events}), encoding="utf-8")
    _assert_complete(done, pairs)
    assert Decimal(done["cleared_amount"]) == 50  # occurrence amount != total debt drop
    assert (people["A"]["pid"], people["B"]["pid"]) not in run._edges_by_equivalent[code]


@pytest.mark.parametrize("surface", ["periodic", "scripted", "cancel-before-publication", "cancel-next-occurrence", "failure-next-occurrence"])
@pytest.mark.asyncio
async def test_tick_closed_line_patches_describe_only_durable_progress(factory, monkeypatch, surface):
    stand = await _stand(factory, T1 + T2)
    async with factory() as session:
        await session.execute(update(TrustLine).values(limit=0, close_requested_at=datetime.now(timezone.utc)))
        await session.commit()
    reached = asyncio.Event()
    occurrences = []
    real_execute = ClearingService.execute_occurrence

    async def execute(self, occurrence, **kwargs):
        occurrences.append(occurrence)
        if len(occurrences) == 2:
            if surface == "failure-next-occurrence":
                raise OSError("second occurrence refused before writing")
            if surface == "cancel-next-occurrence":
                reached.set()
                await asyncio.Event().wait()
        return await real_execute(self, occurrence, **kwargs)

    monkeypatch.setattr(ClearingService, "execute_occurrence", execute)
    real_patches = stand.runner._tick._clearing_patches

    async def patches(*args):
        if surface == "cancel-before-publication" and not reached.is_set():
            reached.set()
            await asyncio.Event().wait()
        return await real_patches(*args)

    monkeypatch.setattr(stand.runner._tick, "_clearing_patches", patches)
    if surface.startswith("cancel"):
        task = asyncio.create_task(stand.tick())
        await asyncio.wait_for(reached.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    elif surface == "scripted":
        notes = []
        monkeypatch.setattr(stand.runner._inject_executor, "_artifacts", SimpleNamespace(
            enqueue_event_artifact=lambda _run_id, payload: notes.append(payload)))
        async with factory() as session:
            _, attempted = await stand.runner._tick.run_scripted_clearings(
                session=session, run_id=stand.run.run_id, run=stand.run,
                scenario={"events": [{"time": 0, "type": "clearing", "equivalent": CODE}]},
                equivalents=[CODE], planned_len=0, tick_t0=time.monotonic(), payments_result=None)
        assert attempted == {CODE} and len(notes) == 1
        assert 0 in stand.run._real_fired_scenario_event_indexes
    else:
        await stand.tick()
    async with factory() as session:
        rows = (await session.execute(select(TrustLine.from_participant_id, TrustLine.to_participant_id, TrustLine.status))).all()
    pids = dict(stand.run._real_participants)
    closed = {(pids[a], pids[b]) for a, b, status in rows if status == "closed"}
    assert len(closed) == (3 if surface in {"cancel-next-occurrence", "failure-next-occurrence"} else 6)
    [done] = stand.done_events()
    _assert_complete(done, closed)
    [removed] = [e for e in stand.sse.events if e.get("reason") == "trustline_closed"]
    assert {(e["from_pid"], e["to_pid"]) for e in removed["payload"]["removed_edges"]} == closed
    assert stand.sse.events.index(done) < stand.sse.events.index(removed)
    assert not closed.intersection(stand.run._edges_by_equivalent[CODE])
    assert stand.run._real_clearing_task is None
    if len(closed) == 3:
        assert await stand.total() in {Decimal("6"), Decimal("9")}, "uncommitted debt must stay nonzero"
    else:
        assert await stand.total() == 0
