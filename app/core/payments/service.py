import uuid
import contextvars
import hashlib
import logging
import asyncio
import random
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import AbstractSet, Any, Awaitable, Callable, List, Literal, NoReturn

from sqlalchemy import select, and_, func, or_, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.core.integrity import compute_integrity_checkpoint_for_equivalent
from app.core.ledger.book import Book, DebtVersionConflict, PaymentFlow, operation_for
from app.core.money_boundary import MoneyBoundary
from app.core.payments.router import PaymentRouter
from app.config import settings
from app.db.models.audit_log import IntegrityAuditLog
from app.db.models.debt import Debt
from app.db.models.prepare_lock import PrepareLock
from app.db.models.transaction import Transaction
from app.db.models.trustline import TrustLine
from app.db.models.participant import Participant
from app.db.models.equivalent import Equivalent
from app.schemas.payment import (
    PaymentConstraints,
    PaymentCreateRequest,
    PaymentResult,
    PaymentRoute,
    PaymentError,
)
from app.core.auth.crypto import verify_signature
from app.core.auth.canonical import canonical_json
from app.utils.exceptions import (
    NotFoundException,
    BadRequestException,
    GeoException,
    ConflictException,
    RetryablePaymentConflictException,
    InvalidSignatureException,
    RoutingException,
    TimeoutException,
)
from app.utils.error_codes import ERROR_MESSAGES, ErrorCode
from app.utils.validation import validate_equivalent_code, validate_tx_id, parse_money_amount

logger = logging.getLogger(__name__)


_RETRYABLE_PAYMENT_SQLSTATES = frozenset({"40001", "40P01"})

#: `details.reason` of the 409 that refuses a `tx_id` replay whose stored PAYMENT row carries no
#: fingerprint (T1548). It names WHY the request is refused rather than answered: the stored row's
#: request identity cannot be verified, so "the same request" cannot be established. Not retryable -
#: repeating the request cannot make the stored row grow a fingerprint.
UNVERIFIABLE_LEGACY_IDENTITY_REASON = "unverifiable_legacy_identity"


def _iter_exception_chain(exc: BaseException):
    """The chain a CLASSIFICATION may read: `orig` / `__cause__` only, never `__context__`.

    ONE RULE FOR BOTH DECISIONS BELOW, 2026-09-12. `_payment_db_sqlstate` and
    `_classify_payment_db_error` both walk this generator, so they cannot drift apart:
    the exception under inspection decides if it carries its own code, and otherwise only
    DELIBERATE wrapping is followed.

    WHY `__context__` IS EXCLUDED, on both backends. Python sets `__context__` to whatever was
    being handled when this exception was raised, which may be an unrelated earlier failure.
    Retry code that catches a conflict and then hits a terminal error inside that `except` block
    is an ordinary shape, and it made the terminal error inherit the conflict's identity: a
    SQLITE_CONSTRAINT_PRIMARYKEY (1555) raised inside a SQLITE_BUSY handler, and equally a
    PostgreSQL 23505 raised inside a 40001 handler, were both classified as retryable and retried
    although retrying them cannot succeed. The SQLite busy predicate (deleted with SQLite, 017 stage
    3) was narrowed for this reason; until this change the fix was defeated one layer up, because
    THIS traversal still handed it nodes found through `__context__`.

    Nothing legitimate is lost. SQLAlchemy raises `DBAPIError` FROM the driver error, so the
    genuine cause is always reachable as `orig` (and as `__cause__`, since `raise ... from` sets
    it). `__context__` adds only the incidental case, which is
    the masking hazard itself rather than a capability.
    """

    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current

        following = getattr(current, "orig", None)
        if not isinstance(following, BaseException):
            following = current.__cause__
        current = following if isinstance(following, BaseException) else None


def _payment_db_sqlstate(exc: BaseException) -> str | None:
    chain = list(_iter_exception_chain(exc))
    for attribute in ("sqlstate", "pgcode"):
        for current in chain:
            value = getattr(current, attribute, None)
            if value:
                return str(value)
    for current in chain:
        # SQLAlchemy's wrapper-level `.code` identifies its documentation page
        # (for example "dbapi"), not PostgreSQL SQLSTATE. Driver exceptions may
        # expose SQLSTATE as `.code`, so inspect only non-wrapper nodes here.
        if isinstance(current, DBAPIError):
            continue
        value = getattr(current, "code", None)
        if value:
            return str(value)
    return None


def _conflict_cause(exc: BaseException) -> str:
    """What a retried conflict actually was, for the log: the SQLSTATE, or the book's conflict type."""

    for current in _iter_exception_chain(exc):
        if isinstance(current, DebtVersionConflict):
            return type(current).__name__
        if isinstance(current, DBAPIError):
            return _payment_db_sqlstate(current) or type(current).__name__
    return type(exc).__name__


def _classify_payment_db_error(exc: BaseException) -> GeoException:
    """Map database concurrency failures without exposing driver details.

    Retryable: a DBAPI error carrying 40001/40P01, and - 019 stage 3, `FORK-1` - the book's
    `DebtVersionConflict` (a payment flow's debt changed underneath this transaction; the book no
    longer retries it from the same snapshot). Only that subclass: any other `StaleDataError`, and
    every other ORM error, is not a conflict a fresh attempt is known to cure.
    """

    for current in _iter_exception_chain(exc):
        if isinstance(current, DebtVersionConflict):
            return RetryablePaymentConflictException()
        if not isinstance(current, DBAPIError):
            continue
        if _payment_db_sqlstate(current) in _RETRYABLE_PAYMENT_SQLSTATES:
            return RetryablePaymentConflictException()
    return GeoException()


async def _drain_payment_cleanup(
    operation: Callable[[], Awaitable[Any]],
) -> BaseException | None:
    """Run session-owned cleanup to a terminal result under caller cancellation."""

    task = asyncio.create_task(operation())
    caller_cancellation: asyncio.CancelledError | None = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as exc:
            if task.done():
                continue
            if caller_cancellation is None:
                caller_cancellation = exc
            # Repeated caller cancellation must not interrupt the session-owned
            # terminalization sequence and leave a durable NEW/PREPARED row.
        except Exception:
            # The task is terminal; classify its exact result below.
            pass

    operation_error: BaseException | None = None
    try:
        task.result()
    except BaseException as exc:
        operation_error = exc
    return caller_cancellation or operation_error


async def _drain_call(
    operation: Callable[[], Awaitable[Any]],
) -> tuple[Any, BaseException | None]:
    """Run `operation` to a terminal result under caller cancellation; return (result, error)."""

    box: list[Any] = []

    async def run() -> None:
        box.append(await operation())

    error = await _drain_payment_cleanup(run)
    return (box[0] if box else None), error


def _count_create_start() -> None:
    try:
        from app.utils.metrics import PAYMENT_EVENTS_TOTAL

        PAYMENT_EVENTS_TOTAL.labels(event="create", result="start").inc()
    except Exception:
        pass


def _payment_deadline(deadline: float | None, total_timeout_s: float):
    """The time bound of one `execute()`: the owner's deadline, or the payment's own total budget."""

    if deadline is not None:
        return asyncio.timeout_at(deadline)
    return asyncio.timeout(total_timeout_s)


def _refusal_error_payload(reason: str | None, code: str | None, details: dict | None) -> dict:
    """The stored `error` of a refused payment, normalized as the removed `PaymentEngine.abort` did."""

    try:
        normalized = ErrorCode(str(code)) if code is not None else ErrorCode.E010
    except Exception:
        normalized = ErrorCode.E010
    return {
        "code": normalized.value,
        "message": str(reason or ERROR_MESSAGES[normalized]),
        "details": dict(details or {}),
    }


def _borrowed_session(session: AsyncSession):
    """A session "factory" that hands out one existing session and never closes it."""

    @asynccontextmanager
    async def borrow():
        yield session

    return borrow


#: The uniqueness a concurrent insert of the same `tx_id` meets first (`T1902`: by the migrated
#: schema's catalogue and by a real duplicate insert). The identity resolver matches exactly this one
#: constraint; any other `23505` is not an identity collision and is not resolved or retried here.
TX_ID_UNIQUE_CONSTRAINT = "transactions_tx_id_key"

#: SQLSTATE classes a server answers a failed `COMMIT` with AFTER rolling the transaction back: an
#: integrity violation found at commit (deferred constraints and triggers, class 23), a transaction
#: rollback (class 40, incl. 40001/40P01), a raised exception of a deferred trigger (class P0). Every
#: other failure of a `COMMIT` - no SQLSTATE at all (a lost connection, a timeout, a cancellation), a
#: connection exception (08), an operator intervention (57) - leaves the outcome UNKNOWN: the commit
#: may have landed, and nothing may be terminalized before the identity is read.
_COMMIT_REFUSED_SQLSTATE_CLASSES = frozenset({"23", "40", "P0"})

#: The least lock wait a refusal recording is granted (019 stage-3 review, P2 #5). The recording is ONE
#: insert that can only wait on a row lock - an uncommitted row of the same `tx_id` in another
#: transaction - and it runs AFTER the attempt, often after its deadline has already expired (a timeout
#: refusal). Its wait is bounded by `SET LOCAL lock_timeout`: the rest of the payment's deadline, but
#: never less than this grace, so an uncontended recording always completes and a contended one gives up
#: instead of outliving the request.
_REFUSAL_RECORD_LOCK_GRACE_MS = 500


class RefusalNotRecorded(Exception):
    """The refusal recording gave up on its bounded lock wait (`55P03`): the refusal is NOT durable, and
    its outcome is left unrecorded - a later submission of the same `tx_id` reads whatever the other
    transaction left, or executes. Never a 500: the caller answers the original error."""


def _constraint_name(exc: BaseException) -> str | None:
    """The constraint a DBAPI error names, across asyncpg (`constraint_name`) and psycopg (`diag`)."""

    for current in _iter_exception_chain(exc):
        direct = getattr(current, "constraint_name", None)
        if direct:
            return str(direct)
        diagnostic = getattr(getattr(current, "diag", None), "constraint_name", None)
        if diagnostic:
            return str(diagnostic)
    return None


def _is_tx_id_collision(exc: BaseException) -> bool:
    """A `23505` on `transactions_tx_id_key` and nothing else (spec, "Идентичность `tx_id`")."""

    return (
        isinstance(exc, IntegrityError)
        and _payment_db_sqlstate(exc) == "23505"
        and _constraint_name(exc) == TX_ID_UNIQUE_CONSTRAINT
    )


@dataclass
class _PaymentAttempt:
    """What one `execute()` established, for the owner of its transaction to act on (019 stage 3).

    `admitted` - the request passed its applicability checks (routing, the stop/hold pre-check) and
    the payment operation began (spec, "Допуск", `FORK-5`); from here on a definitive failure is
    recorded `ABORTED`. `refusal` - (reason, code, details, log event prefix) of a failure inside the
    operation, when the operation named it; the prefix keeps the log event names of the code this
    replaces. `identity_collision` - the insert met a concurrent row of the same `tx_id` on
    `transactions_tx_id_key`. `operation_left_unrolled` - the operation's savepoint could not be rolled
    back and the transaction is unusable.
    """

    tx_id: str | None = None
    fingerprint: str | None = None
    sender_id: uuid.UUID | None = None
    allowed_participant_pids: "AbstractSet[str] | None" = None
    row: dict[str, Any] | None = None
    admitted: bool = False
    identity_collision: bool = False
    refusal: tuple[str, str, dict[str, Any], str] | None = None
    operation_left_unrolled: bool = False


@dataclass(frozen=True)
class _Admission:
    """`pay()`'s in-process memory that THIS request was admitted by an earlier attempt (`FORK-5`).

    Keyed by the request identity - `tx_id`, initiator and fingerprint; the type is always `PAYMENT`
    on this path - and held only by the owner of the retries for the life of one `pay()` call. It is
    never durable and never handed to another request: the simulator has none, because its replay
    re-plans the load and a re-planned action is a different request (spec, "Допуск").
    """

    tx_id: str
    sender_id: uuid.UUID
    fingerprint: str
    row: dict[str, Any]

    @classmethod
    def of(cls, attempt: "_PaymentAttempt") -> "_Admission | None":
        if not attempt.admitted or attempt.row is None or attempt.tx_id is None:
            return None
        return cls(str(attempt.tx_id), attempt.sender_id, str(attempt.fingerprint), dict(attempt.row))  # type: ignore[arg-type]

    def covers(self, attempt: "_PaymentAttempt") -> bool:
        return (
            attempt.tx_id == self.tx_id
            and attempt.sender_id == self.sender_id
            and attempt.fingerprint == self.fingerprint
        )


@dataclass(frozen=True)
class DefinitiveRefusal:
    """A definitive refusal of an ADMITTED payment, as it is recorded `ABORTED` (Q1, `FORK-4`).

    `row` - the `Transaction` values (identity, payload) the `ABORTED` row is written with; `error` - the
    stored `error`; `public_error` - what the caller is answered; `event_prefix` - the log event names of
    a recording that fails (`event=payment.<prefix>_failed`), those of the code this replaces.
    """

    tx_id: str
    sender_id: uuid.UUID
    fingerprint: str
    allowed_participant_pids: "AbstractSet[str] | None"
    row: dict[str, Any]
    error: dict[str, Any]
    public_error: GeoException
    event_prefix: str


#: Where a staged payment hands an ADMITTED refusal it cannot record itself to the owner of the caller's
#: transaction (019 stage-3 review, P2 #3). Set by the simulator's money-phase owner around one attempt
#: (`collect_admitted_refusals`); the executor's tasks copy it with their context. A cancellation is the
#: case: it must propagate, and anything written into the phase's transaction before it propagates is
#: rolled back with the phase - so the refusal is handed over instead, and the owner records it after the
#: phase rollback is established. Without an owner (None) the refusal is written into the caller's
#: transaction as before.
_ADMITTED_REFUSALS: "contextvars.ContextVar[list[DefinitiveRefusal] | None]" = contextvars.ContextVar(
    "payment_admitted_refusals", default=None
)


@contextmanager
def collect_admitted_refusals():
    """For the owner of a caller transaction: collect the admitted refusals its staged payments could not
    record (see `_ADMITTED_REFUSALS`)."""

    sink: list[DefinitiveRefusal] = []
    token = _ADMITTED_REFUSALS.set(sink)
    try:
        yield sink
    finally:
        _ADMITTED_REFUSALS.reset(token)


class PaymentTransactionUnusable(Exception):
    """A staged payment failed and left the CALLER'S transaction unusable (spec, "Ветка непригодной
    транзакции", `T1912`).

    Raised by `execute()` in staged mode instead of recording anything: a structured refusal needs a
    usable transaction, and this one is not - the payment operation's savepoint could not be rolled back
    (a statement cancelled in flight by a timeout leaves the connection invalidated), or the refusal
    could not be written into it. The owner of the transaction - the simulator's money phase - rolls
    back and closes the WHOLE phase, positively establishes that rollback, and only then records
    `refusal` (when there is one) in a short transaction of its own (`record_definitive_refusal`).

    `publish_refusal` is attached by the staged executor: the one observation of this refusal
    (`tx.failed`), which the owner publishes once, after the refusal's outcome is established.

    Deliberately NOT a `GeoException` and not a retryable conflict: nothing may count it as a refused
    payment of a usable transaction, and a replay of the phase must not be its answer.
    """

    def __init__(self, refusal: DefinitiveRefusal | None, cause: BaseException) -> None:
        super().__init__(
            f"the payment's transaction is unusable after {type(cause).__name__}"
            + (f" (tx_id={refusal.tx_id})" if refusal is not None else "")
        )
        self.refusal = refusal
        self.cause = cause
        self.publish_refusal: Callable[[], Any] | None = None


def _public_error_of_stored(result: PaymentResult) -> GeoException | None:
    """The public error a STORED `ABORTED` result was refused with, as the exception class that carried
    it when it was raised (019 stage-3 review, P2 #6): the staged executor classifies a replayed refusal
    by it - a stored `E007` is a timeout, `E010` an internal error, a routing code a routing refusal -
    exactly as it classified the refusal the first time. None for anything but an `ABORTED` result."""

    if result.status != "ABORTED" or result.error is None:
        return None
    code = str(result.error.code or ErrorCode.E010.value)
    message = str(result.error.message or "") or None
    details = dict(result.error.details or {})
    if code == ErrorCode.E007.value:
        return TimeoutException(message, details=details)
    if code in (ErrorCode.E001.value, ErrorCode.E002.value):
        return RoutingException(message, insufficient_capacity=code == ErrorCode.E002.value, details=details)
    if code == ErrorCode.E008.value:
        return ConflictException(message, details=details)
    if code == ErrorCode.E009.value:
        return BadRequestException(message, details=details)
    if code == ErrorCode.E005.value:
        return InvalidSignatureException(message, details=details)
    if code == ErrorCode.E010.value:
        return GeoException(message, code=ErrorCode.E010, details=details, status_code=500)
    return GeoException(message, code=code, details=details, status_code=400)


def _definitive_refusal(
    attempt: "_PaymentAttempt",
    exc: BaseException,
    admission: "_Admission | None" = None,
) -> DefinitiveRefusal | None:
    """The refusal to record for a failed attempt, or None - classified by CAUSE, not by code.

    None when the request was not admitted (by this attempt, or by an earlier attempt of the same
    identity, `admission`): a refusal before admission leaves no row, and a later submission may still
    execute. None for a transaction-level conflict - `40001`, `40P01`, the book's
    `DebtVersionConflict` - including one that exhausted the retry
    budget (it answers `409/E008` with `retryable: true`), and for an identity collision (the identity
    resolver answers it). Otherwise - a capacity, stop or hold refusal, a non-retryable internal
    failure, a terminal timeout or a cancellation - a definitive refusal with the existing public
    classification of the error. Whether the attempt's rollback is CONFIRMED is the caller's to
    establish before recording it.
    """

    admitted = attempt.admitted or (admission is not None and admission.covers(attempt))
    if not admitted or attempt.identity_collision:
        return None
    row = attempt.row if attempt.row is not None else (admission.row if admission is not None else None)
    if row is None or attempt.tx_id is None:
        return None

    if isinstance(exc, asyncio.CancelledError):
        public: GeoException = TimeoutException("Payment cancelled")
        reason, code, details, prefix = "Payment cancelled", ErrorCode.E007.value, {}, "cancellation_cleanup"
    elif isinstance(exc, asyncio.TimeoutError):
        public = TimeoutException("Payment timed out")
        reason, code, details, prefix = "Payment timeout", ErrorCode.E007.value, {}, "timeout_abort"
    else:
        public = exc if isinstance(exc, GeoException) else _classify_payment_db_error(exc)
        if isinstance(public, RetryablePaymentConflictException):
            return None
        if attempt.refusal is not None:
            reason, code, details, prefix = attempt.refusal
        else:
            reason, code, details, prefix = str(public.message), str(public.code), dict(public.details or {}), "abort"
    return DefinitiveRefusal(
        tx_id=str(attempt.tx_id),
        sender_id=attempt.sender_id,  # type: ignore[arg-type]
        fingerprint=str(attempt.fingerprint),
        allowed_participant_pids=attempt.allowed_participant_pids,
        row=dict(row),
        error=_refusal_error_payload(reason, code, details),
        public_error=public,
        event_prefix=prefix,
    )


@dataclass(frozen=True)
class _RetryAttempt:
    """`pay()`: discard this attempt and start another after `delay_seconds`, remembering `admission`."""

    delay_seconds: float
    admission: "_Admission | None" = None


@dataclass
class PaymentPostCommitEffects:
    """Best-effort effects that are only valid after the DB transaction commits."""

    equivalent: str
    recipient_pid: str
    event_payload: dict[str, str]
    invalidate_routing_cache: bool = True
    include_engine_success_metrics: bool = False
    _applied: bool = field(default=False, init=False, repr=False)
    _cache_invalidated: bool = field(default=False, init=False, repr=False)

    def invalidate_routing_cache_once(self) -> bool:
        """Discard possibly stale routes without publishing commit-only effects."""

        if self._cache_invalidated:
            return False
        self._cache_invalidated = True

        if self.invalidate_routing_cache:
            try:
                PaymentRouter.invalidate_cache(self.equivalent)
            except Exception:
                logger.warning(
                    "event=payment.post_commit.cache_invalidation_failed equivalent=%s",
                    self.equivalent,
                    exc_info=True,
                )
        return True

    def apply_once(self) -> bool:
        if self._applied:
            return False
        # Mark first: these effects are process-local and cannot be made exactly-once
        # across a crash without a transactional outbox.
        self._applied = True
        self.invalidate_routing_cache_once()

        try:
            from app.utils.metrics import PAYMENT_EVENTS_TOTAL

            PAYMENT_EVENTS_TOTAL.labels(event="create", result="success").inc()
            if self.include_engine_success_metrics:
                # The commit's success, once per confirmed commit (019 stage 3, `FORK-9`). The
                # `prepare` success that stood here is removed with the prepare phase (stage 4): a
                # payment no longer has a durable prepare to succeed.
                PAYMENT_EVENTS_TOTAL.labels(event="commit", result="success").inc()
        except Exception:
            pass

        try:
            from app.utils.event_bus import event_bus

            event_bus.publish(
                recipient_pid=self.recipient_pid,
                event="payment.received",
                payload=dict(self.event_payload),
            )
        except Exception:
            pass
        return True


@dataclass(frozen=True)
class StagedPaymentResult:
    """What a staged payment returns into the caller's transaction.

    `refusal` - set when `result` is `ABORTED`: the public error the refusal carries, so a caller
    classifies it exactly as it classified the exception the staged path raised before (the executor:
    rejected, timeout or internal) - for a refusal recorded in this transaction (019 stage 3, `T1905`)
    and for a stored one answered by idempotency alike (stage-3 review, P2 #6: a replayed `E007` is a
    timeout, not a generic rejection).

    `written_here` - the row of `result` was written by THIS call into the caller's transaction (a fresh
    payment or a refusal recorded here), as opposed to a stored row of an earlier transaction answered
    by idempotency or by the identity resolver. Only such a row is evidence that the caller's commit
    landed (stage-3 review, P2 #2).
    """

    result: PaymentResult
    post_commit_effects: PaymentPostCommitEffects | None
    refusal: GeoException | None = None
    written_here: bool = False


#: Scale 8, the scale every money column in this repository carries.
_MONEY_QUANTUM = Decimal("1E-8")


def _scale8_money(amount: Decimal) -> str:
    """A payment flow amount as the exact scale-8 string the operation intent records.

    `"8.00"` and `"8.00000000"` are the same money, and an intent recording whichever spelling a route
    happened to carry would give two identical payments two different digests. The amounts reaching
    here passed the storage door (`parse_money_amount`) and carry scale 8 or less, so `quantize` widens
    and never rounds; were one ever to carry more, the journal's own quantization predicate refuses the
    debt write that follows, so the operation fails loudly instead of recording a rounded intent.
    """

    return f"{Decimal(amount).quantize(_MONEY_QUANTUM):f}"


@dataclass(frozen=True)
class DeclaredFlow:
    """One validated segment of a declared route: `from_id` pays `to_id` `amount` in `equivalent_id`."""

    from_id: uuid.UUID
    to_id: uuid.UUID
    amount: Decimal
    equivalent_id: uuid.UUID

    def as_intent(self) -> dict[str, str]:
        return {
            "from": str(self.from_id),
            "to": str(self.to_id),
            "amount": _scale8_money(self.amount),
            "equivalent": str(self.equivalent_id),
        }


@dataclass(frozen=True)
class PaymentDeclaration:
    """THE INTENT of one payment (019 stage 4, `FORK-8`): its validated routes, segment by segment, in order.

    Immutable, and built ONLY from the routes the binding phase validated (`_bind_payment`) - never from
    applied effects, journal entries or resulting debts. It replaces `prepare_locks.effects` as the
    authoritative statement of what the payment is about to do; nothing stores it but the operation
    envelope (no table of its own). The flows are applied exactly in this order.
    """

    tx_id: str
    routes: tuple[tuple[DeclaredFlow, ...], ...]

    def flows(self) -> tuple[DeclaredFlow, ...]:
        return tuple(flow for route in self.routes for flow in route)

    def equivalent_ids(self) -> set[uuid.UUID]:
        return {flow.equivalent_id for flow in self.flows()}

    def intent(self, prestate: list[dict[str, str]]) -> dict[str, Any]:
        """The envelope's intent, encoding version 2, in its existing shape: `tx_id`, `locks: [{flows}]`
        - one container per declared route, its segments in path order - and `prestate`. The verifier
        requires the container and reads no identity inside it (`reconciliation.py`, `_payment_flows`),
        so no synthetic reservation id is recorded."""

        return {
            "tx_id": self.tx_id,
            "locks": [{"flows": [flow.as_intent() for flow in route]} for route in self.routes],
            "prestate": prestate,
        }


async def _read_payment_prestate(
    session: AsyncSession,
    declared_flows: "tuple[DeclaredFlow, ...] | list[DeclaredFlow]",
) -> list[dict[str, str]]:
    """The amounts on BOTH directions of every declared flow pair, before any flow runs, in ONE read.

    WHAT IT IS FOR. A payment flow first reduces the receiver's debt to the sender and nets a mutual
    pair (`app/core/ledger/book.py`, `_apply_payment_flow`), so what a flow does to `debts` depends on
    both directions of its pair - and neither is in the flows. Recorded in the payment envelope (intent
    encoding version 2), they let the scheduled verifier recompute the netted deltas from the envelope
    alone (`app/core/ledger/reconciliation.py`, criterion (b)).

    WHERE IT RUNS (019 stage 4, `FORK-8`; moved here from the engine): once per attempt, after every
    lock of the payment and the operator-stop `FOR SHARE`, immediately before the envelope that records
    it, in the transaction that applies the flows. A retry is a fresh attempt on a fresh snapshot and
    reads again.

    COST: one SELECT per payment, whatever the number of flows, pairs and equivalents.

    Every direction is written, zero included: an absent entry and a zero amount must not be two
    spellings of one fact a reader has to agree on.
    """

    edges = sorted(
        {
            (flow.equivalent_id, debtor_id, creditor_id)
            for flow in declared_flows
            for debtor_id, creditor_id in ((flow.from_id, flow.to_id), (flow.to_id, flow.from_id))
        },
        key=lambda edge: (str(edge[0]), str(edge[1]), str(edge[2])),
    )
    if not edges:
        return []
    rows = (
        await session.execute(
            select(Debt.equivalent_id, Debt.debtor_id, Debt.creditor_id, Debt.amount).where(
                or_(
                    *(
                        and_(
                            Debt.equivalent_id == equivalent_id,
                            Debt.debtor_id == debtor_id,
                            Debt.creditor_id == creditor_id,
                        )
                        for equivalent_id, debtor_id, creditor_id in edges
                    )
                )
            )
        )
    ).all()
    held = {
        (equivalent_id, debtor_id, creditor_id): Decimal(str(amount))
        for equivalent_id, debtor_id, creditor_id, amount in rows
    }
    return [
        {
            "equivalent": str(equivalent_id),
            "debtor": str(debtor_id),
            "creditor": str(creditor_id),
            "amount": _scale8_money(held.get((equivalent_id, debtor_id, creditor_id), Decimal("0"))),
        }
        for equivalent_id, debtor_id, creditor_id in edges
    ]


def _refuse_attempt(attempt: "_PaymentAttempt", error: Exception, prefix: str) -> "NoReturn":
    """Name the refusal a failure inside the payment operation would be terminalized with, and raise.

    A client-level `GeoException` (4xx) is raised as it is - the simulator classifies it as a
    rejection rather than an internal error; anything else is raised as its public classification. A
    retryable conflict names no refusal: it belongs to the owner of the transaction's retries."""

    is_client_error = isinstance(error, GeoException) and 400 <= int(
        getattr(error, "status_code", 500) or 500
    ) < 500
    public_error = error if is_client_error else _classify_payment_db_error(error)
    if not isinstance(public_error, RetryablePaymentConflictException):
        attempt.refusal = (
            str(public_error.message),
            str(getattr(public_error, "code", ErrorCode.E010.value)),
            getattr(public_error, "details", None) or {},
            prefix,
        )
    if is_client_error:
        raise error
    raise public_error from error


class PaymentService:
    def __init__(self, session: AsyncSession):
        self.session = session
        # ONE money boundary for the service's lifetime: the advisory-lock deadline starts at the first
        # lock this service takes and is shared by every later staged acquisition (019 stage 2 review,
        # P2). Since stage 4 the payment itself executes here, through `Book` - no engine.
        self._boundary: MoneyBoundary = MoneyBoundary(session)
        self.router = PaymentRouter(session)

    @staticmethod
    def _resolve_existing_payment(
        existing_tx: Transaction,
        *,
        sender_id: uuid.UUID,
        request_fingerprint: str,
        allowed_participant_pids: "AbstractSet[str] | None" = None,
    ) -> PaymentResult:
        """Apply one idempotency policy to both lookup and insert-race rows."""
        if existing_tx.type != "PAYMENT":
            raise ConflictException("tx_id already used")
        if existing_tx.initiator_id != sender_id:
            raise ConflictException("tx_id already used")

        existing_payload = existing_tx.payload or {}
        existing_idempotency = existing_payload.get("idempotency")
        existing_fp = (
            existing_idempotency.get("fingerprint")
            if isinstance(existing_idempotency, dict)
            else None
        )

        # T1548, 2026-09-14 (step F2 of the 015 closure).  A STORED ROW WITH NO FINGERPRINT
        # CANNOT BE SHOWN TO BE A REPLAY OF THIS REQUEST, so it is refused instead of
        # answered.  Until here the comparison below ran only `if existing_fp is not None`:
        # a row written before fingerprints existed, or by any path that records none, fell
        # through to `idempotent_hit` and the caller was handed a stored result for a request
        # nobody had established was the same one - equality of the canonical payload was
        # GUESSED.  The refusal writes no money, reads no route and compares no payload; the
        # stored result stays readable through `GET /payments/{tx_id}`, which is why refusing
        # here loses nothing the caller could not still see.
        #
        # A fingerprint is "present" only when it is a non-empty string, because that is the
        # only shape the comparison below can decide anything with.  Any other shape - a
        # null, an `idempotency` that is not an object, an empty string - is an identity this
        # code cannot verify, and the one before this one crashed on some of them.
        #
        # FIRST OF THE THREE CHECKS THAT FOLLOW, ahead of the perimeter and of the in-progress
        # branch: those two answer "whose route is this" and "is it still running", and both
        # questions presuppose that the row is this request at all.
        if not isinstance(existing_fp, str) or not existing_fp:
            logger.error(
                "event=payment.replay_without_stored_fingerprint tx_id=%s state=%s",
                str(existing_tx.tx_id),
                str(existing_tx.state),
            )
            try:
                from app.utils.metrics import PAYMENT_EVENTS_TOTAL

                PAYMENT_EVENTS_TOTAL.labels(event="create", result="conflict").inc()
            except Exception:
                pass
            raise ConflictException(
                "tx_id already used by a stored payment whose request identity "
                "cannot be verified",
                details={
                    "reason": UNVERIFIABLE_LEGACY_IDENTITY_REASON,
                    "retryable": False,
                },
            )

        if existing_fp != request_fingerprint:
            raise ConflictException("tx_id already used for a different request")

        # 2026-08-22 / p010.  This shortcut returns a stored result BEFORE the narrowing and
        # the postcondition, so a scoped caller replaying an idempotency key would otherwise
        # be handed whatever route was recorded - and that transaction may have been written
        # by an unscoped caller, or by this code before the perimeter existed, with a route
        # through another run.
        #
        # Placed AFTER the fingerprint comparison on purpose, and the first version had it
        # before: reusing a tx_id for a DIFFERENT request is a declared 409 conflict owned by
        # another program, and checking the route first turned that into a routing 400
        # whenever the stored route also left the perimeter.  Establish that the row is a
        # replay of THIS request, then ask whose route it is.
        if allowed_participant_pids is not None:
            routes = (existing_payload.get("routes") or [])
            paths = [route.get("path") or [] for route in routes]
            if not paths or not all(paths):
                # A payload whose route cannot be read is not a payload that passes - the
                # same rule the clearing replay guard applies, and the first version of this
                # one silently did the opposite for rows with no recorded routes.
                logger.error(
                    "event=payment.idempotent_replay_unverifiable tx_id=%s",
                    str(existing_tx.tx_id),
                )
                raise RoutingException(
                    "No route found with sufficient capacity",
                    insufficient_capacity=False,
                )
            escaped = {
                str(pid)
                for path in paths
                for pid in path
                if str(pid) not in allowed_participant_pids
            }
            if escaped:
                logger.error(
                    "event=payment.idempotent_replay_escaped_perimeter tx_id=%s pids=%s",
                    str(existing_tx.tx_id),
                    sorted(escaped),
                )
                raise RoutingException(
                    "No route found with sufficient capacity",
                    insufficient_capacity=False,
                )

        if existing_tx.state in {
            "NEW",
            "ROUTED",
            "PREPARE_IN_PROGRESS",
            "PREPARED",
            "PROPOSED",
            "WAITING",
        }:
            try:
                from app.utils.metrics import PAYMENT_EVENTS_TOTAL

                PAYMENT_EVENTS_TOTAL.labels(
                    event="create", result="conflict_in_progress"
                ).inc()
            except Exception:
                pass
            raise ConflictException("Payment with same tx_id is in progress")

        try:
            from app.utils.metrics import PAYMENT_EVENTS_TOTAL

            PAYMENT_EVENTS_TOTAL.labels(event="create", result="idempotent_hit").inc()
        except Exception:
            pass
        return PaymentService._tx_to_payment_result(existing_tx)

    async def create_payment(
        self,
        sender_id: uuid.UUID,
        request: PaymentCreateRequest,
        *,
        idempotency_key: str | None = None,
    ) -> PaymentResult:
        """A signed payment on THIS service's session: `pay()` with every attempt on that session.

        For callers that already hold a session (the seed recipe, tests, in-process tools). Each
        attempt is its own database transaction on it - `pay()` rolls back between attempts, so a
        retry reads a fresh snapshot - and the payment ends committed on it. The HTTP endpoint does
        not come here: it calls `pay()` with a session factory, a new session per attempt.
        """

        return await self.pay(
            _borrowed_session(self.session),
            sender_id,
            request,
            idempotency_key=idempotency_key,
            require_signature=True,
            _service_for=lambda _session: self,
        )

    @staticmethod
    def _confine_router_to_perimeter(router, allowed: "AbstractSet[str]") -> None:
        """Narrow the router INSTANCE to one run's participants.

        2026-08-22 / p010 (`F-010-3`).  The endpoints of a payment were already scoped, but
        the graph the route is chosen from covers the whole equivalent, so the hops in
        between belonged to nobody in particular - a payment inside run A would consume the
        trust of a participant of run B and create debt rows in their name.

        Instance state only.  A cache hit copies the graph and the policy maps into the
        instance (`app/core/payments/router.py:167-173`), and a cache miss stores its own
        copies (`:342-351`), so narrowing here cannot reach the shared cache.  The cache key
        stays `equivalent_code`: making it composite would silently break the two callers
        that reach into `_graph_cache` directly and the invalidation done per equivalent by
        trustlines, clearing and integrity - none of which this program may edit.

        `graph` is what actually decides the route; the policy maps default to permissive
        (`router.py:369-373`), so narrowing them changes no outcome today and is here to keep
        the structures consistent for whoever reads them next.  The blocked-participant sets
        are values of a DENY list and are copied unchanged - intersecting them with an allow
        list is backwards, and would weaken the restriction the day someone applies it to an
        endpoint rather than a hop.
        """

        router.graph = {
            u: {v: cap for v, cap in adj.items() if v in allowed}
            for u, adj in router.graph.items()
            if u in allowed
        }
        router.edge_can_be_intermediate = {
            u: {v: flag for v, flag in adj.items() if v in allowed}
            for u, adj in router.edge_can_be_intermediate.items()
            if u in allowed
        }
        router.edge_blocked_participants = {
            u: {v: blocked for v, blocked in adj.items() if v in allowed}
            for u, adj in router.edge_blocked_participants.items()
            if u in allowed
        }

    async def create_payment_internal(
        self,
        sender_id: uuid.UUID,
        *,
        to_pid: str,
        equivalent: str,
        amount: str,
        description: str | None = None,
        constraints: PaymentConstraints | None = None,
        idempotency_key: str | None = None,
        commit: bool = True,
        allowed_participant_pids: "AbstractSet[str] | None" = None,
    ) -> PaymentResult:
        """Internal-only payment path for the simulator runner.

        IMPORTANT:
        - This must never be exposed via HTTP endpoints.
        - It bypasses signature verification and should only be used by trusted
          in-process code.
        """

        if not commit:
            raise ValueError(
                "commit=False is staged work; use create_payment_internal_staged() "
                "and apply its post-commit effects after the caller commits"
            )

        # Internal-only path: tx_id is generated in-process (or derived from idempotency_key)
        # because no external caller is responsible for retries here.
        tx_id = (idempotency_key or "").strip() or str(uuid.uuid4())
        req = PaymentCreateRequest(
            tx_id=tx_id,
            to=to_pid,
            equivalent=equivalent,
            amount=amount,
            description=description,
            constraints=constraints,
            signature="__internal__",
        )
        # 019 stage 3: the same one-transaction `pay()` as the API, on this service's session (see
        # `create_payment`).
        return await self.pay(
            _borrowed_session(self.session),
            sender_id,
            req,
            idempotency_key=idempotency_key,
            require_signature=False,
            allowed_participant_pids=allowed_participant_pids,
            _service_for=lambda _session: self,
        )

    async def create_payment_internal_staged(
        self,
        sender_id: uuid.UUID,
        *,
        to_pid: str,
        equivalent: str,
        amount: str,
        description: str | None = None,
        constraints: PaymentConstraints | None = None,
        idempotency_key: str | None = None,
        allowed_participant_pids: "AbstractSet[str] | None" = None,
    ) -> StagedPaymentResult:
        """Flush an internal payment into the caller transaction without publishing it.

        2026-08-22 / p010 (`F-010-4`): the run perimeter reaches this path too.  Closing it
        only on `create_payment_internal` left the simulator tick able to route a run's
        payment through another run's participant - the same P1, on the path that runs by
        itself and is therefore both more repeatable and less visible.
        """

        tx_id = (idempotency_key or "").strip() or str(uuid.uuid4())
        req = PaymentCreateRequest(
            tx_id=tx_id,
            to=to_pid,
            equivalent=equivalent,
            amount=amount,
            description=description,
            constraints=constraints,
            signature="__internal__",
        )
        return await self.execute(
            sender_id,
            req,
            idempotency_key=idempotency_key,
            require_signature=False,
            allowed_participant_pids=allowed_participant_pids,
        )

    async def acquire_staged_equivalent_owner_locks(
        self,
        equivalent_codes: list[str] | tuple[str, ...] | set[str],
    ) -> None:
        """Pre-acquire one sorted owner set for a caller-owned staged batch."""
        codes = sorted(
            {
                str(code).strip().upper()
                for code in equivalent_codes
                if str(code).strip()
            }
        )
        if not codes:
            return

        rows = (
            await self.session.execute(
                select(Equivalent.id, Equivalent.code).where(Equivalent.code.in_(codes))
            )
        ).all()
        equivalent_ids_by_code = {str(code): equivalent_id for equivalent_id, code in rows}
        # Preserve per-action validation for unknown codes; only persisted
        # equivalents can own monetary resources or an advisory lock.
        resolved_ids = [
            equivalent_ids_by_code[code]
            for code in codes
            if code in equivalent_ids_by_code
        ]
        if not resolved_ids:
            return

        try:
            await self._boundary.acquire_staged_equivalent_owner_locks(
                resolved_ids
            )
        except DBAPIError as exc:
            if _payment_db_sqlstate(exc) == "55P03":
                raise asyncio.TimeoutError(
                    "Payment equivalent owner lock timed out"
                ) from exc
            raise _classify_payment_db_error(exc) from exc

    async def execute(
        self,
        sender_id: uuid.UUID,
        request: PaymentCreateRequest,
        *,
        require_signature: bool,
        idempotency_key: str | None = None,
        allowed_participant_pids: "AbstractSet[str] | None" = None,
        deadline: float | None = None,
        use_shared_routing_cache: bool = False,
        record_refusal: bool = True,
        emit_start: bool = True,
    ) -> StagedPaymentResult:
        """Execute a payment INSIDE THE CALLER'S TRANSACTION; never commit or roll it back.

        Programme 019 stages 3-4 (`specs/019-payment-one-transaction/spec.md`, "Контракт исполнения").
        Steps: validation and idempotency (a stored row answers with its stored result or a 409),
        the best-effort stop/hold pre-check, routing, ADMISSION, and then THE PAYMENT OPERATION - one
        savepoint opened before the `Transaction` is added, around DIRECT EXECUTION (stage 4,
        `_run_payment_operation`): the row inserted `COMMITTED`, the owner, tx and pair locks, the
        capacity of every segment, the stop/hold `FOR SHARE`, the pre-state, the envelope with the
        declared intent, the book with its own savepoint, `check_payment_delta`, the trust-limit and
        symmetry checks, the integrity audit row. No intermediate payment state is written. Nothing of
        it is visible to another transaction before the caller commits, and a failure inside it rolls
        ALL of it back.

        Nesting: caller's transaction -> payment operation savepoint -> the book's savepoint. A transaction-level conflict (40001, 40P01, the book's
        `DebtVersionConflict`) is PROPAGATED as `RetryablePaymentConflictException`: a savepoint
        rollback does not refresh a SERIALIZABLE snapshot, so only the owner of the whole transaction
        can retry it - `pay()` for the API, the money-phase replay for the simulator.

        `record_refusal` (the staged default): a failure is settled here, for a caller that owns the
        transaction (`_settle_staged_failure`) - a definitive refusal after admission is written
        `ABORTED` into the caller's transaction and RETURNED as a structured result, an identity
        collision is resolved, a transaction left unusable raises `PaymentTransactionUnusable` (019
        `T1905`, `T1912`). `pay()` turns it off: every failure is raised with `self._attempt` describing
        it, and `pay()` settles it after rolling the attempt back.
        `deadline` (an event-loop time) bounds the call instead of `PAYMENT_TOTAL_TIMEOUT_SECONDS`.

        Returns the result and the post-commit effects, which only the caller may apply, once, after
        its commit.
        """
        if emit_start:
            _count_create_start()
        attempt = self._attempt = _PaymentAttempt()

        try:
            # Storage-capacity door (012 / F-012-1).  Deliberately the FIRST thing this
            # method does with the request: the signature is taken over `request.amount`
            # verbatim further down (`payload` / `verify_signature` below), so an amount
            # the ledger cannot hold must never become a signed obligation - and the
            # rounds-to-zero case must not reach the positivity CHECK, where it used to
            # escape as HTTP 500 E010 instead of a 400 naming the amount.
            amount = parse_money_amount(
                request.amount, field="amount", require_positive=True
            )
        except BadRequestException:
            # Preserve existing metrics semantics for invalid user input.
            try:
                from app.utils.metrics import PAYMENT_EVENTS_TOTAL

                PAYMENT_EVENTS_TOTAL.labels(event="create", result="bad_request").inc()
            except Exception:
                pass
            raise

        validate_equivalent_code(request.equivalent)

        # Mandatory idempotency key (client-generated).
        tx_id_str = validate_tx_id(request.tx_id)

        # 1. Validation
        sender = await self.session.get(Participant, sender_id)
        if not sender:
            try:
                from app.utils.metrics import PAYMENT_EVENTS_TOTAL

                PAYMENT_EVENTS_TOTAL.labels(event="create", result="not_found").inc()
            except Exception:
                pass
            raise NotFoundException("Sender not found")

        if require_signature:
            if not isinstance(request.signature, str) or not request.signature:
                try:
                    from app.utils.metrics import PAYMENT_EVENTS_TOTAL

                    PAYMENT_EVENTS_TOTAL.labels(
                        event="create", result="bad_request"
                    ).inc()
                except Exception:
                    pass
                raise InvalidSignatureException("Missing signature")

        receiver = (
            await self.session.execute(
                select(Participant).where(Participant.pid == request.to)
            )
        ).scalar_one_or_none()
        if not receiver:
            try:
                from app.utils.metrics import PAYMENT_EVENTS_TOTAL

                PAYMENT_EVENTS_TOTAL.labels(event="create", result="not_found").inc()
            except Exception:
                pass
            raise NotFoundException(f"Receiver {request.to} not found")

        if sender.id == receiver.id:
            try:
                from app.utils.metrics import PAYMENT_EVENTS_TOTAL

                PAYMENT_EVENTS_TOTAL.labels(event="create", result="bad_request").inc()
            except Exception:
                pass
            raise BadRequestException("Cannot pay to yourself")

        equivalent = (
            await self.session.execute(
                select(Equivalent).where(Equivalent.code == request.equivalent)
            )
        ).scalar_one_or_none()
        if not equivalent:
            try:
                from app.utils.metrics import PAYMENT_EVENTS_TOTAL

                PAYMENT_EVENTS_TOTAL.labels(event="create", result="not_found").inc()
            except Exception:
                pass
            raise NotFoundException(f"Equivalent {request.equivalent} not found")

        # A rolled-back savepoint may expire the session identity map. Keep the validated wire identifiers as plain
        # values so result construction never triggers implicit async ORM IO.
        sender_pid = str(sender.pid)
        receiver_pid = str(receiver.pid)
        equivalent_id = equivalent.id
        equivalent_code = str(equivalent.code)

        # Signature payload (canonical JSON) is part of the API contract for MVP.
        # IMPORTANT: it must include tx_id and must exclude the `signature` field itself.
        payload: dict = {
            "tx_id": tx_id_str,
            "to": request.to,
            "equivalent": request.equivalent,
            "amount": request.amount,
        }
        if request.description is not None:
            payload["description"] = request.description
        if request.constraints is not None:
            payload["constraints"] = request.constraints.model_dump(exclude_unset=True)

        message = canonical_json(payload)

        if require_signature:
            # Signature validation (proof-of-possession + binding of request fields).
            try:
                verify_signature(sender.public_key, message, request.signature)
            except Exception:
                try:
                    from app.utils.metrics import PAYMENT_EVENTS_TOTAL

                    PAYMENT_EVENTS_TOTAL.labels(
                        event="create", result="invalid_signature"
                    ).inc()
                except Exception:
                    pass
                raise InvalidSignatureException("Invalid signature")

        # Idempotency: same tx_id + same canonical payload => return same result.
        # same tx_id + different canonical payload => 409.
        request_fingerprint = hashlib.sha256(message).hexdigest()
        attempt.tx_id = tx_id_str
        attempt.fingerprint = request_fingerprint
        attempt.sender_id = sender_id
        attempt.allowed_participant_pids = allowed_participant_pids
        existing_tx = (
            await self.session.execute(
                select(Transaction).where(Transaction.tx_id == tx_id_str)
            )
        ).scalar_one_or_none()
        if existing_tx is not None:
            stored = self._resolve_existing_payment(
                existing_tx,
                sender_id=sender_id,
                request_fingerprint=request_fingerprint,
                allowed_participant_pids=allowed_participant_pids,
            )
            return StagedPaymentResult(
                result=stored,
                post_commit_effects=None,
                refusal=_public_error_of_stored(stored),
            )

        # T1544: a deactivated equivalent takes no new payment. Best effort, on the row loaded above
        # and AFTER the idempotency decision, so a replay of an already-accepted tx_id still answers
        # with its stored result. It is not the binding check - that one is at commit, under the
        # owner lock and `FOR SHARE` (`MoneyBoundary.refuse_inactive_equivalents`) - and it
        # deliberately does not lock: forbidding a new PREPARED state after the PATCH returns would
        # be a stronger rule than this task's.
        if not equivalent.is_active:
            try:
                from app.utils.metrics import PAYMENT_EVENTS_TOTAL

                PAYMENT_EVENTS_TOTAL.labels(event="create", result="conflict").inc()
            except Exception:
                pass
            raise MoneyBoundary.inactive_equivalent_conflict([equivalent_code])
        # Step 5c (`T1546`): the integrity hold, on the same row, with the same best-effort standing -
        # the binding read is the commit's. After the operator stop, so one reason per refusal.
        if equivalent.integrity_hold_result_id is not None:
            try:
                from app.utils.metrics import PAYMENT_EVENTS_TOTAL

                PAYMENT_EVENTS_TOTAL.labels(event="create", result="conflict").inc()
            except Exception:
                pass
            raise MoneyBoundary.integrity_hold_conflict([equivalent_code])

        # 2. Routing
        tx_uuid = uuid.uuid4()

        # Effective routing constraints (signed client request is the source of truth;
        # hub settings may cap it from above).
        client_constraints: PaymentConstraints | None = request.constraints

        multipath_enabled = bool(getattr(settings, "FEATURE_FLAGS_MULTIPATH_ENABLED", True))

        server_max_hops = int(getattr(settings, "ROUTING_MAX_HOPS", 6) or 6)
        server_max_paths = int(getattr(settings, "ROUTING_MAX_PATHS", 3) or 3)
        if not multipath_enabled:
            server_max_paths = 1

        server_timeout_ms = int(
            getattr(settings, "ROUTING_PATH_FINDING_TIMEOUT_MS", 500) or 500
        )

        def _effective_int(*, client_value: int | None, server_default: int) -> int:
            if client_value is None:
                return int(server_default)
            return int(min(int(client_value), int(server_default)))

        effective_max_hops = _effective_int(
            client_value=(client_constraints.max_hops if client_constraints else None),
            server_default=server_max_hops,
        )
        effective_max_paths = _effective_int(
            client_value=(client_constraints.max_paths if client_constraints else None),
            server_default=server_max_paths,
        )
        effective_timeout_ms = _effective_int(
            client_value=(client_constraints.timeout_ms if client_constraints else None),
            server_default=server_timeout_ms,
        )

        effective_avoid: list[str] | None = None
        if client_constraints is not None and client_constraints.avoid:
            effective_avoid = [str(x) for x in client_constraints.avoid if isinstance(x, str) and x]

        routing_timeout_s = float(max(1, effective_timeout_ms)) / 1000.0
        prepare_timeout_s = float(getattr(settings, "PREPARE_TIMEOUT_SECONDS", 3) or 3)
        commit_timeout_s = float(getattr(settings, "COMMIT_TIMEOUT_SECONDS", 5) or 5)
        total_timeout_s = float(
            getattr(settings, "PAYMENT_TOTAL_TIMEOUT_SECONDS", 10) or 10
        )

        try:
            async with _payment_deadline(deadline, total_timeout_s):
                # Build routing graph + compute routes under spec-aligned timeout budget.
                try:
                    if use_shared_routing_cache:
                        build_graph = self.router.build_graph(equivalent_code)
                    else:
                        build_graph = self.router.build_graph(
                            equivalent_code,
                            use_shared_cache=False,
                        )
                    await asyncio.wait_for(
                        build_graph,
                        timeout=routing_timeout_s,
                    )
                except asyncio.TimeoutError:
                    raise TimeoutException("Routing timed out")

                # The route is chosen from the graph, so the perimeter has to be applied
                # here -- after the graph is built (and possibly served from the shared
                # cache), before a route is picked.
                if allowed_participant_pids is not None:
                    if not allowed_participant_pids:
                        # An empty perimeter admits nobody.  `_run_scoped_pids_or_none`
                        # returns exactly that when the perimeter cannot be established, and
                        # treating it as "no restriction" would be a literal return of
                        # `F-009-1`.
                        raise RoutingException(
                            "No route found with sufficient capacity",
                            insufficient_capacity=False,
                        )
                    self._confine_router_to_perimeter(
                        self.router, allowed_participant_pids
                    )

                try:
                    routes_found = await asyncio.wait_for(
                        asyncio.to_thread(
                            self.router.find_flow_routes,
                            sender_pid,
                            receiver_pid,
                            amount,
                            max_hops=effective_max_hops,
                            max_paths=effective_max_paths,
                            timeout_ms=effective_timeout_ms,
                            avoid_participants=effective_avoid,
                        ),
                        timeout=routing_timeout_s,
                    )
                except asyncio.TimeoutError:
                    raise TimeoutException("Routing timed out")

                if not routes_found:
                    try:
                        from app.utils.metrics import (
                            PAYMENT_EVENTS_TOTAL,
                            ROUTING_FAILURES_TOTAL,
                        )

                        PAYMENT_EVENTS_TOTAL.labels(
                            event="create", result="routing_failed"
                        ).inc()
                        ROUTING_FAILURES_TOTAL.labels(
                            reason="insufficient_capacity"
                        ).inc()
                    except Exception:
                        pass
                    raise RoutingException(
                        "No route found with sufficient capacity",
                        insufficient_capacity=True,
                    )

                # Postcondition, and not a formality: the narrowing above and this check
                # fail independently, so either alone would let the route-level test pass.
                # It also covers the receiver, whom `_create_payment_impl` resolves from the
                # global participant table (`:448-451`) rather than from the perimeter.
                if allowed_participant_pids is not None:
                    escaped = {
                        pid
                        for path, _amount in routes_found
                        for pid in path
                        if pid not in allowed_participant_pids
                    }
                    if escaped:
                        logger.error(
                            "event=payment.route_escaped_perimeter pids=%s",
                            sorted(escaped),
                        )
                        raise RoutingException(
                            "No route found with sufficient capacity",
                            insufficient_capacity=False,
                        )

                routes_payload = [
                    {"path": path, "amount": str(route_amount)}
                    for path, route_amount in routes_found
                ]

                # 3-5. THE PAYMENT OPERATION (019 stages 3-4). The `Transaction` insert (`COMMITTED`),
                # the locks, the capacity, the envelope, the book and the checks run inside ONE
                # savepoint of the caller's transaction, opened BEFORE the row is added to the session
                # (see `_open_operation_savepoint`). A failure anywhere in it rolls the whole operation
                # back - no row or debt of this attempt survives in the caller's transaction - and only
                # then is the refusal recorded, if this call records refusals (`record_refusal`).
                attempt.row = {
                    "id": tx_uuid,
                    "tx_id": tx_id_str,
                    "idempotency_key": None,
                    "type": "PAYMENT",
                    "initiator_id": sender_id,
                    "payload": {
                        "from": sender_pid,
                        "to": receiver_pid,
                        "amount": str(amount),
                        "equivalent": equivalent_code,
                        "routes": routes_payload,
                        "idempotency": {
                            "key": tx_id_str,
                            "fingerprint": request_fingerprint,
                        },
                    },
                }
                # THE LOGICAL ADMISSION POINT (spec, "Допуск", `FORK-5`): routing and the stop/hold
                # pre-check passed and the binding phase begins. From here on a definitive failure of
                # this request is recorded `ABORTED`; before it, a refusal leaves no row. Since stage 4
                # there is no `NEW` row to mark it and none is needed: admission is this flag, carried
                # across `pay()`'s attempts by `_Admission` (same identity), never durable - where the
                # `INSERT` stands relative to it decides nothing.
                attempt.admitted = True
                operation = await self._open_operation_savepoint()
                try:
                    await self._run_payment_operation(
                        attempt,
                        routes_found=routes_found,
                        amount=amount,
                        equivalent_id=equivalent_id,
                        prepare_timeout_s=prepare_timeout_s,
                        commit_timeout_s=commit_timeout_s,
                    )
                    await operation.commit()
                except BaseException as exc:
                    await self._abandon_operation(operation, exc)
                    raise
        except BaseException as exc:
            if record_refusal:
                # Staged: the caller owns the transaction, and the outcome goes back through its
                # savepoint as a RESULT (`_settle_staged_failure`).
                return await self._settle_staged_failure(attempt, exc)
            if isinstance(exc, asyncio.TimeoutError):
                if attempt.admitted:
                    attempt.refusal = ("Payment timeout", ErrorCode.E007.value, {}, "timeout_abort")
                raise TimeoutException("Payment timed out") from exc
            raise

        # Fetch server timestamps (created_at/updated_at) explicitly.
        # IMPORTANT: with SQLAlchemy AsyncSession, accessing expired ORM attributes may
        # trigger implicit IO and raise MissingGreenlet. Avoid relying on identity-map
        # instances here.
        created_at = None
        committed_at = None
        tx_row = (
            await self.session.execute(
                select(Transaction.state, Transaction.created_at, Transaction.updated_at).where(
                    Transaction.id == tx_uuid
                )
            )
        ).one_or_none()
        if tx_row is not None:
            state, created_at, updated_at = tx_row
            if state == "COMMITTED":
                committed_at = updated_at

        routes = [
            PaymentRoute(path=path, amount=str(route_amount))
            for path, route_amount in routes_found
        ]
        result = PaymentResult(
            tx_id=tx_id_str,
            status="COMMITTED",
            **{"from": sender_pid},
            to=receiver_pid,
            equivalent=equivalent_code,
            amount=str(amount),
            routes=routes,
            created_at=created_at,
            committed_at=committed_at,
        )
        # Post-commit effects are the CALLER's: they are valid only after its transaction commits,
        # and `apply_once` publishes them - including `PAYMENT_EVENTS_TOTAL{commit,success}` - once
        # (019 stage 3, `FORK-9`). Nothing here counts a success: this transaction may still roll
        # back.
        effects = PaymentPostCommitEffects(
            equivalent=equivalent_code,
            recipient_pid=receiver_pid,
            event_payload={
                "tx_id": tx_id_str,
                "from": sender_pid,
                "to": receiver_pid,
                "equivalent": equivalent_code,
                "amount": str(amount),
            },
            invalidate_routing_cache=True,
            include_engine_success_metrics=True,
        )
        return StagedPaymentResult(result=result, post_commit_effects=effects, written_here=True)

    async def _open_operation_savepoint(self):
        """The payment operation's savepoint, opened for real before anything of it is added.

        Two SQLAlchemy behaviours make the order load-bearing. `begin_nested()` FLUSHES pending state
        before it begins, so a `Transaction` added before it would be written outside the savepoint;
        and the savepoint is LAZY - `SAVEPOINT` is emitted only when the nested transaction first
        procures its connection, so statements sent before that would land outside it too. Asking the
        session for its connection emits it now (the same device as `Book.operation`, `book.py`).
        """

        operation = await self.session.begin_nested()
        await self.session.connection()
        return operation

    async def _abandon_operation(self, operation, original: BaseException) -> None:
        """Roll the payment operation back; never raise over the original exception.

        A rollback that fails leaves this attempt's rows possibly in the transaction, so - as the book
        does for its own savepoint (`book.py`, `_abandon`) - the connection is invalidated: the server
        discards the transaction and a commit of it is impossible. Run to completion even under a
        caller's cancellation.
        """

        async def roll_back() -> None:
            # A flush that failed inside the operation DEACTIVATES it without closing it (`is_active`
            # is then False, yet the session refuses every statement until it is rolled back); so the
            # operation is rolled back while it is still the session's innermost transaction too.
            if (
                operation.is_active
                or operation.sync_transaction is self.session.sync_session.get_nested_transaction()
            ):
                await operation.rollback()

        failure = await _drain_payment_cleanup(roll_back)
        if failure is None or isinstance(failure, asyncio.CancelledError):
            return
        logger.error(
            "event=payment.operation_rollback_failed tx_id=%s error_type=%s",
            str(getattr(self._attempt, "tx_id", None)),
            type(failure).__name__,
        )
        original.add_note(
            f"payment operation: ROLLBACK TO SAVEPOINT failed ({type(failure).__name__}); the "
            f"connection is invalidated so this transaction cannot commit"
        )
        self._attempt.operation_left_unrolled = True
        try:
            connection = await self.session.connection()
            await connection.invalidate()
            return
        except BaseException as exc:  # noqa: BLE001 - attached to the original, never raised over it
            original.add_note(f"payment operation: invalidating the connection failed as well: {exc!r}")
        try:
            connection = await self.session.connection()
            connection.sync_connection.connection.driver_connection.terminate()
        except BaseException as exc:  # noqa: BLE001 - attached to the original, never raised over it
            original.add_note(
                f"payment operation: terminating the driver connection failed too ({exc!r}); the "
                f"transaction may still be open and MUST NOT be committed"
            )

    async def _run_payment_operation(
        self,
        attempt: "_PaymentAttempt",
        *,
        routes_found,
        amount,
        equivalent_id,
        prepare_timeout_s: float,
        commit_timeout_s: float,
    ) -> None:
        """DIRECT EXECUTION (019 stage 4, `T1906`), all inside the operation savepoint the caller opened.

        The `Transaction` row is inserted `COMMITTED` - there is no intermediate payment state any more,
        neither written nor visible: the row exists only inside this savepoint, and a failure anywhere
        below rolls it back together with the money, so a `COMMITTED` row without its money never reaches
        the caller's commit. Then, in this order:

        1. THE BINDING PHASE (`_bind_payment`, log/metric phase name `prepare`): the equivalent owner
           lock, the transaction lock, the pair locks, the capacity of every segment - yielding the
           DECLARATION, the immutable intent (validated ordered route segments and amounts).
        2. THE MONEY (`_apply_payment`, phase name `commit`): the operator stop/hold `FOR SHARE`, the
           authoritative pre-state of both directions of every pair (`_read_payment_prestate`), the v2
           envelope carrying the declaration and the pre-state - BEFORE the first debt write - the book's
           flows, and inside the same rollback boundary `check_payment_delta`, `check_trust_limits`,
           `check_debt_symmetry` and the integrity audit row.

        A refusal sets `attempt.refusal` - what the payment would be terminalized with - and raises; it
        never writes here, because whatever it wrote would be rolled back with the operation.
        """

        tx_id_str = str(attempt.tx_id)
        new_tx = Transaction(**attempt.row, state="COMMITTED")
        self.session.add(new_tx)
        try:
            await self.session.flush()
        except IntegrityError as exc:
            # A concurrent request inserted the same `tx_id` and committed first: THE identity
            # collision, answered by the identity resolver after the operation savepoint is rolled back
            # (`pay()` / `_settle_staged_failure`). Only `transactions_tx_id_key` is that collision; any
            # other integrity error of this insert is an ordinary failure (spec: no broad 23505 retry).
            attempt.identity_collision = _is_tx_id_collision(exc)
            raise
        except DBAPIError as exc:
            public_error = _classify_payment_db_error(exc)
            if not isinstance(public_error, RetryablePaymentConflictException):
                raise
            # A serialization failure or a stale snapshot belongs to the whole transaction; its
            # owner retries it on a fresh one (`pay()` for the API, the money-phase replay for the
            # simulator).
            raise public_error from exc

        if len(routes_found) == 1:
            routes = [(list(routes_found[0][0]), amount)]
        else:
            routes = [(list(path), route_amount) for path, route_amount in routes_found]

        # The advisory-lock waits inside the operation are bounded by the payment's own deadline, not by
        # `SET LOCAL lock_timeout`, which would outlive this savepoint and become the timeout policy of
        # the caller's later statements (the engine's `commit=False` units of work did the same).
        boundary = self._boundary
        previous_timeout = (
            boundary._advisory_lock_timeout_enabled,
            boundary._advisory_lock_deadline,
        )
        boundary._advisory_lock_timeout_enabled = False
        boundary._advisory_lock_deadline = None
        try:
            # 1. The binding phase.
            try:
                declaration = await asyncio.wait_for(
                    self._bind_payment(tx_id_str, routes, equivalent_id),
                    timeout=prepare_timeout_s,
                )
            except asyncio.TimeoutError:
                raise
            except Exception as e:
                logger.error(
                    "event=payment.prepare_failed tx_id=%s error_type=%s",
                    tx_id_str,
                    type(e).__name__,
                )
                try:
                    from app.utils.metrics import PAYMENT_EVENTS_TOTAL

                    PAYMENT_EVENTS_TOTAL.labels(event="prepare", result="error").inc()
                except Exception:
                    pass
                _refuse_attempt(attempt, e, "prepare_nested_abort")

            # 2. The money.
            try:
                await asyncio.wait_for(
                    self._apply_payment(declaration, payload=dict(attempt.row["payload"])),
                    timeout=commit_timeout_s,
                )
            except asyncio.TimeoutError:
                raise
            except Exception as e:
                logger.error(
                    "event=payment.commit_failed tx_id=%s error_type=%s",
                    tx_id_str,
                    type(e).__name__,
                )
                try:
                    from app.utils.metrics import PAYMENT_EVENTS_TOTAL

                    PAYMENT_EVENTS_TOTAL.labels(event="commit", result="error").inc()
                except Exception:
                    pass
                _refuse_attempt(attempt, e, "commit_nested_abort")
        finally:
            boundary._advisory_lock_timeout_enabled, boundary._advisory_lock_deadline = (
                previous_timeout
            )

    async def _bind_payment(
        self,
        tx_id: str,
        routes: "list[tuple[list[str], Decimal]]",
        equivalent_id: uuid.UUID,
    ) -> "PaymentDeclaration":
        """The binding phase: locks, then the capacity of every segment; returns the declaration.

        Lock order (`app/core/money_boundary.py`): the equivalent owner lock, the transaction lock, the
        pair locks in their one global order - and only then the rows. The capacity of each segment is
        read here, in this attempt's snapshot, after the locks: the limit of the receiver's active line
        to the sender, both directions of the pair's debt, and the flows other transactions still hold
        reserved in `prepare_locks` (nothing writes reservations since stage 4; the table and every
        reader of it go in stage 5, `T1909` - until then a reservation, however it got there, is
        honoured). Several routes over one segment are summed (`local_reserved`).

        Refused with `RoutingException` (`E002`, `details` with `available`, `needed`, `reserved`) when a
        segment cannot carry its amount - after admission, so a definitive refusal (spec, "Допуск").
        """

        boundary = self._boundary
        await boundary._acquire_equivalent_owner_locks([equivalent_id])
        await boundary._acquire_tx_advisory_lock(tx_id)

        pids: set[str] = set()
        for path, route_amount in routes:
            if route_amount <= 0:
                raise GeoException("Route amount must be positive")
            if len(path) < 2:
                raise GeoException("Route path must include at least 2 participants")
            pids.update(path)
        participants = {
            str(pid): participant_id
            for participant_id, pid in (
                await self.session.execute(
                    select(Participant.id, Participant.pid).where(Participant.pid.in_(pids))
                )
            ).all()
        }
        if len(participants) != len(pids):
            raise GeoException(f"Participants not found: {pids - set(participants)}")

        await boundary._acquire_segment_advisory_locks(
            equivalent_id=equivalent_id,
            routes=routes,
            participant_map=participants,
        )

        local_reserved: dict[tuple[uuid.UUID, uuid.UUID], Decimal] = {}
        declared_routes: list[tuple[DeclaredFlow, ...]] = []
        for path, route_amount in routes:
            segments: list[DeclaredFlow] = []
            for sender_pid, receiver_pid in zip(path, path[1:]):
                sender_id = participants[sender_pid]
                receiver_id = participants[receiver_pid]
                available, reserved = await self._segment_capacity(
                    tx_id=tx_id,
                    sender_id=sender_id,
                    receiver_id=receiver_id,
                    equivalent_id=equivalent_id,
                )
                reserved += local_reserved.get((sender_id, receiver_id), Decimal("0"))
                if available < (route_amount + reserved):
                    raise RoutingException(
                        f"Insufficient capacity between {sender_pid} and {receiver_pid}. "
                        f"Available: {available}, Needed: {route_amount}, Reserved: {reserved}",
                        insufficient_capacity=True,
                        details={
                            "available": str(available),
                            "needed": str(route_amount),
                            "reserved": str(reserved),
                            "from": sender_pid,
                            "to": receiver_pid,
                        },
                    )
                local_reserved[(sender_id, receiver_id)] = (
                    local_reserved.get((sender_id, receiver_id), Decimal("0")) + route_amount
                )
                segments.append(
                    DeclaredFlow(
                        from_id=sender_id,
                        to_id=receiver_id,
                        amount=Decimal(route_amount),
                        equivalent_id=equivalent_id,
                    )
                )
            declared_routes.append(tuple(segments))
        return PaymentDeclaration(tx_id=tx_id, routes=tuple(declared_routes))

    async def _segment_capacity(
        self,
        *,
        tx_id: str,
        sender_id: uuid.UUID,
        receiver_id: uuid.UUID,
        equivalent_id: uuid.UUID,
    ) -> "tuple[Decimal, Decimal]":
        """(available capacity, capacity other transactions hold reserved) of one flow edge."""

        line = (
            await self.session.execute(
                select(TrustLine.limit).where(
                    TrustLine.from_participant_id == receiver_id,
                    TrustLine.to_participant_id == sender_id,
                    TrustLine.equivalent_id == equivalent_id,
                    TrustLine.status == "active",
                )
            )
        ).scalar_one_or_none()
        limit = line if line is not None else Decimal("0")
        receiver_owes = await self._debt_amount(receiver_id, sender_id, equivalent_id)
        sender_owes = await self._debt_amount(sender_id, receiver_id, equivalent_id)

        reserved = Decimal("0")
        for lock_tx_id, effects in (
            await self.session.execute(
                select(PrepareLock.tx_id, PrepareLock.effects).where(
                    PrepareLock.participant_id == sender_id,
                    PrepareLock.expires_at > func.now(),
                )
            )
        ).all():
            if lock_tx_id == tx_id:
                continue
            for flow in (effects or {}).get("flows", []):
                try:
                    if uuid.UUID(flow["equivalent"]) != equivalent_id:
                        continue
                    flow_from = uuid.UUID(flow["from"])
                    flow_to = uuid.UUID(flow["to"])
                    flow_amount = Decimal(str(flow["amount"]))
                except Exception:
                    continue
                if flow_from == sender_id and flow_to == receiver_id:
                    reserved += flow_amount
        return limit - sender_owes + receiver_owes, reserved

    async def _debt_amount(
        self, debtor_id: uuid.UUID, creditor_id: uuid.UUID, equivalent_id: uuid.UUID
    ) -> Decimal:
        value = (
            await self.session.execute(
                select(Debt.amount).where(
                    Debt.debtor_id == debtor_id,
                    Debt.creditor_id == creditor_id,
                    Debt.equivalent_id == equivalent_id,
                )
            )
        ).scalar_one_or_none()
        return value if value is not None else Decimal("0")

    async def _apply_payment(self, declaration: "PaymentDeclaration", *, payload: dict) -> None:
        """The money: stop/hold, pre-state, the envelope BEFORE the first debt write, the flows, the checks.

        WHERE THE STOP IS READ. After every lock of the binding phase and immediately before the
        pre-state and the envelope - the place the engine's commit read it. `FOR SHARE` holds through
        the caller's commit, so a deactivating PATCH either waits for this payment or makes this read
        fail with 40001 and the owner's next attempt refuse (`MoneyBoundary.refuse_inactive_equivalents`).

        THE ENVELOPE AND ITS INTENT (`FORK-8`). `Book.operation` INSERTs and flushes the envelope at open,
        so it is on this connection before `Book` writes the first debt: a payment's statement of what it
        is about to do exists before it does any of it. The intent is the DECLARATION - built by
        `_bind_payment` from the validated routes, never from applied effects, journal entries or final
        debts - plus the pre-state read here. An intent read back from the result could not disagree
        with it, and being able to disagree is the entire reason it is recorded (criterion (b)).

        THE CHECKS (`FORK-9`), after the writes and a flush, inside the operation's rollback boundary:
        `check_payment_delta` against the declared flows, `check_trust_limits` and `check_debt_symmetry`
        over the affected pairs (`Book.post` replaces none of them). Their refusal raises and the
        operation savepoint rolls everything back, the `COMMITTED` row included. Then the integrity audit
        row per equivalent, TRANSACTIONALLY: a database error writing it propagates; any other failure of
        the audit is best-effort, as before.
        """

        session = self.session
        tx_id = declaration.tx_id
        equivalent_ids = declaration.equivalent_ids()

        # FIX-014: integrity checksums before the flows.
        checkpoints_before: dict[uuid.UUID, object] = {}
        for eq_id in sorted(equivalent_ids, key=str):
            try:
                checkpoints_before[eq_id] = await compute_integrity_checkpoint_for_equivalent(
                    session, equivalent_id=eq_id
                )
            except DBAPIError:
                # PostgreSQL aborts the transaction after a database error: the owner of the retries
                # must see the original SQLSTATE, not a misleading 25P02 later.
                raise
            except Exception as exc:
                logger.warning(
                    "event=payment.audit_checkpoint_before_failed tx_id=%s error_type=%s",
                    tx_id,
                    type(exc).__name__,
                )

        await self._boundary.refuse_inactive_equivalents(equivalent_ids, row_lock=True)

        prestate = await _read_payment_prestate(session, declaration.flows())

        async with Book.operation(
            session,
            operation_for(
                "PAYMENT",
                tx_id,
                tx_id=tx_id,
                intent=declaration.intent(prestate),
                scope_equivalent_ids=equivalent_ids,
                intent_equivalent_ids=equivalent_ids,
            ),
        ) as posting:
            participants_by_equivalent: dict[uuid.UUID, set[uuid.UUID]] = {}
            pairs_by_equivalent: dict[uuid.UUID, set[tuple[uuid.UUID, uuid.UUID]]] = {}
            flows_by_equivalent: dict[uuid.UUID, list[tuple[uuid.UUID, uuid.UUID, Decimal]]] = {}
            for flow in declaration.flows():
                participants_by_equivalent.setdefault(flow.equivalent_id, set()).update(
                    (flow.from_id, flow.to_id)
                )
                pairs_by_equivalent.setdefault(flow.equivalent_id, set()).update(
                    ((flow.from_id, flow.to_id), (flow.to_id, flow.from_id))
                )
                flows_by_equivalent.setdefault(flow.equivalent_id, []).append(
                    (flow.from_id, flow.to_id, flow.amount)
                )

            net_positions_before = {
                eq_id: await self._boundary._snapshot_net_positions(
                    equivalent_id=eq_id, participant_ids=participant_ids
                )
                for eq_id, participant_ids in participants_by_equivalent.items()
            }

            for flow in declaration.flows():
                await posting.apply(
                    PaymentFlow(
                        from_id=flow.from_id,
                        to_id=flow.to_id,
                        amount=flow.amount,
                        equivalent_id=flow.equivalent_id,
                    )
                )
            await session.flush()

            # The zero-sum call that once stood among these checks was removed by T1402 of programme
            # 014 (it could not fail); nothing replaces it here - the replacement invariant is 015's.
            from app.core.invariants import InvariantChecker

            checker = InvariantChecker(session)
            for eq_id, pairs in pairs_by_equivalent.items():
                await checker.check_trust_limits(equivalent_id=eq_id, participant_pairs=list(pairs))
                await checker.check_debt_symmetry(equivalent_id=eq_id, participant_pairs=list(pairs))
                await self._boundary.check_payment_delta(
                    equivalent_id=eq_id,
                    flows=flows_by_equivalent.get(eq_id, []),
                    net_positions_before=net_positions_before.get(eq_id, {}),
                )

            await self._write_integrity_audit(
                tx_id,
                payload=payload,
                equivalent_ids=list(pairs_by_equivalent),
                checkpoints_before=checkpoints_before,
            )

    async def _write_integrity_audit(
        self,
        tx_id: str,
        *,
        payload: dict,
        equivalent_ids: "list[uuid.UUID]",
        checkpoints_before: dict,
    ) -> None:
        """FIX-014: one `IntegrityAuditLog` row per equivalent, in THIS transaction.

        A database error propagates - swallowed, it would poison the live transaction and hide the
        SQLSTATE from the owner of the retries. Any other failure of one equivalent's audit is logged and
        skipped (best-effort, unchanged)."""

        participant_pids: set[str] = set()
        for key in ("from", "to"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                participant_pids.add(value)
        for route in payload.get("routes") or []:
            if not isinstance(route, dict) or not isinstance(route.get("path"), list):
                continue
            participant_pids.update(pid for pid in route["path"] if isinstance(pid, str) and pid)

        for eq_id in equivalent_ids:
            try:
                eq_code = (
                    await self.session.execute(select(Equivalent.code).where(Equivalent.id == eq_id))
                ).scalar_one_or_none()
                before_sum = getattr(checkpoints_before.get(eq_id), "checksum", "") or ""
                cp_after = await compute_integrity_checkpoint_for_equivalent(
                    self.session, equivalent_id=eq_id
                )
                after_sum = getattr(cp_after, "checksum", before_sum) or before_sum
                invariants_status = getattr(cp_after, "invariants_status", {}) or {}
                passed = bool(invariants_status.get("passed", False))
                self.session.add(
                    IntegrityAuditLog(
                        operation_type="PAYMENT",
                        tx_id=tx_id,
                        equivalent_code=str(eq_code or eq_id),
                        state_checksum_before=before_sum,
                        state_checksum_after=after_sum,
                        affected_participants={"participants": sorted(participant_pids)},
                        invariants_checked=invariants_status.get("checks") or invariants_status,
                        verification_passed=passed,
                        error_details=None if passed else invariants_status,
                    )
                )
            except DBAPIError:
                raise
            except Exception as exc:
                logger.warning(
                    "event=payment.audit_log_failed tx_id=%s error_type=%s",
                    tx_id,
                    type(exc).__name__,
                )

    # ── staged: the caller owns the transaction ──────────────────────────────────────────────────

    async def _settle_staged_failure(
        self, attempt: "_PaymentAttempt", exc: BaseException
    ) -> StagedPaymentResult:
        """What a failed STAGED payment hands back through the caller's savepoint (019 stage 3, `T1905`).

        The caller - the simulator's executor, `real_payments_executor.py` - runs each payment inside a
        savepoint of its own and catches exceptions OUTSIDE it, so anything written before raising is
        rolled back with that savepoint. Hence, by cause:

        * refused before admission - raised, no row (the executor counts a rejection);
        * a transaction-level conflict - raised as `RetryablePaymentConflictException` for the owner's
          whole-phase replay (a savepoint rollback does not refresh a SERIALIZABLE snapshot);
        * an identity collision on `transactions_tx_id_key` - the identity resolver (`_resolve_identity`):
          the winner's stored result, a `409`, or - no winner to read - a retryable conflict;
        * a definitive refusal after admission, transaction usable - the `ABORTED` row is written into
          the caller's transaction and RETURNED as a structured result (`StagedPaymentResult.refusal`),
          so the caller's savepoint keeps it and the tick's commit makes it durable;
        * a definitive refusal that finds the transaction unusable - `PaymentTransactionUnusable`, for
          the owner of the transaction to roll back and record (`T1912`);
        * a cancellation - still propagated; the refusal is written first when the transaction is usable,
          as before stage 3 (whether it survives is the caller's affair).
        """

        if isinstance(exc, asyncio.CancelledError):
            refusal = _definitive_refusal(attempt, exc)
            owner_sink = _ADMITTED_REFUSALS.get()
            if refusal is not None and owner_sink is not None:
                # The owner records it after rolling the phase back (stage-3 review, P2 #3).
                owner_sink.append(refusal)
            elif refusal is not None and not attempt.operation_left_unrolled:
                try:
                    await self._record_refusal_in_transaction(refusal)
                except asyncio.CancelledError:
                    raise
                except BaseException:  # noqa: BLE001 - logged by the writer; the signal wins
                    pass
            raise exc

        if attempt.identity_collision:
            return await self._resolve_identity(attempt, exc)

        refusal = _definitive_refusal(attempt, exc)
        if refusal is None:
            if isinstance(exc, asyncio.TimeoutError):
                raise TimeoutException("Payment timed out") from exc
            if not isinstance(exc, GeoException):
                classified = _classify_payment_db_error(exc)
                if isinstance(classified, RetryablePaymentConflictException):
                    raise classified from exc
            raise exc
        if attempt.operation_left_unrolled:
            raise PaymentTransactionUnusable(refusal, exc) from exc
        try:
            row = await self._record_refusal_in_transaction(refusal)
        except asyncio.CancelledError:
            raise
        except BaseException as failure:  # noqa: BLE001 - classified below
            classified = (
                failure if isinstance(failure, GeoException) else _classify_payment_db_error(failure)
            )
            if isinstance(classified, RetryablePaymentConflictException):
                # SERIALIZABLE refused the write (a concurrent row of this `tx_id` behind the snapshot):
                # a conflict of the whole transaction, for its owner to replay.
                raise classified from failure
            raise PaymentTransactionUnusable(refusal, failure) from exc
        if row is None:
            # A visible row of this `tx_id` already exists: whoever wrote it, the resolver answers.
            attempt.identity_collision = True
            return await self._resolve_identity(attempt, exc)
        return StagedPaymentResult(
            result=PaymentService._tx_to_payment_result(row),
            post_commit_effects=None,
            refusal=refusal.public_error,
            written_here=True,
        )

    async def _record_refusal_in_transaction(self, refusal: DefinitiveRefusal) -> Transaction | None:
        """Write `refusal` as an `ABORTED` row into the CALLER's transaction, in a savepoint of its own.

        The payment operation's savepoint is already rolled back, so this is a new row. Returns it, or
        None when a visible row of the `tx_id` already exists (nothing is overwritten). A failure is
        logged under the event names of the `engine.abort(commit=False)` this replaces
        (`event=payment.<phase>_failed`) and raised: the caller decides what an unrecorded refusal is.
        """

        async def write() -> Transaction | None:
            nested = await self.session.begin_nested()
            try:
                row_id = (
                    await self.session.execute(
                        pg_insert(Transaction)
                        .values(**refusal.row, state="ABORTED", error=refusal.error, signatures=[])
                        .on_conflict_do_nothing(index_elements=[Transaction.tx_id])
                        .returning(Transaction.id)
                    )
                ).scalar_one_or_none()
            except BaseException:
                if nested.is_active:
                    await nested.rollback()
                raise
            await nested.commit()
            if row_id is None:
                return None
            return (
                await self.session.execute(
                    select(Transaction)
                    .where(Transaction.id == row_id)
                    .execution_options(populate_existing=True)
                )
            ).scalar_one()

        row, failure = await _drain_call(write)
        if failure is not None:
            logger.error(
                "event=payment.%s_failed tx_id=%s error_type=%s",
                refusal.event_prefix,
                refusal.tx_id,
                type(failure).__name__,
            )
            raise failure
        return row

    async def _resolve_identity(
        self, attempt: "_PaymentAttempt", exc: BaseException
    ) -> StagedPaymentResult:
        """The exact identity resolver of the staged path (spec, "Идентичность `tx_id`").

        The caller's own snapshot cannot see a winner that committed after it began, so the winner is
        read on a FRESH transaction of another connection and compared - type, initiator, fingerprint,
        and the run perimeter - by `_resolve_existing_payment`: its stored result, or a `409`. No row
        there (the winner is gone) is answered as a retryable conflict: the owner's next attempt is the
        "one new attempt" the spec allows, on a fresh snapshot.
        """

        def resolve(existing_tx: Transaction | None) -> PaymentResult:
            if existing_tx is None:
                raise RetryablePaymentConflictException() from exc
            return PaymentService._resolve_existing_payment(
                existing_tx,
                sender_id=attempt.sender_id,
                request_fingerprint=str(attempt.fingerprint),
                allowed_participant_pids=attempt.allowed_participant_pids,
            )

        statement = select(Transaction).where(Transaction.tx_id == attempt.tx_id)
        bind = self.session.bind
        if isinstance(bind, AsyncEngine):
            fresh = async_sessionmaker(bind=bind, expire_on_commit=False, autoflush=False)
            async with fresh() as reader:
                try:
                    result = resolve((await reader.execute(statement)).scalar_one_or_none())
                finally:
                    await reader.rollback()
        else:
            # A session bound to one connection (a test's outer transaction) has no other connection
            # to read from; its own snapshot is all there is.
            result = resolve((await self.session.execute(statement)).scalar_one_or_none())
        return StagedPaymentResult(
            result=result, post_commit_effects=None, refusal=_public_error_of_stored(result)
        )

    # ── pay(): the API wrapper - one transaction per attempt, the owner of its retries ──────────

    @classmethod
    async def pay(
        cls,
        sessions: "Callable[[], Any]",
        sender_id: uuid.UUID,
        request: PaymentCreateRequest,
        *,
        idempotency_key: str | None = None,
        require_signature: bool = True,
        allowed_participant_pids: "AbstractSet[str] | None" = None,
        _service_for: "Callable[[AsyncSession], PaymentService] | None" = None,
    ) -> PaymentResult:
        """Execute a payment as ONE transaction and commit it (019 stage 3).

        Every attempt opens a session from `sessions` (a sessionmaker, or anything whose call returns
        an async context manager yielding a session), runs `execute()` in that session's transaction
        and commits it once. `pay()` owns the retries of the API path: a retryable conflict (40001,
        40P01, the book's `DebtVersionConflict`, a changed owner set) discards the whole attempt and
        the next one starts on a fresh session and snapshot, within `COMMIT_RETRY_ATTEMPTS` attempts
        and the `PAYMENT_TOTAL_TIMEOUT_SECONDS` deadline. A savepoint rollback is never a retry: it
        does not refresh a SERIALIZABLE snapshot.

        OUTCOMES BY CAUSE (spec, "Окончательный отказ…", the seven-row table; `T1905`):

        * refused BEFORE admission (routing, stop/hold pre-check) - no row; a later submission may run;
        * refused AFTER admission - by this attempt, or by an earlier one of the same request
          (`_Admission`, in-process memory of this call) - a capacity, stop, hold, non-retryable
          internal or terminal-timeout refusal: recorded `ABORTED` with its error, after the attempt's
          rollback is confirmed, in a short transaction of its own; the same identity replays it;
        * a transaction-level conflict, including an exhausted retry budget on prepare or commit -
          nothing recorded, `409/E008` with `retryable: true`;
        * an identity collision on `transactions_tx_id_key` - the winner read on a new transaction:
          its stored result or a `409`; no winner - one more attempt within the budget;
        * a COMMIT whose outcome is unknown - neither recorded nor retried: the identity is read, a
          stored row answers, and no row answers with the error (the client's resubmission of the same
          `tx_id` resolves it);
        * a cancellation - propagated; an admitted request whose rollback is confirmed is recorded
          `ABORTED/E007` first; after a confirmed commit it is `COMMITTED` and nothing is recorded.

        Post-commit effects - the SSE event, the route-cache invalidation and the `create`/`commit`
        success metrics - are applied once, after the commit is confirmed, never per attempt and
        never on a stored result's replay.
        """

        _count_create_start()
        make_service = _service_for or cls
        loop = asyncio.get_running_loop()
        total_timeout_s = float(getattr(settings, "PAYMENT_TOTAL_TIMEOUT_SECONDS", 10) or 10)
        deadline = loop.time() + total_timeout_s
        attempts = max(1, int(getattr(settings, "COMMIT_RETRY_ATTEMPTS", 1) or 1))
        attempt_no = 0
        admission: _Admission | None = None
        while True:
            attempt_no += 1
            async with sessions() as session:
                service = make_service(session)
                outcome = await service._pay_attempt(
                    sessions,
                    sender_id,
                    request,
                    idempotency_key=idempotency_key,
                    require_signature=require_signature,
                    allowed_participant_pids=allowed_participant_pids,
                    deadline=deadline,
                    attempt_no=attempt_no,
                    attempts=attempts,
                    admission=admission,
                )
            if isinstance(outcome, _RetryAttempt):
                admission = outcome.admission or admission
                await asyncio.sleep(outcome.delay_seconds)
                continue
            return outcome

    async def _pay_attempt(
        self,
        sessions,
        sender_id: uuid.UUID,
        request: PaymentCreateRequest,
        *,
        idempotency_key: str | None,
        require_signature: bool,
        allowed_participant_pids: "AbstractSet[str] | None",
        deadline: float,
        attempt_no: int,
        attempts: int,
        admission: "_Admission | None" = None,
    ) -> "PaymentResult | _RetryAttempt":
        try:
            staged = await self.execute(
                sender_id,
                request,
                idempotency_key=idempotency_key,
                require_signature=require_signature,
                allowed_participant_pids=allowed_participant_pids,
                deadline=deadline,
                # The first attempt keeps the API's shared route cache; a retry follows a conflict,
                # so it reads the graph afresh.
                use_shared_routing_cache=attempt_no == 1,
                record_refusal=False,
                emit_start=False,
            )
        except BaseException as exc:
            return await self._settle_failed_attempt(
                sessions,
                exc,
                where="execute",
                deadline=deadline,
                attempt_no=attempt_no,
                attempts=attempts,
                admission=admission,
            )

        # THE ONE COMMIT of the payment.
        try:
            async with asyncio.timeout_at(deadline):
                await self.session.commit()
        except BaseException as exc:
            return await self._settle_failed_commit(
                sessions,
                exc,
                deadline=deadline,
                attempt_no=attempt_no,
                attempts=attempts,
                effects=staged.post_commit_effects,
            )

        if staged.post_commit_effects is not None:
            staged.post_commit_effects.apply_once()
        return staged.result

    def _retry_or_none(
        self,
        exc: BaseException,
        *,
        where: str,
        deadline: float,
        attempt_no: int,
        attempts: int,
        admission: "_Admission | None" = None,
    ) -> "_RetryAttempt | None":
        """Another attempt, if the budget has one; the attempt's rollback is the caller's to do first."""

        if attempt_no >= attempts:
            return None
        base_seconds = max(0.0, settings.COMMIT_RETRY_BASE_DELAY_MS / 1000.0)
        cap_seconds = max(base_seconds, settings.COMMIT_RETRY_MAX_DELAY_MS / 1000.0)
        delay = min(cap_seconds, base_seconds * (2 ** (attempt_no - 1)))
        delay *= 1.0 + 0.25 * random.random()
        if asyncio.get_running_loop().time() + delay >= deadline:
            return None
        logger.warning(
            "event=payment.attempt_retry attempt=%s/%s delay_s=%.3f pgcode=%s where=%s",
            attempt_no,
            attempts,
            delay,
            _conflict_cause(exc),
            where,
        )
        return _RetryAttempt(delay, admission)

    async def _rollback_attempt(self) -> None:
        """Roll the attempt's transaction back, to completion even under cancellation."""

        failure = await _drain_payment_cleanup(self.session.rollback)
        if failure is not None and not isinstance(failure, asyncio.CancelledError):
            logger.error(
                "event=payment.attempt_rollback_failed tx_id=%s error_type=%s",
                str(getattr(self._attempt, "tx_id", None)),
                type(failure).__name__,
            )
            raise GeoException() from failure

    async def _end_failed_attempt(self) -> None:
        """End the transaction of an attempt whose `execute()` failed; the next one starts fresh.

        Nothing of the payment is in it: `execute()` rolled its operation savepoint back (or invalidated
        the connection when it could not), and before the operation it only reads. So the transaction is
        COMMITTED when it can be - that keeps a borrowed session's objects loaded (`expire_on_commit` is
        off; a rollback would expire them) and keeps whatever its caller had staged - and rolled back
        when it cannot (an aborted or invalidated transaction). Either way the next statement opens a
        new snapshot.

        RETURNING NORMALLY IS THE CONFIRMATION that the attempt's effects cannot land: its operation was
        rolled back (or its connection invalidated) and its transaction has ended without them. When
        even the rollback fails this raises, and nothing is recorded for the attempt (spec: a refusal is
        recorded only after a confirmed rollback).
        """

        if getattr(getattr(self, "_attempt", None), "operation_left_unrolled", False):
            # The operation's savepoint could not be rolled back: never commit this transaction.
            await self._rollback_attempt()
            return
        failure = await _drain_payment_cleanup(self.session.commit)
        if failure is None or isinstance(failure, asyncio.CancelledError):
            return
        await self._rollback_attempt()

    async def _settle_failed_attempt(
        self,
        sessions,
        exc: BaseException,
        *,
        where: str,
        deadline: float,
        attempt_no: int,
        attempts: int,
        admission: "_Admission | None" = None,
    ) -> "PaymentResult | _RetryAttempt":
        attempt: _PaymentAttempt = getattr(self, "_attempt", None) or _PaymentAttempt()
        await self._end_failed_attempt()
        admission = _Admission.of(attempt) or admission

        if isinstance(exc, asyncio.CancelledError):
            refusal = _definitive_refusal(attempt, exc, admission)
            if refusal is not None:
                await self._record_refusal(sessions, refusal, cancelled=exc, deadline=deadline)
            raise exc

        if attempt.identity_collision:
            # THE EXACT IDENTITY RESOLVER (spec, "Идентичность `tx_id`"): the winner is read on a new
            # transaction and compared - type, initiator, fingerprint; its stored result or a 409.
            existing = await self._read_existing(sessions, str(attempt.tx_id))
            if existing is not None:
                return existing
            # No winner to read (it rolled back): one more attempt within the budget.
            retry = self._retry_or_none(
                exc, where="identity", deadline=deadline, attempt_no=attempt_no,
                attempts=attempts, admission=admission,
            )
            if retry is not None:
                return retry
            raise RetryablePaymentConflictException() from exc

        public_error = exc if isinstance(exc, GeoException) else _classify_payment_db_error(exc)
        if isinstance(public_error, RetryablePaymentConflictException):
            retry = self._retry_or_none(
                exc, where=where, deadline=deadline, attempt_no=attempt_no,
                attempts=attempts, admission=admission,
            )
            if retry is not None:
                return retry
            # EXHAUSTED: still a conflict, never a definitive refusal - nothing is recorded, and the
            # client's resubmission of the same tx_id executes (`FORK-4`).
            if exc is public_error:
                raise exc
            raise public_error from exc

        refusal = _definitive_refusal(attempt, exc, admission)
        if refusal is not None:
            stored = await self._record_refusal(sessions, refusal, deadline=deadline)
            if stored is not None:
                return stored
        raise exc

    async def _settle_failed_commit(
        self,
        sessions,
        exc: BaseException,
        *,
        deadline: float,
        attempt_no: int,
        attempts: int,
        effects: "PaymentPostCommitEffects | None" = None,
    ) -> "PaymentResult | _RetryAttempt":
        attempt: _PaymentAttempt = self._attempt
        sqlstate = _payment_db_sqlstate(exc) if isinstance(exc, DBAPIError) else None
        await self._rollback_attempt()

        if sqlstate is not None and sqlstate[:2] in _COMMIT_REFUSED_SQLSTATE_CLASSES:
            # The server ANSWERED the COMMIT with an error: it rolled the transaction back, nothing
            # landed - the rollback is confirmed.
            if sqlstate in _RETRYABLE_PAYMENT_SQLSTATES:
                retry = self._retry_or_none(
                    exc, where="commit", deadline=deadline, attempt_no=attempt_no,
                    attempts=attempts, admission=_Admission.of(attempt),
                )
                if retry is not None:
                    return retry
                # Exhausted on the commit: a conflict, never recorded (`FORK-4`).
                raise RetryablePaymentConflictException() from exc
            refusal = _definitive_refusal(attempt, exc)
            if refusal is not None:
                stored = await self._record_refusal(sessions, refusal, deadline=deadline)
                if stored is not None:
                    return stored
            raise _classify_payment_db_error(exc) from exc

        # Any other failure of the COMMIT - a timeout, a lost connection, a cancellation - leaves its
        # outcome UNKNOWN: the commit may have landed, and a successful rollback afterwards does not
        # refute it. The identity is read; a stored row answers. No row does NOT prove the rollback (a
        # commit in flight may still land), so nothing is terminalized and nothing is retried: the
        # caller gets the error, and a resubmission of the same tx_id reads whichever outcome it was.
        read, read_error = await _drain_call(
            lambda: self._read_existing_row(sessions, str(attempt.tx_id))
        )
        existing, row_id = read if read is not None else (None, None)
        if read_error is not None:
            logger.error(
                "event=payment.%s tx_id=%s error_type=%s",
                "timeout_recovery_read_failed"
                if isinstance(exc, asyncio.TimeoutError)
                else "commit_recovery_read_failed",
                str(attempt.tx_id),
                type(read_error).__name__,
            )
            if isinstance(exc, asyncio.CancelledError):
                raise exc
            if isinstance(read_error, GeoException) and 400 <= read_error.status_code < 500:
                raise read_error
            raise GeoException() from read_error
        if (
            existing is not None
            and existing.status == "COMMITTED"
            and effects is not None
            and attempt.row is not None
            and row_id == attempt.row.get("id")
        ):
            # THIS attempt's commit landed (the row is its own, by row id): its post-commit effects
            # belong to it, and nothing else will ever apply them - applied once here (019 stage-3
            # review, P2 #4). A stored row of another, older commit is answered without effects.
            effects.apply_once()
        if isinstance(exc, asyncio.CancelledError):
            raise exc
        if existing is not None:
            return existing
        logger.warning(
            "event=payment.commit_outcome_unresolved tx_id=%s error_type=%s",
            str(attempt.tx_id),
            type(exc).__name__,
        )
        if isinstance(exc, asyncio.TimeoutError):
            raise TimeoutException("Payment timed out") from exc
        raise GeoException() from exc

    async def _read_existing(self, sessions, tx_id: str) -> PaymentResult | None:
        """The stored row of `tx_id` on a NEW transaction, through the idempotency policy."""

        result, _row_id = await self._read_existing_row(sessions, tx_id)
        return result

    async def _read_existing_row(
        self, sessions, tx_id: str
    ) -> "tuple[PaymentResult | None, uuid.UUID | None]":
        """`_read_existing`, and the row's id - which tells THIS attempt's row from an older one."""

        attempt: _PaymentAttempt = self._attempt
        async with sessions() as session:
            try:
                existing_tx = (
                    await session.execute(
                        select(Transaction)
                        .where(Transaction.tx_id == tx_id)
                        .execution_options(populate_existing=True)
                    )
                ).scalar_one_or_none()
                if existing_tx is None:
                    return None, None
                row_id = existing_tx.id
                return (
                    self._resolve_existing_payment(
                        existing_tx,
                        sender_id=attempt.sender_id,
                        request_fingerprint=str(attempt.fingerprint),
                        allowed_participant_pids=attempt.allowed_participant_pids,
                    ),
                    row_id,
                )
            finally:
                await session.rollback()

    async def _record_refusal(
        self,
        sessions,
        refusal: DefinitiveRefusal,
        *,
        cancelled: asyncio.CancelledError | None = None,
        deadline: float | None = None,
    ) -> PaymentResult | None:
        """`record_definitive_refusal`, run to its end even under the caller's cancellation.

        A recording that fails is logged (`event=payment.<phase>_abort_failed`, the names the code
        before stage 3 used for its `engine.abort`) and answered with a safe 500 - the original error
        is not answered after a refusal that could not be recorded - except under a cancellation,
        which stays the caller's signal (`cancelled`). A cancellation that arrives while recording is
        re-raised once the recording has finished.
        """

        stored, failure = await _drain_call(
            lambda: record_definitive_refusal(sessions, refusal, deadline=deadline)
        )
        if failure is None:
            return stored
        if isinstance(failure, asyncio.CancelledError):
            raise failure
        if isinstance(failure, RefusalNotRecorded):
            logger.warning(
                "event=payment.refusal_not_recorded tx_id=%s reason=lock_timeout", refusal.tx_id
            )
            if cancelled is not None:
                raise cancelled
            return None
        logger.error(
            "event=payment.%s_failed tx_id=%s error_type=%s",
            refusal.event_prefix.replace("_nested", ""),
            refusal.tx_id,
            type(failure).__name__,
        )
        if cancelled is not None:
            raise cancelled
        if isinstance(failure, GeoException) and 400 <= failure.status_code < 500:
            # The identity check on the row that won: another request owns this tx_id.
            raise failure
        raise GeoException() from failure

    async def get_payment(self, tx_id: str) -> PaymentResult:
        tx = (
            await self.session.execute(
                select(Transaction).where(Transaction.tx_id == tx_id)
            )
        ).scalar_one_or_none()
        if not tx or tx.type != "PAYMENT":
            raise NotFoundException(f"Payment {tx_id} not found")

        return self._tx_to_payment_result(tx)

    async def get_payment_for_participant(
        self,
        tx_id: str,
        *,
        requester_participant_id: uuid.UUID,
        requester_pid: str,
    ) -> PaymentResult:
        tx = (
            await self.session.execute(
                select(Transaction).where(Transaction.tx_id == tx_id)
            )
        ).scalar_one_or_none()
        if not tx or tx.type != "PAYMENT":
            raise NotFoundException(f"Payment {tx_id} not found")

        payload = tx.payload or {}
        # Access rule (MVP): allow initiator or receiver; otherwise return 404 to avoid leaking existence.
        if (
            tx.initiator_id != requester_participant_id
            and str(payload.get("to", "")) != requester_pid
        ):
            raise NotFoundException(f"Payment {tx_id} not found")

        return self._tx_to_payment_result(tx)

    @staticmethod
    def _tx_to_payment_result(tx: Transaction) -> PaymentResult:
        payload = tx.payload or {}
        routes_payload = payload.get("routes")
        routes = None
        if routes_payload is not None:
            routes = [PaymentRoute.model_validate(r) for r in routes_payload] or None

        committed_at = tx.updated_at if tx.state == "COMMITTED" else None
        error = None
        if tx.error:
            error = PaymentError(
                code=str(tx.error.get("code") or ErrorCode.E010.value),
                message=str(tx.error.get("message", "")),
                details=tx.error.get("details"),
            )

        status = tx.state if tx.state in {"COMMITTED", "ABORTED"} else "ABORTED"
        return PaymentResult(
            tx_id=tx.tx_id,
            status=status,
            **{"from": str(payload.get("from", ""))},
            to=str(payload.get("to", "")),
            equivalent=str(payload.get("equivalent", "")),
            amount=str(payload.get("amount", "")),
            routes=routes,
            error=error,
            created_at=tx.created_at,
            committed_at=committed_at,
        )

    async def list_payments(
        self,
        *,
        requester_participant_id: uuid.UUID,
        requester_pid: str,
        direction: Literal["sent", "received", "all"] = "all",
        equivalent: str | None = None,
        status: Literal["COMMITTED", "ABORTED", "all"] = "all",
        from_date: datetime | None = None,
        to_date: datetime | None = None,
        page: int = 1,
        per_page: int = 20,
    ) -> List[PaymentResult]:
        def _normalize_dt(value: datetime | None) -> datetime | None:
            if value is None:
                return None
            # For client/server DBs (e.g. Postgres), prefer aware UTC.
            if value.tzinfo is None:
                return value.replace(tzinfo=timezone.utc)
            return value.astimezone(timezone.utc)

        from_date = _normalize_dt(from_date)
        to_date = _normalize_dt(to_date)

        offset = (page - 1) * per_page

        clauses = [Transaction.type == "PAYMENT"]
        if status != "all":
            clauses.append(Transaction.state == status)
        if from_date is not None:
            clauses.append(Transaction.created_at >= from_date)
        if to_date is not None:
            clauses.append(Transaction.created_at <= to_date)

        # Direction filtering.
        payload = Transaction.payload
        to_expr = payload["to"].as_string()
        from_expr = payload["from"].as_string()
        eq_expr = payload["equivalent"].as_string()

        if direction == "sent":
            clauses.append(
                or_(
                    Transaction.initiator_id == requester_participant_id,
                    from_expr == requester_pid,
                )
            )
        elif direction == "received":
            clauses.append(to_expr == requester_pid)
        else:
            clauses.append(
                or_(
                    Transaction.initiator_id == requester_participant_id,
                    to_expr == requester_pid,
                    from_expr == requester_pid,
                )
            )

        if equivalent:
            clauses.append(eq_expr == equivalent)

        stmt = (
            select(Transaction)
            .where(and_(*clauses))
            .order_by(Transaction.created_at.desc())
            .limit(per_page)
            .offset(offset)
        )

        txs = (await self.session.execute(stmt)).scalars().all()
        return [self._tx_to_payment_result(tx) for tx in txs]


async def record_definitive_refusal(
    sessions, refusal: DefinitiveRefusal, *, deadline: float | None = None
) -> PaymentResult | None:
    """Record `refusal` as an `ABORTED` row in a SHORT TRANSACTION OF ITS OWN (019 stage 3, `T1905`).

    Called only after the refused attempt's own transaction is confirmed rolled back: by `pay()` for
    the API, and by the simulator's money-phase owner for a staged payment that left the tick's
    transaction unusable (`T1912`). `sessions` is a session factory whose call returns an async context
    manager yielding a session.

    RESOLVES A CONCURRENT WINNER, NEVER OVERWRITES. The insert yields to a row of the same `tx_id` that
    already exists (`ON CONFLICT DO NOTHING`); that row is then read and its identity checked - type,
    initiator, fingerprint (`_resolve_existing_payment`, a `409` for another request). A `COMMITTED`
    winner of the same request is returned - it always wins, as it did in `engine.abort`. Returns None
    when the refusal was recorded, or when an `ABORTED` row of the same request already stood.
    """

    values = dict(refusal.row)
    values.update(state="ABORTED", error=refusal.error, signatures=[])
    tries = max(1, int(getattr(settings, "COMMIT_RETRY_ATTEMPTS", 1) or 1))
    last_error: BaseException | None = None
    for _ in range(tries):
        # BOUNDED (stage-3 review, P2 #5): the rest of the deadline, at least the grace.
        if deadline is not None:
            remaining_ms = int((deadline - asyncio.get_running_loop().time()) * 1000)
        else:
            remaining_ms = int(float(getattr(settings, "COMMIT_TIMEOUT_SECONDS", 5) or 5) * 1000)
        lock_timeout_ms = max(_REFUSAL_RECORD_LOCK_GRACE_MS, remaining_ms)
        async with sessions() as session:
            try:
                await session.execute(text(f"SET LOCAL lock_timeout = '{int(lock_timeout_ms)}ms'"))
                inserted = (
                    await session.execute(
                        pg_insert(Transaction)
                        .values(**values)
                        .on_conflict_do_nothing(index_elements=[Transaction.tx_id])
                        .returning(Transaction.id)
                    )
                ).scalar_one_or_none()
                existing = None
                if inserted is None:
                    existing = (
                        await session.execute(
                            select(Transaction)
                            .where(Transaction.tx_id == refusal.tx_id)
                            .execution_options(populate_existing=True)
                        )
                    ).scalar_one_or_none()
                await session.commit()
            except DBAPIError as exc:
                last_error = exc
                await session.rollback()
                if _payment_db_sqlstate(exc) == "55P03":
                    raise RefusalNotRecorded(refusal.tx_id) from exc
                if _payment_db_sqlstate(exc) in _RETRYABLE_PAYMENT_SQLSTATES:
                    continue
                break
            except BaseException:
                await session.rollback()
                raise
        if inserted is not None:
            try:
                from app.utils.metrics import PAYMENT_EVENTS_TOTAL

                PAYMENT_EVENTS_TOTAL.labels(event="abort", result="success").inc()
            except Exception:
                pass
            return None
        if existing is None:
            # Yielded to a row that is gone again: nothing to resolve against, try the insert again.
            continue
        resolved = PaymentService._resolve_existing_payment(
            existing,
            sender_id=refusal.sender_id,
            request_fingerprint=refusal.fingerprint,
            allowed_participant_pids=refusal.allowed_participant_pids,
        )
        return resolved if resolved.status == "COMMITTED" else None
    raise last_error if last_error is not None else GeoException()
