"""T1523 cell 7: the commit really landed, then the caller failed before the response.

WHAT THE INVENTORY FOUND. `tests/unit/test_payment_timeouts.py:112` already asserted that a commit
timeout returns COMMITTED and calls no abort - but its "commit" was a fake: an `UPDATE
transactions SET state='COMMITTED'` with no debt, no lock release and no journal
(`test_payment_timeouts.py:186-194`). It therefore measured the read-after-timeout branch against a
transaction that had moved no money, which is the one case where "the effects are there exactly
once" is trivially true. The DB-error twin of that branch (`app/core/payments/service.py:1155-1185`)
had no test at all.

WHAT THIS MODULE DOES INSTEAD. The REAL commit runs - debts, locks, audit and the journal envelope -
and only then the caller's own step fails, in the two ways the service distinguishes.

SINCE 019 STAGE 3 (`T1904`) the payment is one transaction and the commit that makes it durable is
`PaymentService.pay`'s own `session.commit()` - `PaymentEngine.commit` now runs inside that
transaction and is durable only through it. So the seam moved from the engine's commit to the outer
one: the session's real commit returns, and then

* the caller's deadline (`PAYMENT_TOTAL_TIMEOUT_SECONDS`) expires around it, or
* a database error is raised after it,

both handled by `PaymentService._settle_failed_commit`: the outcome of the commit is unknown to the
caller, so the stored row is read before anything is terminalized. Both must answer COMMITTED from the
stored row, must record no refusal, and must leave one effect.

WHY IT IS NOT VACUOUS. Three assertions carry it, and each fails on its own:
1. the premise - exactly one COMPLETED envelope whose `effect_count` equals its entry rows, and a
   debt of the paid amount - so "the effects are there once" is not green on a payment that wrote
   nothing (the defect this module exists to remove);
2. the refusal recorder (`record_definitive_refusal` in `app/core/payments/service.py`; before stage 3 `engine.abort`) is
   recorded, not executed, and must never have been called - that is the mechanism behind the
   absence of a reversal, asserted directly. Delete the read-before-terminalizing in the service and
   this goes red even though the money is still in place at the end;
3. the failure really happened: the injected step is recorded, and the test asserts the real commit
   completed BEFORE it.

TIER. SQLite. What is exercised is the service's own failure handling, which is dialect-independent;
what this tier cannot show is a commit that is durable on one connection and unseen on another. The
process-restart form of that question is cell 8, on PostgreSQL.
"""

from __future__ import annotations

import asyncio
import base64
import uuid
from decimal import Decimal

import pytest
from nacl.signing import SigningKey
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from app.config import settings
from app.core.auth.canonical import canonical_json
from app.core.auth.crypto import generate_keypair, get_pid_from_public_key
from app.core.payments.router import PaymentRouter
import app.core.payments.service as payment_service_module
from app.core.payments.service import PaymentService
from app.db.journal_tables import debt_journal_entries, debt_operations
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.transaction import Transaction
from app.db.models.trustline import TrustLine
from app.schemas.payment import PaymentCreateRequest


async def _seed(db_session) -> tuple[Participant, Participant, Equivalent, str]:
    """A sender who can pay a receiver 10.00 over one real trust line."""

    code = "T23" + uuid.uuid4().hex[:6].upper()
    sender_pub, sender_priv = generate_keypair()
    receiver_pub, _receiver_priv = generate_keypair()

    sender = Participant(
        id=uuid.uuid4(),
        pid=get_pid_from_public_key(sender_pub),
        display_name="Sender",
        public_key=sender_pub,
        type="person",
        status="active",
        profile={},
    )
    receiver = Participant(
        id=uuid.uuid4(),
        pid=get_pid_from_public_key(receiver_pub),
        display_name="Receiver",
        public_key=receiver_pub,
        type="person",
        status="active",
        profile={},
    )
    equivalent = Equivalent(code=code, precision=2, is_active=True)
    db_session.add_all([sender, receiver, equivalent])
    await db_session.commit()

    # TrustLine(from=receiver, to=sender) is the graph edge sender -> receiver.
    db_session.add(
        TrustLine(
            from_participant_id=receiver.id,
            to_participant_id=sender.id,
            equivalent_id=equivalent.id,
            limit=Decimal("100.00"),
            status="active",
        )
    )
    await db_session.commit()
    PaymentRouter.invalidate_cache()
    return sender, receiver, equivalent, sender_priv


def _signed_request(sender_priv: str, receiver_pid: str, code: str) -> PaymentCreateRequest:
    tx_id = str(uuid.uuid4())
    message = canonical_json(
        {"tx_id": tx_id, "to": receiver_pid, "equivalent": code, "amount": "10.00"}
    )
    signature = base64.b64encode(
        SigningKey(base64.b64decode(sender_priv)).sign(message).signature
    ).decode("utf-8")
    return PaymentCreateRequest(
        tx_id=tx_id,
        to=receiver_pid,
        equivalent=code,
        amount="10.00",
        signature=signature,
    )


async def _one_effect(db_session, tx_id: str) -> dict[str, object]:
    """The state a single successful commit leaves, as the three tables see it."""

    transactions = sorted(
        (str(state),)
        for (state,) in (
            await db_session.execute(
                select(Transaction.state).where(Transaction.tx_id == tx_id)
            )
        ).all()
    )
    debts = sorted(
        (str(debtor), str(creditor), str(amount))
        for debtor, creditor, amount in (
            await db_session.execute(
                select(Debt.debtor_id, Debt.creditor_id, Debt.amount)
            )
        ).all()
    )
    envelopes = (
        await db_session.execute(
            select(
                debt_operations.c.id,
                debt_operations.c.state,
                debt_operations.c.effect_count,
            ).where(
                debt_operations.c.kind == "PAYMENT",
                debt_operations.c.identity == tx_id,
            )
        )
    ).all()
    entries: list[tuple[str, str]] = []
    for operation_id, _state, _count in envelopes:
        rows = (
            await db_session.execute(
                select(
                    debt_journal_entries.c.effect, debt_journal_entries.c.delta
                ).where(debt_journal_entries.c.operation_id == operation_id)
            )
        ).all()
        entries.extend((str(effect), str(delta)) for effect, delta in rows)
    entries.sort()
    return {
        "transactions": transactions,
        "debts": debts,
        "envelopes": sorted((str(state), count) for _id, state, count in envelopes),
        "entries": entries,
    }


def _assert_exactly_one_committed_effect(effects: dict[str, object], amount: str) -> None:
    assert effects["transactions"] == [("COMMITTED",)], effects["transactions"]
    assert effects["envelopes"] == [("COMPLETED", len(effects["entries"]))], effects
    assert len(effects["entries"]) > 0, effects
    assert len(effects["debts"]) == 1, effects["debts"]
    debtor, creditor, moved = effects["debts"][0]
    assert debtor != creditor, effects["debts"]
    assert Decimal(moved) == Decimal(amount), effects["debts"]


@pytest.mark.asyncio
async def test_a_commit_that_landed_and_then_timed_out_answers_committed_once(
    db_session, monkeypatch
):
    """Cell 7, timeout branch: the real commit, then the payment's deadline expires around it."""

    sender, receiver, equivalent, sender_priv = await _seed(db_session)
    request = _signed_request(sender_priv, receiver.pid, equivalent.code)

    # The deadline has to outlast the payment, its REAL commit and the snapshot taken right after it,
    # otherwise it cancels the wrapper mid-read and the premise below has nothing to assert on. The
    # budget is the test's, not the application's; what is under test is the handler that runs once
    # the deadline does fire.
    monkeypatch.setattr(settings, "PREPARE_TIMEOUT_SECONDS", 5, raising=False)
    monkeypatch.setattr(settings, "COMMIT_TIMEOUT_SECONDS", 5, raising=False)
    monkeypatch.setattr(settings, "PAYMENT_TOTAL_TIMEOUT_SECONDS", 3, raising=False)

    service = PaymentService(db_session)
    original_session_commit = db_session.commit
    observed: dict[str, object] = {}
    refusal_records: list[tuple] = []

    async def _real_commit_then_fail():
        if "commit_returned" in observed:
            return await original_session_commit()
        await original_session_commit()
        observed["commit_returned"] = True
        observed["effects_at_commit"] = await _one_effect(db_session, request.tx_id)
        # Never set: the deadline around the commit cancels this wait, which is the real
        # cancellation the handler has to survive - not a TimeoutError raised by the test.
        await asyncio.Event().wait()

    async def _record_refusal(*args, **kwargs):
        refusal_records.append((args, kwargs))

    monkeypatch.setattr(db_session, "commit", _real_commit_then_fail)
    monkeypatch.setattr(payment_service_module, "record_definitive_refusal", _record_refusal)

    result = await service.create_payment(sender.id, request)

    # The failure really happened after a real commit, not instead of one.
    assert observed.get("commit_returned") is True, observed
    assert "effects_at_commit" in observed, (
        "the deadline fired before the real commit and its snapshot finished; raise "
        "PAYMENT_TOTAL_TIMEOUT_SECONDS in this test - as it stands the cell measured nothing"
    )
    _assert_exactly_one_committed_effect(observed["effects_at_commit"], "10.00")

    assert result.status == "COMMITTED", result
    assert str(result.tx_id) == request.tx_id
    assert refusal_records == [], (
        "the service terminalized a payment whose commit was already durable: "
        f"{refusal_records!r}"
    )
    _assert_exactly_one_committed_effect(await _one_effect(db_session, request.tx_id), "10.00")


@pytest.mark.asyncio
async def test_a_commit_that_landed_and_then_raised_a_db_error_answers_committed_once(
    db_session, monkeypatch
):
    """Cell 7, database-error branch: the real commit, then the connection fails under it.

    This is the branch `PaymentService._settle_failed_commit` exists for - the outcome of a commit is
    unknown to the caller, so the row is read before anything is terminalized.
    """

    sender, receiver, equivalent, sender_priv = await _seed(db_session)
    request = _signed_request(sender_priv, receiver.pid, equivalent.code)

    monkeypatch.setattr(settings, "PREPARE_TIMEOUT_SECONDS", 5, raising=False)
    monkeypatch.setattr(settings, "COMMIT_TIMEOUT_SECONDS", 5, raising=False)
    monkeypatch.setattr(settings, "PAYMENT_TOTAL_TIMEOUT_SECONDS", 15, raising=False)

    service = PaymentService(db_session)
    original_session_commit = db_session.commit
    observed: dict[str, object] = {}
    refusal_records: list[tuple] = []

    async def _real_commit_then_fail():
        if "commit_returned" in observed:
            return await original_session_commit()
        await original_session_commit()
        observed["commit_returned"] = True
        observed["effects_at_commit"] = await _one_effect(db_session, request.tx_id)
        raise OperationalError(
            "SELECT 1", {}, Exception("server closed the connection unexpectedly")
        )

    async def _record_refusal(*args, **kwargs):
        refusal_records.append((args, kwargs))

    monkeypatch.setattr(db_session, "commit", _real_commit_then_fail)
    monkeypatch.setattr(payment_service_module, "record_definitive_refusal", _record_refusal)

    result = await service.create_payment(sender.id, request)

    assert observed.get("commit_returned") is True, observed
    _assert_exactly_one_committed_effect(observed["effects_at_commit"], "10.00")

    assert result.status == "COMMITTED", result
    assert str(result.tx_id) == request.tx_id
    assert refusal_records == [], (
        "the service terminalized a payment whose commit was already durable: "
        f"{refusal_records!r}"
    )
    _assert_exactly_one_committed_effect(await _one_effect(db_session, request.tx_id), "10.00")
