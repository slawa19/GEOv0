"""026 S3 (`T2603.1`), В2: a pair with a requested close carries a payment only if the pair's debt SHRINKS.

THE RULE (owner В2, 2026-09-29; spec 026, "Техническая конкретизация В2"): over a pair with a pending close,
`D = debt[A->B] + debt[B->A]` must end the whole operation strictly lower: `D_after < D_before`. The directed
growth limits (S1) and the policies of both active lines still apply; `T2415.2` is unchanged for other pairs.

STAND. A trusts B 100 (to be closed), B trusts A 100, A and C trust each other 100. B owes A 50 by a real
payment, then A asks to close A -> B (the unsigned `execute_close`, the simulator's path into the same service),
so the pair is pending with D = 50. C pays B through A in the direction that repays B. The router, the core
(a route handed to it) and a direct production book call are each checked.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select, update
from sqlalchemy.exc import DBAPIError

from app.core.ledger.book import Book, PaymentFlow
from app.core.payments.router import PaymentRouter
from app.core.payments.service import PaymentService
from app.core.trustlines.service import TrustLineService
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine
from app.db.sqlstate import sqlstate
from app.schemas.trustline import TrustLineCloseRequest, TrustLineUpdateRequest
from app.utils.exceptions import GeoException, IntegrityViolationException
from tests.conftest import MODE_B, sessionmaker_of
from tests.debt_setup import writer_operation
from tests.p019_support import TargetMismatch, require_target


async def _stand(db_session, *, ab_policy=None):
    code = f"V{uuid.uuid4().hex[:6].upper()}"
    eq = Equivalent(code=code, precision=2)
    p = {n: Participant(pid=f"{n}-{code}", display_name=n, public_key=f"pk-{n}-{code}") for n in "ABC"}
    db_session.add_all([eq, *p.values()])
    await db_session.flush()
    for creditor, debtor in (("A", "B"), ("B", "A"), ("A", "C"), ("C", "A")):
        db_session.add(TrustLine(from_participant_id=p[creditor].id, to_participant_id=p[debtor].id,
                                 equivalent_id=eq.id, limit=Decimal("100"), status="active",
                                 policy=(ab_policy if (creditor, debtor) == ("A", "B") else None) or {}))
    await db_session.commit()
    factory = sessionmaker_of(db_session)
    async with factory() as s:
        await PaymentService(s).create_payment_internal(p["B"].id, to_pid=p["A"].pid, equivalent=code, amount="50")
    async with factory() as s:
        line_id = (await s.execute(select(TrustLine.id).where(
            TrustLine.from_participant_id == p["A"].id, TrustLine.to_participant_id == p["B"].id))).scalar_one()
        service = TrustLineService(s)
        batch = service.begin_internal_batch()
        try:
            await service.execute_close(batch, line_id, p["A"].id, TrustLineCloseRequest(signature="-"),
                                        require_signature=False)
        except GeoException as exc:
            raise TargetMismatch(f"setup stops: the close of A -> B with B's debt 50 was refused: {exc}") from exc
        await batch.finish()
        await s.commit()
    return eq, p, factory, line_id


async def _state(factory, eq, p, line_id):
    names = {v.id: k for k, v in p.items()}
    async with factory() as s:
        debts = {(names[d], names[c]): Decimal(str(a)) for d, c, a in (await s.execute(
            select(Debt.debtor_id, Debt.creditor_id, Debt.amount).where(Debt.equivalent_id == eq.id))).all()}
        status = (await s.execute(select(TrustLine.status).where(TrustLine.id == line_id))).scalar_one()
    return debts, status


# (amount C pays B, path forced on the core or None for the router, expected debts, expected A->B status)
_CASES = {
    "decrease": ("30", None, {("B", "A"): 20, ("C", "A"): 30}, "active"),
    "decrease_to_zero": ("50", None, {("C", "A"): 50}, "closed"),
    "decrease_across_zero": ("70", None, {("A", "B"): 20, ("C", "A"): 70}, "closed"),
    "equal_router": ("100", None, None, "active"),
    "equal_core": ("100", "CAB", None, "active"),
    "grow_core": ("120", "CAB", None, "active"),
    "reverse_orientation_core": ("10", "BAC", None, "active"),
}


@pytest.mark.parametrize("case", list(_CASES))
@MODE_B
@pytest.mark.asyncio
async def test_a_pending_pair_carries_only_what_shrinks_its_debt(db_session, case) -> None:
    amount, forced, expected, status = _CASES[case]
    eq, p, factory, line_id = await _stand(db_session)
    before = await _state(factory, eq, p, line_id)
    payer, payee = (forced[0], forced[-1]) if forced else ("C", "B")
    async with factory() as s:
        service = PaymentService(s)
        if forced:
            path = [p[n].pid for n in forced]
            service.router.find_flow_routes = lambda *_a, **_k: [(path, Decimal(amount))]
        try:
            outcome = (await service.create_payment_internal(
                p[payer].id, to_pid=p[payee].pid, equivalent=eq.code, amount=amount)).status
        except GeoException as exc:
            outcome = f"refused {type(exc).__name__}"
    after = await _state(factory, eq, p, line_id)
    want = (({k: Decimal(v) for k, v in expected.items()}, status) if expected else before)
    assert before == ({("B", "A"): Decimal("50")}, "active"), before
    require_target(after == want and outcome.startswith("COMMITTED") == bool(expected),
                   f"{case}: {payer} pays {payee} {amount} -> {outcome}; state {after}, expected {want}")


@pytest.mark.parametrize("side", ["closing_line_forbids_mediation", "other_line_blocks_the_mediator"])
@MODE_B
@pytest.mark.asyncio
async def test_both_lines_policies_still_apply_on_a_pending_pair(db_session, side) -> None:
    eq, p, factory, line_id = await _stand(
        db_session, ab_policy={"can_be_intermediate": False} if side.startswith("closing") else None)
    if side.startswith("other"):  # B's own line B -> A blocks A as a mediator, through the service (unsigned)
        async with factory() as s:
            service = TrustLineService(s)
            ba = (await s.execute(select(TrustLine.id).where(TrustLine.from_participant_id == p["B"].id,
                                                             TrustLine.to_participant_id == p["A"].id))).scalar_one()
            batch = service.begin_internal_batch()
            await service.execute_update(batch, ba, p["B"].id, TrustLineUpdateRequest(
                policy={"blocked_participants": [p["A"].pid]}, signature="-"), require_signature=False)
            await batch.finish()
            await s.commit()
    async with factory() as s:
        service = PaymentService(s)
        path = [p[n].pid for n in "CAB"]
        service.router.find_flow_routes = lambda *_a, **_k: [(path, Decimal("30"))]
        try:
            outcome = (await service.create_payment_internal(
                p["C"].id, to_pid=p["B"].pid, equivalent=eq.code, amount="30")).status
        except GeoException as exc:
            outcome = f"refused {type(exc).__name__}"
    state = await _state(factory, eq, p, line_id)
    require_target(outcome.startswith("refused") and state == ({("B", "A"): Decimal("50")}, "active"),
                   f"{side}: C pays B 30 through A -> {outcome}; {state}")


async def _book_flows(factory, eq, p, flows):
    async with factory() as s:
        try:
            async with writer_operation(s, kind="PAYMENT", equivalent_ids=[eq.id], initiator_id=p["A"].id):
                for payer, payee, amount in flows:
                    await Book.current(s).apply(PaymentFlow(p[payer].id, p[payee].id, Decimal(amount), eq.id))
            await s.commit()
            return "applied"
        except IntegrityViolationException as exc:
            return exc.details.get("invariant")


@MODE_B
@pytest.mark.asyncio
async def test_the_book_judges_the_whole_operation_not_each_flow(db_session) -> None:
    eq, p, factory, line_id = await _stand(db_session)
    # Opposite flows over the pair: D 50 -> 50, refused by the book itself, whoever called it.
    refused = await _book_flows(factory, eq, p, [("A", "B", "10"), ("B", "A", "10")])
    # A temporary zero inside the operation is not the final state: 50 -> 0 -> 20 stays pending.
    partial = await _book_flows(factory, eq, p, [("A", "B", "50"), ("B", "A", "20")])
    debts_partial = await _state(factory, eq, p, line_id)
    # The final zero closes, through a direct production book call.
    closing = await _book_flows(factory, eq, p, [("A", "B", "20")])
    final = await _state(factory, eq, p, line_id)
    require_target(
        (refused, partial, debts_partial, closing, final) == (
            "PENDING_CLOSE_PAIR_NOT_REDUCED", "applied", ({("B", "A"): Decimal("20")}, "active"),
            "applied", ({}, "closed")),
        f"book: opposite flows {refused}; 50->0->20 {partial} {debts_partial}; final zero {closing} {final}")


@pytest.mark.xfail(raises=TargetMismatch, strict=True, reason="red: a frozen requested line is invisible to the router and the core")
@pytest.mark.parametrize("amount", ["120", "50"])
@MODE_B
@pytest.mark.asyncio
async def test_a_frozen_requested_line_still_bounds_and_locks_its_pair(db_session, monkeypatch, amount) -> None:
    # S3 adversarial pass F1: the requested line A -> B is FROZEN, B -> A stays active. The router and the core must
    # still see the request - A -> B bounded by B's debt 50, A -> B locked FOR UPDATE before any debt is written -
    # while the frozen line itself carries no capacity or policy (024 `T2415.2`). 120 must be refused by routing,
    # never by the book (E008); 50 closes the frozen line.
    eq, p, factory, line_id = await _stand(db_session)
    async with factory() as s:
        await s.execute(update(TrustLine).where(TrustLine.id == line_id).values(status="frozen"))
        await s.commit()
    PaymentRouter.invalidate_cache(eq.code)
    segment, probes = PaymentService._segment, []

    async def segment_then_probe(self, *args):
        result = await segment(self, *args)  # the pair's lines are locked; no debt is written yet
        async with factory() as probe:
            try:
                await probe.execute(select(TrustLine.id).where(TrustLine.id == line_id).with_for_update(
                    read=True, nowait=True))
                probes.append("free")
            except DBAPIError as exc:
                probes.append(sqlstate(exc))
            await probe.rollback()
        return result

    monkeypatch.setattr(PaymentService, "_segment", segment_then_probe)
    async with factory() as s:
        try:
            outcome = (await PaymentService(s).create_payment_internal(
                p["A"].id, to_pid=p["B"].pid, equivalent=eq.code, amount=amount)).status
        except GeoException as exc:
            outcome = f"refused {type(exc).__name__}"
    state = await _state(factory, eq, p, line_id)
    if amount == "120":
        ok = (outcome.startswith("refused") and "IntegrityViolation" not in outcome
              and state == ({("B", "A"): Decimal("50")}, "frozen"))
    else:
        ok = outcome.startswith("COMMITTED") and state == ({}, "closed") and probes and set(probes) == {"55P03"}
    require_target(ok, f"A pays B {amount} over a frozen requested A -> B: {outcome}; {state}; probes {probes}")
