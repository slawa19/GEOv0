"""Programme 021, `T2106` (stage 4): the Interact actions' wire, pinned before the handlers become thin wrappers.

WHAT THIS IS. A CHARACTERISATION, green on the code before `T2106`, not a red-first reproducer: `T2106` is a
behaviour-preserving consolidation of the `action_*` block of `app/api/v1/simulator.py` into "perimeter -> service
-> SSE" (spec, "Стадии", stage 4), and the spec (Non-goals) forbids changing the actions' error codes and bodies,
SSE or responses. What the existing suites leave open is pinned here: the FULL error bodies (not only the
code), the ORDER of the refusals (which one answers when a request breaks two rules), the `topology.changed`
payloads of the three trust-line actions, and the run's in-memory topology after them. A consolidation that
drops a check, reorders two of them, loses a detail or emits a different payload goes red here.

WHAT IT DOES NOT SEE. The SSE emitter is replaced by a recorder of what the handler hands it (the transport is
not what is checked; the payload is, dumped as the wire dumps it). Mode A (`client` on the fixture's rolled-back
transaction): no concurrency, no durability - the concurrent-create answer (`CONCURRENT_TRUSTLINE_CREATE`) is not
exercised here. The payment and clearing
actions are covered by `tests/unit/test_interact_actions_backend_p1.py`; here only the payment's refusal ORDER.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.config import settings
from app.core.simulator.models import RunRecord
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant

from tests.debt_setup import debt_fixture_setup

HEADERS = {"X-Admin-Token": settings.ADMIN_TOKEN}
BASE = "/api/v1/simulator/runs/wire-run/actions"


class _Recorder:
    """Stands in for `SseEventEmitter`; records each `topology.changed` exactly as the wire would carry it."""

    events: list[dict] = []

    def __init__(self, *, sse, utc_now, logger):
        return None

    def emit_topology_changed(self, *, run_id, run, equivalent, payload, reason=None):
        type(self).events.append(
            {
                "run_id": run_id,
                "equivalent": equivalent,
                "reason": reason,
                "payload": payload.model_dump(mode="json", by_alias=True),
            }
        )
        return "evt"


@pytest.fixture
async def stand(db_session, monkeypatch):
    import app.api.v1.simulator as simulator_module

    monkeypatch.setenv("SIMULATOR_ACTIONS_ENABLE", "1")
    alice = Participant(pid="alice", display_name="Alice", public_key="A" * 64, type="person", status="active", profile={})
    bob = Participant(pid="bob", display_name="Bob", public_key="B" * 64, type="person", status="active", profile={})
    # Outside the run: exists in the database, not in the run's perimeter.
    mallory = Participant(
        pid="mallory", display_name="Mallory", public_key="C" * 64, type="person", status="active", profile={}
    )
    uah = Equivalent(code="UAH", precision=2, is_active=True)
    db_session.add_all([alice, bob, mallory, uah])
    await db_session.commit()

    run = RunRecord(run_id="wire-run", scenario_id="wire-scenario", mode="real", state="running")
    run._scenario_raw = {
        "participants": [
            {"id": "alice", "name": "Alice", "type": "person", "status": "active"},
            {"id": "bob", "name": "Bob", "type": "person", "status": "active"},
        ],
        "trustlines": [],
    }
    run._edges_by_equivalent = {"UAH": []}
    run._real_participants = [(alice.id, "alice"), (bob.id, "bob")]
    run._real_seeded = True
    monkeypatch.setitem(simulator_module.runtime._runs, "wire-run", run)
    _Recorder.events = []
    monkeypatch.setattr(simulator_module, "SseEventEmitter", _Recorder)
    return {"run": run, "alice": alice, "bob": bob, "uah": uah, "db": db_session}


async def _post(client, action: str, body: dict):
    return await client.post(f"{BASE}/{action}", headers=HEADERS, json=body)


def _error(code: str, message: str, details) -> dict:
    return {"code": code, "message": message, "details": details}


async def _debt(db, *, debtor, creditor, eq, amount: str) -> None:
    async with debt_fixture_setup(db, label="setup"):
        db.add(Debt(debtor_id=debtor.id, creditor_id=creditor.id, equivalent_id=eq.id, amount=Decimal(amount)))
    await db.commit()


TRIPLE = {"from_pid": "alice", "to_pid": "bob", "equivalent": "UAH"}


@pytest.mark.asyncio
async def test_create_update_close_answer_and_publish_as_before(client, stand) -> None:
    run = stand["run"]

    created = await _post(client, "trustline-create", {**TRIPLE, "limit": "10", "client_action_id": "c1"})
    assert created.status_code == 200, created.text
    body = created.json()
    trustline_id = body.pop("trustline_id")
    assert trustline_id
    assert body == {"ok": True, "from_pid": "alice", "to_pid": "bob", "equivalent": "UAH", "limit": "10", "client_action_id": "c1"}
    [event] = _Recorder.events
    assert (event["run_id"], event["equivalent"], event["reason"]) == ("wire-run", "UAH", "interact.trustline_create")
    payload = event["payload"]
    assert payload["added_edges"] == [{"from_pid": "alice", "to_pid": "bob", "equivalent_code": "UAH", "limit": "10"}]
    assert payload.get("removed_edges") in (None, [])
    assert payload["node_patch"] is None
    assert [(p["source"], p["target"]) for p in payload["edge_patch"]] == [("alice", "bob")]
    assert run._scenario_raw["trustlines"] == [
        {"equivalent": "UAH", "from": "alice", "to": "bob", "limit": "10", "status": "active"}
    ]
    assert run._edges_by_equivalent["UAH"] == [("alice", "bob")]

    _Recorder.events.clear()
    updated = await _post(client, "trustline-update", {**TRIPLE, "new_limit": "15", "client_action_id": "c2"})
    assert updated.status_code == 200, updated.text
    assert updated.json() == {
        "ok": True,
        "trustline_id": trustline_id,
        "old_limit": "10.00000000",
        "new_limit": "15",
        "client_action_id": "c2",
    }
    [event] = _Recorder.events
    assert (event["equivalent"], event["reason"]) == ("UAH", "interact.trustline_update")
    assert event["payload"].get("added_edges") in (None, [])
    assert event["payload"]["node_patch"] is None
    assert [(p["source"], p["target"]) for p in event["payload"]["edge_patch"]] == [("alice", "bob")]
    assert run._scenario_raw["trustlines"][0]["limit"] == "15"

    _Recorder.events.clear()
    closed = await _post(client, "trustline-close", {**TRIPLE, "client_action_id": "c3"})
    assert closed.status_code == 200, closed.text
    assert closed.json() == {"ok": True, "trustline_id": trustline_id, "client_action_id": "c3"}
    [event] = _Recorder.events
    assert (event["equivalent"], event["reason"]) == ("UAH", "interact.trustline_close")
    assert event["payload"]["removed_edges"] == [{"from_pid": "alice", "to_pid": "bob", "equivalent_code": "UAH", "limit": None}]
    assert event["payload"]["edge_patch"] is None and event["payload"]["node_patch"] is None
    assert run._scenario_raw["trustlines"] == []
    assert run._edges_by_equivalent["UAH"] == []


@pytest.mark.asyncio
async def test_refusal_bodies_of_the_trust_line_actions(client, stand) -> None:
    alice, bob, uah, db = stand["alice"], stand["bob"], stand["uah"], stand["db"]
    triple_details = {"from_pid": "alice", "to_pid": "bob", "equivalent": "UAH"}

    # No line yet: update and close answer 404 with the triple.
    for action, extra in (("trustline-update", {"new_limit": "5"}), ("trustline-close", {})):
        r = await _post(client, action, {**TRIPLE, **extra})
        assert (r.status_code, r.json()) == (404, _error("TRUSTLINE_NOT_FOUND", "Trustline not found", triple_details)), action

    # Create below an existing debt of the pair (the action's check, stronger than the service's).
    await _debt(db, debtor=bob, creditor=alice, eq=uah, amount="7")
    r = await _post(client, "trustline-create", {**TRIPLE, "limit": "5"})
    assert (r.status_code, r.json()) == (
        409,
        _error(
            "USED_EXCEEDS_NEW_LIMIT",
            "Limit is below current used amount",
            {**triple_details, "used": "7.00000000", "limit": "5"},
        ),
    )

    assert (await _post(client, "trustline-create", {**TRIPLE, "limit": "10"})).status_code == 200
    r = await _post(client, "trustline-create", {**TRIPLE, "limit": "10"})
    assert (r.status_code, r.json()) == (
        409,
        _error("TRUSTLINE_EXISTS", "Active trustline already exists", {"from_pid": "alice", "to_pid": "bob", "equivalent": "UAH"}),
    )

    r = await _post(client, "trustline-update", {**TRIPLE, "new_limit": "6"})
    assert (r.status_code, r.json()) == (
        409,
        _error(
            "USED_EXCEEDS_NEW_LIMIT",
            "Cannot reduce trustline limit below used amount",
            {**triple_details, "used": "7.00000000", "new_limit": "6"},
        ),
    )

    # Close refuses on debt either way; both amounts are reported.
    await _debt(db, debtor=alice, creditor=bob, eq=uah, amount="2")
    r = await _post(client, "trustline-close", dict(TRIPLE))
    assert (r.status_code, r.json()) == (
        409,
        _error(
            "TRUSTLINE_HAS_DEBT",
            "Cannot close trustline with non-zero debt",
            {**triple_details, "used": "7.00000000", "reverse_used": "2.00000000"},
        ),
    )
    assert _Recorder.events[-1]["reason"] == "interact.trustline_create", "a refusal published an event"


@pytest.mark.asyncio
async def test_a_failed_debt_read_answers_503_on_every_trust_line_action(client, stand, monkeypatch) -> None:
    import app.api.v1.simulator as simulator_module

    assert (await _post(client, "trustline-create", {**TRIPLE, "limit": "10"})).status_code == 200

    async def _boom(*_a, **_k):
        raise RuntimeError("debt read down")

    monkeypatch.setattr(simulator_module, "_trustline_used_amount", _boom)
    unavailable = _error(
        "TRUSTLINE_USED_UNAVAILABLE",
        "Temporary error while reading current used amount",
        {"equivalent": "UAH", "from_pid": "alice", "to_pid": "bob"},
    )
    for action, extra in (("trustline-update", {"new_limit": "5"}), ("trustline-close", {})):
        r = await _post(client, action, {**TRIPLE, **extra})
        assert (r.status_code, r.json()) == (503, unavailable), action


@pytest.mark.asyncio
async def test_the_order_of_refusals(client, stand) -> None:
    foreign = {**TRIPLE, "to_pid": "mallory"}

    # Trust-line actions: the self-loop, then the amount, BEFORE the perimeter.
    r = await _post(client, "trustline-create", {**TRIPLE, "to_pid": "alice", "limit": "x"})
    assert (r.status_code, r.json()["code"], r.json()["details"]["reason"]) == (400, "INVALID_REQUEST", "self_loop_trustline")
    r = await _post(client, "trustline-create", {**foreign, "limit": "x"})
    assert (r.status_code, r.json()) == (400, _error("INVALID_AMOUNT", "Invalid limit", {"limit": "x"}))
    r = await _post(client, "trustline-update", {**foreign, "new_limit": "-1"})
    assert (r.status_code, r.json()) == (400, _error("INVALID_AMOUNT", "Invalid new_limit", {"new_limit": "-1"}))

    # Then the perimeter: from, then to, then the equivalent.
    not_in_run = _error("PARTICIPANT_NOT_FOUND", "Participant not found", {"field": "to_pid", "pid": "mallory"})
    for action, extra in (
        ("trustline-create", {"limit": "1"}),
        ("trustline-update", {"new_limit": "1"}),
        ("trustline-close", {}),
    ):
        r = await _post(client, action, {**foreign, "equivalent": "NOPE", **extra})
        assert (r.status_code, r.json()) == (404, not_in_run), action
    r = await _post(client, "trustline-close", {**TRIPLE, "from_pid": "mallory", "to_pid": "ghost"})
    assert r.json()["details"] == {"field": "from_pid", "pid": "mallory"}
    r = await _post(client, "trustline-close", {**TRIPLE, "equivalent": "NOPE"})
    assert (r.status_code, r.json()) == (404, _error("EQUIVALENT_NOT_FOUND", "Equivalent not found", {"equivalent": "NOPE"}))

    # Payment: the perimeter BEFORE the amount.
    r = await _post(client, "payment-real", {**foreign, "amount": "x"})
    assert (r.status_code, r.json()) == (404, not_in_run)
    r = await _post(client, "payment-real", {**TRIPLE, "amount": "0"})
    assert (r.status_code, r.json()["code"]) == (400, "INVALID_AMOUNT")
    assert _Recorder.events == [], "a refusal published an event"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "extra"),
    [
        ("trustline-create", {"limit": "1"}),
        ("trustline-update", {"new_limit": "1"}),
        ("trustline-close", {}),
        ("payment-real", {"amount": "1"}),
    ],
)
async def test_an_unmeasurable_perimeter_is_refused_before_any_participant(client, stand, monkeypatch, action, extra) -> None:
    import app.api.v1.simulator as simulator_module

    async def _broken(**_k):
        raise RuntimeError("snapshot down")

    monkeypatch.setattr(simulator_module.runtime, "build_graph_snapshot", _broken)
    r = await _post(client, action, {**TRIPLE, "to_pid": "mallory", **extra})
    assert (r.status_code, r.json()) == (
        503,
        _error("RUN_PERIMETER_UNAVAILABLE", "Run perimeter could not be established", {"run_id": "wire-run"}),
    )
