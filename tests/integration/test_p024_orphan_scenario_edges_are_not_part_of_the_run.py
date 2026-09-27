"""Programme 024, stage 0, external review P2: an edge whose end is not a scenario participant is not
part of the run.

The scenario schema checks a trustline's ends only syntactically, not for membership in
`participants`. The seeder skips such an edge (its ends do not resolve among the scenario's
participants), but `scenario_to_snapshot` kept it, and `actions/trustlines-list` resolved the ends of
every snapshot link GLOBALLY and read `Debt` for them - so a scenario naming two real pids only in
an edge returned their real debt as `reverse_used`. One rule, one owner: `scenario_to_snapshot`
drops the orphan edge (with a warning), and `trustlines-list` resolves only perimeter pids, so the
rule holds even without the snapshot.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

import app.core.simulator.storage as simulator_storage
from app.core.auth.crypto import generate_keypair, get_pid_from_public_key
from app.core.simulator.models import RunRecord
from app.core.simulator.real_scenario_seeder import simulated_public_key
from app.core.simulator.snapshot_builder import scenario_to_snapshot
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine
from tests.debt_setup import debt_fixture_setup


def _real() -> Participant:
    public_key, _private = generate_keypair()
    return Participant(
        pid=get_pid_from_public_key(public_key), display_name="real", public_key=public_key,
        type="person", status="active", profile={},
    )


def _simulated(pid: str) -> Participant:
    return Participant(
        pid=pid, display_name=pid, public_key=simulated_public_key(pid),
        type="person", status="active", profile={},
    )


def _line(creditor: Participant, debtor: Participant, eq: Equivalent) -> TrustLine:
    return TrustLine(
        from_participant_id=creditor.id, to_participant_id=debtor.id, equivalent_id=eq.id,
        limit=Decimal("500"), status="active", policy={"auto_clearing": True},
    )


async def _world(db_session):
    n = uuid.uuid4().hex[:8].upper()
    eq = Equivalent(code=f"P24O{n}", precision=2, is_active=True, metadata_={})
    s1, s2 = _simulated(f"p024_s1_{n}"), _simulated(f"p024_s2_{n}")
    r1, r2 = _real(), _real()
    db_session.add_all([eq, s1, s2, r1, r2])
    await db_session.flush()
    db_session.add_all([_line(r1, r2, eq), _line(s1, s2, eq)])
    debts = [
        # reverse_used of edge from->to is Debt(debtor=from, creditor=to)
        Debt(debtor_id=r1.id, creditor_id=r2.id, equivalent_id=eq.id, amount=Decimal("321")),
        Debt(debtor_id=s1.id, creditor_id=s2.id, equivalent_id=eq.id, amount=Decimal("7")),
    ]
    async with debt_fixture_setup(db_session, label="setup"):
        db_session.add_all(debts)
    await db_session.commit()
    scenario = {
        "equivalents": [eq.code],
        # The real pids appear ONLY as the ends of an edge, never among the participants.
        "participants": [{"id": s1.pid, "type": "person"}, {"id": s2.pid, "type": "person"}],
        "trustlines": [
            {"from": r1.pid, "to": r2.pid, "equivalent": eq.code, "limit": "1"},
            {"from": s1.pid, "to": s2.pid, "equivalent": eq.code, "limit": "500"},
        ],
    }
    return eq, s1, s2, r1, r2, scenario


def test_the_snapshot_drops_an_edge_whose_end_is_not_a_participant(caplog) -> None:
    scenario = {
        "equivalents": ["UAH"],
        "participants": [{"id": "a"}, {"id": "b"}],
        "trustlines": [
            {"from": "a", "to": "b", "equivalent": "UAH", "limit": "1"},
            {"from": "x", "to": "y", "equivalent": "UAH", "limit": "1"},
            {"from": "a", "to": "y", "equivalent": "UAH", "limit": "1"},
        ],
    }

    with caplog.at_level(logging.WARNING):
        snap = scenario_to_snapshot(scenario, equivalent="UAH", utc_now=lambda: datetime.now(timezone.utc))

    assert [(link.source, link.target) for link in snap.links] == [("a", "b")]
    assert any("orphan" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_trustlines_list_does_not_read_an_orphan_edge(client, db_session, monkeypatch) -> None:
    import app.api.v1.simulator as simulator_module

    monkeypatch.setenv("SIMULATOR_ACTIONS_ENABLE", "1")
    monkeypatch.setattr(simulator_storage, "db_enabled", lambda: True)
    eq, s1, s2, r1, r2, scenario = await _world(db_session)
    client.cookies.clear()
    owner_id = (await client.post("/api/v1/simulator/session/ensure")).json()["owner_id"]
    run_id = f"p024-orphan-{uuid.uuid4().hex[:6]}"
    registered = RunRecord(run_id=run_id, scenario_id=run_id, mode="real", state="paused", owner_id=owner_id)
    registered._scenario_raw = scenario
    monkeypatch.setitem(simulator_module.runtime._runs, run_id, registered)
    stub = SimpleNamespace(
        run_id=run_id, scenario_id=run_id, mode="real", state="paused", owner_id=owner_id,
        _scenario_raw=scenario, _real_seeded=True, _real_seeding_lock=None,
    )
    monkeypatch.setattr(simulator_module.runtime, "get_run", lambda _rid: stub)

    listed = await client.get(
        f"/api/v1/simulator/runs/{run_id}/actions/trustlines-list", params={"equivalent": eq.code}
    )
    assert listed.status_code == 200, listed.text
    items = {(i["from_pid"], i["to_pid"]): i for i in listed.json()["items"]}
    # The real pair's debt first, so a regression reports the leaked amount itself.
    assert "321" not in listed.text, listed.text
    assert (r1.pid, r2.pid) not in items, items
    # Counter-check (anti-vacuum): the run's own edge is listed with its reverse debt.
    assert Decimal(items[(s1.pid, s2.pid)]["reverse_used"]) == Decimal("7"), items

    snap = await client.get(f"/api/v1/simulator/runs/{run_id}/graph/snapshot", params={"equivalent": eq.code})
    assert snap.status_code == 200, snap.text
    assert {(ln["source"], ln["target"]) for ln in snap.json()["links"]} == {(s1.pid, s2.pid)}
