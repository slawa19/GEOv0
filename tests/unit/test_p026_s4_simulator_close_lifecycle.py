"""026 S4 (`T2603.2`): the simulator shows a requested close as a request and a completed one only after its commit.

THE RULE (owner В1, 2026-09-29; spec 026, inventory rows "Simulator close topology" and "Simulator schemas"). A
close with debt is a REQUEST - limit 0 and `close_requested_at` - and the line stays live: the Interact answer, the
SSE patch, `trustlines-list` and the run snapshot all carry the request, and none of them removes the edge. The
line leaves the run's topology (scenario, edge cache, `topology.changed.removed_edges`) when a money operation
brings the debt it supports to 0 and that operation's commit is CONFIRMED - here the Interact payment and the
tick's clearing step; the tick's payments and the commit/rollback/unknown outcomes are the PostgreSQL stand
`tests/integration/test_p026_s4_tick_close_publication_postgres.py`. A line closed outside the run is not
resurrected by the run's scenario, which still lists it.

Mode A (the fixture's transaction), the SSE emitter replaced by a recorder of what the handler hands it. Debts of
the stand are the fixture's within the limit; the request comes only from the Interact close (Verification plan §4).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

import app.api.v1.simulator as simulator_module
from app.core.simulator.real_scenario_seeder import simulated_public_key
from app.core.trustlines.service import TrustLineService
from app.db.models.trustline import TrustLine
from app.schemas.trustline import TrustLineCloseRequest
from tests.p019_support import TargetMismatch, require_target
from tests.unit.test_p021_interact_trust_line_actions_wire import (  # noqa: F401 - `stand` is a fixture
    HEADERS,
    TRIPLE,
    _debt,
    _post,
    _Recorder,
    stand,
)

_XFAIL = pytest.mark.xfail(raises=TargetMismatch, strict=True, reason="026 target, delivered by T2603.2")


class _AllEvents(_Recorder):
    """The wire recorder, plus the money events the payment action publishes before any removal."""

    def emit_tx_updated(self, *, run_id, run, equivalent, **_kwargs):
        type(self).events.append({"type": "tx.updated", "equivalent": equivalent, "payload": {}})


def _ts(value) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


async def _requested(client, stand) -> tuple[TrustLine, dict]:
    """alice -> bob 10, bob owes alice 7 (fixture, within the limit), then the Interact close: a request."""

    db, alice, bob = stand["db"], stand["alice"], stand["bob"]
    for p in (alice, bob):  # the run snapshot reads DB state only for simulator-created rows
        p.public_key = simulated_public_key(p.pid)
    await db.commit()
    assert (await _post(client, "trustline-create", {**TRIPLE, "limit": "10"})).status_code == 200
    await _debt(db, debtor=bob, creditor=alice, eq=stand["uah"], amount="7")
    _Recorder.events.clear()
    r = await _post(client, "trustline-close", dict(TRIPLE))
    assert r.status_code == 200, r.text
    line = await db.get(TrustLine, uuid.UUID(r.json()["trustline_id"]))
    await db.refresh(line)
    assert line.status == "active" and line.limit == 0 and line.close_requested_at is not None  # S3 delivered it
    return line, r.json()


@_XFAIL
@pytest.mark.asyncio
async def test_a_close_with_debt_is_a_request_on_every_projection(client, stand) -> None:
    line, answer = await _requested(client, stand)
    asked = line.close_requested_at
    [event] = _Recorder.events
    assert event["reason"] == "interact.trustline_update" and not event["payload"].get("removed_edges"), event
    [patch] = event["payload"]["edge_patch"]
    listed = await client.get(f"/api/v1/simulator/runs/{stand['run'].run_id}/actions/trustlines-list",
                              headers=HEADERS, params={"equivalent": "UAH"})
    assert listed.status_code == 200, listed.text
    [item] = listed.json()["items"]
    snap = await simulator_module.runtime.build_graph_snapshot(run_id="wire-run", equivalent="UAH", session=stand["db"])
    [link] = snap.links
    assert (Decimal(item["limit"]), Decimal(item["available"]), patch["trust_limit"]) == (0, -7, "0.00"), (item, patch)

    seen = {"answer": answer, "patch": patch, "list": item, "snapshot": link.model_dump(mode="json")}
    require_target(
        answer.get("status") == "active"
        and all(_ts(view.get("close_requested_at")) == asked for view in seen.values()),
        f"a requested close (asked {asked.isoformat()}) is not carried by every projection: {seen}",
    )


@_XFAIL
@pytest.mark.asyncio
async def test_a_payment_that_completes_the_close_removes_the_edge_after_its_commit(client, stand, monkeypatch) -> None:
    monkeypatch.setattr(simulator_module, "SseEventEmitter", _AllEvents)
    (line, _), db, run = await _requested(client, stand), stand["db"], stand["run"]
    _Recorder.events.clear()

    r = await _post(client, "payment-real", {**TRIPLE, "amount": "7"})
    assert r.status_code == 200, r.text
    await db.refresh(line)
    assert line.status == "closed", "the book did not complete the close (S3 control)"
    assert [e["type"] for e in _Recorder.events if "type" in e] == ["tx.updated"]

    removals = [e["payload"]["removed_edges"] for e in _Recorder.events if e["payload"].get("removed_edges")]
    snap = await simulator_module.runtime.build_graph_snapshot(run_id="wire-run", equivalent="UAH", session=db)
    require_target(
        removals == [[{"from_pid": "alice", "to_pid": "bob", "equivalent_code": "UAH", "limit": None}]]
        and run._scenario_raw["trustlines"] == [] and run._edges_by_equivalent["UAH"] == [] and not snap.links,
        f"after the committed payment closed alice -> bob: removals {removals}, scenario "
        f"{run._scenario_raw['trustlines']}, cache {run._edges_by_equivalent['UAH']}, "
        f"snapshot {[(x.source, x.target, x.status, x.trust_limit) for x in snap.links]}",
    )


@_XFAIL
@pytest.mark.asyncio
async def test_the_run_snapshot_does_not_resurrect_a_line_closed_outside_the_run(client, stand) -> None:
    db, alice, bob, run = stand["db"], stand["alice"], stand["bob"], stand["run"]
    for p in (alice, bob):
        p.public_key = simulated_public_key(p.pid)
    await db.commit()
    created = await _post(client, "trustline-create", {**TRIPLE, "limit": "10"})
    assert created.status_code == 200, created.text
    # Closed by the trust-line service directly - the run's scenario still lists alice -> bob with limit 10.
    service = TrustLineService(db)
    batch = service.begin_internal_batch()
    await service.execute_close(batch, uuid.UUID(created.json()["trustline_id"]), alice.id,
                                TrustLineCloseRequest(signature="__internal__"), require_signature=False)
    await batch.finish()
    await db.commit()
    assert run._scenario_raw["trustlines"][0]["limit"] == "10"

    snap = await simulator_module.runtime.build_graph_snapshot(run_id="wire-run", equivalent="UAH", session=db)
    listed = await client.get(f"/api/v1/simulator/runs/{run.run_id}/actions/trustlines-list",
                              headers=HEADERS, params={"equivalent": "UAH"})
    assert listed.status_code == 200, listed.text
    require_target(
        not snap.links and listed.json()["items"] == [],
        f"a closed line came back from the scenario: {[(x.source, x.target, x.status, x.trust_limit) for x in snap.links]}"
        f", list {listed.json()['items']}",
    )


@_XFAIL
@pytest.mark.asyncio
async def test_the_tick_clearing_removes_what_its_commits_closed_once(monkeypatch) -> None:
    from app.core.clearing.runner import ClearingPassResult, CommittedEdge, CommittedOccurrence
    from app.core.simulator.models import RunRecord
    from tests.simulator_tick_stand import RecordingSse, clearing_unit_tick
    from tests.unit.test_tick_clearing_publishes_progress import ALICE, BOB, _SessionContext, _VizHelper

    run = RunRecord(run_id="s4-clearing", scenario_id="s", mode="real", state="running")
    run._real_viz_by_eq["USD"] = _VizHelper()
    run._scenario_raw = {"trustlines": [{"equivalent": "USD", "from": "bob", "to": "alice", "limit": "0"}]}
    run._edges_by_equivalent = {"USD": [("bob", "alice")]}
    run._real_participants = [(ALICE, "alice"), (BOB, "bob")]
    sse = RecordingSse()

    async def _pass(_factory, equivalent, *, allowed_participant_pids, on_committed, deadline):
        occurrence = CommittedOccurrence(occurrence_id="o", plan_id=uuid.uuid4(), ordinal=0, amount_atoms=5 * 10**8,
                                         edges=(CommittedEdge(uuid.uuid4(), ALICE, BOB),))
        on_committed(occurrence)
        return ClearingPassResult(equivalent=equivalent, status="complete", reason=None, committed=(occurrence,),
                                  remaining_cycles=0, remaining_v_edge_atoms=0, plans=1, distributed_exclusive=False)

    async def _patches(*, edges_pairs, closed=None, **_kwargs):
        # The builder found no live row of bob -> alice after the commit: the clearing closed it.
        if closed is not None:
            closed.update(edges_pairs)
        return []

    async def _growth(**_kwargs):
        return SimpleNamespace(updated_count=0)

    tick = clearing_unit_tick(monkeypatch, sse=sse, session_factory=lambda: _SessionContext(), runner_pass=_pass,
                              apply_trust_growth=_growth,
                              edge_patch_builder=SimpleNamespace(build_edge_patch_for_pairs=_patches))
    for _ in range(2):  # a second clearing that touches the same, already removed, edge publishes nothing more
        await tick._run_clearing(session=None, run_id=run.run_id, run=run, equivalents=["USD"], committed={})

    assert sse.published("clearing.done") == 2
    removals = [e["payload"]["removed_edges"] for e in sse.events if e["type"] == "topology.changed"]
    require_target(
        removals == [[{"from_pid": "bob", "to_pid": "alice", "equivalent_code": "USD", "limit": None}]]
        and run._edges_by_equivalent["USD"] == [] and run._scenario_raw["trustlines"] == [],
        f"the clearing's closed line: removals {removals}, cache {run._edges_by_equivalent}",
    )
