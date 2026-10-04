"""028 E2 (`T2821`-`T2825`): the accounting step `10**-precision` at every door, in the pair capacity and in the core.

Owner decision В-4 (2026-10-04): precision is the accounting step - an amount or a limit with more fraction digits
is REFUSED at every entrance, never rounded; no debt is ever a non-multiple of the step; the admin does not lower
the precision of an equivalent that holds data. Each test below names what the base `0a569ba5` did.
"""

from __future__ import annotations

import asyncio
import base64
import uuid
from decimal import Decimal

import pytest
from nacl.signing import SigningKey
from sqlalchemy import select, text

from app.config import settings
from app.core.payments.service import PaymentService
from app.core.simulator.models import RunRecord
from app.core.simulator.real_scenario_seeder import RealScenarioSeeder
from app.core.trustlines.service import TrustLineService
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine
from app.schemas.trustline import TrustLineCloseRequest, TrustLineCreateRequest
from app.utils.exceptions import BadRequestException, GeoException
from tests.conftest import MODE_B, sessionmaker_of
from tests.integration.test_scenarios import (
    _sign_trustline_create_request,
    _sign_trustline_update_request,
    register_and_login,
)

ADMIN = {"X-Admin-Token": settings.ADMIN_TOKEN}
STEP = "amount_precision_exceeded"


def _key(user: dict) -> SigningKey:
    return SigningKey(base64.b64decode(user["priv"]))


def _reason(response) -> str | None:
    return ((response.json().get("error") or {}).get("details") or {}).get("reason")


async def _equivalent(db_session, precision: int = 2) -> Equivalent:
    eq = Equivalent(code=f"S{uuid.uuid4().hex[:7].upper()}", precision=precision, is_active=True)
    db_session.add(eq)
    await db_session.commit()
    return eq


# ------------------------------------------------------------------ F-028-2: the policy door over HTTP


@pytest.mark.parametrize("value", ["NaN", "Infinity"])
@pytest.mark.asyncio
async def test_a_non_finite_max_hop_usage_is_refused_with_400(client, db_session, value) -> None:
    """Base: `"NaN"` -> 500 `E010` (`Decimal('NaN') < 0` raised), `"Infinity"` -> 201 and stored."""

    eq = await _equivalent(db_session)
    lender = await register_and_login(client, f"E2 nan lender {value}")
    borrower = await register_and_login(client, f"E2 nan borrower {value}")
    policy = {"max_hop_usage": value}
    sig = _sign_trustline_create_request(signing_key=_key(lender), to_pid=borrower["pid"],
                                         equivalent=eq.code, limit="10", policy=policy)
    response = await client.post("/api/v1/trustlines", headers=lender["headers"],
                                 json={"to": borrower["pid"], "equivalent": eq.code, "limit": "10",
                                       "policy": policy, "signature": sig})
    assert response.status_code == 400, response.text


# ------------------------------------------------------------------ F-028-23: the doors of a trust line


@pytest.mark.asyncio
async def test_a_trust_line_limit_finer_than_the_step_is_refused_at_create_and_update(client, db_session) -> None:
    """Base: `"10.005"` at precision 2 -> 201 (create) and 200 (update)."""

    eq = await _equivalent(db_session)
    lender = await register_and_login(client, "E2 step lender")
    borrower = await register_and_login(client, "E2 step borrower")

    async def create(limit: str):
        sig = _sign_trustline_create_request(signing_key=_key(lender), to_pid=borrower["pid"],
                                             equivalent=eq.code, limit=limit)
        return await client.post("/api/v1/trustlines", headers=lender["headers"],
                                 json={"to": borrower["pid"], "equivalent": eq.code, "limit": limit, "signature": sig})

    refused = await create("10.005")
    assert refused.status_code == 400 and _reason(refused) == STEP, refused.text
    assert refused.json()["error"]["details"] == {"field": "limit", "reason": STEP, "equivalent": eq.code,
                                                  "precision": 2}
    created = await create("10.500")  # positive control: a multiple, whatever its spelling
    assert created.status_code == 201, created.text

    line_id = created.json()["id"]

    async def update(limit: str):
        sig = _sign_trustline_update_request(signing_key=_key(lender), trustline_id=line_id, limit=limit)
        return await client.patch(f"/api/v1/trustlines/{line_id}", headers=lender["headers"],
                                  json={"limit": limit, "signature": sig})

    refused = await update("12.345")
    assert refused.status_code == 400 and _reason(refused) == STEP, refused.text
    assert (await update("12.30")).status_code == 200


# ------------------------------------------------------------------ F-028-23: the payment door, the pair, the core


async def _payment_stand(db_session, *, pending: bool):
    """A, B, C at precision 2: A -> B direct and A -> C -> B. With `pending`, B owes A 1 and A asked to close
    A -> B (the spec's schedule: debt B->A = 1, close requested, the counter limit 10)."""

    eq = await _equivalent(db_session)
    p = {n: Participant(pid=f"{n}-{eq.code}", display_name=n, public_key=f"pk-{n}-{eq.code}") for n in "ABC"}
    db_session.add_all(p.values())
    await db_session.flush()
    for creditor, debtor in (("A", "B"), ("B", "A"), ("C", "A"), ("B", "C")):
        db_session.add(TrustLine(from_participant_id=p[creditor].id, to_participant_id=p[debtor].id,
                                 equivalent_id=eq.id, limit=Decimal("10"), status="active", policy={}))
    await db_session.commit()
    factory = sessionmaker_of(db_session)
    if pending:
        async with factory() as s:
            await PaymentService(s).create_payment_internal(p["B"].id, to_pid=p["A"].pid, equivalent=eq.code,
                                                            amount="1")
        async with factory() as s:
            line_id = (await s.execute(select(TrustLine.id).where(
                TrustLine.from_participant_id == p["A"].id, TrustLine.to_participant_id == p["B"].id))).scalar_one()
            service = TrustLineService(s)
            batch = service.begin_internal_batch()
            await service.execute_close(batch, line_id, p["A"].id, TrustLineCloseRequest(signature="-"),
                                        require_signature=False)
            await batch.finish()
            await s.commit()
    return eq, p, factory


async def _debts(factory, eq) -> dict:
    async with factory() as s:
        return {(d, c): a for d, c, a in (await s.execute(
            select(Debt.debtor_id, Debt.creditor_id, Debt.amount).where(Debt.equivalent_id == eq.id))).all()}


@MODE_B
@pytest.mark.asyncio
async def test_a_payment_finer_than_the_step_is_refused_at_the_door(db_session) -> None:
    """Base: `0.005` at precision 2 was admitted and committed."""

    eq, p, factory = await _payment_stand(db_session, pending=False)
    async with factory() as s:
        with pytest.raises(BadRequestException) as caught:
            await PaymentService(s).create_payment_internal(p["A"].id, to_pid=p["B"].pid, equivalent=eq.code,
                                                            amount="0.005")
    assert caught.value.details["reason"] == STEP
    assert await _debts(factory, eq) == {}
    async with factory() as s:  # positive control: "0.010" is one step
        result = await PaymentService(s).create_payment_internal(p["A"].id, to_pid=p["B"].pid,
                                                                 equivalent=eq.code, amount="0.010")
    assert result.status == "COMMITTED"


@MODE_B
@pytest.mark.asyncio
async def test_a_pending_pair_never_splits_a_payment_below_the_step(db_session) -> None:
    """Base: `2.00` A -> B became `1.99999999` over the pending pair plus `0.00000001` over A -> C -> B."""

    eq, p, factory = await _payment_stand(db_session, pending=True)
    async with factory() as s:
        result = await PaymentService(s).create_payment_internal(p["A"].id, to_pid=p["B"].pid,
                                                                 equivalent=eq.code, amount="2.00")
    assert result.status == "COMMITTED"
    debts = await _debts(factory, eq)
    assert debts and all(a % Decimal("0.01") == 0 for a in debts.values()), debts


@MODE_B
@pytest.mark.asyncio
async def test_the_core_refuses_a_route_split_finer_than_the_step(db_session) -> None:
    """The core's final check does not trust the router. Base: a forced split `1.995 + 0.005` committed."""

    eq, p, factory = await _payment_stand(db_session, pending=False)
    paths = [[p["A"].pid, p["B"].pid], [p["A"].pid, p["C"].pid, p["B"].pid]]
    async with factory() as s:
        service = PaymentService(s)
        service.router.find_flow_routes = lambda *_a, **_k: [(paths[0], Decimal("1.995")),
                                                             (paths[1], Decimal("0.005"))]
        try:
            outcome = (await service.create_payment_internal(p["A"].id, to_pid=p["B"].pid, equivalent=eq.code,
                                                             amount="2.00")).status
            reason = None
        except GeoException as exc:
            outcome, reason = "refused", (exc.details or {}).get("reason")
    assert (outcome, reason) == ("refused", STEP)
    assert await _debts(factory, eq) == {}


@pytest.mark.asyncio
async def test_the_capacity_probe_refuses_an_amount_finer_than_the_step(client, db_session) -> None:
    eq = await _equivalent(db_session)
    payer = await register_and_login(client, "E2 capacity payer")
    payee = await register_and_login(client, "E2 capacity payee")
    response = await client.get("/api/v1/payments/capacity", headers=payer["headers"],
                                params={"to": payee["pid"], "equivalent": eq.code, "amount": "0.005"})
    assert response.status_code == 400 and _reason(response) == STEP, response.text


# ------------------------------------------------------------------ F-028-3 / F-028-24: scenario seeding


def _scenario(policy=None, limit="10") -> dict:
    n = uuid.uuid4().hex[:6].upper()
    a, b = f"E2S_A_{n}", f"E2S_B_{n}"
    line = {"from": a, "to": b, "equivalent": f"E2{n}", "limit": limit}
    if policy is not None:
        line["policy"] = policy
    return {"equivalents": [f"E2{n}"], "participants": [{"id": a}, {"id": b}], "trustlines": [line]}


@pytest.mark.parametrize("policy,limit,reason", [
    ({"can_be_intermediate": "false"}, "10", "invalid_policy"),
    ({"max_hop_usage": "NaN"}, "10", "invalid_policy"),
    (None, "10.005", STEP),
    (None, "10.000000001", STEP),  # 028 E4 (T2899.1 class 2): unstorable, was skipped silently before the step
])
@pytest.mark.asyncio
async def test_seeding_refuses_a_line_the_doors_would_refuse_and_names_it(db_session, policy, limit, reason) -> None:
    """Base: the string `"false"` was copied and then read by `bool()` as PERMITTING mediation; `10.005` was
    seeded at precision 2."""

    scenario = _scenario(policy, limit)
    with pytest.raises(GeoException) as caught:
        await RealScenarioSeeder().seed_scenario_into_db(session=db_session, scenario=scenario)
    line = scenario["trustlines"][0]
    assert type(caught.value).__name__ == "ScenarioTrustLineRefused"
    assert caught.value.details["reason"] == reason
    assert caught.value.details["line"] == f"{line['from']}->{line['to']} {line['equivalent']}"


@pytest.mark.asyncio
async def test_seeding_still_takes_a_valid_policy_and_a_multiple(db_session) -> None:
    scenario = _scenario({"auto_clearing": False, "can_be_intermediate": False}, "10.50")
    await RealScenarioSeeder().seed_scenario_into_db(session=db_session, scenario=scenario)
    await db_session.flush()
    eq_id = (await db_session.execute(select(Equivalent.id).where(
        Equivalent.code == scenario["equivalents"][0]))).scalar_one()
    [limit] = (await db_session.execute(select(TrustLine.limit).where(TrustLine.equivalent_id == eq_id))).scalars()
    assert limit == Decimal("10.5")


# ------------------------------------------------------------------ F-028-23: the simulator's manual actions


@pytest.fixture
async def sim_stand(db_session, monkeypatch):
    import app.api.v1.simulator as simulator_module

    monkeypatch.setattr("app.config.settings.SIMULATOR_ACTIONS_ENABLE", True)
    eq = Equivalent(code=f"E2{uuid.uuid4().hex[:6].upper()}", precision=2, is_active=True)
    alice, bob = (Participant(pid=f"{n}-{eq.code}", display_name=n, public_key=n[0] * 64, type="person",
                              status="active", profile={}) for n in ("alice", "bob"))
    db_session.add_all([eq, alice, bob])
    await db_session.flush()
    db_session.add(TrustLine(from_participant_id=alice.id, to_participant_id=bob.id, equivalent_id=eq.id,
                             limit=Decimal("10"), status="active", policy={}))
    await db_session.commit()
    run = RunRecord(run_id="e2-run", scenario_id="e2-scenario", mode="real", state="running")
    run._scenario_raw = {"participants": [{"id": alice.pid}, {"id": bob.pid}], "trustlines": []}
    run._edges_by_equivalent = {eq.code: []}
    run._real_participants = [(alice.id, alice.pid), (bob.id, bob.pid)]
    run._real_seeded = True
    monkeypatch.setitem(simulator_module.runtime._runs, "e2-run", run)
    return {"eq": eq, "alice": alice, "bob": bob}


@pytest.mark.parametrize("action,from_,body", [
    ("trustline-create", "bob", {"limit": "10.005"}),
    ("trustline-update", "alice", {"new_limit": "10.005"}),
    ("payment-real", "bob", {"amount": "0.005"}),
])
@pytest.mark.asyncio
async def test_a_manual_action_finer_than_the_step_is_refused(client, sim_stand, action, from_, body) -> None:
    """Base: create and update answered 200 with `10.005`; the payment reached the engine (and committed)."""

    to = "bob" if from_ == "alice" else "alice"
    response = await client.post(f"/api/v1/simulator/runs/e2-run/actions/{action}", headers=ADMIN, json={
        "from_pid": sim_stand[from_].pid, "to_pid": sim_stand[to].pid, "equivalent": sim_stand["eq"].code, **body})
    assert response.status_code == 400, response.text
    answer = response.json()
    assert answer["code"] == "INVALID_AMOUNT" and answer["details"]["reason"] == STEP, answer


# ------------------------------------------------------------------ F-028-25: precision is not lowered under data


@pytest.mark.asyncio
async def test_lowering_precision_of_an_equivalent_with_a_line_is_409(client, db_session) -> None:
    """Base: 200, and the stored `10.50` became finer than the new step."""

    eq, p, _ = await _payment_stand(db_session, pending=False)
    response = await client.patch(f"/api/v1/admin/equivalents/{eq.code}", headers=ADMIN, json={"precision": 1})
    assert response.status_code == 409 and _reason(response) == "precision_in_use", response.text
    raised = await client.patch(f"/api/v1/admin/equivalents/{eq.code}", headers=ADMIN, json={"precision": 3})
    assert raised.status_code == 200, raised.text  # raising is always allowed
    empty = await _equivalent(db_session)
    lowered = await client.patch(f"/api/v1/admin/equivalents/{empty.code}", headers=ADMIN, json={"precision": 0})
    assert lowered.status_code == 200, lowered.text  # nothing stored, nothing to break


@MODE_B
@pytest.mark.asyncio
async def test_a_precision_patch_racing_a_create_waits_and_refuses(client, db_session) -> None:
    """The spec's schedule: the create checked `1.23` at precision 2 and stands before its commit; the PATCH
    lowers to 1. Base: the PATCH did not wait (200), the create committed `1.23` under precision 1."""

    eq = await _equivalent(db_session)
    a, b = (Participant(pid=f"{n}-{eq.code}", display_name=n, public_key=f"pk-{n}-{eq.code}") for n in "AB")
    db_session.add_all([a, b])
    await db_session.commit()
    factory = sessionmaker_of(db_session)
    async with factory() as creator, factory() as probe:
        service = TrustLineService(creator)
        batch = service.begin_internal_batch()
        await service.execute_create(batch, a.id, TrustLineCreateRequest(
            to=b.pid, equivalent=eq.code, limit="1.23", signature="-"), require_signature=False)
        patch = asyncio.create_task(client.patch(f"/api/v1/admin/equivalents/{eq.code}", headers=ADMIN,
                                                 json={"precision": 1}))
        for _ in range(400):  # the barrier: the PATCH is either answered or seen waiting on a lock
            if patch.done() or await probe.scalar(text(
                    "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
                    "AND wait_event_type = 'Lock'")):
                break
            await asyncio.sleep(0.01)
        await batch.finish()
        await creator.commit()
        response = await patch
    async with factory() as s:
        precision = (await s.execute(select(Equivalent.precision).where(Equivalent.id == eq.id))).scalar_one()
        limit = (await s.execute(select(TrustLine.limit).where(TrustLine.equivalent_id == eq.id))).scalar_one()
    assert (response.status_code, _reason(response), precision, limit) == (409, "precision_in_use", 2,
                                                                            Decimal("1.23")), response.text
