"""Programme 024, stage 0, external review P2: `payment-targets` reads only the run's graph.

`GET /simulator/runs/{run_id}/payment-targets` resolved `from_pid` from the global participant table
and built the routing graph of the WHOLE equivalent, so the owner of any run - an anonymous visitor
included - learned the neighbours, hop counts and available capacity of real participants, and saw
targets reachable only THROUGH them. The read is now confined like the money path: `from_pid` is
resolved within the run perimeter, and the router instance is narrowed with the payment service's
own `_confine_router_to_perimeter` (the mechanism behind `allowed_participant_pids`) before any
target or max-flow is computed.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.core.auth.crypto import generate_keypair, get_pid_from_public_key
from app.core.payments.router import PaymentRouter
from app.core.simulator.models import RunRecord
from app.core.simulator.real_scenario_seeder import simulated_public_key
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine


def _simulated(pid: str) -> Participant:
    return Participant(
        pid=pid, display_name=pid, public_key=simulated_public_key(pid),
        type="person", status="active", profile={},
    )


def _real() -> Participant:
    public_key, _private = generate_keypair()
    return Participant(
        pid=get_pid_from_public_key(public_key), display_name="real", public_key=public_key,
        type="person", status="active", profile={},
    )


def _both_ways(a: Participant, b: Participant, eq: Equivalent) -> list[TrustLine]:
    return [
        TrustLine(
            from_participant_id=u.id, to_participant_id=v.id, equivalent_id=eq.id,
            limit=Decimal("100"), status="active", policy={"can_be_intermediate": True},
        )
        for u, v in ((a, b), (b, a))
    ]


@pytest.fixture
def run_world(monkeypatch):
    import app.api.v1.simulator as simulator_module

    monkeypatch.setattr("app.config.settings.SIMULATOR_ACTIONS_ENABLE", True)

    def install(owner_id: str, scenario: dict) -> str:
        run_id = f"p024-targets-{uuid.uuid4().hex[:6]}"
        registered = RunRecord(run_id=run_id, scenario_id=run_id, mode="real", state="paused", owner_id=owner_id)
        registered._scenario_raw = scenario
        monkeypatch.setitem(simulator_module.runtime._runs, run_id, registered)
        stub = SimpleNamespace(
            run_id=run_id, scenario_id=run_id, mode="real", state="paused", owner_id=owner_id,
            _scenario_raw=scenario, _real_seeded=True, _real_seeding_lock=None,
        )
        monkeypatch.setattr(simulator_module.runtime, "get_run", lambda _rid: stub)
        return run_id

    return install


async def _world(db_session):
    n = uuid.uuid4().hex[:8].upper()
    eq = Equivalent(code=f"P24T{n}", precision=2, is_active=True, metadata_={})
    x, y, z = (_simulated(f"p024_{k}_{n}") for k in ("x", "y", "z"))
    a, b = _real(), _real()
    db_session.add_all([eq, x, y, z, a, b])
    await db_session.flush()
    # X - A - Y: Y is reachable from X only THROUGH the real A.  X - Z is a direct run edge.
    # A - B: a real pair with capacity the run must not see.
    db_session.add_all(_both_ways(x, a, eq) + _both_ways(a, y, eq) + _both_ways(x, z, eq) + _both_ways(a, b, eq))
    await db_session.commit()
    PaymentRouter.invalidate_cache(eq.code)
    scenario = {
        "equivalents": [eq.code],
        "participants": [{"id": p.pid, "type": "person"} for p in (x, y, z)],
        "trustlines": [{"from": x.pid, "to": z.pid, "equivalent": eq.code, "limit": "100"}],
    }
    return eq, x, y, z, a, b, scenario


async def _anonymous_owner(client) -> str:
    client.cookies.clear()
    ensured = await client.post("/api/v1/simulator/session/ensure")
    assert ensured.status_code == 200, ensured.text
    return ensured.json()["owner_id"]


@pytest.mark.asyncio
async def test_a_real_participant_is_not_a_payment_source_of_the_run(client, db_session, run_world) -> None:
    eq, x, y, z, a, b, scenario = await _world(db_session)
    run_id = run_world(await _anonymous_owner(client), scenario)

    response = await client.get(
        f"/api/v1/simulator/runs/{run_id}/payment-targets",
        params={"equivalent": eq.code, "from_pid": a.pid, "include_max_available": "true"},
    )

    assert 400 <= response.status_code < 500, response.text
    assert b.pid not in response.text
    assert x.pid not in response.text and y.pid not in response.text


@pytest.mark.asyncio
async def test_targets_neither_include_nor_pass_through_real_participants(
    client, db_session, run_world
) -> None:
    eq, x, y, z, a, b, scenario = await _world(db_session)
    run_id = run_world(await _anonymous_owner(client), scenario)

    response = await client.get(
        f"/api/v1/simulator/runs/{run_id}/payment-targets",
        params={"equivalent": eq.code, "from_pid": x.pid, "include_max_available": "true"},
    )

    assert response.status_code == 200, response.text
    targets = {item["to_pid"]: item for item in response.json()["items"]}
    # Counter-check (anti-vacuum): the run's own direct edge is still a target, with capacity.
    assert z.pid in targets, targets
    assert Decimal(targets[z.pid]["max_available"]) > 0
    # No real participant as a target, and no run participant reachable only through one.
    assert a.pid not in targets and b.pid not in targets, targets
    assert y.pid not in targets, targets
