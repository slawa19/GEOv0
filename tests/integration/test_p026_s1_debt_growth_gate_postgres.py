"""026 S1 (`T2601`): the trust limit forbids GROWTH over the limit, not the over-limit state (owner, В3).

THE RULE (spec 026, "Решения консультации T2600", fork 3): for every DIRECTED debt a money operation
touches, `after > before => after <= limit` - the creditor's live (`active`/`frozen`) line toward the
debtor, closed or missing = 0. A debt already above the limit may shrink or stay; it may not grow.
The rule runs on the write path, once in the payment (its `before` is the payment's own prestate) and
once in the book's completion (its `before` is the journal's first `amount_before`), so a direct
production `Book` call cannot skip it. The periodic check cannot see `before`: it reports an over-limit
debt on a live line as `over_limit_allowed` and says growth is not verified by a snapshot.

HOW THE OVER-LIMIT STATE IS REACHED HERE, AND WHAT THAT DOES NOT PROVE. The debt is created by a real
payment within the limit; the line is then lowered by an `UPDATE trust_lines` - a stand-in for the
PATCH below `used` that S2 (`T2602`) delivers, which the current service refuses. No row of `debts`
is written by hand (Verification plan §4). R-026-4 as acceptance evidence - excess reached through the
real PATCH/close - is S2's, per the staged plan (Verification plan §1); these tests are its S1 part:
the before/after predicate and a production book trying to grow past an existing limit.

MUTATIONS each of these reddens: `abs(net)` in place of directed debts (the reverse-direction test);
`before` defaulting to zero, in the payment or in the book (the reduction test); the periodic check
back to a critical violation (the reporting test).
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select, text

import app.core.payments.service as payment_service
from app.core.integrity import compute_integrity_checkpoint_for_equivalent
from app.core.invariants import InvariantChecker
from app.core.ledger.book import Book, InjectIncrease, PaymentFlow
from app.core.payments.router import PaymentRouter
from app.core.payments.service import PaymentService
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine
from app.schemas.payment import PaymentCreateRequest
from app.utils.exceptions import IntegrityViolationException
from tests.conftest import MODE_B, sessionmaker_of
from tests.debt_setup import writer_operation
from tests.integration.test_scenarios import register_and_login
from tests.p019_support import require_target

NOT_VERIFIED_GROWTH = {"status": "not_verified", "reason": "requires_operation_prestate"}


async def _parties(session, *lines: tuple[str, str, str]):
    """An equivalent, participants A and B, and `lines` as (creditor, debtor, limit) of live lines."""

    n = uuid.uuid4().hex[:10]
    eq = Equivalent(code=("G" + n).upper(), symbol="G", precision=2, metadata_={}, is_active=True)
    people = {
        role: Participant(pid=role + n, display_name=role, public_key=f"pk{role}-{n}", type="person",
                          status="active", profile={})
        for role in ("A", "B")
    }
    session.add_all([eq, *people.values()])
    await session.flush()
    for creditor, debtor, limit in lines:
        session.add(TrustLine(from_participant_id=people[creditor].id, to_participant_id=people[debtor].id,
                              equivalent_id=eq.id, limit=Decimal(limit), status="active"))
    await session.flush()
    return eq, people["A"], people["B"]


async def _debts(session, eq_id) -> dict[tuple[uuid.UUID, uuid.UUID], Decimal]:
    rows = (await session.execute(
        select(Debt.debtor_id, Debt.creditor_id, Debt.amount).where(Debt.equivalent_id == eq_id))).all()
    return {(d, c): Decimal(str(amount)) for d, c, amount in rows}


async def _pay(factory, sender_id, receiver_pid: str, eq_code: str, amount: str):
    request = PaymentCreateRequest(tx_id="tx-" + uuid.uuid4().hex, to=receiver_pid, equivalent=eq_code,
                                   amount=amount, signature="__internal__")
    try:
        return await PaymentService.pay(factory, sender_id, request, require_signature=False)
    finally:
        PaymentRouter.invalidate_cache(eq_code)


async def _over_limit_world(db_session):
    """B owes A 50 by a real payment on A's line of 100, then A's line is lowered to 10 (S2's stand-in)."""

    eq, a, b = await _parties(db_session, ("A", "B", "100"), ("B", "A", "1"))
    ids = eq.id, eq.code, a.id, a.pid, b.id, b.pid
    await db_session.commit()
    factory = sessionmaker_of(db_session)
    eq_id, eq_code, a_id, a_pid, b_id, _b_pid = ids
    assert (await _pay(factory, b_id, a_pid, eq_code, "50")).status == "COMMITTED"
    async with factory() as s:
        await s.execute(text('UPDATE trust_lines SET "limit" = 10 WHERE from_participant_id = :a '
                             "AND to_participant_id = :b AND equivalent_id = :eq"),
                        {"a": a_id, "b": b_id, "eq": eq_id})
        await s.commit()
    PaymentRouter.invalidate_cache(eq_code)
    async with factory() as s:
        # CONTROL: the state the tests below start from - over the limit, reached without writing `debts`.
        assert await _debts(s, eq_id) == {(b_id, a_id): Decimal("50")}
    return factory, ids


@MODE_B
@pytest.mark.asyncio
async def test_a_payment_that_only_reduces_an_over_limit_debt_commits(db_session) -> None:
    factory, (eq_id, eq_code, a_id, _a_pid, b_id, b_pid) = await _over_limit_world(db_session)

    try:
        result = await _pay(factory, a_id, b_pid, eq_code, "10")
    except IntegrityViolationException as exc:
        require_target(False, f"a reduction 50 -> 40 of an over-limit debt was refused: {exc.details}")
    require_target(result.status == "COMMITTED", f"the reduction did not commit: {result}")
    async with factory() as s:
        assert await _debts(s, eq_id) == {(b_id, a_id): Decimal("40")}


@MODE_B
@pytest.mark.asyncio
async def test_the_periodic_check_reports_an_over_limit_debt_as_allowed(db_session, client) -> None:
    factory, (eq_id, eq_code, a_id, _a_pid, b_id, _b_pid) = await _over_limit_world(db_session)
    user = await register_and_login(client, "P026Observer")

    async with factory() as s:
        try:
            allowed = await InvariantChecker(s).check_trust_limits(equivalent_id=eq_id)
        except IntegrityViolationException as exc:
            require_target(False, f"the snapshot raised the excess as a violation: {exc.details}")
        checkpoint = await compute_integrity_checkpoint_for_equivalent(s, equivalent_id=eq_id)
    (entry,) = allowed
    assert (entry["debtor_id"], entry["creditor_id"]) == (str(b_id), str(a_id))
    assert [Decimal(entry[k]) for k in ("debt_amount", "trust_limit", "excess")] == [50, 10, 40]
    status = checkpoint.invariants_status
    assert (status["status"], status["passed"], status["alerts"]) == ("healthy", True, [])
    trust = status["checks"]["trust_limits"]
    assert (trust["passed"], trust["violations"], trust["growth"]) == (True, 0, NOT_VERIFIED_GROWTH)
    assert trust["over_limit_allowed"] == allowed

    for response in (await client.get("/api/v1/integrity/status", headers=user["headers"]),
                     await client.post("/api/v1/integrity/verify", json={"equivalent": eq_code},
                                       headers=user["headers"])):
        assert response.status_code == 200, response.text
        body = response.json()
        view = body["equivalents"][eq_code]
        # `/status` also folds in the reconciliation (no result here: warning); the limit adds nothing.
        assert view["status"] != "critical" and not any("Trust limit" in a for a in body["alerts"]), body
        assert view["invariants"]["trust_limits"]["passed"] is True
        assert view["invariants"]["trust_limits"]["over_limit_allowed"] == allowed
        assert view["invariants"]["trust_limits"]["growth"] == NOT_VERIFIED_GROWTH


async def _book_refusal(db_session, kind: str, eq, effect) -> IntegrityViolationException | None:
    initiator = getattr(effect, "from_id", None)
    try:
        async with writer_operation(db_session, kind=kind, equivalent_ids=[eq.id], initiator_id=initiator):
            await Book.current(db_session).apply(effect)
    except IntegrityViolationException as exc:
        return exc
    return None


@pytest.mark.asyncio
async def test_a_direct_book_payment_cannot_grow_a_debt_over_the_limit(db_session) -> None:
    eq, a, b = await _parties(db_session, ("A", "B", "10"))
    # COUNTER-CHECK first: growth up to the limit is applied (the gate is not a blanket refusal).
    assert await _book_refusal(db_session, "PAYMENT", eq, PaymentFlow(b.id, a.id, Decimal("10"), eq.id)) is None
    assert await _debts(db_session, eq.id) == {(b.id, a.id): Decimal("10")}

    refused = await _book_refusal(db_session, "PAYMENT", eq, PaymentFlow(b.id, a.id, Decimal("0.01"), eq.id))
    require_target(refused is not None, "the book let B's debt grow 10 -> 10.01 over A's limit of 10")
    assert refused.details["invariant"] == "TRUST_LIMIT_VIOLATION"
    assert await _debts(db_session, eq.id) == {(b.id, a.id): Decimal("10")}


@pytest.mark.asyncio
async def test_a_direct_book_payment_cannot_create_a_reverse_debt_without_a_line(db_session) -> None:
    eq, a, b = await _parties(db_session, ("A", "B", "100"))
    assert await _book_refusal(db_session, "PAYMENT", eq, PaymentFlow(b.id, a.id, Decimal("50"), eq.id)) is None

    # A pays B 80: B's debt of 50 is repaid and A now owes B 30 - but B extends A no line (limit 0).
    # |net| falls 50 -> 30, so an `abs(net)` rule would accept it; the directed debt A->B grew 0 -> 30.
    refused = await _book_refusal(db_session, "PAYMENT", eq, PaymentFlow(a.id, b.id, Decimal("80"), eq.id))
    require_target(refused is not None, "the book created A->B 30 against a limit of 0")
    assert await _debts(db_session, eq.id) == {(b.id, a.id): Decimal("50")}


@pytest.mark.asyncio
async def test_inject_cannot_pass_the_real_limit_with_a_larger_ceiling(db_session) -> None:
    eq, a, b = await _parties(db_session, ("A", "B", "10"))
    effect = InjectIncrease(debtor_id=b.id, creditor_id=a.id, equivalent_id=eq.id, amount=Decimal("20"),
                            ceiling=Decimal("100"))
    refused = await _book_refusal(db_session, "INJECT", eq, effect)
    require_target(refused is not None, "INJECT wrote B->A 20 against A's limit of 10 because its ceiling said 100")
    assert await _debts(db_session, eq.id) == {}


@MODE_B
@pytest.mark.asyncio
async def test_a_payment_missing_the_prestate_of_a_touched_debt_is_refused_not_read_as_zero(
    db_session, monkeypatch
) -> None:
    eq, a, b = await _parties(db_session, ("A", "B", "100"))
    eq_id, eq_code, a_id, a_pid, b_id = eq.id, eq.code, a.id, a.pid, b.id
    await db_session.commit()
    original = payment_service._read_payment_prestate

    async def one_direction_dropped(session, flows):
        return (await original(session, flows))[1:]

    monkeypatch.setattr(payment_service, "_read_payment_prestate", one_direction_dropped)
    factory = sessionmaker_of(db_session)
    with pytest.raises(Exception) as refused:
        await _pay(factory, b_id, a_pid, eq_code, "5")
    chain = [refused.value, refused.value.__cause__, refused.value.__context__]
    assert any("no prestate" in str(exc) for exc in chain if exc is not None), chain
    async with factory() as s:
        assert await _debts(s, eq_id) == {}