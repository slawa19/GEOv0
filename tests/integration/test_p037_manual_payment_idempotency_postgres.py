"""037 `T3701`: reproducers for F-037-2 (no idempotency key on the manual payment) and the P3 `routes[]` field.

`action_payment_real` calls `create_payment_internal(..., idempotency_key=None)` (`app/api/v1/simulator.py:1666`),
so the core mints a fresh `uuid4` per call (`payments/service.py:1038`) and a retried `payment-real` is a
SECOND payment. The UI's own refusal text meanwhile advises "send the same payment again"
(`simulator-ui/v2/src/utils/paymentRefusalText.ts:35`).

REAL MULTI-HOP STAND, no mock of `create_payment_internal`: a1 -> b1 -> a2 with limits 1000 each (the stand of
`tests/unit/test_p1_payment_run_perimeter.py`; `TrustLine(from=Y, to=X)` is the graph edge `X -> Y`). The SSE
emitter is replaced by a counter, because "no second `tx.updated`" is part of the target and the real
emitter has no observable here without a subscriber.

What each test is for:

* `test_BEFORE_*` is a CHARACTERISATION, green now and on purpose: it records the defect as it is (two payments,
  double debt, two `tx.updated`). It also stays green after the fix, because `client_action_id` keeps its
  meaning (a correlation id, the T3700 decision) - so it doubles as the control "no key -> no dedup".
* The `*_idempotency_key_*` tests are RED now. The first thing they assert is that the request is accepted at all;
  the schema is `extra="forbid"` (`app/schemas/simulator.py:657`), so today they fail with HTTP 400
  `INVALID_REQUEST` / `extra_forbidden` on `body.idempotency_key`, and the message says so. A rejection alone would not prove the double debt - that is the job of
  the characterisation above, which is why both exist.
* The control that needs the new field (`other key, same body -> two payments`) is red now for the SAME reason as
  the rest; the spec asked for green controls, but a control that must send `idempotency_key` cannot be green
  before the field exists. The key-less control above is the one that is green now.

What this does NOT see: the UI's key lifecycle (`simulator-ui/v2/src/composables/paymentIdempotencyKey.p037.test.ts`),
and a key reused by another run owner (the core derives `tx_id` from the key, `payments/service.py:1036`, and
`tx_id` is globally unique - a cross-owner collision is a question for the fix, not asserted here).
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from sqlalchemy import func, select, update

from app.config import settings
from app.core.payments.router import PaymentRouter
from app.core.simulator.models import RunRecord
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.transaction import Transaction
from app.db.models.trustline import TrustLine

_EQ = "RPY"
_RUN = "run-p037"
_URL = f"/api/v1/simulator/runs/{_RUN}/actions/payment-real"
_HEADERS = {"X-Admin-Token": settings.ADMIN_TOKEN}


class _CountingEmitter:
    """Stands for `SseEventEmitter`: counts `tx.updated` publications, keeps what was published."""

    published: list[dict] = []

    def __init__(self, *, sse, utc_now, logger):
        return None

    def emit_tx_updated(self, **kw) -> None:
        type(self).published.append(kw)


@pytest.fixture
def stand(monkeypatch):
    import app.api.v1.simulator as simulator_module

    monkeypatch.setattr("app.config.settings.SIMULATOR_ACTIONS_ENABLE", True)
    monkeypatch.setattr(
        simulator_module.runtime,
        "get_run",
        lambda rid: SimpleNamespace(
            run_id=str(rid), state="running", owner_id="", _real_seeded=True, _real_seeding_lock=None
        ),
    )
    run = RunRecord(run_id=_RUN, scenario_id="scn", mode="real", state="running")
    run._scenario_raw = {
        "participants": [
            {"id": p, "name": p.upper(), "type": "person", "status": "active"} for p in ("a1", "b1", "a2")
        ],
        "trustlines": [],
    }
    monkeypatch.setitem(simulator_module.runtime._runs, _RUN, run)
    _CountingEmitter.published = []
    monkeypatch.setattr(simulator_module, "SseEventEmitter", _CountingEmitter)
    PaymentRouter.invalidate_cache()
    return _CountingEmitter


async def _seed(db_session) -> Equivalent:
    eq = Equivalent(code=_EQ, precision=2, is_active=True)
    db_session.add(eq)
    people: dict[str, Participant] = {}
    for pid in ("a1", "b1", "a2"):
        p = Participant(
            id=uuid.uuid4(), pid=pid, display_name=pid.upper(), public_key=pid * 20,
            type="person", status="active", profile={},
        )
        people[pid] = p
        db_session.add(p)
    await db_session.commit()
    for trusts, owes in (("b1", "a1"), ("a2", "b1")):  # edges a1 -> b1 and b1 -> a2
        db_session.add(
            TrustLine(
                from_participant_id=people[trusts].id,
                to_participant_id=people[owes].id,
                equivalent_id=eq.id,
                limit=Decimal("1000"),
                status="active",
            )
        )
    await db_session.commit()
    return eq


def _body(**over) -> dict:
    return {"from_pid": "a1", "to_pid": "a2", "equivalent": _EQ, "amount": "10", "client_action_id": "c-37", **over}


async def _total_debt(db_session) -> Decimal:
    await db_session.commit()  # fresh snapshot of what the handler committed
    return (await db_session.execute(select(func.coalesce(func.sum(Debt.amount), 0)))).scalar_one()


async def _payments(db_session) -> int:
    await db_session.commit()
    return (
        await db_session.execute(select(func.count()).select_from(Transaction).where(Transaction.type == "PAYMENT"))
    ).scalar_one()


class _ResponseLost(Exception):
    """The answer of a payment that HAS committed never reached the client (stands for a dropped connection)."""


@pytest.mark.asyncio
async def test_R1_a_committed_payment_whose_answer_was_lost_is_paid_twice_by_the_retry(client, db_session, stand, monkeypatch):
    """R1 (037 A1, arbiter's schedule): the payment is committed, the answer is lost, the client sends the same request again.

    PHASE 1 is the loss as it is on main, measured with the only request form main accepts (no key): the first request
    commits (debt 20), the client gets nothing, the retry pays AGAIN (debt 40, two payments). That is the observable loss
    the key exists for. It stays true after the change (no key - no guarantee), so phase 1 is an assertion of the premise.

    PHASE 2 is the target: the same schedule with `idempotency_key`; the retry must be answered with the stored payment,
    so ONE more payment, not two (3 payments, debt 60 in all). On main phase 2 stops at the request itself (400
    `extra_forbidden`), and the message carries phase 1's measured loss, so the red is not only the missing field.
    """
    import app.api.v1.simulator as simulator_module

    await _seed(db_session)
    lose = {"armed": True}
    real_publish = simulator_module._publish_closed_best_effort

    async def publish_then_lose_the_answer(**kw):
        await real_publish(**kw)
        if lose["armed"]:
            lose["armed"] = False
            raise _ResponseLost()

    monkeypatch.setattr(simulator_module, "_publish_closed_best_effort", publish_then_lose_the_answer)

    # PHASE 1 - no key.
    with pytest.raises(_ResponseLost):
        await client.post(_URL, headers=_HEADERS, json=_body())
    assert await _payments(db_session) == 1, "premise: the first request committed although its answer never arrived"
    assert await _total_debt(db_session) == Decimal("20")
    retry = await client.post(_URL, headers=_HEADERS, json=_body())
    assert retry.status_code == 200, retry.text
    measured = (await _payments(db_session), await _total_debt(db_session))
    assert measured == (2, Decimal("40")), f"premise: the unkeyed retry is expected to pay twice, measured {measured}"

    # PHASE 2 - the same schedule, with the key.
    lose["armed"] = True
    key = f"r1-{uuid.uuid4()}"
    lost = None
    try:
        lost = await client.post(_URL, headers=_HEADERS, json=_body(idempotency_key=key))
    except _ResponseLost:
        pass
    assert lost is None, (
        f"measured on main without a key: payments={measured[0]}, total debt={measured[1]} after ONE intent sent twice; "
        f"the keyed request itself was answered {lost.status_code} {lost.text}"
    )
    again = await client.post(_URL, headers=_HEADERS, json=_body(idempotency_key=key))
    assert again.status_code == 200, again.text
    assert (await _payments(db_session), await _total_debt(db_session)) == (3, Decimal("60")), "the keyed retry paid again"


@pytest.mark.asyncio
async def test_R2_a_raw_key_is_shared_by_two_runs_of_one_scenario_at_the_core(db_session, stand):
    """R2 (037 A1): why the key cannot be handed to the core as it is (variant A, rejected by the arbiter).

    Participants of the simulator are shared by `pid` between runs and owners (`Participant.pid` is unique, the seeder
    reuses the row). Two owners in two runs of one scenario therefore pay from the SAME `initiator_id`, and the core
    keys a payment by `tx_id = idempotency_key`, globally. At the core (the layer the handler calls), the second run:
    gets the first run's payment as its own answer, with no effect of its own; and with another body it is refused
    `tx_id_reused`, which tells it the key exists. The core is not changed by the slice, so this stays true after it:
    the scoping has to happen in the handler (the route-level test `test_the_key_is_scoped_by_the_run`).
    """
    from app.core.payments.service import PaymentService
    from app.utils.exceptions import ConflictException

    await _seed(db_session)
    a1 = (await db_session.execute(select(Participant).where(Participant.pid == "a1"))).scalar_one()
    perimeter_x = {"a1", "b1", "a2"}  # run RX of owner X
    perimeter_y = {"a1", "b1", "a2"}  # run RY of owner Y, same scenario, same participant rows
    key = "shared-key-1"

    first = await PaymentService(db_session).create_payment_internal(
        a1.id, to_pid="a2", equivalent=_EQ, amount="10", idempotency_key=key, allowed_participant_pids=perimeter_x
    )
    PaymentRouter.invalidate_cache()
    second = await PaymentService(db_session).create_payment_internal(
        a1.id, to_pid="a2", equivalent=_EQ, amount="10", idempotency_key=key, allowed_participant_pids=perimeter_y
    )

    assert first.status == "COMMITTED"
    assert second.tx_id == first.tx_id == key, "the second run was handed the first run's payment"
    assert await _payments(db_session) == 1
    assert await _total_debt(db_session) == Decimal("20"), "the second run's payment moved nothing"

    with pytest.raises(ConflictException) as other_body:
        await PaymentService(db_session).create_payment_internal(
            a1.id, to_pid="a2", equivalent=_EQ, amount="11", idempotency_key=key, allowed_participant_pids=perimeter_y
        )
    assert (other_body.value.details or {}).get("reason") == "tx_id_reused", "the key's existence is told to another run"


_R3_KEY = "r3-stored-refusal"


@pytest.mark.asyncio
async def test_R3_a_stored_aborted_payment_is_answered_as_success_and_published(client, db_session, stand, monkeypatch):
    """R3 (037 A1, arbiter's finding (a)): a stored `ABORTED` row replayed under its `tx_id` is returned by the service
    WITHOUT raising, and `action_payment_real` answers `200 ok` and publishes `tx.updated` for it.

    On main the handler passes no key, so the same `tx_id` cannot reach the core through the route. The stand puts it
    there the way the key will: a test shim around `PaymentService.create_payment_internal` forces `idempotency_key`
    (and, for the first call only, a route past the router, as `test_p030_s4_staged_refusal_publishes_the_winner_postgres`
    does) - the handler's own call is otherwise untouched. After the change the shim still forces the same key, so the
    test keeps its meaning: the stored refusal must answer as a refusal. (The shim does not stand in for the handler's
    key derivation; that is covered by the keyed route-level tests.)
    """
    from app.core.payments.service import PaymentService

    await _seed(db_session)
    a2 = (await db_session.execute(select(Participant).where(Participant.pid == "a2"))).scalar_one()
    b1 = (await db_session.execute(select(Participant).where(Participant.pid == "b1"))).scalar_one()
    # The line b1 -> a2 (TrustLine(from=a2, to=b1)) lost its limit after the router saw it: the book refuses the route.
    await db_session.execute(
        update(TrustLine).where(TrustLine.from_participant_id == a2.id, TrustLine.to_participant_id == b1.id).values(limit=Decimal("0"))
    )
    await db_session.commit()

    original = PaymentService.create_payment_internal
    calls: list[int] = []

    async def with_the_key_and_the_forced_route(self, sender_id, **kw):
        calls.append(1)
        if len(calls) == 1:
            self.router.find_flow_routes = lambda *_a, **_k: [(["a1", "b1", "a2"], Decimal(kw["amount"]))]
        kw["idempotency_key"] = _R3_KEY
        return await original(self, sender_id, **kw)

    monkeypatch.setattr(PaymentService, "create_payment_internal", with_the_key_and_the_forced_route)

    first = await client.post(_URL, headers=_HEADERS, json=_body())
    assert first.status_code == 409, f"premise: the refusal after admission: {first.status_code} {first.text}"
    assert first.json()["code"] == "INSUFFICIENT_CAPACITY", first.text
    row = (await db_session.execute(select(Transaction.state).where(Transaction.tx_id == _R3_KEY))).scalar_one_or_none()
    assert row == "ABORTED", f"premise: the refusal is stored under the key, state={row!r}"
    assert stand.published == [], "premise: a refusal publishes no tx.updated"

    PaymentRouter.invalidate_cache()
    again = await client.post(_URL, headers=_HEADERS, json=_body())

    observed = {
        "status": again.status_code,
        "ok": again.json().get("ok"),
        "code": again.json().get("code"),
        "published_tx_updated": len(stand.published),
        "total_debt": await _total_debt(db_session),
        "status_in_body": again.json().get("status"),
    }
    assert observed == {
        "status": 409, "ok": None, "code": "INSUFFICIENT_CAPACITY", "published_tx_updated": 0,
        "total_debt": Decimal("0"), "status_in_body": None,
    }, f"a stored refusal replayed under its tx_id: {observed}"


@pytest.mark.asyncio
async def test_BEFORE_same_client_action_id_twice_makes_two_payments_and_double_debt(client, db_session, stand):
    """CHARACTERISATION (green now and after): `client_action_id` is correlation only - no dedup, two effects."""
    await _seed(db_session)

    r1 = await client.post(_URL, headers=_HEADERS, json=_body())
    r2 = await client.post(_URL, headers=_HEADERS, json=_body())

    assert r1.status_code == 200, r1.text
    assert r2.status_code == 200, r2.text
    assert r1.json()["payment_id"] != r2.json()["payment_id"]
    assert await _payments(db_session) == 2
    # a1 -> b1 -> a2: two debt rows of 10 per payment, so 40 after two payments (20 after one).
    assert await _total_debt(db_session) == Decimal("40")
    assert len(stand.published) == 2


@pytest.mark.asyncio
async def test_same_idempotency_key_same_body_is_one_payment_one_debt_one_publication(client, db_session, stand):
    """F-037-2. RED now: the field is rejected (`extra="forbid"`); after: ONE payment, replay == first, ONE tx.updated."""
    await _seed(db_session)
    key = f"k-{uuid.uuid4()}"

    r1 = await client.post(_URL, headers=_HEADERS, json=_body(idempotency_key=key))
    assert r1.status_code == 200, f"idempotency_key is not accepted by payment-real: {r1.status_code} {r1.text}"
    r2 = await client.post(_URL, headers=_HEADERS, json=_body(idempotency_key=key))

    assert r2.status_code == 200, r2.text
    assert r2.json()["payment_id"] == r1.json()["payment_id"]
    assert r2.json()["status"] == r1.json()["status"]
    assert await _payments(db_session) == 1
    assert await _total_debt(db_session) == Decimal("20")  # one payment: 10 + 10
    assert len(stand.published) == 1, "the replay published a second tx.updated (a second FX flight)"


@pytest.mark.asyncio
async def test_other_idempotency_key_same_body_makes_two_payments(client, db_session, stand):
    """CONTROL (red now only because the field does not exist): a different key is a different intent."""
    await _seed(db_session)

    r1 = await client.post(_URL, headers=_HEADERS, json=_body(idempotency_key=f"k-{uuid.uuid4()}"))
    assert r1.status_code == 200, f"idempotency_key is not accepted by payment-real: {r1.status_code} {r1.text}"
    r2 = await client.post(_URL, headers=_HEADERS, json=_body(idempotency_key=f"k-{uuid.uuid4()}"))

    assert r2.status_code == 200, r2.text
    assert r2.json()["payment_id"] != r1.json()["payment_id"]
    assert await _payments(db_session) == 2
    assert len(stand.published) == 2


@pytest.mark.asyncio
async def test_same_idempotency_key_other_body_is_409_without_effect(client, db_session, stand):
    """F-037-2. RED now (field rejected); after: 409, no second payment, no second publication."""
    await _seed(db_session)
    key = f"k-{uuid.uuid4()}"

    r1 = await client.post(_URL, headers=_HEADERS, json=_body(idempotency_key=key))
    assert r1.status_code == 200, f"idempotency_key is not accepted by payment-real: {r1.status_code} {r1.text}"
    r2 = await client.post(_URL, headers=_HEADERS, json=_body(idempotency_key=key, amount="11"))

    assert r2.status_code == 409, r2.text
    assert await _payments(db_session) == 1
    assert await _total_debt(db_session) == Decimal("20")
    assert len(stand.published) == 1


@pytest.mark.asyncio
async def test_payment_real_response_names_the_route_steps(client, db_session, stand):
    """P3 (`routes[]`, spec "Цепочка"). RED now: the response has no `routes`. After: hops with names and amounts."""
    await _seed(db_session)

    r = await client.post(_URL, headers=_HEADERS, json=_body())
    assert r.status_code == 200, r.text
    body = r.json()

    assert "routes" in body, f"payment-real answers without routes[]; keys: {sorted(body)}"
    routes = body["routes"]
    assert len(routes) == 1, routes
    hops = routes[0]["hops"]
    assert [(h["from"], h["to"]) for h in hops] == [("a1", "b1"), ("b1", "a2")]
    assert [Decimal(h["amount"]) for h in hops] == [Decimal("10"), Decimal("10")]


@pytest.mark.asyncio
async def test_a_direct_payment_has_a_one_step_route_and_no_invented_intermediary(client, db_session, stand):
    """P3 anti-vacuum: with a direct edge present the route is ONE step; nothing is added to it."""
    eq = await _seed(db_session)
    a1 = (await db_session.execute(select(Participant).where(Participant.pid == "a1"))).scalar_one()
    a2 = (await db_session.execute(select(Participant).where(Participant.pid == "a2"))).scalar_one()
    db_session.add(
        TrustLine(from_participant_id=a2.id, to_participant_id=a1.id, equivalent_id=eq.id,
                  limit=Decimal("1000"), status="active")
    )
    await db_session.commit()
    PaymentRouter.invalidate_cache()

    r = await client.post(_URL, headers=_HEADERS, json=_body())
    assert r.status_code == 200, r.text
    # Precondition independent of the field under test: the stand really paid over the direct edge (one debt row).
    assert await _total_debt(db_session) == Decimal("10")
    assert "routes" in r.json(), f"payment-real answers without routes[]; keys: {sorted(r.json())}"
    hops = r.json()["routes"][0]["hops"]
    assert [(h["from"], h["to"]) for h in hops] == [("a1", "a2")]


def test_openapi_describes_idempotency_key_and_routes():
    """The wire contract is `api/openapi.yaml` (AGENTS.md 8). RED now: neither field is described."""
    doc = yaml.safe_load((Path(__file__).resolve().parents[2] / "api" / "openapi.yaml").read_text(encoding="utf-8"))
    schemas = doc["components"]["schemas"]
    req = schemas["SimulatorActionPaymentRealRequest"]["properties"]
    resp = schemas["SimulatorActionPaymentRealResponse"]["properties"]
    assert "client_action_id" in req, "precondition: the schema is the one we think it is"
    assert "idempotency_key" in req, f"request properties: {sorted(req)}"
    assert "routes" in resp, f"response properties: {sorted(resp)}"
