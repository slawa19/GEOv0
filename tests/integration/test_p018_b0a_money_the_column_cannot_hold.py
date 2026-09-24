"""018 / FORK-1 (slice B0a): money the column cannot hold is refused by the book and at bind.

Before stage B removes the debt journal's listener, the listener's `_check_storable` was the only
writer-level refusal of an amount `NUMERIC(20, 8)` cannot hold exactly. PostgreSQL cannot take it
over: the column coerces `0.123456789` to `0.12345679` before any CHECK or trigger sees it. The
consultation of 2026-09-24 (`specs/018-single-debt-writer/spec.md`, stage B decisions) decided ONE
shared predicate (`app/utils/validation.py::money_storability_violation`) enforced at two
boundaries, and this module holds both:

* THE BOOK, before a debt changes - on an effect's input AND on the amount the book calculates, so
  a valid increment that overflows an existing debt is refused too (`BookMoneyError`, with the
  predicate's name as `reason`). The listener is still armed in B0; these tests assert that the
  refusal the caller sees is the BOOK's, which is new behaviour - on the tree before B0a the same
  writes were refused by the journal (`DebtJournalError`) and these assertions were red.
* `MoneyNumeric` AT BIND, for a typed ORM/Core write that never came through the book - here a
  trust line's limit, where nothing refused a ninth digit before (PostgreSQL stored it rounded),
  and a magnitude overflow surfaced as the driver's numeric-overflow error.

Counter-checks (anti-vacuum, `AGENTS.md` §9): insignificant trailing zeros and the full width
`999999999999.99999999` pass both boundaries and are stored exactly.

MUTATIONS that must redden this module, each measured on 2026-09-24 (spec 018 changelog):
make `book._refuse_unstorable` a no-op - the book tests fail (the journal refuses instead, or the
overflow reaches the database); make `book._set_amount` assign without checking - only the overflow
tests fail (the input check alone cannot see a sum); make `MoneyNumeric._refuse_unstorable` a
no-op - the bind tests fail.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import StatementError

from app.core.ledger.book import (
    APPLIED,
    Book,
    BookMoneyError,
    InjectIncrease,
    PaymentFlow,
)
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine
from app.db.types import MoneyNumeric
from tests.debt_setup import debt_fixture_setup, writer_operation

FULL_WIDTH = Decimal("999999999999.99999999")
SCALE_9 = Decimal("0.123456789")


async def _world(session, *, debt_b_owes_a: str | None = None):
    n = uuid.uuid4().hex[:10]
    eq = Equivalent(code=("M" + n[:15]).upper(), symbol="M", precision=2, metadata_={},
                    is_active=True)
    a = Participant(pid="A" + n, display_name="A", public_key="pkA-" + n, type="person",
                    status="active", profile={})
    b = Participant(pid="B" + n, display_name="B", public_key="pkB-" + n, type="person",
                    status="active", profile={})
    session.add_all([eq, a, b])
    await session.flush()
    if debt_b_owes_a is not None:
        async with debt_fixture_setup(session, label="p018-b0a-precision"):
            session.add(Debt(debtor_id=b.id, creditor_id=a.id, equivalent_id=eq.id,
                             amount=Decimal(debt_b_owes_a)))
        await session.flush()
    return eq, a, b


async def _debts(session, eq) -> dict[tuple[uuid.UUID, uuid.UUID], str]:
    rows = (
        await session.execute(
            select(Debt.debtor_id, Debt.creditor_id, Debt.amount).where(Debt.equivalent_id == eq.id)
        )
    ).all()
    return {(d, c): format(Decimal(str(amount)), "f") for d, c, amount in rows}


# =================================================================================================
# The book
# =================================================================================================


@pytest.mark.asyncio
async def test_the_book_refuses_a_scale_9_input_before_any_debt_changes(db_session) -> None:
    eq, a, b = await _world(db_session, debt_b_owes_a="5.00")
    with pytest.raises(BookMoneyError) as refused:
        async with writer_operation(db_session, kind="INJECT", equivalent_ids=[eq.id]):
            await Book.current(db_session).apply(
                InjectIncrease(debtor_id=b.id, creditor_id=a.id, equivalent_id=eq.id,
                               amount=SCALE_9, ceiling=Decimal("100"))
            )
    assert refused.value.reason == "money_quantization", refused.value
    await db_session.rollback()


@pytest.mark.asyncio
async def test_the_book_refuses_an_inject_sum_that_overflows_the_column(db_session) -> None:
    """The input `1` is storable; the SUM is not. Only a check on the calculated amount sees it."""

    eq, a, b = await _world(db_session, debt_b_owes_a="999999999999.5")
    with pytest.raises(BookMoneyError) as refused:
        async with writer_operation(db_session, kind="INJECT", equivalent_ids=[eq.id]):
            await Book.current(db_session).apply(
                InjectIncrease(debtor_id=b.id, creditor_id=a.id, equivalent_id=eq.id,
                               amount=Decimal("1"), ceiling=Decimal("1E13"))
            )
    assert refused.value.reason == "money_magnitude", refused.value
    await db_session.rollback()


@pytest.mark.asyncio
async def test_the_book_refuses_a_payment_sum_that_overflows_the_column(db_session) -> None:
    """The same overflow on the payment algebra: the sender's existing debt grows past 10^12."""

    eq, a, b = await _world(db_session)
    async with debt_fixture_setup(db_session, label="p018-b0a-precision"):
        db_session.add(Debt(debtor_id=a.id, creditor_id=b.id, equivalent_id=eq.id,
                            amount=Decimal("999999999999.5")))
    await db_session.flush()
    with pytest.raises(BookMoneyError) as refused:
        async with writer_operation(db_session, kind="PAYMENT", equivalent_ids=[eq.id],
                                    initiator_id=a.id):
            await Book.current(db_session).apply(
                PaymentFlow(from_id=a.id, to_id=b.id, amount=Decimal("1"), equivalent_id=eq.id)
            )
    assert refused.value.reason == "money_magnitude", refused.value
    await db_session.rollback()


@pytest.mark.asyncio
async def test_the_book_accepts_trailing_zeros_and_the_full_width(db_session) -> None:
    """Counter-check: the refusals above are not a refusal of long spellings or of large money."""

    eq, a, b = await _world(db_session, debt_b_owes_a="1.00")
    async with writer_operation(db_session, kind="INJECT", equivalent_ids=[eq.id]):
        posting = Book.current(db_session)
        # Ten fraction digits, the last two zero: the same number as 0.10000000.
        trailing = await posting.apply(
            InjectIncrease(debtor_id=b.id, creditor_id=a.id, equivalent_id=eq.id,
                           amount=Decimal("0.1000000000"), ceiling=Decimal("1E13"))
        )
        # Grow to exactly the column's maximum.
        full = await posting.apply(
            InjectIncrease(debtor_id=b.id, creditor_id=a.id, equivalent_id=eq.id,
                           amount=FULL_WIDTH - Decimal("1.1"), ceiling=Decimal("1E13"))
        )
    await db_session.flush()
    assert (trailing, full) == (APPLIED, APPLIED)
    assert await _debts(db_session, eq) == {(b.id, a.id): format(FULL_WIDTH, "f")}


# =================================================================================================
# MoneyNumeric at bind
# =================================================================================================


async def _trust_line(session, *, limit):
    eq, a, b = await _world(session)
    line = TrustLine(from_participant_id=a.id, to_participant_id=b.id, equivalent_id=eq.id,
                     limit=limit, status="active")
    session.add(line)
    return line


@pytest.mark.parametrize(
    ("limit", "reason"),
    [(SCALE_9, "money_quantization"), (Decimal("1E12"), "money_magnitude")],
)
@pytest.mark.asyncio
async def test_a_typed_write_outside_the_book_is_refused_at_bind(db_session, limit, reason) -> None:
    """Before B0a PostgreSQL stored `0.12345679` for the first and raised numeric overflow for the
    second; neither was refused as money before the statement was sent."""

    await _trust_line(db_session, limit=limit)
    with pytest.raises(StatementError) as refused:
        await db_session.flush()
    assert isinstance(refused.value.orig, ValueError), refused.value
    assert str(refused.value.orig).startswith(f"{reason}:"), refused.value.orig
    await db_session.rollback()


@pytest.mark.parametrize("limit", [Decimal("5.0000000000"), FULL_WIDTH])
@pytest.mark.asyncio
async def test_a_typed_write_of_storable_money_is_accepted_at_bind(db_session, limit) -> None:
    """Counter-check: insignificant trailing zeros and the full width bind and store exactly."""

    line = await _trust_line(db_session, limit=limit)
    await db_session.flush()
    stored = (
        await db_session.execute(select(TrustLine.limit).where(TrustLine.id == line.id))
    ).scalar_one()
    assert Decimal(str(stored)) == limit
    await db_session.rollback()


#: Every column typed `MoneyNumeric` in the application metadata, enumerated 2026-09-24 (B0a).
#: A new money column must be added here deliberately - its binding contract is the one below.
MONEY_NUMERIC_COLUMNS = {
    "debts.amount",
    "trust_lines.limit",
    "debt_journal_entries.amount_before",
    "debt_journal_entries.amount_after",
    "debt_journal_entries.delta",
    "debt_reconciliation_baseline_offsets.offset_amount",
}


def test_every_money_numeric_column_applies_the_same_binding_contract() -> None:
    import app.db.models  # noqa: F401 - registers every table on the metadata
    from app.db.base import Base

    columns = {
        f"{table.name}.{column.name}": column
        for table in Base.metadata.tables.values()
        for column in table.columns
        if isinstance(column.type, MoneyNumeric)
    }
    assert set(columns) == MONEY_NUMERIC_COLUMNS, sorted(columns)
    for name, column in columns.items():
        bind = column.type.process_bind_param
        for good in (Decimal("5.0000000000"), FULL_WIDTH, -FULL_WIDTH, Decimal("0")):
            assert bind(good, None) == good, name
        for bad in (SCALE_9, Decimal("1E12"), Decimal("NaN")):
            with pytest.raises(ValueError):
                bind(bad, None)
