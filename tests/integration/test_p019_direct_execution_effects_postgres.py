"""Programme 019 stage 4 (`T1906`, `FORK-9`): the effects of the old engine commit, on DIRECT execution.

Since stage 4 `PaymentService` executes a payment itself - no `PaymentEngine.prepare`/`.commit`. The
three effects the engine's commit had besides moving money are carried inside the payment operation's
rollback boundary, and this module holds them on the ordinary API path (`pay()`, mode B, SERIALIZABLE):

1. `check_trust_limits` - after the writes and a flush, inside the operation: a payment whose debts end
   over a creditor's limit is refused, the operation rolls back (money, envelope, the `COMMITTED` row,
   the audit row) and the admitted refusal is recorded `ABORTED` with the invariant named.
2. the integrity audit - ONE `IntegrityAuditLog` row per equivalent for a committed payment, in the
   payment's own transaction (a refused payment leaves none), with the participants of the route.
3. the metrics - `PAYMENT_EVENTS_TOTAL{commit,success}` once per confirmed commit; the obsolete
   `{prepare,success}` emission is gone with the prepare phase.

THE CORRUPTION IS REAL AND NARROW. The limit test does not fake the check: the flow is applied by the
real book, and then - inside the same transaction, through the book's own seam `_apply_payment_flow` -
the creditor's line is lowered below the debt it now secures. The per-participant delta check passes
(the flows are exactly the declared ones), so ONLY the trust-limit check can refuse it.

MUTATIONS: drop `check_trust_limits` from `PaymentService._apply_payment` - the limit test commits the
payment (red); drop `_write_integrity_audit` - the audit test finds no row (red).
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.core.ledger.book as book_module
from app.core.payments.service import PaymentService
from app.db.journal_tables import debt_operations
from app.db.models.audit_log import IntegrityAuditLog
from app.db.models.transaction import Transaction
from app.schemas.payment import PaymentCreateRequest
from app.utils.exceptions import IntegrityViolationException
from app.utils.metrics import PAYMENT_EVENTS_TOTAL
from tests.integration.test_p015_p1_money_replay_postgres import (
    _OPENING,
    _debts,
    _forget_the_route_cache,
    _seed,
)


@pytest_asyncio.fixture
async def factory(committed_database):
    engine = create_async_engine(
        committed_database.url, pool_size=5, max_overflow=0, isolation_level="SERIALIZABLE"
    )
    try:
        yield async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False)
    finally:
        await engine.dispose()


def _metric(event: str, result: str) -> float:
    return PAYMENT_EVENTS_TOTAL.labels(event=event, result=result)._value.get()


def _request(world, amount: str) -> PaymentCreateRequest:
    return PaymentCreateRequest(
        tx_id=str(uuid.uuid4()),
        to=world.receiver.pid,
        equivalent=world.equivalent.code,
        amount=amount,
        signature="__internal__",
    )


async def _audit_rows(factory, tx_id: str) -> list[IntegrityAuditLog]:
    async with factory() as s:
        return list(
            (
                await s.execute(select(IntegrityAuditLog).where(IntegrityAuditLog.tx_id == tx_id))
            ).scalars().all()
        )


async def _envelopes(factory, tx_id: str) -> int:
    async with factory() as s:
        return int(
            await s.scalar(
                select(func.count()).select_from(debt_operations).where(debt_operations.c.tx_id == tx_id)
            )
        )


@pytest.mark.asyncio
async def test_a_committed_payment_writes_one_audit_row_and_counts_one_commit_and_no_prepare(factory) -> None:
    world = await _seed(factory)
    request = _request(world, "10.00")
    commit_before = _metric("commit", "success")
    prepare_before = _metric("prepare", "success")
    try:
        result = await PaymentService.pay(factory, world.sender.id, request, require_signature=False)
    finally:
        _forget_the_route_cache(world)

    # NON-VACUITY: the payment really committed and really moved the money.
    assert result.status == "COMMITTED", result
    assert await _debts(factory, world) == {
        (world.sender.pid, world.receiver.pid): _OPENING + Decimal("10.00")
    }
    assert await _envelopes(factory, request.tx_id) == 1

    rows = await _audit_rows(factory, request.tx_id)
    assert len(rows) == 1, (
        f"a committed payment in one equivalent must leave exactly ONE integrity audit row (FIX-014, "
        f"carried into the payment operation by 019 stage 4): {rows}"
    )
    row = rows[0]
    assert row.operation_type == "PAYMENT" and row.equivalent_code == world.equivalent.code, row
    assert row.verification_passed is True, row.error_details
    assert row.affected_participants == {
        "participants": sorted([world.sender.pid, world.receiver.pid])
    }, row.affected_participants
    assert row.state_checksum_before and row.state_checksum_after, row
    assert row.state_checksum_before != row.state_checksum_after, (
        "the audit row's checksums are equal although the payment moved money: the 'before' "
        "checkpoint was not taken before the flows"
    )

    assert _metric("commit", "success") - commit_before == 1
    assert _metric("prepare", "success") - prepare_before == 0, (
        "the obsolete prepare-success metric is still emitted (019 stage 4 removes it)"
    )


@pytest.mark.asyncio
async def test_a_payment_that_ends_over_a_limit_is_refused_and_leaves_nothing_but_its_refusal(
    factory, monkeypatch
) -> None:
    world = await _seed(factory)
    original = book_module._apply_payment_flow
    lowered: list[int] = []

    async def apply_then_lower_the_creditors_line(session, flow):
        applied = await original(session, flow)
        # The creditor's line is lowered below the debt it now secures, in the payment's own
        # transaction: every later check sees it, and the declared flows are untouched.
        await session.execute(
            text(
                "UPDATE trust_lines SET \"limit\" = 1 WHERE from_participant_id = :creditor "
                "AND to_participant_id = :debtor AND equivalent_id = :eq"
            ),
            {"creditor": world.receiver.id, "debtor": world.sender.id, "eq": world.equivalent.id},
        )
        lowered.append(1)
        return applied

    monkeypatch.setattr(book_module, "_apply_payment_flow", apply_then_lower_the_creditors_line)
    request = _request(world, "10.00")
    commit_before = _metric("commit", "success")
    try:
        with pytest.raises(IntegrityViolationException) as refused:
            await PaymentService.pay(factory, world.sender.id, request, require_signature=False)
    finally:
        _forget_the_route_cache(world)

    # NON-VACUITY: the corruption really ran inside the payment.
    assert lowered == [1], "premise: the flow was never applied, so nothing was checked"

    assert (refused.value.details or {}).get("invariant") == "TRUST_LIMIT_VIOLATION", (
        f"the payment was refused, but not by the trust-limit check: {refused.value.details}"
    )
    # The operation rolled back as a whole: money, the lowered line, the envelope, the audit.
    assert await _debts(factory, world) == {(world.sender.pid, world.receiver.pid): _OPENING}
    async with factory() as s:
        limit = await s.scalar(
            text(
                "SELECT \"limit\" FROM trust_lines WHERE from_participant_id = :creditor "
                "AND to_participant_id = :debtor AND equivalent_id = :eq"
            ),
            {"creditor": world.receiver.id, "debtor": world.sender.id, "eq": world.equivalent.id},
        )
        stored = (
            await s.execute(select(Transaction).where(Transaction.tx_id == request.tx_id))
        ).scalar_one()
    assert Decimal(str(limit)) != Decimal("1"), "the lowered line outlived the refused payment"
    assert await _envelopes(factory, request.tx_id) == 0
    assert await _audit_rows(factory, request.tx_id) == []
    # Admitted, then refused: the refusal is durable (Q1), and it names the invariant.
    assert stored.state == "ABORTED", stored.state
    assert (stored.error or {}).get("details", {}).get("invariant") == "TRUST_LIMIT_VIOLATION", stored.error
    assert _metric("commit", "success") - commit_before == 0
