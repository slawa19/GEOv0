"""Programme 024, stage 0 fix-delta B1: the graph views never read a real participant's state.

`SnapshotBuilder._enrich_snapshot_from_db` enriched a snapshot from the database by the pids the
SCENARIO names, whoever created those rows. An anonymous visitor could upload a scenario naming real
participants (pids are public) and read their trust limits, debts and net balances through
`GET /scenarios/{id}/graph/preview?mode=real`, and through `graph/snapshot` / `graph/ego` of a run
left paused after the seeding refusal (F-024-4b). The rule is the run perimeter's
(`real_scenario_seeder.simulated_public_key`): only a row the simulator created is read; a real one
is treated as absent, before the `Debt`/`TrustLine` queries.
"""

from __future__ import annotations

import logging
import threading
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

import app.core.simulator.storage as simulator_storage
from app.config import settings
from app.core.auth.crypto import generate_keypair, get_pid_from_public_key
from app.core.simulator.models import RunRecord
from app.core.simulator.real_scenario_seeder import simulated_public_key
from app.core.simulator.runtime import runtime
from app.core.simulator.scenario_registry import ScenarioRegistry
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine
from tests.debt_setup import debt_fixture_setup

REPO_ROOT = Path(__file__).resolve().parents[2]
ORIGIN = {"Origin": "http://localhost:5176"}


def _real(tag: str) -> Participant:
    public_key, _private = generate_keypair()
    return Participant(
        pid=get_pid_from_public_key(public_key), display_name=tag, public_key=public_key,
        type="person", status="active", profile={},
    )


def _simulated(pid: str) -> Participant:
    return Participant(
        pid=pid, display_name=pid, public_key=simulated_public_key(pid),
        type="person", status="active", profile={},
    )


@pytest.fixture
def world(monkeypatch, tmp_path: Path):
    # The registry writes into the dict `runtime.get_scenario` reads, a copy restored on teardown.
    scenarios = dict(runtime._scenarios)
    monkeypatch.setattr(runtime, "_scenarios", scenarios)
    registry = ScenarioRegistry(
        lock=threading.RLock(),
        scenarios=scenarios,
        fixtures_dir=tmp_path / "fixtures",
        schema_path=REPO_ROOT / "fixtures" / "simulator" / "scenario.schema.json",
        local_state_dir=tmp_path / "state",
        utc_now=lambda: datetime.now(timezone.utc),
        logger=logging.getLogger(__name__),
    )
    monkeypatch.setattr(runtime, "_scenario_registry", registry)
    monkeypatch.setattr(settings, "SIMULATOR_CSRF_ORIGIN_ALLOWLIST", ORIGIN["Origin"])
    monkeypatch.setattr(simulator_storage, "db_enabled", lambda: True)


async def _seed(db_session):
    n = uuid.uuid4().hex[:8].upper()
    eq = Equivalent(code=f"P24V{n}", precision=2, is_active=True, metadata_={})
    r1, r2 = _real("R1"), _real("R2")
    s1, s2 = _simulated(f"p024_s1_{n}"), _simulated(f"p024_s2_{n}")
    db_session.add_all([eq, r1, r2, s1, s2])
    await db_session.flush()
    for creditor, debtor in ((r1, r2), (s1, s2)):
        db_session.add(
            TrustLine(
                from_participant_id=creditor.id, to_participant_id=debtor.id, equivalent_id=eq.id,
                limit=Decimal("500"), status="active", policy={"auto_clearing": True},
            )
        )
    debts = [
        Debt(debtor_id=r2.id, creditor_id=r1.id, equivalent_id=eq.id, amount=Decimal("123")),
        Debt(debtor_id=s2.id, creditor_id=s1.id, equivalent_id=eq.id, amount=Decimal("45")),
    ]
    async with debt_fixture_setup(db_session, label="setup"):
        db_session.add_all(debts)
    await db_session.commit()
    return eq, r1, r2, s1, s2


def _scenario(scenario_id: str, eq: Equivalent, pairs) -> dict:
    pids = [p.pid for pair in pairs for p in pair]
    return {
        "schema_version": "scenario/1",
        "scenario_id": scenario_id,
        "equivalents": [eq.code],
        "participants": [{"id": pid, "type": "person"} for pid in pids],
        "trustlines": [
            {"from": c.pid, "to": d.pid, "equivalent": eq.code, "limit": "1"} for c, d in pairs
        ],
    }


def _assert_views(snap: dict, real: tuple, simulated: tuple) -> None:
    nodes = {n["id"]: n for n in snap["nodes"]}
    links = {(ln["source"], ln["target"]): ln for ln in snap["links"]}
    r1, r2 = real
    s1, s2 = simulated
    for p in real:
        assert nodes[p.pid].get("net_balance") is None, nodes[p.pid]
        assert nodes[p.pid].get("net_balance_atoms") is None, nodes[p.pid]
    # The real pair keeps only what the SCENARIO says (limit "1", nothing used); the database's
    # limit 500, debt 123 and availability 377 must not appear.
    real_link = links[(r1.pid, r2.pid)]
    assert Decimal(str(real_link.get("used") or 0)) == 0, real_link
    assert Decimal(str(real_link.get("trust_limit"))) == Decimal("1"), real_link
    assert Decimal(str(real_link.get("available") or 0)) <= Decimal("1"), real_link
    # Counter-check (anti-vacuum): simulator-created rows are enriched as before.
    sim_link = links[(s1.pid, s2.pid)]
    assert Decimal(sim_link["used"]) == Decimal("45"), sim_link
    assert Decimal(sim_link["trust_limit"]) == Decimal("500"), sim_link
    assert Decimal(nodes[s1.pid]["net_balance"]) == Decimal("45"), nodes[s1.pid]


@pytest.mark.asyncio
async def test_anonymous_real_preview_does_not_read_real_participants(client, db_session, world) -> None:
    eq, r1, r2, s1, s2 = await _seed(db_session)
    client.cookies.clear()
    ensured = await client.post("/api/v1/simulator/session/ensure")
    assert ensured.json()["actor_kind"] == "anon"
    scenario_id = f"p024-leak-{uuid.uuid4().hex[:8]}"
    up = await client.post(
        "/api/v1/simulator/scenarios", headers=ORIGIN,
        json={"scenario": _scenario(scenario_id, eq, [(r1, r2), (s1, s2)])},
    )
    assert up.status_code == 200, up.text

    preview = await client.get(
        f"/api/v1/simulator/scenarios/{scenario_id}/graph/preview",
        params={"equivalent": eq.code, "mode": "real"},
    )

    assert preview.status_code == 200, preview.text
    _assert_views(preview.json(), (r1, r2), (s1, s2))


@pytest.mark.asyncio
async def test_run_snapshot_and_ego_do_not_read_real_participants(
    client, db_session, world, monkeypatch
) -> None:
    eq, r1, r2, s1, s2 = await _seed(db_session)
    client.cookies.clear()
    ensured = await client.post("/api/v1/simulator/session/ensure")
    owner_id = ensured.json()["owner_id"]
    run_id = f"p024-paused-{uuid.uuid4().hex[:8]}"
    # A real run left paused after the seeding refusal: its scenario still names the real pids.
    run = RunRecord(run_id=run_id, scenario_id=run_id, mode="real", state="paused", owner_id=owner_id)
    run._scenario_raw = _scenario(run_id, eq, [(r1, r2), (s1, s2)])
    monkeypatch.setitem(runtime._runs, run_id, run)
    monkeypatch.setattr(runtime, "get_active_run_id", lambda owner_id="": run_id)

    snap = await client.get(f"/api/v1/simulator/runs/{run_id}/graph/snapshot", params={"equivalent": eq.code})
    assert snap.status_code == 200, snap.text
    _assert_views(snap.json(), (r1, r2), (s1, s2))

    active = await client.get("/api/v1/simulator/graph/snapshot", params={"equivalent": eq.code})
    assert active.status_code == 200, active.text
    _assert_views(active.json(), (r1, r2), (s1, s2))

    ego = await client.get(
        "/api/v1/simulator/graph/ego", params={"equivalent": eq.code, "pid": r1.pid, "depth": 1}
    )
    assert ego.status_code == 200, ego.text
    ego_links = {(ln["source"], ln["target"]): ln for ln in ego.json()["links"]}
    assert (r1.pid, r2.pid) in ego_links, ego.json()  # premise: the ego view did include the pair
    assert Decimal(str(ego_links[(r1.pid, r2.pid)].get("used") or 0)) == 0
    assert Decimal(str(ego_links[(r1.pid, r2.pid)].get("trust_limit"))) == Decimal("1")
    for node in ego.json()["nodes"]:
        if node["id"] in (r1.pid, r2.pid):
            assert node.get("net_balance") is None, node
