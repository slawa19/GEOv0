import logging
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm.exc import StaleDataError

from app.core.ledger.book import Book, DebtVersionConflict, PaymentFlow
from app.core.payments.service import _classify_payment_db_error
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.utils.exceptions import RetryablePaymentConflictException

from tests.conftest import MODE_B, sessionmaker_of
from tests.debt_setup import debt_fixture_setup, writer_operation


# MODE B (017 stage 2b, T1702). The stale version is made by a SECOND session's commit. In mode A on
# PostgreSQL the seed is never committed - `commit()` releases a SAVEPOINT inside the fixture's
# outer transaction - so another session cannot see it: `NoResultFound` on the second session's read
# (stage-2 catalogue, class VIS). Mode B commits for real on a clone; the second session reaches the
# clone through `sessionmaker_of`, not through `TestingSessionLocal`, which is the tier's database
# there.
@MODE_B
@pytest.mark.asyncio
async def test_apply_flow_raises_a_stale_version_to_the_owner_of_the_transaction(db_session, caplog):
    """A stale debt version is RAISED as `DebtVersionConflict`, not retried in place (019 stage 3).

    Scenario:
    - Session S_stale loads the debt (version N)
    - Session S_fresh updates it (version N+1) and commits
    - S_stale applies a flow over its stale instance and hits `StaleDataError`

    Until 019 stage 3 `_apply_flow` answered by expiring the identity map and retrying inside the SAME
    transaction, and this test expected the retry to win (`80`). That retry reads from the same
    snapshot under SERIALIZABLE, so `FORK-1` removed it: the book raises the narrow
    `DebtVersionConflict` (still a `StaleDataError`), and the owner of the whole transaction - `pay()`,
    or the simulator's money-phase replay - classifies it as a retryable conflict and starts again on a
    fresh session (`tests/integration/test_p019_pay_retries_a_debt_version_conflict_postgres.py`).

    MUTATION: put the retry loop back - the flow then wins on the second try and nothing is raised.
    """

    nonce = uuid.uuid4().hex[:10]
    eq = Equivalent(
        code=("AF" + nonce[:14]).upper(),
        symbol="AF",
        description=None,
        precision=2,
        metadata_={},
        is_active=True,
    )
    sender = Participant(
        pid="S" + nonce,
        display_name="S",
        public_key="pkS-" + nonce,
        type="person",
        status="active",
        profile={},
    )
    receiver = Participant(
        pid="R" + nonce,
        display_name="R",
        public_key="pkR-" + nonce,
        type="person",
        status="active",
        profile={},
    )
    db_session.add_all([eq, sender, receiver])
    await db_session.flush()

    # Receiver owes sender 100.
    debt = Debt(
        debtor_id=receiver.id,
        creditor_id=sender.id,
        equivalent_id=eq.id,
        amount=Decimal("100"),
    )
    async with debt_fixture_setup(db_session, label="setup"):
        db_session.add(debt)
    await db_session.commit()

    async with sessionmaker_of(db_session)() as s_fresh:
        # Bump the version in a separate session.
        d2 = (
            await s_fresh.execute(select(Debt).where(Debt.id == debt.id))
        ).scalar_one()
        async with debt_fixture_setup(s_fresh, label="version-bump"):
            d2.amount = Decimal("90")
        await s_fresh.commit()

    # Load stale instance in db_session and attempt to apply flow.
    _ = (
        await db_session.execute(select(Debt).where(Debt.id == debt.id))
    ).scalar_one()

    debt_id = debt.id
    # THE WRITER'S OWN OPERATION, not a fixture context (design v2 §8 R5/F7). A payment flow is
    # production code that moves money; applied directly it opens no operation, and the journal
    # refuses its flush. Declaring `TEST_FIXTURE` here would journal a payment's effects under the
    # kind reserved for scaffolding, so the real kind is declared instead. Since 019 stage 4 the flow
    # is applied as the payment path applies it - `posting.apply(PaymentFlow(...))` on the open book
    # posting - not through the removed `PaymentEngine._apply_flow` forwarder.
    with caplog.at_level(logging.WARNING, logger="app.core.ledger.book"):
        with pytest.raises(DebtVersionConflict) as raised:
            async with writer_operation(
                db_session, kind="PAYMENT", equivalent_ids=[eq.id], initiator_id=sender.id
            ):
                await Book.current(db_session).apply(
                    PaymentFlow(from_id=sender.id, to_id=receiver.id, amount=Decimal("10"), equivalent_id=eq.id)
                )
    assert isinstance(raised.value, StaleDataError)
    assert isinstance(raised.value.__cause__, StaleDataError)
    assert isinstance(_classify_payment_db_error(raised.value), RetryablePaymentConflictException)
    conflicts = [
        r for r in caplog.records if "event=apply_flow.debt_version_conflict" in r.getMessage()
    ]
    assert len(conflicts) == 1, [r.getMessage() for r in caplog.records]
    await db_session.rollback()

    async with sessionmaker_of(db_session)() as observer:
        stored = (
            await observer.execute(select(Debt.amount).where(Debt.id == debt_id))
        ).scalar_one()
    # The concurrent writer's value stands; the stale flow moved nothing.
    assert stored == Decimal("90")
