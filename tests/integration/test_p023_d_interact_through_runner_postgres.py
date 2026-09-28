"""Programme 023, slice (d): the Interact action `clearing-real` through the common runner (spec decisions 8, 10; R2, R3, R4).

`POST /simulator/runs/{run_id}/actions/clearing-real` loses its own "find, then execute the minimum" loop and calls
the runner in the run's perimeter. Through REAL paths on a disposable clone (`MODE_B`):

* the pass goes through `execute_occurrence` (the plan-scoped v2 occurrence), and every committed occurrence is
  answered with its PIDs in the trust-line direction creditor -> debtor (`SimulatorActionEdgeRef`: `from` is the
  creditor), in the response and in `clearing.done.cycle_edges`;
* `max_depth` is no longer part of the request: the body schema forbids extra fields (`extra="forbid"`), and the
  action family answers schema errors with the flat `400 INVALID_REQUEST` envelope;
* an `interrupted` pass is NOT an unconditional success: the retry budget of one occurrence spent (decision 4,
  `operational_limit`) after a first commit answers `409 CLEARING_INTERRUPTED` with the reason, the progress and
  the remainder, and the progress is published;
* the operator stop after progress keeps this route's existing answer - `409 CONFLICT` with the partial progress.

RED BEFORE THE SWITCH: the route runs its own detector loop through the v1 executor; the spies on the v2 entry
see nothing and a `max_depth` body is accepted.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import update

from app.config import settings
from app.core.clearing.service import ClearingService, RetryableClearingConflictException
from app.core.simulator.models import RunRecord
from app.db.models.equivalent import Equivalent
from tests.conftest import MODE_B, sessionmaker_of
from tests.p020_support import debt_uuid, ring, seed_graph
from tests.p023_support import fresh_read, positive_debt_total, require_target, target_xfail_023

pytestmark = MODE_B

CODE = "PQV"
T1 = ring(["p023va", "p023vb", "p023vc"], ["2", "2", "2"], [debt_uuid(0x23D2, k) for k in range(3)])
T2 = ring(["p023vd", "p023ve", "p023vf"], ["3", "3", "3"], [debt_uuid(0x23D2, 10 + k) for k in range(3)])
PIDS = sorted({e.debtor for e in T1 + T2})
URL = "/api/v1/simulator/runs/p023d-run/actions/clearing-real"
HEADERS = {"X-Admin-Token": settings.ADMIN_TOKEN}


@pytest.fixture
def interact_run(monkeypatch):
    """Interact actions on, and a REAL run in the registry whose perimeter is the six participants."""

    import app.api.v1.simulator as simulator_module

    monkeypatch.setenv("SIMULATOR_ACTIONS_ENABLE", "1")
    monkeypatch.setattr(
        simulator_module.runtime,
        "get_run",
        lambda run_id: SimpleNamespace(
            run_id=str(run_id), state="running", owner_id="", _real_seeded=True, _real_seeding_lock=None
        ),
    )
    run = RunRecord(run_id="p023d-run", scenario_id="p023d", mode="real", state="running")
    run._scenario_raw = {
        "participants": [{"id": pid, "name": pid, "type": "person", "status": "active"} for pid in PIDS],
        "trustlines": [],
    }
    monkeypatch.setitem(simulator_module.runtime._runs, "p023d-run", run)

    emitted: list[dict] = []

    class _Emitter:
        def __init__(self, *, sse, utc_now, logger):
            return None

        def emit_clearing_done(self, **kwargs) -> None:
            emitted.append(kwargs)

    async def _no_patches(**_kwargs):
        return None, None

    monkeypatch.setattr(simulator_module, "SseEventEmitter", _Emitter)
    monkeypatch.setattr(simulator_module, "_compute_viz_patches_best_effort", _no_patches)
    return emitted


def _spy_execute(monkeypatch, before_call):
    real = ClearingService.execute_occurrence
    calls: list = []

    async def spy(self, occurrence, **kwargs):
        calls.append((occurrence, kwargs.get("allowed_participant_pids")))
        await before_call(len(calls))
        return await real(self, occurrence, **kwargs)

    monkeypatch.setattr(ClearingService, "execute_occurrence", spy)
    return calls


async def _noop(_n: int) -> None:
    return None


def _creditor_to_debtor(cycle) -> set[tuple[str, str]]:
    return {(e.creditor, e.debtor) for e in cycle}


@target_xfail_023("(d)", "Interact clears through the runner and answers creditor -> debtor PIDs")
@pytest.mark.asyncio
async def test_interact_clears_through_the_runner_with_creditor_to_debtor_pids(db_session, client, interact_run, monkeypatch) -> None:
    await seed_graph(db_session, CODE, T1 + T2, precision=2)
    calls = _spy_execute(monkeypatch, _noop)

    response = await client.post(URL, headers=HEADERS, json={"equivalent": CODE, "client_action_id": "c1"})

    assert response.status_code == 200, response.text
    body = response.json()
    require_target(len(calls) == 2, f"the route did not go through the runner's occurrences ({len(calls)} calls)")
    assert all(pids == set(PIDS) for _, pids in calls), "the run perimeter reaches every occurrence"
    assert body["ok"] is True and body["cleared_cycles"] == 2 and body["client_action_id"] == "c1"
    assert Decimal(body["total_cleared_amount"]) == Decimal("5")
    got = sorted(
        (Decimal(c["cleared_amount"]), frozenset((e["from"], e["to"]) for e in c["edges"])) for c in body["cycles"]
    )
    assert got == sorted(
        [(Decimal("2"), frozenset(_creditor_to_debtor(T1))), (Decimal("3"), frozenset(_creditor_to_debtor(T2)))]
    ), got
    [done] = interact_run
    assert done["cleared_cycles"] == 2 and Decimal(done["cleared_amount"]) == Decimal("5")
    assert {(e["from"], e["to"]) for e in done["cycle_edges"]} == _creditor_to_debtor(T1) | _creditor_to_debtor(T2)
    assert await fresh_read(db_session, positive_debt_total, CODE) == 0


@target_xfail_023("(d)", "R2: the Interact request has no max_depth")
@pytest.mark.asyncio
async def test_max_depth_is_not_part_of_the_interact_request(db_session, client, interact_run, monkeypatch) -> None:
    await seed_graph(db_session, CODE, T1 + T2, precision=2)
    calls = _spy_execute(monkeypatch, _noop)

    response = await client.post(URL, headers=HEADERS, json={"equivalent": CODE, "max_depth": 6})

    untouched = await fresh_read(db_session, positive_debt_total, CODE) == Decimal("15")
    require_target(
        response.status_code == 400 and response.json().get("code") == "INVALID_REQUEST" and untouched and calls == [],
        f"{response.status_code} {response.text}",
    )


@target_xfail_023("(d)", "R3: an interrupted Interact pass is an explicit outcome, not a success")
@pytest.mark.asyncio
async def test_an_interrupted_pass_is_an_explicit_conflict_with_progress_and_remainder(db_session, client, interact_run, monkeypatch) -> None:
    await seed_graph(db_session, CODE, T1 + T2, precision=2)

    async def before(n: int) -> None:
        if n == 2:
            raise RetryableClearingConflictException()

    calls = _spy_execute(monkeypatch, before)
    response = await client.post(URL, headers=HEADERS, json={"equivalent": CODE})

    body = response.json()
    require_target(response.status_code == 409 and body.get("code") == "CLEARING_INTERRUPTED", f"{response.status_code} {body!r}")
    assert len(calls) == 2
    details = body["details"]
    assert details["reason"] == "operational_limit"
    assert details["partial_cleared_cycles"] == 1 and details["remaining_cycles"] == 1
    assert Decimal(details["partial_cleared_amount"]) in (Decimal("2"), Decimal("3"))
    assert Decimal(details["remaining_v_edge"]) > 0
    [done] = interact_run
    assert done["cleared_cycles"] == 1, "the durable progress is published"


@target_xfail_023("(d)", "the operator stop after progress keeps 409 CONFLICT with the partial progress, through the runner")
@pytest.mark.asyncio
async def test_the_operator_stop_after_progress_keeps_the_conflict_answer(db_session, client, interact_run, monkeypatch) -> None:
    await seed_graph(db_session, CODE, T1 + T2, precision=2)
    factory = sessionmaker_of(db_session)

    async def before(n: int) -> None:
        if n == 2:
            async with factory() as session:
                await session.execute(update(Equivalent).where(Equivalent.code == CODE).values(is_active=False))
                await session.commit()

    calls = _spy_execute(monkeypatch, before)
    response = await client.post(URL, headers=HEADERS, json={"equivalent": CODE})

    body = response.json()
    require_target(len(calls) == 2, f"the stop was not met through the runner ({len(calls)} calls; {response.status_code} {body!r})")
    assert response.status_code == 409 and body["code"] == "CONFLICT", body
    assert body["details"]["reason"] == "equivalent_inactive"
    assert body["details"]["partial_cleared_cycles"] == 1
    [done] = interact_run
    assert done["cleared_cycles"] == 1
