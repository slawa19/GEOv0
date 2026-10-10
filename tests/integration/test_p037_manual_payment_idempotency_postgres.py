"""037 A1: the manual payment's idempotency key and `routes[]` (`POST .../actions/payment-real`), on a real route.

The decision (`specs/037-.../spec.md`, Changelog 2026-10-10, `037-KEY-DECISION: B`): the client's key is only an INPUT;
the handler derives `tx_id = "man:" + sha256("manual|" + run_id + "|" + key)[:32]` and hands THAT to the core, because the
core keys a payment by `tx_id`, globally and run-blind, and simulator participants are shared by `pid` between runs.

REAL MULTI-HOP STAND, no mock of `create_payment_internal` (except where a test says it is shimmed): a1 -> b1 -> a2 with
limits 1000 each (the stand of `tests/unit/test_p1_payment_run_perimeter.py`; `TrustLine(from=Y, to=X)` is the graph
edge `X -> Y`). The SSE emitter is replaced by a counter, because "no second `tx.updated`" is part of the contract and
the real emitter has no observable here without a subscriber.

The first commit of the slice carried the reproducers R1-R3 (red on main); this file keeps them as the permanent tests:

* R1 `test_R1_*` - a committed payment whose answer was lost is paid twice by a retry WITHOUT a key (phase 1, premise,
  unchanged by the slice) and once WITH it (phase 2);
* R2 `test_R2_*` - the hazard of a raw key at the core, green on main and after (the core is not changed); the handler's
  answer to it is `test_the_key_is_scoped_by_the_run`;
* R3 `test_R3_*` - a stored `ABORTED` row replayed under its `tx_id` must answer as a refusal, never as a success.
  The stand shims `create_payment_internal` to force the key; the real-key twin is
  `test_a_refusal_after_admission_is_repeated_as_a_refusal`.
* `test_BEFORE_*` - no key, no guarantee: two calls are two payments (`client_action_id` is a correlation id only).

What this does NOT see: the UI's key lifecycle (`simulator-ui/v2/src/composables/paymentIdempotencyKey.p037.test.ts`,
the next part of slice A); a real `restart` of a run (the epoch is bumped on the run object, the field `restart`
changes); two real owners' actors (the admin token is the actor throughout); the real SSE emitter.
"""

from __future__ import annotations

import asyncio
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
from tests.conftest import MODE_B

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
    """F-037-2. ONE payment, the replay answers the first, ONE tx.updated."""
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
    """CONTROL: a different key is a different intent."""
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
    """F-037-2. 409, no second payment, no second publication."""
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
    """P3 (`routes[]`, spec "Цепочка"). Hops with names and amounts."""
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
    """The wire contract is `api/openapi.yaml` (AGENTS.md 8). Both fields are described."""
    doc = yaml.safe_load((Path(__file__).resolve().parents[2] / "api" / "openapi.yaml").read_text(encoding="utf-8"))
    schemas = doc["components"]["schemas"]
    req = schemas["SimulatorActionPaymentRealRequest"]["properties"]
    resp = schemas["SimulatorActionPaymentRealResponse"]["properties"]
    assert "client_action_id" in req, "precondition: the schema is the one we think it is"
    assert "idempotency_key" in req, f"request properties: {sorted(req)}"
    assert "routes" in resp, f"response properties: {sorted(resp)}"


# ---------------------------------------------------------------------------------------------------------------------
# 037 A1, the route-level tests of the arbiter's list. Real route, real service, PostgreSQL; the SSE emitter is the counter.
# ---------------------------------------------------------------------------------------------------------------------


def _url(run_id: str) -> str:
    return f"/api/v1/simulator/runs/{run_id}/actions/payment-real"


def _register_run(monkeypatch, run_id: str, pids=("a1", "b1", "a2")) -> RunRecord:
    """Another run of the same scenario: its perimeter is the same participants (shared by `pid` between runs)."""
    import app.api.v1.simulator as simulator_module

    run = RunRecord(run_id=run_id, scenario_id="scn", mode="real", state="running")
    run._scenario_raw = {
        "participants": [{"id": p, "name": p.upper(), "type": "person", "status": "active"} for p in pids],
        "trustlines": [],
    }
    monkeypatch.setitem(simulator_module.runtime._runs, run_id, run)
    return run


def _tx_id_of(run_id: str, key: str) -> str:
    """What the handler must derive, spelled out here so a change of the rule is a change of this test."""
    import hashlib

    return "man:" + hashlib.sha256(("manual|" + run_id + "|" + key).encode("utf-8")).hexdigest()[:32]


def _key() -> str:
    return f"k-{uuid.uuid4()}"


@pytest.mark.asyncio
async def test_the_key_is_scoped_by_the_run(client, db_session, stand, monkeypatch):
    """R2 after the change: one key, one body, two runs of one scenario (shared participants) are two payments."""
    await _seed(db_session)
    _register_run(monkeypatch, "run-p037-other")
    key = _key()

    mine = await client.post(_url(_RUN), headers=_HEADERS, json=_body(idempotency_key=key))
    other = await client.post(_url("run-p037-other"), headers=_HEADERS, json=_body(idempotency_key=key))

    assert (mine.status_code, other.status_code) == (200, 200), (mine.text, other.text)
    assert mine.json()["payment_id"] == _tx_id_of(_RUN, key)
    assert other.json()["payment_id"] == _tx_id_of("run-p037-other", key)
    assert mine.json()["payment_id"] != other.json()["payment_id"]
    assert await _payments(db_session) == 2
    assert await _total_debt(db_session) == Decimal("40"), "the second run's payment moved nothing"
    assert len(stand.published) == 2


@pytest.mark.asyncio
async def test_a_sequential_repeat_answers_the_stored_payment_and_routes_without_effect_or_publication(
    client, db_session, stand, monkeypatch
):
    from app.core.payments.service import PaymentService

    await _seed(db_session)
    key = _key()
    reached_the_service: list[int] = []
    original = PaymentService.create_payment_internal

    async def counting(self, *a, **kw):
        reached_the_service.append(1)
        return await original(self, *a, **kw)

    monkeypatch.setattr(PaymentService, "create_payment_internal", counting)

    first = await client.post(_URL, headers=_HEADERS, json=_body(idempotency_key=key))
    published_after_first = len(stand.published)
    debt_after_first = await _total_debt(db_session)
    again = await client.post(_URL, headers=_HEADERS, json=_body(idempotency_key=key, client_action_id="another-correlation"))

    assert (first.status_code, again.status_code) == (200, 200), (first.text, again.text)
    assert again.json()["payment_id"] == first.json()["payment_id"] == _tx_id_of(_RUN, key)
    assert again.json()["routes"] == first.json()["routes"] and first.json()["routes"], "the stored routes are answered"
    assert again.json()["status"] == "COMMITTED"
    assert again.json()["client_action_id"] == "another-correlation", "correlation is the request's, not the stored one"
    assert await _payments(db_session) == 1
    assert await _total_debt(db_session) == debt_after_first == Decimal("20")
    assert (published_after_first, len(stand.published)) == (1, 1), "the repeat published a second tx.updated"
    assert len(reached_the_service) == 2, "the pre-read is a hint: the service is called for the repeat too"


@pytest.mark.asyncio
@MODE_B
async def test_two_simultaneous_identical_requests_make_one_payment(client, db_session, stand, monkeypatch):
    """Both reach the service before either commits (a barrier, not a sleep). One money effect; the publication is a
    hint, so up to two `tx.updated` are accepted (arbiter's `ACCEPT-RESIDUAL-RACE`) - never more than the requests."""
    from app.api.deps import get_db
    from app.core.payments.service import PaymentService
    from app.main import app
    from tests.conftest import sessionmaker_of

    await _seed(db_session)
    maker = sessionmaker_of(db_session)

    async def a_session_per_request():
        async with maker(expire_on_commit=False) as session:  # as production's `get_db` session does
            yield session

    app.dependency_overrides[get_db] = a_session_per_request

    barrier = asyncio.Barrier(2)
    original = PaymentService.create_payment_internal
    seen_at_the_barrier: list[int] = []

    async def meet_then_pay(self, *a, **kw):
        seen_at_the_barrier.append(1)
        await asyncio.wait_for(barrier.wait(), timeout=20)
        return await original(self, *a, **kw)

    monkeypatch.setattr(PaymentService, "create_payment_internal", meet_then_pay)
    key = _key()

    first, second = await asyncio.gather(
        client.post(_URL, headers=_HEADERS, json=_body(idempotency_key=key)),
        client.post(_URL, headers=_HEADERS, json=_body(idempotency_key=key)),
    )

    assert len(seen_at_the_barrier) == 2, "premise: both requests were inside the service together"
    assert (first.status_code, second.status_code) == (200, 200), (first.text, second.text)
    assert first.json()["payment_id"] == second.json()["payment_id"] == _tx_id_of(_RUN, key)
    assert await _payments(db_session) == 1
    assert await _total_debt(db_session) == Decimal("20"), "one money effect for two identical requests"
    assert 1 <= len(stand.published) <= 2, f"published {len(stand.published)} tx.updated for two requests"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        pytest.param({"from_pid": "b1", "to_pid": "a2"}, id="sender"),
        pytest.param({"to_pid": "b1"}, id="receiver"),
        pytest.param({"equivalent": "RP2"}, id="equivalent"),
        pytest.param({"amount": "11"}, id="amount"),
        pytest.param({"amount": "10.00"}, id="amount-spelling"),
    ],
)
async def test_the_same_key_with_another_intent_is_refused_without_effect(client, db_session, stand, change):
    """409 `tx_id_reused` for any change of the identity; `10` and `10.00` are different requests (declared behaviour)."""
    await _seed(db_session)
    db_session.add(Equivalent(code="RP2", precision=2, is_active=True))
    await db_session.commit()
    key = _key()

    paid = await client.post(_URL, headers=_HEADERS, json=_body(idempotency_key=key))
    assert paid.status_code == 200, paid.text
    changed = await client.post(_URL, headers=_HEADERS, json=_body(idempotency_key=key, **change))

    assert changed.status_code == 409, f"{changed.status_code} {changed.text}"
    assert changed.json().get("ok") is not True
    assert (changed.json().get("details") or {}).get("reason") == "tx_id_reused", changed.text
    assert await _payments(db_session) == 1
    assert await _total_debt(db_session) == Decimal("20")
    assert len(stand.published) == 1


async def _lower_the_second_line(db_session, limit: str) -> None:
    a2 = (await db_session.execute(select(Participant).where(Participant.pid == "a2"))).scalar_one()
    b1 = (await db_session.execute(select(Participant).where(Participant.pid == "b1"))).scalar_one()
    await db_session.execute(
        update(TrustLine).where(TrustLine.from_participant_id == a2.id, TrustLine.to_participant_id == b1.id).values(limit=Decimal(limit))
    )
    await db_session.commit()
    PaymentRouter.invalidate_cache()


@pytest.mark.asyncio
async def test_a_refusal_after_admission_is_repeated_as_a_refusal(client, db_session, stand, monkeypatch):
    """The first attempt is refused at the book (a route past the router onto a line with no room), stored `ABORTED`;
    the repeat under the same key is that refusal again - no success, no debt, no publication."""
    await _seed(db_session)
    await _lower_the_second_line(db_session, "0")
    forced = {"on": True}
    real = PaymentRouter.find_flow_routes

    def forced_past_the_router_while_armed(self, *a, **kw):
        # The service re-routes once on a refused book (027), so the forced route is given to every call while armed.
        if forced["on"]:
            return [(["a1", "b1", "a2"], Decimal("10"))]
        return real(self, *a, **kw)

    monkeypatch.setattr(PaymentRouter, "find_flow_routes", forced_past_the_router_while_armed)
    key = _key()

    first = await client.post(_URL, headers=_HEADERS, json=_body(idempotency_key=key))
    forced["on"] = False  # from here the router is the real one: the repeat must be answered from the stored row
    stored = (await db_session.execute(select(Transaction.state).where(Transaction.tx_id == _tx_id_of(_RUN, key)))).scalar_one_or_none()
    again = await client.post(_URL, headers=_HEADERS, json=_body(idempotency_key=key))

    assert (first.status_code, first.json().get("code")) == (409, "INSUFFICIENT_CAPACITY"), first.text
    assert stored == "ABORTED", f"premise: the refusal is stored under the derived identity, state={stored!r}"
    assert (again.status_code, again.json().get("code"), again.json().get("ok")) == (409, "INSUFFICIENT_CAPACITY", None), again.text
    assert await _total_debt(db_session) == Decimal("0")
    assert stand.published == []


@pytest.mark.asyncio
async def test_a_refusal_before_admission_leaves_no_row_and_the_same_key_pays_after_the_cause_is_gone(client, db_session, stand):
    await _seed(db_session)
    await _lower_the_second_line(db_session, "0")
    key = _key()

    refused = await client.post(_URL, headers=_HEADERS, json=_body(idempotency_key=key))
    rows = (await db_session.execute(select(func.count()).select_from(Transaction).where(Transaction.tx_id == _tx_id_of(_RUN, key)))).scalar_one()

    assert refused.status_code == 409 and refused.json().get("code") == "NO_ROUTE", refused.text
    assert rows == 0, "a refusal before admission stores nothing"

    await _lower_the_second_line(db_session, "1000")
    paid = await client.post(_URL, headers=_HEADERS, json=_body(idempotency_key=key))

    assert paid.status_code == 200, paid.text
    assert paid.json()["payment_id"] == _tx_id_of(_RUN, key)
    assert await _payments(db_session) == 1
    assert len(stand.published) == 1


def _statements_on_transactions(db_session):
    """Record every SQL statement that touches the `transactions` table, from here until the returned stop() is called."""
    from sqlalchemy import event as sa_event

    seen: list[str] = []
    engine = db_session.sync_session.get_bind().engine

    def record(conn, cursor, statement, parameters, context, executemany):
        if "transactions" in statement.lower():
            seen.append(statement)

    sa_event.listen(engine, "before_cursor_execute", record)

    def stop() -> None:
        sa_event.remove(engine, "before_cursor_execute", record)

    return seen, stop


@pytest.mark.asyncio
async def test_a_stopped_run_answers_run_terminal_without_the_pre_read_even_for_a_stored_key(client, db_session, stand, monkeypatch):
    """The run check comes first: on a stopped run the handler reads nothing of the payment (observed: no statement on
    `transactions`, the service not called), whether the key is stored or not. A positive control proves the recorder
    sees the pre-read of an ordinary keyed request."""
    import app.api.v1.simulator as simulator_module
    from app.core.payments.service import PaymentService

    await _seed(db_session)
    key = _key()
    seen, stop = _statements_on_transactions(db_session)
    try:
        paid = await client.post(_URL, headers=_HEADERS, json=_body(idempotency_key=key))
        assert paid.status_code == 200, paid.text
        assert seen, "control: the recorder must see the statements on `transactions` of an ordinary keyed request"
        seen.clear()

        reached_the_service: list[int] = []
        original = PaymentService.create_payment_internal

        async def counting(self, *a, **kw):
            reached_the_service.append(1)
            return await original(self, *a, **kw)

        monkeypatch.setattr(PaymentService, "create_payment_internal", counting)
        monkeypatch.setattr(
            simulator_module.runtime,
            "get_run",
            lambda rid: SimpleNamespace(run_id=str(rid), state="stopped", owner_id="", _real_seeded=True, _real_seeding_lock=None),
        )
        stored = await client.post(_URL, headers=_HEADERS, json=_body(idempotency_key=key))
        unknown = await client.post(_URL, headers=_HEADERS, json=_body(idempotency_key=_key()))
    finally:
        stop()

    assert (stored.status_code, stored.json().get("code")) == (409, "RUN_TERMINAL"), stored.text
    assert stored.content == unknown.content, "a known and an unknown key must be answered alike on a stopped run"
    assert seen == [], f"the handler read `transactions` on a stopped run: {seen}"
    assert reached_the_service == []
    assert await _payments(db_session) == 1
    assert len(stand.published) == 1


@pytest.mark.asyncio
async def test_the_key_survives_a_restart_of_the_run_and_a_new_key_pays_again(client, db_session, stand, monkeypatch):
    """`restart` bumps `_launch_epoch` and undoes no debt (`run_lifecycle.py:514-516`); the epoch is not in the key.

    The run object the handler reads is the one `runtime.get_run` returns, so that is the object whose epoch is bumped
    (the real `restart` is a lifecycle operation of its own; what it does to the run's identity is exactly this field).
    """
    import app.api.v1.simulator as simulator_module

    await _seed(db_session)
    the_run = SimpleNamespace(
        run_id=_RUN, state="running", owner_id="", _real_seeded=True, _real_seeding_lock=None, _launch_epoch=0
    )
    monkeypatch.setattr(simulator_module.runtime, "get_run", lambda rid: the_run)
    key = _key()
    first = await client.post(_URL, headers=_HEADERS, json=_body(idempotency_key=key))
    the_run._launch_epoch += 1  # what `restart` does to the run

    again = await client.post(_URL, headers=_HEADERS, json=_body(idempotency_key=key))
    new_intent = await client.post(_URL, headers=_HEADERS, json=_body(idempotency_key=_key()))

    assert (first.status_code, again.status_code, new_intent.status_code) == (200, 200, 200)
    assert again.json()["payment_id"] == first.json()["payment_id"]
    assert new_intent.json()["payment_id"] != first.json()["payment_id"]
    assert await _payments(db_session) == 2
    assert await _total_debt(db_session) == Decimal("40")
    assert len(stand.published) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_key",
    [
        pytest.param("", id="empty"),
        pytest.param("k" * 129, id="129-chars"),
        pytest.param("a b", id="space"),
        pytest.param(" abc", id="leading-space"),
        pytest.param("abc\n", id="trailing-newline"),
        pytest.param("a/b", id="slash"),
        pytest.param("ключ", id="non-ascii"),
        pytest.param(123, id="number"),
        pytest.param(True, id="boolean"),
        pytest.param(["k"], id="list"),
    ],
)
async def test_an_invalid_key_is_a_flat_400_at_the_edge_with_no_effect(client, db_session, stand, bad_key):
    await _seed(db_session)

    r = await client.post(_URL, headers=_HEADERS, json=_body(idempotency_key=bad_key))

    assert r.status_code == 400, f"{r.status_code} {r.text}"
    assert r.json().get("code") == "INVALID_REQUEST", r.text
    assert await _payments(db_session) == 0
    assert stand.published == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "good_key",
    [
        pytest.param("k" * 128, id="128-chars"),
        pytest.param("sim:" + "0" * 32, id="sim-prefix"),
        pytest.param("man:" + "0" * 32, id="man-prefix"),
        pytest.param("A.b_c-d:9", id="every-class"),
    ],
)
async def test_a_valid_key_passes_and_reserved_looking_prefixes_are_only_an_input(client, db_session, stand, good_key):
    await _seed(db_session)

    r = await client.post(_URL, headers=_HEADERS, json=_body(idempotency_key=good_key))

    assert r.status_code == 200, r.text
    assert r.json()["payment_id"] == _tx_id_of(_RUN, good_key), "the client's text is hashed, never used as the identity"
    assert len(r.json()["payment_id"]) <= 64


@pytest.mark.asyncio
async def test_a_null_key_is_the_same_as_no_key(client, db_session, stand):
    await _seed(db_session)

    r1 = await client.post(_URL, headers=_HEADERS, json=_body(idempotency_key=None))
    r2 = await client.post(_URL, headers=_HEADERS, json=_body(idempotency_key=None))

    assert (r1.status_code, r2.status_code) == (200, 200)
    assert r1.json()["payment_id"] != r2.json()["payment_id"] and not r1.json()["payment_id"].startswith("man:")
    assert await _payments(db_session) == 2


async def _seed_two_routes(db_session) -> None:
    """a1 -> b1 -> a2 and a1 -> c1 -> a2, each route carrying 6 at most, so a payment of 10 needs both."""
    eq = Equivalent(code=_EQ, precision=2, is_active=True)
    db_session.add(eq)
    people = {}
    for pid in ("a1", "b1", "c1", "a2"):
        p = Participant(
            id=uuid.uuid4(), pid=pid, display_name=pid.upper(), public_key=pid * 20, type="person", status="active", profile={}
        )
        people[pid] = p
        db_session.add(p)
    await db_session.commit()
    for trusts, owes in (("b1", "a1"), ("a2", "b1"), ("c1", "a1"), ("a2", "c1")):
        db_session.add(
            TrustLine(from_participant_id=people[trusts].id, to_participant_id=people[owes].id, equivalent_id=eq.id,
                      limit=Decimal("6"), status="active")
        )
    await db_session.commit()


@pytest.mark.asyncio
async def test_routes_of_a_multi_route_payment_are_all_of_them_and_equal_the_stored_payment(client, db_session, stand, monkeypatch):
    await _seed_two_routes(db_session)
    _register_run(monkeypatch, _RUN, pids=("a1", "b1", "c1", "a2"))
    PaymentRouter.invalidate_cache()

    r = await client.post(_URL, headers=_HEADERS, json=_body(idempotency_key=_key()))
    assert r.status_code == 200, r.text
    routes = r.json()["routes"]

    stored = (await db_session.execute(select(Transaction.payload).where(Transaction.tx_id == r.json()["payment_id"]))).scalar_one()
    expected = [
        [{"from": a, "to": b, "amount": route["amount"]} for a, b in zip(route["path"], route["path"][1:])]
        for route in stored["routes"]
    ]
    got = [[{"from": h["from"], "to": h["to"], "amount": h["amount"]} for h in route["hops"]] for route in routes]
    assert len(routes) == 2, f"premise: the stand must need two routes, got {routes}"
    assert {tuple((h["from"], h["to"]) for h in hops) for hops in got} == {
        (("a1", "b1"), ("b1", "a2")),
        (("a1", "c1"), ("c1", "a2")),
    }
    assert [[(h["from"], h["to"], Decimal(h["amount"])) for h in hops] for hops in got] == [
        [(h["from"], h["to"], Decimal(h["amount"])) for h in hops] for hops in expected
    ]
    assert sum(Decimal(hops[0]["amount"]) for hops in got) == Decimal("10"), "the routes carry the whole amount"


@pytest.mark.asyncio
async def test_a_result_without_routes_answers_an_empty_list_and_invents_none(client, db_session, stand, monkeypatch):
    """The core's answer is replaced by one with no recorded route: the response says so; the endpoints are not joined."""
    from app.core.payments.service import PaymentService

    await _seed(db_session)
    original = PaymentService.create_payment_internal

    async def without_routes(self, *a, **kw):
        result = await original(self, *a, **kw)
        return result.model_copy(update={"routes": None})

    monkeypatch.setattr(PaymentService, "create_payment_internal", without_routes)

    r = await client.post(_URL, headers=_HEADERS, json=_body())

    assert r.status_code == 200, r.text
    assert r.json()["routes"] == []


# ---------------------------------------------------------------------------------------------------------------------
# 037 A1, fix-delta: the client must be able to tell "this key is spent for good" from "the outcome is unknown, a repeat
# pays". The error of a keyed payment carries `details.idempotency_key_spent` (true: an `ABORTED` row stands under the
# key and every repeat is that refusal; false: no row, or a committed one - a repeat is executed, or returns the
# committed payment; absent: the state could not be read, the client keeps the key). No key - no field.
# ---------------------------------------------------------------------------------------------------------------------


def _use_a_session_per_request(db_session) -> None:
    """Every request gets its own session on the test's database, as production's `get_db` does (`expire_on_commit=False`)."""
    from app.api.deps import get_db
    from app.main import app
    from tests.conftest import sessionmaker_of

    maker = sessionmaker_of(db_session)

    async def a_session_per_request():
        async with maker(expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[get_db] = a_session_per_request


async def _state_of(db_session, tx_id: str):
    await db_session.commit()
    return (await db_session.execute(select(Transaction.state).where(Transaction.tx_id == tx_id))).scalar_one_or_none()


def _spent(response) -> object:
    """`details.idempotency_key_spent` of an action error, or the string 'ABSENT'."""
    return (response.json().get("details") or {}).get("idempotency_key_spent", "ABSENT")


class _Armed:
    """A one-shot switch the stands below flip from the test."""

    def __init__(self, on: bool = True) -> None:
        self.on = on


@pytest.mark.asyncio
@MODE_B
async def test_a_timeout_after_admission_spends_the_key_and_says_so(client, db_session, stand, monkeypatch):
    """Entry 1: the payment is admitted, the operation times out, `ABORTED/E007` is stored under the key. Every repeat
    is the same 503 - the key is gone - and the answer says so, on the first response and on the repeats alike."""
    from app.core.payments.service import PaymentService

    await _seed(db_session)
    _use_a_session_per_request(db_session)
    real, armed = PaymentService._run_payment_operation, _Armed()

    async def times_out(self, *a, **kw):
        if armed.on:
            raise asyncio.TimeoutError()
        return await real(self, *a, **kw)

    monkeypatch.setattr(PaymentService, "_run_payment_operation", times_out)
    key = _key()

    first = await client.post(_URL, headers=_HEADERS, json=_body(idempotency_key=key))
    stored = await _state_of(db_session, _tx_id_of(_RUN, key))
    armed.on = False
    again = await client.post(_URL, headers=_HEADERS, json=_body(idempotency_key=key))

    assert (first.status_code, first.json().get("code")) == (503, "ENGINE_TIMEOUT"), first.text
    assert stored == "ABORTED", f"premise: the refusal is stored under the derived identity, state={stored!r}"
    assert (again.status_code, again.json().get("code")) == (503, "ENGINE_TIMEOUT"), "premise: every repeat is the same refusal"
    assert (_spent(first), _spent(again)) == (True, True), (first.text, again.text)
    assert await _payments(db_session) == 0
    assert stand.published == []


@pytest.mark.asyncio
@MODE_B
@pytest.mark.parametrize("keyed", [True, False], ids=["keyed", "unkeyed"])
async def test_a_commit_timeout_that_did_not_land_is_a_flat_503_and_a_repeat_with_the_key_pays_once(
    client, db_session, stand, monkeypatch, keyed
):
    """Entry 2: `TimeoutError` on the payment's COMMIT, the commit did not land, no row. The answer is byte-for-byte the
    503 of entry 1 except for the flag; a repeat with the same key pays - once. The branch answers from plain values:
    the rollback after a failed COMMIT expires every ORM object the handler loaded (`_settle_failed_commit` ->
    `_rollback_attempt`), and reading `eq.code` there was a 500 (`MissingGreenlet`), with a key or without."""
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.payments.service import PaymentService

    await _seed(db_session)
    _use_a_session_per_request(db_session)
    real_execute, real_commit = PaymentService.execute, AsyncSession.commit
    armed_commit = _Armed(False)

    async def execute_then_arm(self, *a, **kw):
        out = await real_execute(self, *a, **kw)
        armed_commit.on = True  # the next COMMIT is the payment's
        return out

    async def commit_that_times_out_and_does_not_land(self, *a, **kw):
        if armed_commit.on:
            armed_commit.on = False
            await self.rollback()
            raise asyncio.TimeoutError()
        return await real_commit(self, *a, **kw)

    monkeypatch.setattr(PaymentService, "execute", execute_then_arm)
    monkeypatch.setattr(AsyncSession, "commit", commit_that_times_out_and_does_not_land)
    key = _key()
    body = _body(idempotency_key=key) if keyed else _body()

    first = await client.post(_URL, headers=_HEADERS, json=body)
    monkeypatch.setattr(AsyncSession, "commit", real_commit)
    monkeypatch.setattr(PaymentService, "execute", real_execute)

    assert (first.status_code, first.json().get("code")) == (503, "ENGINE_TIMEOUT"), first.text
    assert first.json()["details"]["equivalent"] == _EQ, "the timeout branch answers from plain values"
    assert await _payments(db_session) == 0, "premise: the commit did not land"
    if not keyed:
        assert _spent(first) == "ABSENT", "no key - no field"
        return
    assert await _state_of(db_session, _tx_id_of(_RUN, key)) is None, "premise: no row under the key"
    assert _spent(first) is False, first.text
    again = await client.post(_URL, headers=_HEADERS, json=body)
    assert again.status_code == 200, again.text
    assert again.json()["payment_id"] == _tx_id_of(_RUN, key)
    assert await _payments(db_session) == 1
    assert await _total_debt(db_session) == Decimal("20")


@pytest.mark.asyncio
@MODE_B
async def test_entry_1_and_entry_2_differ_on_the_wire_only_by_the_flag(client, db_session, stand, monkeypatch):
    """The finding in one test: before the flag the two outcomes - a spent key and a free one - were indistinguishable."""
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.payments.service import PaymentService

    await _seed(db_session)
    _use_a_session_per_request(db_session)
    real_operation, real_execute, real_commit = PaymentService._run_payment_operation, PaymentService.execute, AsyncSession.commit
    mode = {"entry": 1}
    armed_commit = _Armed(False)

    async def operation(self, *a, **kw):
        if mode["entry"] == 1:
            raise asyncio.TimeoutError()
        return await real_operation(self, *a, **kw)

    async def execute_then_arm(self, *a, **kw):
        out = await real_execute(self, *a, **kw)
        if mode["entry"] == 2:
            armed_commit.on = True
        return out

    async def commit(self, *a, **kw):
        if armed_commit.on:
            armed_commit.on = False
            await self.rollback()
            raise asyncio.TimeoutError()
        return await real_commit(self, *a, **kw)

    monkeypatch.setattr(PaymentService, "_run_payment_operation", operation)
    monkeypatch.setattr(PaymentService, "execute", execute_then_arm)
    monkeypatch.setattr(AsyncSession, "commit", commit)

    one = await client.post(_URL, headers=_HEADERS, json=_body(idempotency_key=_key()))
    mode["entry"] = 2
    two = await client.post(_URL, headers=_HEADERS, json=_body(idempotency_key=_key()))

    def without_the_flag(response):
        body = response.json()
        body["details"] = {k: v for k, v in body["details"].items() if k != "idempotency_key_spent"}
        return response.status_code, body

    assert without_the_flag(one) == without_the_flag(two), "premise: the two outcomes are the same refusal but for the flag"
    assert (_spent(one), _spent(two)) == (True, False), (one.text, two.text)


@pytest.mark.asyncio
@MODE_B
async def test_a_commit_that_landed_but_whose_answer_was_lost_is_not_spent_and_a_repeat_returns_the_payment(
    client, db_session, stand, monkeypatch
):
    """The declared behaviour (not turned into a success in this change): the COMMIT lands, `TimeoutError` is raised, the
    recovery read fails - the core raises. The row is `COMMITTED`: the key is NOT spent, and a repeat with the same key
    returns the committed payment. (When the recovery read works the core itself answers the committed payment: 200.)"""
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.payments.service import PaymentService

    await _seed(db_session)
    _use_a_session_per_request(db_session)
    real_execute, real_commit, real_read = PaymentService.execute, AsyncSession.commit, PaymentService._read_existing_row
    armed_commit = _Armed(False)

    async def execute_then_arm(self, *a, **kw):
        out = await real_execute(self, *a, **kw)
        armed_commit.on = True
        return out

    async def commit_that_lands_then_times_out(self, *a, **kw):
        out = await real_commit(self, *a, **kw)
        if armed_commit.on:
            armed_commit.on = False
            raise asyncio.TimeoutError()
        return out

    async def the_recovery_read_fails(self, *a, **kw):
        raise RuntimeError("the recovery read is down")

    monkeypatch.setattr(PaymentService, "execute", execute_then_arm)
    monkeypatch.setattr(AsyncSession, "commit", commit_that_lands_then_times_out)
    monkeypatch.setattr(PaymentService, "_read_existing_row", the_recovery_read_fails)
    key = _key()

    first = await client.post(_URL, headers=_HEADERS, json=_body(idempotency_key=key))
    monkeypatch.setattr(AsyncSession, "commit", real_commit)
    monkeypatch.setattr(PaymentService, "execute", real_execute)
    monkeypatch.setattr(PaymentService, "_read_existing_row", real_read)

    assert first.status_code == 500 and first.json().get("code") == "PAYMENT_REJECTED", first.text
    assert await _state_of(db_session, _tx_id_of(_RUN, key)) == "COMMITTED", "premise: the commit landed"
    assert _spent(first) is False, first.text
    again = await client.post(_URL, headers=_HEADERS, json=_body(idempotency_key=key))
    assert again.status_code == 200, again.text
    assert again.json()["payment_id"] == _tx_id_of(_RUN, key)
    assert await _payments(db_session) == 1
    assert await _total_debt(db_session) == Decimal("20")


@pytest.mark.asyncio
@MODE_B
async def test_a_commit_that_landed_and_is_recovered_answers_the_payment(client, db_session, stand, monkeypatch):
    """The other half of the same schedule: the recovery read works, the core answers the committed payment - a 200."""
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.payments.service import PaymentService

    await _seed(db_session)
    _use_a_session_per_request(db_session)
    real_execute, real_commit = PaymentService.execute, AsyncSession.commit
    armed_commit = _Armed(False)

    async def execute_then_arm(self, *a, **kw):
        out = await real_execute(self, *a, **kw)
        armed_commit.on = True
        return out

    async def commit_that_lands_then_times_out(self, *a, **kw):
        out = await real_commit(self, *a, **kw)
        if armed_commit.on:
            armed_commit.on = False
            raise asyncio.TimeoutError()
        return out

    monkeypatch.setattr(PaymentService, "execute", execute_then_arm)
    monkeypatch.setattr(AsyncSession, "commit", commit_that_lands_then_times_out)
    key = _key()

    answered = await client.post(_URL, headers=_HEADERS, json=_body(idempotency_key=key))

    assert answered.status_code == 200, answered.text
    assert answered.json()["payment_id"] == _tx_id_of(_RUN, key) and answered.json()["routes"]
    assert await _payments(db_session) == 1


@pytest.mark.asyncio
@MODE_B
async def test_a_cancellation_after_admission_spends_the_key_and_the_repeat_says_so(client, db_session, stand, monkeypatch):
    """A real cancellation: the request task is cancelled while the operation stands past admission. The core stores
    `ABORTED/E007 'Payment cancelled'`; the client never got an answer, and the repeat is told the key is spent."""
    from app.core.payments.service import PaymentService

    await _seed(db_session)
    _use_a_session_per_request(db_session)
    real = PaymentService._run_payment_operation
    inside = asyncio.Event()

    async def stands_inside_the_operation(self, *a, **kw):
        inside.set()
        await asyncio.Event().wait()  # until the task is cancelled

    monkeypatch.setattr(PaymentService, "_run_payment_operation", stands_inside_the_operation)
    key = _key()

    request = asyncio.create_task(client.post(_URL, headers=_HEADERS, json=_body(idempotency_key=key)))
    await asyncio.wait_for(inside.wait(), timeout=20)
    request.cancel()
    with pytest.raises(asyncio.CancelledError):
        await request
    monkeypatch.setattr(PaymentService, "_run_payment_operation", real)

    stored = await _state_of(db_session, _tx_id_of(_RUN, key))
    again = await client.post(_URL, headers=_HEADERS, json=_body(idempotency_key=key))

    assert stored == "ABORTED", f"premise: the cancellation after admission is stored, state={stored!r}"
    assert (again.status_code, again.json().get("code")) == (503, "ENGINE_TIMEOUT"), again.text
    assert _spent(again) is True, again.text
    assert await _payments(db_session) == 0


@pytest.mark.asyncio
@MODE_B
async def test_an_internal_failure_after_admission_spends_the_key(client, db_session, stand, monkeypatch):
    """A non-business exception inside the operation after admission is stored as `ABORTED/E010` and re-raised by the
    core: that FIRST attempt is answered by the application's general handler (not by this action's error chain, so it
    carries no flag - the client cannot tell and keeps the key, as it must for an unknown outcome). The repeat is the
    stored refusal, `500 PAYMENT_REJECTED`, and it says the key is spent."""
    from app.core.payments.service import PaymentService

    await _seed(db_session)
    _use_a_session_per_request(db_session)
    real, armed = PaymentService._run_payment_operation, _Armed()

    async def fails(self, *a, **kw):
        if armed.on:
            raise RuntimeError("the book is broken")
        return await real(self, *a, **kw)

    monkeypatch.setattr(PaymentService, "_run_payment_operation", fails)
    key = _key()

    with pytest.raises(RuntimeError, match="the book is broken"):
        await client.post(_URL, headers=_HEADERS, json=_body(idempotency_key=key))
    stored = await _state_of(db_session, _tx_id_of(_RUN, key))
    armed.on = False
    again = await client.post(_URL, headers=_HEADERS, json=_body(idempotency_key=key))
    third = await client.post(_URL, headers=_HEADERS, json=_body(idempotency_key=key))

    assert stored == "ABORTED", f"premise: the failure after admission is stored, state={stored!r}"
    assert (again.status_code, again.json().get("code")) == (500, "PAYMENT_REJECTED"), again.text
    assert _spent(again) is True and again.content == third.content, (again.text, third.text)


@pytest.mark.asyncio
@MODE_B
async def test_a_refusal_before_admission_does_not_spend_the_key(client, db_session, stand):
    await _seed(db_session)
    _use_a_session_per_request(db_session)
    await _lower_the_second_line(db_session, "0")
    key = _key()

    refused = await client.post(_URL, headers=_HEADERS, json=_body(idempotency_key=key))

    assert (refused.status_code, refused.json().get("code")) == (409, "NO_ROUTE"), refused.text
    assert await _state_of(db_session, _tx_id_of(_RUN, key)) is None
    assert _spent(refused) is False, refused.text


@pytest.mark.asyncio
@MODE_B
async def test_a_refusal_after_admission_at_the_book_spends_the_key(client, db_session, stand, monkeypatch):
    await _seed(db_session)
    _use_a_session_per_request(db_session)
    await _lower_the_second_line(db_session, "0")
    forced = _Armed()
    real = PaymentRouter.find_flow_routes

    def forced_past_the_router(self, *a, **kw):
        if forced.on:
            return [(["a1", "b1", "a2"], Decimal("10"))]
        return real(self, *a, **kw)

    monkeypatch.setattr(PaymentRouter, "find_flow_routes", forced_past_the_router)
    key = _key()

    first = await client.post(_URL, headers=_HEADERS, json=_body(idempotency_key=key))
    forced.on = False
    again = await client.post(_URL, headers=_HEADERS, json=_body(idempotency_key=key))

    assert (first.status_code, first.json().get("code")) == (409, "INSUFFICIENT_CAPACITY"), first.text
    assert (again.status_code, again.json().get("code")) == (409, "INSUFFICIENT_CAPACITY"), again.text
    assert (_spent(first), _spent(again)) == (True, True), (first.text, again.text)


@pytest.mark.asyncio
@MODE_B
async def test_an_unkeyed_error_carries_no_flag_and_the_state_read_cannot_break_an_error_answer(
    client, db_session, stand, monkeypatch
):
    """No key - no field, and no read (the recorder sees none). With a key, a state read that FAILS leaves the field out
    and the error answer intact (the client then keeps its key)."""
    import app.db.session as app_db_session

    await _seed(db_session)
    _use_a_session_per_request(db_session)
    await _lower_the_second_line(db_session, "0")

    unkeyed = await client.post(_URL, headers=_HEADERS, json=_body())
    assert (unkeyed.status_code, unkeyed.json().get("code")) == (409, "NO_ROUTE"), unkeyed.text
    assert _spent(unkeyed) == "ABSENT"

    class _Broken:
        def __call__(self, *a, **kw):
            raise RuntimeError("no connection for the state read")

    monkeypatch.setattr(app_db_session, "AsyncSessionLocal", _Broken())
    keyed = await client.post(_URL, headers=_HEADERS, json=_body(idempotency_key=_key()))

    assert (keyed.status_code, keyed.json().get("code")) == (409, "NO_ROUTE"), keyed.text
    assert _spent(keyed) == "ABSENT", "an unknown state is not guessed"


@pytest.mark.asyncio
@MODE_B
async def test_a_second_request_arriving_while_the_first_stands_inside_the_operation_collides_into_one_payment(
    client, db_session, stand, monkeypatch
):
    """Deterministic collision: the first request stands INSIDE the core's operation past admission - its row inserted,
    its transaction not committed - held by an event on `_run_payment_operation` (not a barrier at the handler's door).
    The second request enters the same operation and blocks on the first's row (observed in `pg_stat_activity`); the
    first is released, commits, and the second resolves to the stored payment: one payment, both answers 200 with the
    same routes, at most two publications."""
    from sqlalchemy import text

    from app.core.payments.service import PaymentService

    await _seed(db_session)
    _use_a_session_per_request(db_session)
    real = PaymentService._run_payment_operation
    first_inside, second_entered, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    calls: list[int] = []

    async def held_inside(self, *a, **kw):
        calls.append(1)
        if len(calls) == 1:
            out = await real(self, *a, **kw)  # the row is in, the transaction is open
            first_inside.set()
            await release.wait()
            return out
        second_entered.set()
        return await real(self, *a, **kw)

    monkeypatch.setattr(PaymentService, "_run_payment_operation", held_inside)
    key = _key()

    one = asyncio.create_task(client.post(_URL, headers=_HEADERS, json=_body(idempotency_key=key)))
    await asyncio.wait_for(first_inside.wait(), timeout=20)
    two = asyncio.create_task(client.post(_URL, headers=_HEADERS, json=_body(idempotency_key=key)))
    await asyncio.wait_for(second_entered.wait(), timeout=20)

    async def a_backend_waits_on_a_lock() -> bool:
        await db_session.commit()
        return bool((await db_session.execute(text(
            "select count(*) from pg_stat_activity where datname = current_database() and wait_event_type = 'Lock'"
        ))).scalar_one())

    waited = False
    for _ in range(200):
        if await a_backend_waits_on_a_lock():
            waited = True
            break
        await asyncio.sleep(0.05)  # polling a condition with a bound, not a synchronisation by time
    release.set()
    first, second = await asyncio.gather(one, two)

    assert waited, "premise: the second request is blocked on the first one's uncommitted row"
    assert (first.status_code, second.status_code) == (200, 200), (first.text, second.text)
    assert first.json()["payment_id"] == second.json()["payment_id"] == _tx_id_of(_RUN, key)
    assert first.json()["routes"] == second.json()["routes"] and first.json()["routes"]
    assert await _payments(db_session) == 1
    assert await _total_debt(db_session) == Decimal("20")
    assert 1 <= len(stand.published) <= 2


@pytest.fixture
def real_runs(monkeypatch):
    """No stub of `runtime.get_run`: the runs are real `RunRecord`s with owners, so the real `_check_run_access` decides."""
    import app.api.v1.simulator as simulator_module

    monkeypatch.setattr("app.config.settings.SIMULATOR_ACTIONS_ENABLE", True)
    _CountingEmitter.published = []
    monkeypatch.setattr(simulator_module, "SseEventEmitter", _CountingEmitter)
    PaymentRouter.invalidate_cache()
    return _CountingEmitter


def _owner_run(monkeypatch, run_id: str, owner_id: str, pids=("a1", "b1", "a2")) -> RunRecord:
    run = _register_run(monkeypatch, run_id, pids)
    run.owner_id = owner_id
    run._real_seeded = True
    return run


def _cookie_headers(cookie: str) -> dict:
    from app.core.simulator.session import COOKIE_NAME

    return {"Cookie": f"{COOKIE_NAME}={cookie}", "Origin": "http://localhost"}


@pytest.mark.asyncio
async def test_two_real_owners_pay_twice_and_a_foreign_run_answers_the_same_403_for_any_key(client, db_session, real_runs, monkeypatch):
    """Not the admin: anonymous cookie owners, the real `_check_run_access`. The same key and body in the runs of A and B
    are two payments with two derived ids. A on B's run is refused 403 `ACCESS_DENIED` before anything of the payment is
    read - for B's existing key, for an unknown key and for another body, byte for byte alike (no oracle for keys)."""
    from app.core.simulator.session import create_session

    await _seed(db_session)
    cookie_a, info_a = create_session(settings.SIMULATOR_SESSION_SECRET)
    cookie_b, info_b = create_session(settings.SIMULATOR_SESSION_SECRET)
    _owner_run(monkeypatch, "run-owner-a", info_a.owner_id)
    _owner_run(monkeypatch, "run-owner-b", info_b.owner_id)
    key = _key()

    a = await client.post(_url("run-owner-a"), headers=_cookie_headers(cookie_a), json=_body(idempotency_key=key))
    b = await client.post(_url("run-owner-b"), headers=_cookie_headers(cookie_b), json=_body(idempotency_key=key))
    seen, stop = _statements_on_transactions(db_session)
    try:
        known = await client.post(_url("run-owner-b"), headers=_cookie_headers(cookie_a), json=_body(idempotency_key=key))
        unknown = await client.post(_url("run-owner-b"), headers=_cookie_headers(cookie_a), json=_body(idempotency_key=_key()))
        other_body = await client.post(_url("run-owner-b"), headers=_cookie_headers(cookie_a), json=_body(idempotency_key=key, amount="11"))
    finally:
        stop()

    assert (a.status_code, b.status_code) == (200, 200), (a.text, b.text)
    assert a.json()["payment_id"] == _tx_id_of("run-owner-a", key) and b.json()["payment_id"] == _tx_id_of("run-owner-b", key)
    assert a.json()["payment_id"] != b.json()["payment_id"]
    assert await _payments(db_session) == 2
    assert await _total_debt(db_session) == Decimal("40")
    assert (known.status_code, known.json().get("code")) == (403, "ACCESS_DENIED"), known.text
    assert known.content == unknown.content == other_body.content, "a foreign run must not tell known keys from unknown ones"
    assert seen == [], f"the foreign owner's requests read `transactions`: {seen}"


@pytest.mark.asyncio
async def test_a_real_stop_and_restart_of_the_run_keep_the_key(client, db_session, real_runs, monkeypatch):
    """The lifecycle is the runtime facade's own: `runtime.stop`, then `runtime.restart` (its collaborators that would
    start background work - the heartbeat, the artifacts writer, the storage upsert - are stand-ins). On the stopped run a
    known and an unknown key are answered alike, `409 RUN_TERMINAL`; after the restart the same key returns the same
    payment and routes - one payment, one publication."""
    from unittest.mock import AsyncMock

    import app.core.simulator.storage as simulator_storage
    from app.core.simulator.runtime import runtime

    await _seed(db_session)
    run = _register_run(monkeypatch, "run-p037-lifecycle")
    run._real_seeded = True
    monkeypatch.setattr(simulator_storage, "upsert_run", AsyncMock())
    monkeypatch.setattr(simulator_storage, "sync_artifacts", AsyncMock())
    monkeypatch.setattr(runtime._run_lifecycle, "_ensure_heartbeat", AsyncMock())
    for name in ("stop_events_writer", "finalize_run_artifacts"):
        monkeypatch.setattr(runtime._artifacts, name, AsyncMock())
    monkeypatch.setattr(runtime._artifacts, "start_events_writer", lambda _run_id: None)
    url = _url("run-p037-lifecycle")
    key = _key()

    paid = await client.post(url, headers=_HEADERS, json=_body(idempotency_key=key))
    await runtime.stop("run-p037-lifecycle")
    known = await client.post(url, headers=_HEADERS, json=_body(idempotency_key=key))
    unknown = await client.post(url, headers=_HEADERS, json=_body(idempotency_key=_key()))
    status = await runtime.restart("run-p037-lifecycle")
    again = await client.post(url, headers=_HEADERS, json=_body(idempotency_key=key))

    assert paid.status_code == 200, paid.text
    assert (known.status_code, known.json().get("code")) == (409, "RUN_TERMINAL"), known.text
    assert known.content == unknown.content, "a stopped run must not tell known keys from unknown ones"
    assert status.state == "running" and run._launch_epoch == 1, "premise: a real restart happened"
    assert again.status_code == 200, again.text
    assert again.json()["payment_id"] == paid.json()["payment_id"] == _tx_id_of("run-p037-lifecycle", key)
    assert again.json()["routes"] == paid.json()["routes"] and paid.json()["routes"]
    assert await _payments(db_session) == 1
    assert len(real_runs.published) == 1
