"""018 `T1801`, mechanism: a REAL `40001` inside `Book.post` leaves no envelope and no entry behind.

Spec 018, Verification plan §1: "настоящая пара конкурирующих SERIALIZABLE-транзакций, проигравшая
получает 40001 внутри Book.post: исходный SQLSTATE виден предикату повтора, после отката нет ни
конверта, ни записей проигравшей попытки; повтор завершается одним конвертом". And §4: no mocked
exception and no synthetic `40001` - the failure here is PostgreSQL's own.

THE RACE. Debt p0 -> p1 of 10 is committed. The LOSER begins at SERIALIZABLE and takes its snapshot
(one read of the debt). The WINNER then pays through the book - p0 -> p1, 1 - and commits: the row is
11. The loser now posts its own payment through the book; `_apply_payment_flow` updates the row its
snapshot saw at 10, and PostgreSQL refuses: "could not serialize access due to concurrent update",
SQLSTATE 40001, raised inside `Book.post` (the flush of the payment flow).

WHAT IS ASSERTED: the exception reaching the caller carries 40001 and `PaymentEngine`'s own retry
predicate classifies it as retryable (the book did not replace it, e.g. with a failed cleanup); the
context is empty after the book's savepoint rollback; after the loser's rollback there is no envelope
of its identity and no entry of it; the retry on a fresh snapshot completes with exactly one envelope
and one entry, and the debt is 12.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ledger.book import Book, NewDebt, PaymentFlow, operation_for
from app.core.payments.engine import PaymentEngine
from app.db.models.transaction import Transaction
from tests.p018_support import (
    SERIALIZATION_FAILURE,
    context_of,
    entries_of,
    envelopes_named,
    seed_world,
    serializable_engine,
    sqlstate_of,
)


def _payment(tx_id: str, eq: uuid.UUID):
    return operation_for(
        "PAYMENT",
        tx_id,
        {"tx_id": tx_id},
        tx_id=tx_id,
        scope_equivalent_ids={eq},
        intent_equivalent_ids={eq},
    )


@pytest.mark.asyncio
async def test_t1801_a_serialization_failure_inside_the_book_leaves_no_envelope_and_retries_clean(
    committed_database,
) -> None:
    engine = serializable_engine(committed_database.url)

    def session() -> AsyncSession:
        return AsyncSession(bind=engine, expire_on_commit=False, autoflush=False)

    try:
        winner_tx, loser_tx = f"WIN-{uuid.uuid4()}", f"LOSE-{uuid.uuid4()}"
        async with session() as setup:
            world = await seed_world(setup)
            setup.add_all(
                Transaction(tx_id=tx_id, type="PAYMENT", initiator_id=world.p(0), payload={},
                            state="NEW")
                for tx_id in (winner_tx, loser_tx)
            )
            await Book.post(
                setup,
                operation_for("TEST_FIXTURE", f"t1801-40001-{uuid.uuid4()}", {"seed": True}),
                [NewDebt(world.p(0), world.p(1), world.eq, Decimal("10"))],
            )
            await setup.commit()
        flow = PaymentFlow(world.p(0), world.p(1), Decimal("1"), world.eq)

        loser = session()
        try:
            # The loser's snapshot: taken now, before the winner commits.
            seen = (
                await loser.execute(
                    text("SELECT amount FROM debts WHERE equivalent_id = :eq"), {"eq": world.eq}
                )
            ).scalar_one()
            assert seen == Decimal("10.00000000")

            async with session() as winner:
                await Book.post(winner, _payment(winner_tx, world.eq), [flow])
                await winner.commit()

            with pytest.raises(DBAPIError) as caught:
                await Book.post(loser, _payment(loser_tx, world.eq), [flow])
            assert sqlstate_of(caught.value) == SERIALIZATION_FAILURE, caught.value
            assert PaymentEngine(loser)._is_retryable_db_error(caught.value, op="commit")
            assert not hasattr(caught.value, "book_rollback_error")
            assert await context_of(await loser.connection()) in (None, "")
            await loser.rollback()
        finally:
            await loser.close()

        async with engine.connect() as observer:
            assert await envelopes_named(observer, loser_tx) == []
            assert (
                await observer.execute(
                    text(
                        "SELECT count(*) FROM debt_journal_entries e JOIN debt_operations o "
                        "ON o.id = e.operation_id WHERE o.identity = :i"
                    ),
                    {"i": loser_tx},
                )
            ).scalar_one() == 0

        # The retry, on a fresh snapshot: one envelope, one entry, the debt moved once more.
        async with session() as retry:
            await Book.post(retry, _payment(loser_tx, world.eq), [flow])
            await retry.commit()
        async with engine.connect() as observer:
            [envelope] = await envelopes_named(observer, loser_tx)
            assert envelope.state == "COMPLETED"
            entries = await entries_of(observer, envelope.id)
            assert [(row.effect, row.amount_before, row.amount_after) for row in entries] == [
                ("U", Decimal("11.00000000"), Decimal("12.00000000"))
            ]
    finally:
        await engine.dispose()
