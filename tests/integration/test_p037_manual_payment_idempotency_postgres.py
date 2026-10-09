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
    import app.api.v1.simulator as simulator_module
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
    assert simulator_module is not None


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


@pytest.mark.asyncio
async def test_a_stopped_run_answers_run_terminal_before_anything_is_read_even_for_a_stored_key(client, db_session, stand, monkeypatch):
    import app.api.v1.simulator as simulator_module

    await _seed(db_session)
    key = _key()
    paid = await client.post(_URL, headers=_HEADERS, json=_body(idempotency_key=key))
    assert paid.status_code == 200, paid.text

    monkeypatch.setattr(
        simulator_module.runtime,
        "get_run",
        lambda rid: SimpleNamespace(run_id=str(rid), state="stopped", owner_id="", _real_seeded=True, _real_seeding_lock=None),
    )
    stopped = await client.post(_URL, headers=_HEADERS, json=_body(idempotency_key=key))

    assert (stopped.status_code, stopped.json().get("code")) == (409, "RUN_TERMINAL"), stopped.text
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
