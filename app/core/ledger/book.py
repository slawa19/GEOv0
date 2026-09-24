"""The book: the single writer of `debts`.

Programme 018, stage A (`specs/018-single-debt-writer/spec.md`, `T1802`). This is the only module in
`app/` and `scripts/` that constructs a `Debt`, assigns `Debt.amount` or deletes a `Debt`
(`tests/unit/test_p018_only_book_writes_debts.py`, with its declared blind spots). Every caller
declares an `Operation` and hands the book `Effect`s; the book applies them with the semantics of the
operation's kind, which are PRESERVED per kind and NOT unified:

* `PAYMENT` - `PaymentFlow`: `PaymentEngine._apply_flow`'s algebra verbatim - reduce the receiver's
  debt to the sender, grow the sender's debt to the receiver, net a mutual pair, delete a zero -
  including its three-attempt `StaleDataError` loop under `begin_nested`. Removing that loop is an
  isolation-model decision and belongs to programme 019 (`docs/ru/09-decisions-and-defaults.md:237`).
* `CLEARING` - `ClearingReduction`: decrease or delete only. Growth or a new row is refused.
* `INJECT` - `InjectIncrease`: increase or refuse. An effect opposite to an existing debt `> 0` is
  REFUSED and returned to the caller as refused (F-015-12, `6a882d2`); so is an effect whose result
  would exceed the caller's ceiling (the trust limit). Never nets, so the criterion (b) rule
  `reconciliation._inject_subset` stays true unchanged.
* `SEED`, `TEST_FIXTURE` - `NewDebt`: create. The refusal after a baseline stays where it is today,
  in the journal's completion (`journal.py`, `_complete`).

THE ENVELOPE IN STAGE A is still the journal's `debt_operation` (`app/core/ledger/journal.py`): the
book opens it, the listener records the effects exactly as before. No second journal, no schema
change. Stage B replaces the listener with a database trigger and makes the book open the envelope
itself.

TWO SHAPES OF USE:

* `async with Book.operation(session, op) as posting:` - one envelope around a block in which the
  caller applies debt effects with `await posting.apply(effect)` IN ITS OWN ORDER, interleaved with
  work the book does not own (an inject event's participants, trust lines and freezes; a payment's
  invariant checks; a clearing's audit row). The effects of an inject event see each other, so
  collecting them at one end or posting them as separate operations would change the outcome.
* `await Book.post(session, op, effects)` - the shorthand for an operation made of debt effects only.

`Book.current(session)` returns the posting open on a session. It exists for the one writer whose
signature tests pin (`PaymentEngine._apply_flow`, kept as a forwarding method so that tests which
perturb it still execute the perturbation) and for the inject executor, which runs inside the
envelope its owner opened.
"""

from __future__ import annotations

import logging
import uuid
import weakref
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, AsyncIterator, Iterable, Sequence

from sqlalchemy import and_, select
from sqlalchemy.orm.exc import StaleDataError

from app.core.ledger.journal import debt_operation
from app.db.models.debt import Debt

logger = logging.getLogger(__name__)

#: The unique constraints that spell an operation envelope's IDENTITY - the declaration "this
#: operation has already been opened", and nothing else about it (T1529). Written out rather than
#: read off `debt_operations.constraints`, because a retry predicate that widens itself whenever
#: somebody adds a unique constraint to that table is a policy change nobody decided;
#: `tests/unit/test_p015_t1529_the_envelope_identity_is_a_retryable_race.py` reddens if the set and
#: the schema ever disagree. Moved here from `app/core/payments/engine.py` by 018 stage A: the
#: envelope is the book's.
DEBT_OPERATION_IDENTITY_CONSTRAINTS = frozenset(
    {"uq_debt_operations_kind_identity", "uq_debt_operations_tx_id"}
)


class BookError(Exception):
    """A request the book refuses: the wrong effect for the kind, no open operation, bad input.

    These are programming errors of a caller, not business outcomes. A business refusal (an inject
    effect opposite to an existing debt, or over its ceiling) is RETURNED, never raised.
    """


# =================================================================================================
# The declaration and the effects
# =================================================================================================


@dataclass(frozen=True)
class Operation:
    """What an operation is, as its envelope records it.

    `kind` is one of `OPERATION_KINDS` (`app/db/journal_tables.py`); `tx_id` names the already
    inserted `transactions` row for `PAYMENT`/`CLEARING` and is `None` otherwise;
    `scope_equivalent_ids` is `None` only for `SEED`/`TEST_FIXTURE`.
    """

    kind: str
    identity: str
    intent: Any
    tx_id: str | None = None
    scope_equivalent_ids: frozenset[uuid.UUID] | None = None
    intent_equivalent_ids: frozenset[uuid.UUID] = frozenset()


@dataclass(frozen=True)
class PaymentFlow:
    """`amount` flows from `from_id` (sender) to `to_id` (receiver) in `equivalent_id`."""

    from_id: uuid.UUID
    to_id: uuid.UUID
    amount: Decimal
    equivalent_id: uuid.UUID


@dataclass(frozen=True)
class ClearingReduction:
    """Reduce the locked `debt` by `amount`; a debt reduced to zero is deleted."""

    debt: Debt
    amount: Decimal


@dataclass(frozen=True)
class InjectIncrease:
    """Grow `debtor -> creditor` by `amount`, refusing an opposing debt or a result over `ceiling`."""

    debtor_id: uuid.UUID
    creditor_id: uuid.UUID
    equivalent_id: uuid.UUID
    amount: Decimal
    ceiling: Decimal


@dataclass(frozen=True)
class NewDebt:
    """Create `debtor -> creditor` holding `amount` (seed or test fixture)."""

    debtor_id: uuid.UUID
    creditor_id: uuid.UUID
    equivalent_id: uuid.UUID
    amount: Decimal


Effect = PaymentFlow | ClearingReduction | InjectIncrease | NewDebt

#: The outcomes `Posting.apply` returns. Only `INJECT` can refuse without raising.
APPLIED = "APPLIED"
REFUSED_OPPOSING_DEBT = "REFUSED_OPPOSING_DEBT"
REFUSED_OVER_CEILING = "REFUSED_OVER_CEILING"

#: Which effect each kind accepts. One effect type per kind: the semantics are per kind.
_EFFECT_FOR_KIND: dict[str, type] = {
    "PAYMENT": PaymentFlow,
    "CLEARING": ClearingReduction,
    "INJECT": InjectIncrease,
    "SEED": NewDebt,
    "TEST_FIXTURE": NewDebt,
}


@dataclass
class Posted:
    """What `Book.post` did: one outcome per effect, in order."""

    outcomes: list[str] = field(default_factory=list)


# =================================================================================================
# Per-kind semantics
# =================================================================================================


async def _get_debt(
    session: Any, debtor_id: uuid.UUID, creditor_id: uuid.UUID, equivalent_id: uuid.UUID
) -> Debt | None:
    stmt = select(Debt).where(
        and_(
            Debt.debtor_id == debtor_id,
            Debt.creditor_id == creditor_id,
            Debt.equivalent_id == equivalent_id,
        )
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def _apply_payment_flow(session: Any, flow: PaymentFlow) -> str:
    """`PaymentEngine._apply_flow`, moved verbatim (018 stage A).

    Apply flow of `amount` from `from_id` to `to_id`.
    Logic:
      1. If receiver owes sender: reduce that debt.
      2. If remaining amount > 0: increase sender's debt to receiver.
    """
    from_id, to_id, amount, equivalent_id = (
        flow.from_id,
        flow.to_id,
        flow.amount,
        flow.equivalent_id,
    )
    max_retries = 3
    for attempt in range(max_retries):
        try:
            async with session.begin_nested():
                remaining_amount = amount

                # 1. Check if Receiver owes Sender (Debt: debtor=to, creditor=from)
                debt_r_s = await _get_debt(session, to_id, from_id, equivalent_id)

                if debt_r_s and debt_r_s.amount > 0:
                    reduction = min(remaining_amount, debt_r_s.amount)
                    debt_r_s.amount -= reduction
                    remaining_amount -= reduction

                    if debt_r_s.amount == 0:
                        await session.delete(debt_r_s)
                    else:
                        session.add(debt_r_s)

                if remaining_amount > 0:
                    # 2. Increase Sender's debt to Receiver (Debt: debtor=from, creditor=to)
                    debt_s_r = await _get_debt(session, from_id, to_id, equivalent_id)
                    if not debt_s_r:
                        # Create new debt record
                        debt_s_r = Debt(
                            debtor_id=from_id,
                            creditor_id=to_id,
                            equivalent_id=equivalent_id,
                            amount=Decimal("0"),
                        )

                    debt_s_r.amount += remaining_amount
                    session.add(debt_s_r)

                # NOTE: app sessions may run with autoflush=False. Ensure the DB view is
                # consistent before the symmetry-netting queries below.
                await session.flush()

                # Enforce debt symmetry by netting mutual debts, if any.
                debt_forward = await _get_debt(session, from_id, to_id, equivalent_id)
                debt_reverse = await _get_debt(session, to_id, from_id, equivalent_id)
                if (
                    debt_forward
                    and debt_reverse
                    and debt_forward.amount > 0
                    and debt_reverse.amount > 0
                ):
                    net = min(debt_forward.amount, debt_reverse.amount)
                    debt_forward.amount -= net
                    debt_reverse.amount -= net

                    if debt_forward.amount == 0:
                        await session.delete(debt_forward)
                    else:
                        session.add(debt_forward)

                    if debt_reverse.amount == 0:
                        await session.delete(debt_reverse)
                    else:
                        session.add(debt_reverse)

                    # Flush netting effects immediately so later flows / invariant checks
                    # don't observe a transient mutual-debt state.
                    await session.flush()

            return APPLIED
        except StaleDataError:
            if attempt >= max_retries - 1:
                raise
            logger.warning(
                "event=apply_flow.stale_data retry=%s/%s from=%s to=%s",
                attempt + 1,
                max_retries,
                str(from_id),
                str(to_id),
            )
            try:
                session.expire_all()
            except Exception:
                pass
    raise AssertionError("unreachable: the loop returns or raises")  # pragma: no cover


async def _apply_clearing_reduction(session: Any, effect: ClearingReduction) -> str:
    """Decrease or delete. Anything that is not a decrease within the debt is refused."""

    debt, amount = effect.debt, effect.amount
    if not amount > 0:
        raise BookError(f"a clearing reduction must be positive, got {amount}")
    if debt.amount < amount:
        raise BookError(
            f"a clearing reduction of {amount} exceeds debt {debt.id} holding {debt.amount}; "
            f"CLEARING only decreases"
        )
    debt.amount -= amount
    if debt.amount == 0:
        await session.delete(debt)
    else:
        session.add(debt)
    return APPLIED


async def _apply_inject_increase(session: Any, effect: InjectIncrease) -> str:
    """Increase or refuse (F-015-12). Moved from `InjectExecutor.stage_inject_event` (018 stage A).

    ONE DIRECTION PER PAIR (protocol §11.2.4). A debt the other way round between the same two
    participants in the same equivalent refuses this effect: the caller counts it as skipped, like an
    effect over the trust limit. Refusal, not netting - netting would write a decrease, which the
    INJECT rule of criterion (b) reads as a contradiction (`reconciliation._inject_subset`,
    `entry_is_not_an_increase`).

    The flush first: the session runs with `autoflush=False`, so a reverse debt STAGED by an earlier
    effect of this same event would be invisible to the read below. A flush error propagates
    (`SQLAlchemyError`), as the inject executor's contract requires.

    A reverse debt committed concurrently is not missed either: this read and the write below run in
    the owner's SERIALIZABLE transaction, and every other writer that can create a direction reads
    the opposite edge too (`_apply_payment_flow`, and this very check), so the pair of transactions
    is a read-write cycle that PostgreSQL breaks with 40001. The owner retries once on a fresh
    snapshot (which then sees the reverse debt and refuses); if the retry conflicts again, the owner
    rolls back and leaves the event pending - fail-closed, never both directions
    (`real_runner_impl.py`, the inject retry loop). Reasoned from SSI, not measured by a concurrent
    stand.
    """

    debtor_id, creditor_id, eq_id = effect.debtor_id, effect.creditor_id, effect.equivalent_id
    amount = effect.amount
    if not amount > 0:
        raise BookError(f"an inject increase must be positive, got {amount}")

    await session.flush()
    reverse_amount = (
        await session.execute(
            select(Debt.amount).where(
                Debt.debtor_id == creditor_id,
                Debt.creditor_id == debtor_id,
                Debt.equivalent_id == eq_id,
            )
        )
    ).scalar_one_or_none()
    if reverse_amount is not None and Decimal(str(reverse_amount)) > 0:
        return REFUSED_OPPOSING_DEBT

    existing = (
        await session.execute(
            select(Debt).where(
                Debt.debtor_id == debtor_id,
                Debt.creditor_id == creditor_id,
                Debt.equivalent_id == eq_id,
            )
        )
    ).scalar_one_or_none()

    if existing is None:
        new_amt = amount
        if new_amt > effect.ceiling:
            return REFUSED_OVER_CEILING
        session.add(
            Debt(
                debtor_id=debtor_id,
                creditor_id=creditor_id,
                equivalent_id=eq_id,
                amount=new_amt,
            )
        )
    else:
        # NO RE-QUANTISATION OF WHAT IS ALREADY STORED. T1514 of programme 015.
        #
        # This read the row back, added its own amount and rounded the SUM down to cents before
        # writing it back. `debts` is shared with the production core, which stores at the column's
        # own scale - `Numeric(20, 8)` - and since 012/T1201 the money door refuses anything the
        # column cannot hold unchanged, so eight fraction digits are legitimate ledger content. A
        # debt of 5.12345678 became 6.12 after an injected 1.00: 0.00345678 destroyed by a rounding
        # nobody asked for, in a table the simulator does not own, and the result feeds the next
        # operation.
        #
        # `amount` is already normalised to the simulator's own input scale by the caller, so the
        # sum is storable as it stands. Normalising the INPUT is the simulator's business; rewriting
        # a stored value is not.
        new_amt = Decimal(str(existing.amount)) + amount
        if new_amt > effect.ceiling:
            return REFUSED_OVER_CEILING
        existing.amount = new_amt
    return APPLIED


async def _apply_new_debt(session: Any, effect: NewDebt) -> str:
    if not effect.amount > 0:
        raise BookError(f"a new debt must be positive, got {effect.amount}")
    session.add(
        Debt(
            debtor_id=effect.debtor_id,
            creditor_id=effect.creditor_id,
            equivalent_id=effect.equivalent_id,
            amount=effect.amount,
        )
    )
    return APPLIED


# =================================================================================================
# The posting and the book
# =================================================================================================


class Posting:
    """The open operation. `apply` is the only way an effect reaches `debts`."""

    def __init__(self, session: Any, op: Operation) -> None:
        self._session = session
        self.operation = op
        self._open = True

    @property
    def is_open(self) -> bool:
        return self._open

    async def apply(self, effect: Effect) -> str:
        if not self._open:
            raise BookError(
                f"operation {self.operation.kind}/{self.operation.identity} is closed; an effect "
                f"after the envelope would belong to no operation"
            )
        expected = _EFFECT_FOR_KIND.get(self.operation.kind)
        if expected is None or not isinstance(effect, expected):
            raise BookError(
                f"a {self.operation.kind} operation does not take {type(effect).__name__}; it "
                f"takes {getattr(expected, '__name__', 'nothing')}"
            )
        session = self._session
        if isinstance(effect, PaymentFlow):
            return await _apply_payment_flow(session, effect)
        if isinstance(effect, ClearingReduction):
            return await _apply_clearing_reduction(session, effect)
        if isinstance(effect, InjectIncrease):
            return await _apply_inject_increase(session, effect)
        return await _apply_new_debt(session, effect)


#: The posting open on each (sync) session. Weak: a session that goes away takes its entry along.
_OPEN: "weakref.WeakKeyDictionary[Any, Posting]" = weakref.WeakKeyDictionary()


def _key(session: Any) -> Any:
    return getattr(session, "sync_session", session)


class Book:
    """The single writer of `debts`. Stateless; the state is the posting open on a session."""

    @staticmethod
    @asynccontextmanager
    async def operation(session: Any, op: Operation) -> AsyncIterator[Posting]:
        """One envelope; effects applied through the yielded posting, in the caller's order.

        Nesting, a pending `Debt` at entry, an unusable transaction and a bad declaration are
        refused by the envelope (`debt_operation`) exactly as before stage A.
        """

        async with debt_operation(
            session,
            kind=op.kind,
            identity=op.identity,
            intent=op.intent,
            tx_id=op.tx_id,
            scope_equivalent_ids=op.scope_equivalent_ids,
            intent_equivalent_ids=op.intent_equivalent_ids,
        ):
            posting = Posting(session, op)
            key = _key(session)
            previous = _OPEN.get(key)
            _OPEN[key] = posting
            try:
                yield posting
            finally:
                posting._open = False
                if previous is None:
                    _OPEN.pop(key, None)
                else:  # pragma: no cover - the envelope refuses nesting before this is reachable
                    _OPEN[key] = previous

    @staticmethod
    async def post(session: Any, op: Operation, effects: Iterable[Effect]) -> Posted:
        """An operation made of debt effects only, applied in order."""

        posted = Posted()
        async with Book.operation(session, op) as posting:
            for effect in effects:
                posted.outcomes.append(await posting.apply(effect))
        return posted

    @staticmethod
    def current(session: Any) -> Posting:
        """The posting open on this session, or a refusal: no debt moves outside an operation."""

        posting = _OPEN.get(_key(session))
        if posting is None or not posting.is_open:
            raise BookError("no Book operation is open on this session")
        return posting


def operation_for(
    kind: str,
    identity: str,
    intent: Any,
    *,
    tx_id: str | None = None,
    scope_equivalent_ids: Iterable[uuid.UUID] | None = None,
    intent_equivalent_ids: Iterable[uuid.UUID] = (),
) -> Operation:
    """An `Operation` from the keyword shape callers already spell for `debt_operation`."""

    return Operation(
        kind=kind,
        identity=identity,
        intent=intent,
        tx_id=tx_id,
        scope_equivalent_ids=None
        if scope_equivalent_ids is None
        else frozenset(scope_equivalent_ids),
        intent_equivalent_ids=frozenset(intent_equivalent_ids),
    )


__all__: Sequence[str] = (
    "APPLIED",
    "REFUSED_OPPOSING_DEBT",
    "REFUSED_OVER_CEILING",
    "DEBT_OPERATION_IDENTITY_CONSTRAINTS",
    "Book",
    "BookError",
    "ClearingReduction",
    "Effect",
    "InjectIncrease",
    "NewDebt",
    "Operation",
    "PaymentFlow",
    "Posted",
    "Posting",
    "operation_for",
)
