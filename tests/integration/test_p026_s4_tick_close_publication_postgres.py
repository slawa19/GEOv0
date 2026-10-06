"""026 S4 (`T2603.2`): the tick and Interact clearing publish a closed line only after the commit that closed it.

THE CLAIM. The book closes a requested line inside the money transaction (S3); the simulator removes it from the
run's topology - scenario, edge cache, `topology.changed.removed_edges` - only once that transaction's commit is
CONFIRMED, and once. Rolled back, of unknown outcome, or a superseded replay attempt: nothing is published and the
run keeps the edge (the snapshot reads the database and shows the truth either way).

THE STAND. Real PostgreSQL (mode B): debts by real payments, the request by the signed `DELETE`. The tick's money
phase is the production `RealPaymentsExecutor` on its own session; its outcome is decided by the test - `commit`,
or a rollback resolved as `rollback`, `unknown` or `discard` (a replayed attempt) - and the publication is the
production `DeferredRealPaymentEffects` resolution. Interact clearing is the production route and runner.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

import pytest
from nacl.signing import SigningKey

import app.api.v1.simulator as simulator_module
from app.config import settings
from app.core.simulator.edge_patch_builder import EdgePatchBuilder
from app.core.simulator.models import RunRecord
from app.core.simulator.real_payments_executor import RealPaymentsExecutor
from tests.conftest import MODE_B
from tests.integration.test_p026_s2_limit_below_used_postgres import _debts, _pay, _world
from tests.integration.test_p026_s3_close_request_postgres import _close, _line
from tests.integration.test_scenarios import _sign_trustline_create_request
from tests.p019_support import require_target
from tests.simulator_tick_stand import RecordingSse

_LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Planned:
    seq: int
    equivalent: str
    sender_pid: str
    receiver_pid: str
    amount: str


def _run(code: str, p: dict) -> RunRecord:
    run = RunRecord(run_id="s4-" + uuid.uuid4().hex[:8], scenario_id="s4", mode="real", state="running")
    pairs = [(p[x]["pid"], p[y]["pid"]) for x, y in ("AB", "BC", "CA")]
    run._scenario_raw = {
        "participants": [{"id": p[k]["pid"], "name": k, "type": "person", "status": "active"} for k in "ABC"],
        "trustlines": [{"equivalent": code, "from": a, "to": b, "limit": "100"} for a, b in pairs],
    }
    run._edges_by_equivalent = {code: list(pairs)}
    run._real_participants = [(p[k]["id"], p[k]["pid"]) for k in "ABC"]
    run._real_seeded = True
    return run


async def _requested(client, db_session):
    """B owes A 50 (a real payment); A asks to close A -> B: the request stands (S3)."""

    code, p, lines, factory = await _world(client, db_session)
    assert await _pay(factory, p["B"], p["A"], code, "50")
    r = await _close(client, p["A"], lines["AB"])
    assert r.status_code == 200 and r.json()["trustline"]["status"] == "active", r.text
    return code, p, lines, factory


async def _tick_payment(session, run: RunRecord, sse: RecordingSse, code: str, p: dict):
    """B pays A 50 through the production tick executor on `session` (outcome left to the caller)."""

    executor = RealPaymentsExecutor(
        lock=threading.RLock(), sse=sse, utc_now=lambda: datetime.now(timezone.utc), logger=_LOG,
        edge_patch_builder=EdgePatchBuilder(logger=_LOG), should_warn_this_tick=lambda *_: False,
        sim_idempotency_key=lambda **_kw: "s4-" + uuid.uuid4().hex)
    a, b = p["A"]["pid"], p["B"]["pid"]
    return await executor.execute_planned_payments(
        session=session, run_id=run.run_id, run=run, planned=[_Planned(0, code, a, b, "50")], equivalents=[code],
        sender_id_by_pid={a: p["A"]["id"]}, max_in_flight=1, max_timeouts_per_tick=0, fail_run=lambda *_: None)


def _removals(events: list[dict]) -> list:
    return [e["payload"]["removed_edges"] for e in events if e.get("type") == "topology.changed"]


@MODE_B
@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["rollback", "unknown", "discard", "commit"])
async def test_the_tick_removes_a_closed_line_only_after_a_confirmed_commit(client, db_session, outcome) -> None:
    code, p, lines, factory = await _requested(client, db_session)
    a, b = p["A"]["pid"], p["B"]["pid"]
    run, sse = _run(code, p), RecordingSse()

    async with factory() as session:
        result = await _tick_payment(session, run, sse, code, p)
        assert result.committed == 1, result
        effects = result.deferred_effects
        assert sse.events == [], "an observation was published before the transaction's outcome"
        if outcome == "commit":
            await session.commit()
            assert effects.apply_after_commit()
            await asyncio.gather(*effects.closed_publications)
        else:
            await session.rollback()
            resolve = {"rollback": effects.apply_after_rollback, "discard": effects.discard,
                       "unknown": effects.apply_after_unknown_transaction_outcome}[outcome]
            assert resolve()
    line = await _line(client, p["A"], lines["AB"])

    if outcome != "commit":  # counter-check: nothing durable, nothing published, the edge stays
        assert line["status"] == "active" and line["close_requested_at"], line
        assert sse.events == [] and (a, b) in run._edges_by_equivalent[code], sse.events
        assert any(t["from"] == a and t["to"] == b for t in run._scenario_raw["trustlines"])
        return

    assert line["status"] == "closed" and await _debts(factory, code) == {}, line
    assert sse.published("tx.updated") == 1
    assert not effects.apply_after_commit(), "the buffer resolved twice"
    require_target(
        _removals(sse.events) == [[{"from_pid": a, "to_pid": b, "equivalent_code": code, "limit": None}]]
        and (a, b) not in run._edges_by_equivalent[code]
        and not any(t["from"] == a and t["to"] == b for t in run._scenario_raw["trustlines"]),
        f"after the confirmed commit that closed {a} -> {b}: events {[e['type'] for e in sse.events]}, removals "
        f"{_removals(sse.events)}, cache {run._edges_by_equivalent[code]}",
    )


@MODE_B
@pytest.mark.asyncio
async def test_an_interact_clearing_that_completes_a_close_removes_the_edge_once(client, db_session, monkeypatch) -> None:
    code, p, lines, factory = await _requested(client, db_session)
    # A owes C 50 and C owes B 50: with B owing A 50, the cycle clears 50 and brings A -> B's supported debt to 0.
    assert await _pay(factory, p["A"], p["C"], code, "50") and await _pay(factory, p["C"], p["B"], code, "50")
    run, sse = _run(code, p), RecordingSse()
    monkeypatch.setattr("app.config.settings.SIMULATOR_ACTIONS_ENABLE", True)
    monkeypatch.setitem(simulator_module.runtime._runs, run.run_id, run)
    monkeypatch.setattr(simulator_module.runtime, "_sse", sse)

    r = await client.post(f"/api/v1/simulator/runs/{run.run_id}/actions/clearing-real",
                          headers={"X-Admin-Token": settings.ADMIN_TOKEN}, json={"equivalent": code})
    assert r.status_code == 200 and r.json()["cleared_cycles"] == 1, r.text
    assert (await _line(client, p["A"], lines["AB"]))["status"] == "closed", "the clearing did not complete the close"
    assert sse.published("clearing.done") == 1
    a, b = p["A"]["pid"], p["B"]["pid"]
    require_target(
        _removals(sse.events) == [[{"from_pid": a, "to_pid": b, "equivalent_code": code, "limit": None}]]
        and (a, b) not in run._edges_by_equivalent[code],
        f"after the committed clearing closed {a} -> {b}: removals {_removals(sse.events)}, "
        f"cache {run._edges_by_equivalent[code]}",
    )


@MODE_B
@pytest.mark.asyncio
async def test_a_late_publication_does_not_erase_a_line_recreated_after_the_close(client, db_session, monkeypatch) -> None:
    """Adversarial S4, cause B: the commit closed A -> B, A re-created it (Interact create mutates the run after its
    commit), and only then were the observations published (a late `_attempt_landed`, money_replay.py)."""

    code, p, lines, factory = await _requested(client, db_session)
    a, b = p["A"]["pid"], p["B"]["pid"]
    run, sse = _run(code, p), RecordingSse()
    monkeypatch.setitem(simulator_module.runtime._runs, run.run_id, run)
    async with factory() as session:
        result = await _tick_payment(session, run, sse, code, p)
        assert result.committed == 1, result
        await session.commit()
    assert (await _line(client, p["A"], lines["AB"]))["status"] == "closed", "the book did not complete the close"

    key = SigningKey(base64.b64decode(p["A"]["priv"]))
    r = await client.post("/api/v1/trustlines", headers=p["A"]["headers"], json={
        "to": b, "equivalent": code, "limit": "30",
        "signature": _sign_trustline_create_request(signing_key=key, to_pid=b, equivalent=code, limit="30")})
    assert r.status_code == 201, r.text
    simulator_module._mutate_runtime_trustline_topology_best_effort(
        run_id=run.run_id, op="create", equivalent=code, from_pid=a, to_pid=b, limit="30")

    effects = result.deferred_effects
    assert effects.apply_after_commit()
    await asyncio.gather(*effects.closed_publications)  # the re-read runs off the commit callback
    assert sse.published("tx.updated") == 1
    require_target(
        _removals(sse.events) == [] and (a, b) in run._edges_by_equivalent[code]
        and any(t["from"] == a and t["to"] == b and t["limit"] == "30" for t in run._scenario_raw["trustlines"]),
        f"the live A -> B re-created before the publication: removals {_removals(sse.events)}, cache "
        f"{run._edges_by_equivalent[code]}, scenario {[t for t in run._scenario_raw['trustlines'] if t['from'] == a]}",
    )


@MODE_B
@pytest.mark.asyncio
async def test_a_line_recreated_after_the_liveness_read_is_not_erased(client, db_session, monkeypatch) -> None:
    """§15 review of S4, P2-1: the re-read is not atomic with the removal - leaving its session can suspend. Here the
    re-create commits and mutates the run AFTER the SELECT found no live row and BEFORE the publisher takes the lock
    (the barrier is placed inside the re-read, after its SELECT); the old publication must not remove the new line."""

    import app.core.simulator.sse_broadcast as sse_broadcast

    code, p, lines, factory = await _requested(client, db_session)
    a, b = p["A"]["pid"], p["B"]["pid"]
    run, sse = _run(code, p), RecordingSse()
    monkeypatch.setitem(simulator_module.runtime._runs, run.run_id, run)
    async with factory() as session:
        result = await _tick_payment(session, run, sse, code, p)
        assert result.committed == 1, result
        await session.commit()
    assert (await _line(client, p["A"], lines["AB"]))["status"] == "closed", "the book did not complete the close"

    read_live = sse_broadcast._live_pairs
    seen: list = []

    async def _recreate_after_the_select(eq, pairs):
        live = await read_live(eq, pairs)  # the SELECT ran: A -> B has no live row
        seen.append(set(live))
        key = SigningKey(base64.b64decode(p["A"]["priv"]))
        r = await client.post("/api/v1/trustlines", headers=p["A"]["headers"], json={
            "to": b, "equivalent": code, "limit": "30",
            "signature": _sign_trustline_create_request(signing_key=key, to_pid=b, equivalent=code, limit="30")})
        assert r.status_code == 201, r.text
        simulator_module._mutate_runtime_trustline_topology_best_effort(
            run_id=run.run_id, op="create", equivalent=code, from_pid=a, to_pid=b, limit="30")
        return live

    monkeypatch.setattr(sse_broadcast, "_live_pairs", _recreate_after_the_select)
    effects = result.deferred_effects
    assert effects.apply_after_commit()
    await asyncio.gather(*effects.closed_publications)
    assert seen == [set()], f"the barrier did not run after a SELECT that found A -> B closed: {seen}"
    require_target(
        _removals(sse.events) == [] and (a, b) in run._edges_by_equivalent[code]
        and any(t["from"] == a and t["to"] == b and t["limit"] == "30" for t in run._scenario_raw["trustlines"]),
        f"the live A -> B re-created after the re-read: removals {_removals(sse.events)}, cache "
        f"{run._edges_by_equivalent[code]}, scenario {[t for t in run._scenario_raw['trustlines'] if t['from'] == a]}",
    )
