"""The book: the single writer of `debts`.

Programme 018, stage A (`specs/018-single-debt-writer/spec.md`, `T1802`). This is the only module in
`app/` and `scripts/` that constructs a `Debt`, assigns `Debt.amount` or deletes a `Debt`
(`tests/unit/test_p018_only_book_writes_debts.py`, with its declared blind spots). Every caller
declares an `Operation` and hands the book `Effect`s; the book applies them with the semantics of the
operation's kind, which are PRESERVED per kind and NOT unified:

* `PAYMENT` - `PaymentFlow`: `PaymentEngine._apply_flow`'s algebra verbatim - reduce the receiver's
  debt to the sender, grow the sender's debt to the receiver, net a mutual pair, delete a zero -
  WITHOUT a retry of its own since programme 019 stage 3 (`FORK-1`): a debt whose `version` moved
  underneath this transaction is raised as `DebtVersionConflict`, and the owner of the whole
  transaction retries it on a fresh snapshot.
* `CLEARING` - `ClearingReduction`: decrease or delete only. Growth or a new row is refused.
* `INJECT` - takes no effect since 030 S3b (F-030-6, owner В1): the simulator's `inject_debt` and its
  `InjectIncrease` are deleted; the kind stays in the journal's CHECK for the rows already written.
* `SEED`, `TEST_FIXTURE` - `NewDebt`: create. After a reconciliation baseline of a touched equivalent
  the operation is refused at completion (`T1501`; moved here from the deleted listener journal).

MONEY THE COLUMN CANNOT HOLD IS REFUSED HERE, before any debt changes (018 / FORK-1, slice B0a,
2026-09-24). Every effect's input amount AND every amount the book calculates - a sum after an
increase, a difference after a reduction or netting - passes THE storability predicate
(`app/utils/validation.py::money_storability_violation`) with `debts.amount`'s declared capacity
before it is assigned, and a failure raises `BookMoneyError` naming the predicate. Checking inputs
alone would miss a valid increment that overflows an existing debt. `MoneyNumeric` applies the same
predicate again at bind (`app/db/types.py`) for writes that never came through here.

THE ENVELOPE IS THE BOOK'S AND THE JOURNAL IS THE DATABASE'S (018 stage B, `T1803`/`T1805`). The book
opens the envelope itself and names it in the transaction-local setting `geo.operation_id`; the
`debts` trigger (`app/db/journal_triggers.py`, migration 029) writes one entry per changed row from
`OLD`/`NEW` and refuses a write with no `OPEN` envelope named (`GE001`). The listener journal that did
this from the ORM (`app/core/ledger/journal.py`) is deleted.

THE TRANSACTION CONTRACT (spec 018, "Контракт транзакции `Book.post` (стадия B)", `FORK-2`, `FORK-6`),
implemented by `Book.operation` below:

1. The connection first (`await session.connection()`, which also autobegins a fresh session), THEN
   the transaction's state: `session.in_transaction()`, the AUTOCOMMIT setting SQLAlchemy knows of,
   and - after the first statement - the DRIVER's own answer (asyncpg `is_in_transaction()`), so an
   engine-level AUTOCOMMIT that leaves no trace in the options is still refused. Every statement goes
   through that one connection.
2. No nesting: an open posting of THIS session (per session, never per process) or a non-empty
   `geo.operation_id` in this transaction (which catches another session sharing the connection)
   refuses. A `Debt` already pending in the session refuses BEFORE `begin_nested()`, which would
   flush it into this operation.
3. Everything inside the book's own savepoint: envelope `OPEN`, `set_config(.., true)`, the caller's
   block, `flush`, the entries read back by `ordinal`, `debt_operation_equivalents`, `COMPLETED`,
   `set_config(.., '', true)`, `RELEASE`.
4. Any exception, cancellation included: `ROLLBACK TO SAVEPOINT` - which undoes the envelope, the
   entries and the `SET LOCAL` together - and the ORIGINAL exception re-raised. No unconditional SQL
   in a `finally`, which in an aborted transaction would replace a `40001`. A rollback that fails is
   attached to the original as a note and MAKES THE TRANSACTION UNUSABLE, even after `COMPLETED`: the
   connection is invalidated, the server discards the transaction and a commit is impossible.
5. No session-level `SET`: only `set_config(.., true)`.
6. Completion writes a `debt_operation_equivalents` row per touched equivalent and per intent
   equivalent with no effects; an entry outside `scope_equivalent_ids` refuses (and `Posting.apply`
   refuses such an effect before it writes); `SEED`/`TEST_FIXTURE` after a baseline refuses.

WHAT THE TRIGGER DOES NOT GIVE (spec, "узко", `FORK-3`): every DML statement of this physical
transaction, while the context is open, belongs to this operation, whoever issued it. The book's own
ownership is per session: `Book.current(other_session)` refuses.

TWO SHAPES OF USE:

* `async with Book.operation(session, op) as posting:` - one envelope around a block in which the
  caller applies debt effects with `await posting.apply(effect)` IN ITS OWN ORDER, interleaved with
  work the book does not own (a payment's invariant checks; a clearing's audit row).
* `await Book.post(session, op, effects)` - the shorthand for an operation made of debt effects only.

`Book.current(session)` returns the posting open on a session (the inject executor's entrance until 030
S3b; the payment path's perturbation seam is `_apply_payment_flow` below).
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
import weakref
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, AsyncIterator, Iterable, Sequence

from sqlalchemy import and_, func, insert, select, tuple_, update
from sqlalchemy.orm import aliased
from sqlalchemy.orm.exc import StaleDataError

from app.core.auth.canonical import canonical_json
from app.core.invariants import InvariantChecker
from app.db.journal_tables import (
    MONEY_ENCODING_VERSION,
    OPERATION_KINDS,
    OPERATION_KINDS_WITH_TX,
    SCHEMA_VERSION,
    debt_journal_entries,
    debt_operation_equivalents,
    debt_operations,
    intent_encoding_version_for,
)
from app.db.journal_triggers import GUC_OPERATION_ID
from app.db.models.audit_log import TRUST_LINE_CLOSE, IntegrityAuditLog, trust_line_close_completed
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine
from app.db.reconciliation_tables import debt_reconciliation_baselines
from app.utils.exceptions import IntegrityViolationException
from app.utils.validation import money_storability_violation

logger = logging.getLogger(__name__)

#: The unique constraints that spell an operation envelope's IDENTITY - the declaration "this
#: operation has already been opened", and nothing else about it (T1529). Written out rather than
#: read off `debt_operations.constraints`;
#: `tests/unit/test_p015_t1529_the_envelope_identity_is_a_retryable_race.py` reddens if the set and
#: the schema ever disagree. Moved here from `app/core/payments/engine.py` by 018 stage A. The retry
#: predicate it fed lived in that engine and left with it (019): no production code reads the set
#: today, only that test.
DEBT_OPERATION_IDENTITY_CONSTRAINTS = frozenset(
    {"uq_debt_operations_kind_identity", "uq_debt_operations_tx_id"}
)


class Refusal:
    """Why the book refused, as a value a test asserts on instead of a message.

    Values that existed in the deleted listener journal's `Reason` keep their spelling.
    """

    BAD_ARGUMENT = "bad_argument"
    WRONG_EFFECT = "wrong_effect"
    CLOSED_OPERATION = "closed_operation"
    NO_OPERATION = "no_operation"
    NO_TRANSACTION = "no_transaction"
    AUTOCOMMIT_ROOT = "autocommit_root"
    UNMEASURED_TRANSACTION = "unmeasured_db_transaction"
    NESTED_OPERATION = "nested_operation"
    INCOMPLETE_DEBT = "incomplete_debt"
    OUT_OF_SCOPE = "out_of_scope"
    ENVELOPE_LOST = "envelope_lost"
    UNVERIFIABLE_WRITER_AFTER_BASELINE = "unverifiable_writer_after_baseline"


class BookError(Exception):
    """A request the book refuses: the wrong effect for the kind, no open operation, bad input.

    These are programming errors of a caller, not business outcomes. `reason` is a `Refusal` value (for
    `BookMoneyError`, the money predicate that failed).
    """

    def __init__(
        self, message: str, *, reason: str = Refusal.WRONG_EFFECT, **context: Any
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.context = context


class DebtVersionConflict(StaleDataError):
    """A payment flow's debt was changed by another transaction: its `version` no longer matches.

    Programme 019 stage 3 (`FORK-1`, `specs/019-payment-one-transaction/spec.md`, "Вложенность и
    владение повторами"). Until then `_apply_payment_flow` answered a `StaleDataError` by expiring the
    identity map and re-running the flow up to three times INSIDE the same database transaction. That
    is a retry from the same snapshot: under SERIALIZABLE the rolled-back savepoint does not refresh
    it, so the retry reads what the first attempt read. The retry now belongs to the owner of the
    whole transaction - `PaymentService.pay` for the API, the money-phase replay for the simulator -
    which starts again on a fresh session.

    NARROW ON PURPOSE. Only a `StaleDataError` raised by a payment flow becomes this type, and only
    this type is classified as a retryable conflict (`app/core/payments/service.py`,
    `_classify_payment_db_error`; `app/core/simulator/money_replay.py`, `money_conflict_name`). A
    `StaleDataError` from anywhere else, and every other ORM error, stays what it was and is not
    retried (negative controls: `tests/unit/test_p019_debt_version_conflict_is_narrow.py`).

    It subclasses `StaleDataError`, so it is still the original ORM failure for anyone who catches that
    (Book contract item 4: the original exception reaches the caller); the SQLAlchemy exception is its
    `__cause__`.
    """


class BookMoneyError(BookError):
    """An amount `debts.amount` cannot hold exactly: an input, or a sum the book calculated.

    `reason` is the predicate that failed - `money_finiteness`, `money_magnitude` or
    `money_quantization` (`app/utils/validation.py`) - so the refusal names its rule.
    """

    def __init__(self, reason: str, message: str, *, value: str) -> None:
        super().__init__(f"{reason}: {message}", reason=reason)
        self.value = value


_AMOUNT_TYPE = Debt.__table__.c.amount.type
#: `debts.amount`'s declared capacity, read off the model: the book refuses what the column cannot
#: hold, not what the money door (`MONEY_MAX_*`) happens to admit.
_MAX_SCALE = int(_AMOUNT_TYPE.scale)
_MAX_INTEGER_DIGITS = int(_AMOUNT_TYPE.precision) - _MAX_SCALE


def _refuse_unstorable(value: Any, *, what: str) -> None:
    """The book's one storability enforcement point: raise unless `debts.amount` holds `value`."""

    reason = money_storability_violation(
        value, max_integer_digits=_MAX_INTEGER_DIGITS, max_scale=_MAX_SCALE
    )
    if reason is not None:
        raise BookMoneyError(
            reason,
            f"{what} is {value!r}, which NUMERIC({_MAX_INTEGER_DIGITS + _MAX_SCALE}, {_MAX_SCALE}) "
            f"cannot hold exactly; no debt is changed",
            value=str(value),
        )


def _set_amount(debt: Debt, value: Decimal, *, what: str) -> None:
    """Assign a calculated amount to a debt, after the storability check."""

    _refuse_unstorable(value, what=what)
    debt.amount = value


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
    #: The intent encoding version the envelope records. `None` - the kind's default. A declared version
    #: must be one the kind writes (`intent_encoding_version_for`); 023 slice (b) declares CLEARING v2.
    intent_encoding_version: int | None = None


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
class NewDebt:
    """Create `debtor -> creditor` holding `amount` (seed or test fixture)."""

    debtor_id: uuid.UUID
    creditor_id: uuid.UUID
    equivalent_id: uuid.UUID
    amount: Decimal


Effect = PaymentFlow | ClearingReduction | NewDebt

#: The outcome `Posting.apply` returns; a refusal raises.
APPLIED = "APPLIED"

#: Which effect each kind accepts. One effect type per kind: the semantics are per kind.
_EFFECT_FOR_KIND: dict[str, type] = {
    "PAYMENT": PaymentFlow,
    "CLEARING": ClearingReduction,
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
    # 019 stage 3 (`FORK-1`): no retry here. See `DebtVersionConflict`.
    try:
        remaining_amount = amount

        # 1. Check if Receiver owes Sender (Debt: debtor=to, creditor=from)
        debt_r_s = await _get_debt(session, to_id, from_id, equivalent_id)

        if debt_r_s and debt_r_s.amount > 0:
            reduction = min(remaining_amount, debt_r_s.amount)
            _set_amount(
                debt_r_s, debt_r_s.amount - reduction, what="the reduced receiver debt"
            )
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

            _set_amount(
                debt_s_r,
                debt_s_r.amount + remaining_amount,
                what="the increased sender debt",
            )
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
            _set_amount(
                debt_forward, debt_forward.amount - net, what="the netted forward debt"
            )
            _set_amount(
                debt_reverse, debt_reverse.amount - net, what="the netted reverse debt"
            )

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
    except DebtVersionConflict:
        raise
    except StaleDataError as exc:
        logger.warning(
            "event=apply_flow.debt_version_conflict from=%s to=%s",
            str(from_id),
            str(to_id),
        )
        raise DebtVersionConflict(
            f"a debt of the pair {from_id} -> {to_id} was changed by another transaction"
        ) from exc
    return APPLIED


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
    _set_amount(debt, debt.amount - amount, what="the cleared debt")
    if debt.amount == 0:
        await session.delete(debt)
    else:
        session.add(debt)
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


def _effect_equivalent(effect: Effect) -> uuid.UUID:
    if isinstance(effect, ClearingReduction):
        return effect.debt.equivalent_id
    return effect.equivalent_id


class Posting:
    """The open operation. `apply` is the only way an effect reaches `debts`."""

    def __init__(self, session: Any, op: Operation) -> None:
        self._session = session
        self.operation = op
        #: The envelope's id once it is written; the value of `geo.operation_id` while open.
        self.operation_id: uuid.UUID | None = None
        self._open = True

    @property
    def is_open(self) -> bool:
        return self._open

    async def apply(self, effect: Effect) -> str:
        if not self._open:
            raise BookError(
                f"operation {self.operation.kind}/{self.operation.identity} is closed; an effect "
                f"after the envelope would belong to no operation",
                reason=Refusal.CLOSED_OPERATION,
            )
        expected = _EFFECT_FOR_KIND.get(self.operation.kind)
        if expected is None or not isinstance(effect, expected):
            raise BookError(
                f"a {self.operation.kind} operation does not take {type(effect).__name__}; it "
                f"takes {getattr(expected, '__name__', 'nothing')}"
            )
        # The input first: an amount the column cannot hold is refused before anything is read or
        # changed. The calculated amounts are checked where they are computed, below.
        _refuse_unstorable(effect.amount, what=f"the {type(effect).__name__} amount")
        # An effect outside the declared scope is refused BEFORE it writes (contract item 6).
        # Completion refuses an out-of-scope ENTRY again, for writes that did not come through here.
        scope = self.operation.scope_equivalent_ids
        if scope is not None and _effect_equivalent(effect) not in scope:
            raise BookError(
                f"{self.operation.kind}/{self.operation.identity} declared the equivalents "
                f"{sorted(str(value) for value in scope)} and was handed an effect in "
                f"{_effect_equivalent(effect)}",
                reason=Refusal.OUT_OF_SCOPE,
            )
        session = self._session
        if isinstance(effect, PaymentFlow):
            return await _apply_payment_flow(session, effect)
        if isinstance(effect, ClearingReduction):
            return await _apply_clearing_reduction(session, effect)
        return await _apply_new_debt(session, effect)


#: The posting open on each (sync) session. Weak: a session that goes away takes its entry along.
#: PER SESSION, NOT PER PROCESS (contract item 2, `FORK-6`): a process-wide flag would refuse the
#: legitimate concurrent operations of other connections.
_OPEN: "weakref.WeakKeyDictionary[Any, Posting]" = weakref.WeakKeyDictionary()


def _key(session: Any) -> Any:
    return getattr(session, "sync_session", session)


# =================================================================================================
# The envelope: declaration, digests, completion, and the way out on failure
# =================================================================================================

#: Writers whose effects nothing can recompute (step 5 key review, `T1501`): refused after a baseline.
_PRE_BASELINE_ONLY_KINDS = frozenset({"SEED", "TEST_FIXTURE"})

_MONEY_QUANTUM = Decimal("1E-8")

_READ_CONTEXT = f"SELECT current_setting('{GUC_OPERATION_ID}', true)"


def _declaration(op: Operation) -> tuple[Any, str]:
    """Validate the declaration and canonicalise the intent: `(stored intent, its digest)`.

    Stored value and digest come from ONE canonicalisation, so a reader recomputing the digest from
    the stored column gets the same answer.
    """

    if op.kind not in OPERATION_KINDS:
        raise BookError(
            f"unknown operation kind {op.kind!r}; expected one of {OPERATION_KINDS}",
            reason=Refusal.BAD_ARGUMENT,
        )
    if not op.identity:
        raise BookError("an operation needs a non-empty identity", reason=Refusal.BAD_ARGUMENT)
    if (op.tx_id is not None) != (op.kind in OPERATION_KINDS_WITH_TX):
        needs = "requires" if op.kind in OPERATION_KINDS_WITH_TX else "must not carry"
        raise BookError(f"kind {op.kind} {needs} a tx_id", reason=Refusal.BAD_ARGUMENT)
    if op.scope_equivalent_ids is None and op.kind not in _PRE_BASELINE_ONLY_KINDS:
        raise BookError(
            f"kind {op.kind} must declare the equivalents it is allowed to touch; an unscoped "
            f"operation cannot be told from one that touched the wrong book.",
            reason=Refusal.BAD_ARGUMENT,
        )
    try:
        intent_encoding_version_for(op.kind, op.intent_encoding_version)
    except ValueError as exc:
        raise BookError(str(exc), reason=Refusal.BAD_ARGUMENT) from exc
    try:
        canonical = canonical_json(op.intent)
    except Exception as exc:  # noqa: BLE001 - any canonicalisation failure is the same refusal
        raise BookError(
            f"intent cannot be canonicalised, so it cannot be recorded: {exc}",
            reason=Refusal.BAD_ARGUMENT,
        ) from exc
    return json.loads(canonical.decode()), hashlib.sha256(canonical).hexdigest()


def _money_text(value: Any) -> str:
    """A money value as its exact scale-8 decimal string - the digest's only encoding."""

    if value is None:
        return ""
    return format(Decimal(value).quantize(_MONEY_QUANTUM), "f")


def _entry_digest(codes: Iterable[tuple[Any, ...]]) -> str:
    """A SUMMARY of entries, stored on completion. NOTHING compares it today: no recount or reconciliation reads
    `effect_digest` (audit 2026-10-02, `specs/BACKLOG.md` G1). Not a seal either (no chain, no anchor)."""

    digest = hashlib.sha256()
    for code in codes:
        line = "|".join("" if part is None else str(part) for part in code)
        digest.update((line + "\n").encode())
    return digest.hexdigest()


def _is_autocommit_configured(async_conn: Any) -> bool:
    """AUTOCOMMIT as SQLAlchemy knows it: the connection's options, or the engine-level setting.

    `create_engine(url, isolation_level="AUTOCOMMIT")` leaves `_execution_options` empty and keeps the
    level on the dialect (measured 2026-09-12 for the listener journal). This is SQLAlchemy's opinion
    only; `_refuse_unless_the_driver_is_in_a_transaction` asks the database.
    """

    conn = async_conn.sync_connection
    level = conn._execution_options.get("isolation_level")
    if level is None:
        level = getattr(conn.engine.dialect, "_on_connect_isolation_level", None)
    return level == "AUTOCOMMIT"


def _refuse_unless_the_driver_is_in_a_transaction(async_conn: Any) -> None:
    """The DRIVER's answer, after a statement has run: is a database transaction open?

    asyncpg's `Connection.is_in_transaction()` reports the status the server sent with its last
    ReadyForQuery - the database's word, not SQLAlchemy's bookkeeping. Only a real `bool` is an
    answer; anything else is an unmeasured state and refuses (an absent measurement may not read as
    a clean one, AGENTS.md §1).
    """

    try:
        driver = async_conn.sync_connection.connection.driver_connection
        probe = getattr(driver, "is_in_transaction", None)
        answer = probe() if callable(probe) else None
        why = f"{type(driver).__name__}.is_in_transaction() answered {answer!r}"
    except Exception as exc:  # noqa: BLE001 - an unanswerable probe is the refusal below
        answer, why = None, repr(exc)
    if answer is True:
        return
    if answer is False:
        raise BookError(
            "the database reports no transaction open on this connection (AUTOCOMMIT): the "
            "envelope, the entries and the debts would be separate durable facts and a refusal "
            "could undo none of them",
            reason=Refusal.AUTOCOMMIT_ROOT,
        )
    raise BookError(
        f"whether a database transaction is open could not be measured ({why})",
        reason=Refusal.UNMEASURED_TRANSACTION,
    )


async def _settle_requested_closes(session: Any, op: Operation, rows: Sequence[Any]) -> None:
    """026 `T2603.1` (owner В1/В2): the pairs this operation touched that hold a requested close.

    ONE SELECT of the live lines with `close_requested_at` among the touched pairs (both orientations), then:

    * В2 - the pair's debt `D = debt[A->B] + debt[B->A]` must end the operation strictly lower than it began,
      from the journal's first `amount_before` and final `amount_after` of each directed debt (an untouched
      direction is the same before and after, so it drops out). Clearing only decreases; a payment over the
      pair - transit or direct - is refused otherwise (`PENDING_CLOSE_PAIR_NOT_REDUCED`). The directed growth
      limits (`check_debt_growth`, above) still apply on top.
    * В1 - a requested line whose SUPPORTED debt (debtor `to`, creditor `from`) ends the operation at exactly 0
      is closed here, in this transaction, with its completion row (`completed_by` = the operation kind). The
      FINAL state only: a debt that passes through zero and ends above it keeps the request. A debt the other
      way is the other line's and does not hold it. A zero-limit line without a request never closes here.

    The UPDATE touches only the lines it closes: on a payment it upgrades the `FOR SHARE` the core holds on
    them (024 `T2415.3`); a real 40P01/40001 propagates to the owner's existing whole-transaction retry, and
    the book's savepoint rollback takes the status and the row with it.
    """

    first: dict[tuple[Any, Any, Any], Decimal] = {}
    last: dict[tuple[Any, Any, Any], Decimal] = {}
    for row in rows:
        edge = (row.equivalent_id, row.debtor_id, row.creditor_id)
        first.setdefault(edge, Decimal(row.amount_before or 0))
        last[edge] = Decimal(row.amount_after or 0)
    keys = {(e, d, c) for e, d, c in first} | {(e, c, d) for e, d, c in first}
    creditor, debtor = aliased(Participant), aliased(Participant)
    lines = (await session.execute(
        select(TrustLine.id, TrustLine.equivalent_id, TrustLine.from_participant_id, TrustLine.to_participant_id,
               creditor.pid.label("from_pid"), debtor.pid.label("to_pid"), Equivalent.code)
        .join(creditor, creditor.id == TrustLine.from_participant_id)
        .join(debtor, debtor.id == TrustLine.to_participant_id)
        .join(Equivalent, Equivalent.id == TrustLine.equivalent_id)
        .where(tuple_(TrustLine.equivalent_id, TrustLine.from_participant_id, TrustLine.to_participant_id).in_(
            sorted(keys, key=str)), TrustLine.close_requested_at.is_not(None), TrustLine.status != "closed")
    )).all()
    closing = []
    for line in lines:
        supported = (line.equivalent_id, line.to_participant_id, line.from_participant_id)
        pair = (supported, (line.equivalent_id, line.from_participant_id, line.to_participant_id))
        change = sum((last[e] - first[e] for e in pair if e in first), Decimal("0"))
        if change >= 0:
            raise IntegrityViolationException(
                f"a pair with a requested close may only shrink its debt; {op.kind} {op.identity} changed it by "
                f"{change}",
                details={"invariant": "PENDING_CLOSE_PAIR_NOT_REDUCED", "trustline_id": str(line.id),
                         "pair_debt_change": str(change)},
            )
        if supported in last and last[supported] == 0:
            closing.append(line)
    if not closing:
        return
    await session.execute(update(TrustLine).where(TrustLine.id.in_([line.id for line in closing]))
                          .values(status="closed"))
    for line in closing:
        session.add(IntegrityAuditLog(
            operation_type=TRUST_LINE_CLOSE, tx_id=op.tx_id, equivalent_code=line.code, state_checksum_before="",
            state_checksum_after="", affected_participants=trust_line_close_completed(
                line.from_pid, line.to_pid, str(line.id), op.kind),
            invariants_checked={}, verification_passed=None, error_details=None))
    await session.flush()


async def _complete(
    session: Any, async_conn: Any, op: Operation, operation_id: uuid.UUID
) -> None:
    """Flush, read the entries back, refuse what completion refuses, write membership, COMPLETED."""

    await session.flush()
    entries = debt_journal_entries.c
    rows = (
        await async_conn.execute(
            select(
                entries.ordinal,
                entries.equivalent_id,
                entries.debtor_id,
                entries.creditor_id,
                entries.effect,
                entries.amount_before,
                entries.amount_after,
                entries.delta,
            )
            .where(entries.operation_id == operation_id)
            .order_by(entries.ordinal)
        )
    ).all()

    per_equivalent: dict[uuid.UUID, list[tuple[Any, ...]]] = {}
    for row in rows:
        per_equivalent.setdefault(row.equivalent_id, []).append(
            (
                int(row.ordinal),
                str(row.equivalent_id),
                str(row.debtor_id),
                str(row.creditor_id),
                str(row.effect),
                _money_text(row.amount_before),
                _money_text(row.amount_after),
                _money_text(row.delta),
            )
        )
    touched = sorted(per_equivalent, key=lambda value: value.bytes)

    scope = op.scope_equivalent_ids
    if scope is not None:
        outside = [value for value in touched if value not in scope]
        if outside:
            raise BookError(
                f"{op.kind}/{op.identity} changed debts in {sorted(str(v) for v in outside)}, "
                f"outside its declared scope {sorted(str(v) for v in scope)}",
                reason=Refusal.OUT_OF_SCOPE,
            )

    # 026 `T2601`: NO DIRECTED DEBT GROWS ABOVE ITS LIMIT, whoever called the book. `before` is the
    # first `amount_before` of each debt in this operation, `after` its final amount; a row the
    # operation inserted starts from NULL = no debt. `SEED`/`TEST_FIXTURE` are designated initial
    # states before a baseline (below) and are not growth. The rule is `check_debt_growth`'s; the
    # payment runs it earlier against its prestate, this covers every other production caller.
    if op.kind not in _PRE_BASELINE_ONLY_KINDS:
        before: dict[tuple[Any, Any, Any], Decimal] = {}
        after: dict[tuple[Any, Any, Any], Decimal] = {}
        for row in rows:
            edge = (row.equivalent_id, row.debtor_id, row.creditor_id)
            before.setdefault(edge, Decimal(row.amount_before or 0))
            after[edge] = Decimal(row.amount_after or 0)
        grown = {edge: was for edge, was in before.items() if after[edge] > was}
        if grown:
            await InvariantChecker(session).check_debt_growth(grown)
        if rows:
            await _settle_requested_closes(session, op, rows)

    # T1501: A SEED OR TEST_FIXTURE WRITE AFTER THE BASELINE IS REFUSED, never recorded. Read from the
    # stored entries, so it covers every statement of the operation. A baseline taken concurrently cannot
    # commit alongside it: the baseline takes the equivalent row `FOR UPDATE`, this holds it `FOR SHARE`
    # through the commit and reads the baselines after (027 stage 2; until then SERIALIZABLE) - one waits
    # for the other (`tests/integration/test_p015_step5a_reconciliation_postgres.py`).
    if op.kind in _PRE_BASELINE_ONLY_KINDS and touched:
        await async_conn.execute(select(Equivalent.id).where(Equivalent.id.in_(touched)).with_for_update(read=True))
        column = debt_reconciliation_baselines.c.equivalent_id
        baselined = (
            (await async_conn.execute(select(column).where(column.in_(touched)))).scalars().all()
        )
        if baselined:
            raise BookError(
                f"{op.kind} operation {op.identity} changed debts in {len(baselined)} "
                f"equivalent(s) that already have a reconciliation baseline. Such a write can only "
                f"precede the baseline: nothing can recompute it, and nothing re-baselines.",
                reason=Refusal.UNVERIFIABLE_WRITER_AFTER_BASELINE,
                equivalent_ids=sorted(str(value) for value in baselined),
            )

    for equivalent_id in op.intent_equivalent_ids:
        per_equivalent.setdefault(equivalent_id, [])
    membership = [
        {
            "operation_id": operation_id,
            "equivalent_id": equivalent_id,
            "in_intent": equivalent_id in op.intent_equivalent_ids,
            "in_scope": scope is None or equivalent_id in scope,
            "effect_count": len(codes),
            "effect_digest": _entry_digest(codes),
        }
        for equivalent_id, codes in sorted(per_equivalent.items(), key=lambda kv: kv[0].bytes)
    ]
    if membership:
        await async_conn.execute(insert(debt_operation_equivalents), membership)

    ordered = sorted(
        (code for codes in per_equivalent.values() for code in codes),
        key=lambda code: (code[0], code[1], code[2], code[3]),
    )
    result = await async_conn.execute(
        update(debt_operations)
        .where(debt_operations.c.id == operation_id, debt_operations.c.state == "OPEN")
        .values(
            state="COMPLETED",
            completed_at=datetime.now(timezone.utc),
            # BY ROWS, never from `ordinal` arithmetic: the sequence has gaps.
            effect_count=len(rows),
            effect_digest=_entry_digest(ordered),
        )
    )
    if result.rowcount != 1:
        raise BookError(
            f"completing {op.kind}/{op.identity} updated {result.rowcount} envelope rows; exactly "
            f"one OPEN envelope was expected.",
            reason=Refusal.ENVELOPE_LOST,
        )


async def _set_context(async_conn: Any, value: str) -> None:
    """`set_config('geo.operation_id', value, true)`: transaction-local, never session-level."""

    await async_conn.execute(select(func.set_config(GUC_OPERATION_ID, value, True)))


async def _clear_context(async_conn: Any) -> None:
    """The context is emptied before the savepoint is released (contract item 3)."""

    await _set_context(async_conn, "")


async def _release(nested: Any) -> None:
    await nested.commit()


async def _roll_back_savepoint(nested: Any) -> None:
    await nested.rollback()


async def _make_unusable(async_conn: Any, original: BaseException) -> None:
    """The savepoint could not be rolled back: end the database transaction by closing the connection.

    `FORK-2`: a failed rollback may not become an ordinary refusal followed by a commit - not even
    after `COMPLETED`, when the deferred completion check would let the commit through. Invalidating
    the connection makes the server discard the transaction, and SQLAlchemy refuses a commit on it
    until the caller rolls back. If even that fails, the driver connection is terminated outright.
    """

    try:
        await async_conn.invalidate()
        return
    except BaseException as exc:  # noqa: BLE001 - attached to the original, never raised over it
        original.add_note(f"Book: invalidating the connection failed as well: {exc!r}")
    try:
        async_conn.sync_connection.connection.driver_connection.terminate()
    except BaseException as exc:  # noqa: BLE001 - attached to the original, never raised over it
        original.add_note(
            f"Book: terminating the driver connection failed too ({exc!r}); the transaction may "
            f"still be open and MUST NOT be committed"
        )


async def _abandon(async_conn: Any, nested: Any, original: BaseException) -> None:
    """Contract item 4: roll the book's savepoint back; never raise over the original exception."""

    try:
        await _roll_back_savepoint(nested)
    except BaseException as rollback_error:  # noqa: BLE001 - see `_make_unusable`
        original.add_note(
            f"Book: ROLLBACK TO SAVEPOINT failed ({type(rollback_error).__name__}: "
            f"{rollback_error}); the connection is invalidated so this transaction cannot commit"
        )
        setattr(original, "book_rollback_error", rollback_error)
        await _make_unusable(async_conn, original)


class Book:
    """The single writer of `debts`. Stateless; the state is the posting open on a session."""

    @staticmethod
    @asynccontextmanager
    async def operation(session: Any, op: Operation) -> AsyncIterator[Posting]:
        """One envelope; effects applied through the yielded posting, in the caller's order.

        The transaction contract is the module docstring's, items 1-6, in that order.
        """

        stored_intent, intent_digest = _declaration(op)
        key = _key(session)
        held = _OPEN.get(key)
        if held is not None:
            raise BookError(
                f"operation {held.operation.kind}/{held.operation.identity} is still open on this "
                f"session; operations do not nest",
                reason=Refusal.NESTED_OPERATION,
            )

        # 1. The connection first - it autobegins a fresh session's transaction - then the state.
        async_conn = await session.connection()
        if not session.in_transaction():
            raise BookError("no transaction is open on this session", reason=Refusal.NO_TRANSACTION)
        if _is_autocommit_configured(async_conn):
            raise BookError(
                "this connection is configured AUTOCOMMIT: every statement commits itself",
                reason=Refusal.AUTOCOMMIT_ROOT,
            )
        # 2. Nesting through the TRANSACTION: another session sharing this connection may hold the
        # context. The same statement is what the driver's transaction status is read after.
        current = (await async_conn.exec_driver_sql(_READ_CONTEXT)).scalar()
        _refuse_unless_the_driver_is_in_a_transaction(async_conn)
        if current:
            raise BookError(
                f"this database transaction already carries operation {current}; operations do "
                f"not nest, and a write now could not be told apart from that operation's",
                reason=Refusal.NESTED_OPERATION,
            )
        sync_session = _key(session)
        pending = list(sync_session.new) + list(sync_session.dirty) + list(sync_session.deleted)
        if any(isinstance(obj, Debt) for obj in pending):
            raise BookError(
                "a Debt is already pending in this session. It was not declared by this operation "
                "and would be recorded as if it had been.",
                reason=Refusal.INCOMPLETE_DEBT,
            )

        posting = Posting(session, op)
        _OPEN[key] = posting
        try:
            # 3. The book's own savepoint around everything.
            nested = await session.begin_nested()
            try:
                # THE SAVEPOINT IS LAZY in SQLAlchemy 2.0: `begin_nested()` emits `SAVEPOINT` only
                # when the nested transaction first procures its connection. Statements sent on the
                # connection object before that land OUTSIDE the savepoint - measured 2026-09-24:
                # the envelope and the context survived a rollback, and a cancelled operation's
                # commit met the deferred check. Asking the session for its connection here emits
                # the SAVEPOINT now; it is the same physical connection (`FORK-6`).
                if await session.connection() is not async_conn:
                    raise BookError(
                        "the session changed its connection inside the operation",
                        reason=Refusal.UNMEASURED_TRANSACTION,
                    )
                operation_id = uuid.uuid4()
                await async_conn.execute(
                    insert(debt_operations).values(
                        id=operation_id,
                        kind=op.kind,
                        identity=op.identity,
                        tx_id=op.tx_id,
                        intent=stored_intent,
                        intent_digest=intent_digest,
                        schema_version=SCHEMA_VERSION,
                        money_encoding_version=MONEY_ENCODING_VERSION,
                        intent_encoding_version=intent_encoding_version_for(
                            op.kind, op.intent_encoding_version
                        ),
                        opened_at=datetime.now(timezone.utc),
                        state="OPEN",
                    )
                )
                await _set_context(async_conn, str(operation_id))
                posting.operation_id = operation_id
                try:
                    yield posting
                finally:
                    posting._open = False
                await _complete(session, async_conn, op, operation_id)
                await _clear_context(async_conn)
                await _release(nested)
            except BaseException as exc:
                # 4. Roll the savepoint back and re-raise the ORIGINAL exception.
                await _abandon(async_conn, nested, exc)
                raise
        finally:
            posting._open = False
            if _OPEN.get(key) is posting:
                _OPEN.pop(key, None)

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
            raise BookError("no Book operation is open on this session", reason=Refusal.NO_OPERATION)
        return posting


def operation_for(
    kind: str,
    identity: str,
    intent: Any,
    *,
    tx_id: str | None = None,
    scope_equivalent_ids: Iterable[uuid.UUID] | None = None,
    intent_equivalent_ids: Iterable[uuid.UUID] = (),
    intent_encoding_version: int | None = None,
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
        intent_encoding_version=intent_encoding_version,
    )


__all__: Sequence[str] = (
    "APPLIED",
    "DEBT_OPERATION_IDENTITY_CONSTRAINTS",
    "Book",
    "BookError",
    "BookMoneyError",
    "ClearingReduction",
    "Effect",
    "NewDebt",
    "Operation",
    "PaymentFlow",
    "Posted",
    "Posting",
    "Refusal",
    "operation_for",
)
