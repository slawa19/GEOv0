import uuid
import hashlib
import logging
import asyncio
import random
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import AbstractSet, Any, Awaitable, Callable, List, Literal

from sqlalchemy import select, and_, or_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.core.ledger.book import DebtVersionConflict
from app.core.money_boundary import MoneyBoundary
from app.core.payments.engine import PaymentEngine
from app.core.payments.router import PaymentRouter
from app.config import settings
from app.db.models.transaction import Transaction
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
    """The stored `error` of a refused payment, normalized as `PaymentEngine.abort` normalizes it."""

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


@dataclass
class _PaymentAttempt:
    """What one `execute()` established, for the owner of its transaction to act on (019 stage 3).

    `inserted` - the payment's row was added to the session (before stage 3 it was then durable, and
    every later failure terminalized it). `refusal` - (reason, code, details, log event prefix) the
    payment is terminalized with when the attempt fails after `inserted`; the prefix keeps the log
    event names of the code this replaces. `insert_conflict` - the insert itself met an existing
    `tx_id`.
    """

    tx_id: str | None = None
    fingerprint: str | None = None
    sender_id: uuid.UUID | None = None
    allowed_participant_pids: "AbstractSet[str] | None" = None
    row: dict[str, Any] | None = None
    inserted: bool = False
    insert_conflict: bool = False
    refusal: tuple[str, str, dict[str, Any], str] | None = None
    operation_left_unrolled: bool = False


@dataclass(frozen=True)
class _RetryAttempt:
    """`pay()`: discard this attempt and start another after `delay_seconds`."""

    delay_seconds: float


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
                PAYMENT_EVENTS_TOTAL.labels(event="prepare", result="success").inc()
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
    result: PaymentResult
    post_commit_effects: PaymentPostCommitEffects | None


class PaymentService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.engine = PaymentEngine(session)
        # ONE money boundary for the service's lifetime, and it IS the engine: the advisory-lock
        # deadline starts at the first lock this service takes and is shared by every later staged
        # acquisition and by the engine's own units of work (019 stage 2 review, P2).
        self._boundary: MoneyBoundary = self.engine
        self.router = PaymentRouter(session)

    def _resolve_existing_payment(
        self,
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
        return self._tx_to_payment_result(existing_tx)

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
            if self.engine._get_pgcode(exc) == "55P03":
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

        Programme 019 stage 3 (`specs/019-payment-one-transaction/spec.md`, "Контракт исполнения").
        Steps: validation and idempotency (a stored row answers with its stored result or a 409),
        the best-effort stop/hold pre-check, routing, and then THE PAYMENT OPERATION - one savepoint
        opened before the `Transaction` is added, around the insert, the engine's transient
        `NEW -> PREPARED -> COMMITTED` (`prepare(commit=False)`, `commit(commit=False)`: the owner,
        tx and pair locks, the reservations, the book with its own savepoint, `check_payment_delta`,
        the trust-limit and symmetry checks, the integrity audit row). Nothing of it is visible to
        another transaction before the caller commits, and a failure inside it rolls ALL of it back.

        Nesting: caller's transaction -> payment operation savepoint -> the engine's savepoints ->
        the book's savepoint. A transaction-level conflict (40001, 40P01, the book's
        `DebtVersionConflict`) is PROPAGATED as `RetryablePaymentConflictException`: a savepoint
        rollback does not refresh a SERIALIZABLE snapshot, so only the owner of the whole transaction
        can retry it - `pay()` for the API, the money-phase replay for the simulator.

        `record_refusal` (the staged default) writes a refused payment as `ABORTED` into the caller's
        transaction after the operation is rolled back, as the staged path did before stage 3; `pay()`
        turns it off and records the refusal durably itself after rolling the attempt back.
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

        # Payment engine retries may expire the session identity map after an
        # optimistic-lock conflict. Keep the validated wire identifiers as plain
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
            return StagedPaymentResult(
                result=self._resolve_existing_payment(
                    existing_tx,
                    sender_id=sender_id,
                    request_fingerprint=request_fingerprint,
                    allowed_participant_pids=allowed_participant_pids,
                ),
                post_commit_effects=None,
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

                # 3-5. THE PAYMENT OPERATION (019 stage 3). The `Transaction` insert, the engine's
                # transient NEW -> PREPARED -> COMMITTED, the book and `check_payment_delta` run
                # inside ONE savepoint of the caller's transaction, opened BEFORE the row is added to
                # the session (see `_open_operation_savepoint`). A failure anywhere in it rolls the
                # whole operation back - no NEW, PREPARED or COMMITTED row, reservation or debt of
                # this attempt survives in the caller's transaction - and only then is the refusal
                # recorded, if this call records refusals (`record_refusal`).
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
        except asyncio.CancelledError:
            if attempt.inserted:
                attempt.refusal = ("Payment cancelled", ErrorCode.E007.value, {}, "cancellation_cleanup")
            if attempt.inserted and record_refusal:
                await self._record_refusal_in_transaction(attempt)
            raise
        except asyncio.TimeoutError:
            if attempt.inserted:
                attempt.refusal = ("Payment timeout", ErrorCode.E007.value, {}, "timeout_abort")
                if record_refusal:
                    await self._record_refusal_in_transaction(attempt)
            raise TimeoutException("Payment timed out")
        except IntegrityError:
            if not attempt.insert_conflict:
                raise
            # The `Transaction` insert collided with an existing `tx_id` and the operation savepoint
            # is rolled back, so this transaction is usable again. The row that won is looked up in
            # THIS transaction's snapshot, as before stage 3; under SERIALIZABLE a concurrently
            # committed winner is not visible here, and the error is raised for the owner of the
            # transaction to resolve on a fresh one (`pay()` does; the exact identity resolver is
            # `T1905`).
            existing_tx = (
                await self.session.execute(
                    select(Transaction).where(Transaction.tx_id == tx_id_str)
                )
            ).scalar_one_or_none()
            if existing_tx is not None:
                return StagedPaymentResult(
                    result=self._resolve_existing_payment(
                        existing_tx,
                        sender_id=sender_id,
                        request_fingerprint=request_fingerprint,
                        allowed_participant_pids=allowed_participant_pids,
                    ),
                    post_commit_effects=None,
                )
            raise
        except BaseException:
            if attempt.refusal is not None and record_refusal:
                await self._record_refusal_in_transaction(attempt)
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
        return StagedPaymentResult(result=result, post_commit_effects=effects)

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
        """Insert the `Transaction` and run the engine's NEW -> PREPARED -> COMMITTED, all inside
        the operation savepoint the caller opened. A refusal sets `attempt.refusal` - what the
        payment would be terminalized with - and raises; it never writes here, because whatever it
        wrote would be rolled back with the operation."""

        tx_id_str = str(attempt.tx_id)
        new_tx = Transaction(**attempt.row, state="NEW")
        attempt.inserted = True
        self.session.add(new_tx)
        try:
            await self.session.flush()
        except IntegrityError:
            # tx_id is globally unique. The operation savepoint is rolled back by the caller; the
            # existing row is looked up after that, on a usable transaction (`execute`).
            attempt.inserted = False
            attempt.insert_conflict = True
            raise
        except DBAPIError as exc:
            attempt.inserted = False
            public_error = _classify_payment_db_error(exc)
            if not isinstance(public_error, RetryablePaymentConflictException):
                raise
            # A serialization failure or a stale snapshot belongs to the whole transaction; its
            # owner retries it on a fresh one (`pay()` for the API, the money-phase replay for the
            # simulator).
            raise public_error from exc

        # 4. Engine Prepare
        try:
            if len(routes_found) == 1:
                await asyncio.wait_for(
                    self.engine.prepare(
                        tx_id_str,
                        routes_found[0][0],
                        amount,
                        equivalent_id,
                        commit=False,
                    ),
                    timeout=prepare_timeout_s,
                )
            else:
                await asyncio.wait_for(
                    self.engine.prepare_routes(
                        tx_id_str,
                        routes_found,
                        equivalent_id,
                        commit=False,
                    ),
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

            is_client_error = isinstance(e, GeoException) and 400 <= int(
                getattr(e, "status_code", 500) or 500
            ) < 500
            public_error = e if is_client_error else _classify_payment_db_error(e)
            attempt.refusal = (
                str(public_error.message),
                str(getattr(public_error, "code", ErrorCode.E010.value)),
                getattr(public_error, "details", None) or {},
                "prepare_nested_abort",
            )
            if is_client_error:
                raise
            raise public_error from e

        # 5. Engine Commit
        try:
            await asyncio.wait_for(
                self.engine.commit(tx_id_str, commit=False),
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

            # A user-level GeoException (4xx) is preserved, so simulator real-mode can classify it
            # as REJECTED instead of INTERNAL_ERROR.
            if isinstance(e, GeoException) and 400 <= int(
                getattr(e, "status_code", 500) or 500
            ) < 500:
                attempt.refusal = (
                    str(e.message),
                    str(e.code),
                    e.details or {},
                    "commit_nested_abort",
                )
                raise
            public_error = _classify_payment_db_error(e)
            attempt.refusal = (
                str(public_error.message),
                str(public_error.code),
                public_error.details or {},
                "commit_nested_abort",
            )
            raise public_error from e

    async def _record_refusal_in_transaction(self, attempt: "_PaymentAttempt") -> None:
        """Terminalize a staged payment as ABORTED in the caller's transaction, as before stage 3.

        The operation savepoint is already rolled back, so the refusal is written as a new
        `ABORTED` row in a savepoint of its own. Best effort, exactly as the staged
        `engine.abort(commit=False)` it replaces: a failure is logged under the same event names and
        the original exception is what the caller sees. Whether the row survives the caller's own
        savepoint is the caller's affair - the executor's rolls it back; making a staged refusal
        durable is `T1905`.
        """

        reason, code, details, event_prefix = attempt.refusal  # type: ignore[misc]

        async def write() -> None:
            nested = await self.session.begin_nested()
            try:
                self.session.add(
                    Transaction(
                        **attempt.row,
                        state="ABORTED",
                        error=_refusal_error_payload(reason, code, details),
                    )
                )
                await self.session.flush()
            except BaseException:
                if nested.is_active:
                    await nested.rollback()
                raise
            await nested.commit()

        failure = await _drain_payment_cleanup(write)
        if failure is not None:
            logger.error(
                "event=payment.%s_failed tx_id=%s error_type=%s",
                event_prefix,
                str(attempt.tx_id),
                type(failure).__name__,
            )
            if isinstance(failure, asyncio.CancelledError):
                raise failure

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

        OUTCOMES ARE THOSE OF THE API BEFORE STAGE 3 (T1904 changes the transaction, not the
        outcome table - that is `T1905`): a refusal after the payment's row would have existed is
        recorded `ABORTED` with its error, in a short transaction of its own after the attempt's
        rollback; an exhausted retryable conflict is recorded `ABORTED`/`E008` the same way; a
        timeout `ABORTED`/`E007`.

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
                )
            if isinstance(outcome, _RetryAttempt):
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
            )

        if staged.post_commit_effects is not None:
            staged.post_commit_effects.apply_once()
        return staged.result

    def _retry_or_none(
        self, exc: BaseException, *, where: str, deadline: float, attempt_no: int, attempts: int
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
        return _RetryAttempt(delay)

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
    ) -> "PaymentResult | _RetryAttempt":
        attempt: _PaymentAttempt = getattr(self, "_attempt", None) or _PaymentAttempt()
        await self._end_failed_attempt()

        if isinstance(exc, asyncio.CancelledError):
            if attempt.inserted and attempt.refusal is not None:
                await self._record_refusal(sessions, attempt, cancelled=exc)
            raise exc

        public_error = exc if isinstance(exc, GeoException) else _classify_payment_db_error(exc)
        if isinstance(public_error, RetryablePaymentConflictException):
            retry = self._retry_or_none(
                exc, where=where, deadline=deadline, attempt_no=attempt_no, attempts=attempts
            )
            if retry is not None:
                return retry

        if attempt.insert_conflict:
            # The same shape as before stage 3: after the rollback, read the row that won on a new
            # transaction and apply the idempotency policy to it.
            existing = await self._read_existing(sessions, str(attempt.tx_id))
            if existing is not None:
                return existing
            raise exc

        if attempt.refusal is not None:
            stored = await self._record_refusal(sessions, attempt)
            if stored is not None:
                return stored
        if not isinstance(exc, GeoException) and isinstance(
            public_error, RetryablePaymentConflictException
        ):
            raise public_error from exc
        raise exc

    async def _settle_failed_commit(
        self,
        sessions,
        exc: BaseException,
        *,
        deadline: float,
        attempt_no: int,
        attempts: int,
    ) -> "PaymentResult | _RetryAttempt":
        attempt: _PaymentAttempt = self._attempt
        sqlstate = _payment_db_sqlstate(exc) if isinstance(exc, DBAPIError) else None
        await self._rollback_attempt()

        if sqlstate in _RETRYABLE_PAYMENT_SQLSTATES:
            # PostgreSQL refused the COMMIT and rolled the transaction back: nothing landed.
            retry = self._retry_or_none(
                exc, where="commit", deadline=deadline, attempt_no=attempt_no, attempts=attempts
            )
            if retry is not None:
                return retry
            public_error = RetryablePaymentConflictException()
            attempt.refusal = (
                str(public_error.message),
                str(public_error.code),
                dict(public_error.details),
                "commit_abort",
            )
            stored = await self._record_refusal(sessions, attempt)
            if stored is not None:
                return stored
            raise public_error from exc

        # Any other failure of the COMMIT - a timeout, a lost connection, a cancellation - leaves its
        # outcome unknown: the commit may have landed. Read before terminalizing, as before stage 3.
        existing, read_error = await _drain_call(
            lambda: self._read_existing(sessions, str(attempt.tx_id))
        )
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
        if isinstance(exc, asyncio.CancelledError):
            if existing is None:
                attempt.refusal = ("Payment cancelled", ErrorCode.E007.value, {}, "cancellation_cleanup")
                await self._record_refusal(sessions, attempt, cancelled=exc)
            raise exc
        if existing is not None:
            return existing
        if isinstance(exc, asyncio.TimeoutError):
            attempt.refusal = ("Payment timeout", ErrorCode.E007.value, {}, "timeout_abort")
            public: GeoException = TimeoutException("Payment timed out")
        else:
            public = _classify_payment_db_error(exc)
            attempt.refusal = (str(public.message), str(public.code), public.details or {}, "commit_abort")
        stored = await self._record_refusal(sessions, attempt)
        if stored is not None:
            return stored
        raise public from exc

    async def _read_existing(self, sessions, tx_id: str) -> PaymentResult | None:
        """The stored row of `tx_id` on a NEW transaction, through the idempotency policy."""

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
                    return None
                return self._resolve_existing_payment(
                    existing_tx,
                    sender_id=attempt.sender_id,
                    request_fingerprint=str(attempt.fingerprint),
                    allowed_participant_pids=attempt.allowed_participant_pids,
                )
            finally:
                await session.rollback()

    async def _record_refusal_durably(
        self, sessions, attempt: "_PaymentAttempt"
    ) -> PaymentResult | None:
        """Record `attempt.refusal` as an `ABORTED` row in a short transaction of its own.

        Called only after the attempt's own transaction was rolled back. The insert yields to a row
        that already exists (a concurrent request of the same `tx_id` may have finished first):
        nothing is overwritten, and when that row is COMMITTED its result is returned instead -
        COMMITTED always wins, as it did in `engine.abort`. Returns None when the refusal was
        recorded (or yielded to an ABORTED row).
        """

        reason, code, details, _event_prefix = attempt.refusal  # type: ignore[misc]
        values = dict(attempt.row or {})
        values.update(
            state="ABORTED",
            error=_refusal_error_payload(reason, code, details),
            signatures=[],
        )
        tries = max(1, int(getattr(settings, "COMMIT_RETRY_ATTEMPTS", 1) or 1))
        last_error: BaseException | None = None
        for _ in range(tries):
            async with sessions() as session:
                try:
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
                                .where(Transaction.tx_id == attempt.tx_id)
                                .execution_options(populate_existing=True)
                            )
                        ).scalar_one_or_none()
                    await session.commit()
                except DBAPIError as exc:
                    last_error = exc
                    await session.rollback()
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
            if existing is not None and existing.state == "COMMITTED":
                return self._resolve_existing_payment(
                    existing,
                    sender_id=attempt.sender_id,
                    request_fingerprint=str(attempt.fingerprint),
                    allowed_participant_pids=attempt.allowed_participant_pids,
                )
            return None
        raise last_error if last_error is not None else GeoException()

    async def _record_refusal(
        self,
        sessions,
        attempt: "_PaymentAttempt",
        *,
        cancelled: asyncio.CancelledError | None = None,
    ) -> PaymentResult | None:
        """`_record_refusal_durably`, run to its end even under the caller's cancellation.

        A recording that fails is logged (`event=payment.<phase>_abort_failed`, the names the code
        before stage 3 used for its `engine.abort`) and answered with a safe 500 - the original error
        is not answered after a refusal that could not be recorded - except under a cancellation,
        which stays the caller's signal (`cancelled`). A cancellation that arrives while recording is
        re-raised once the recording has finished.
        """

        stored, failure = await _drain_call(lambda: self._record_refusal_durably(sessions, attempt))
        if failure is None:
            return stored
        if isinstance(failure, asyncio.CancelledError):
            raise failure
        event_prefix = attempt.refusal[3].replace("_nested", "") if attempt.refusal else "abort"
        logger.error(
            "event=payment.%s_failed tx_id=%s error_type=%s",
            event_prefix,
            str(attempt.tx_id),
            type(failure).__name__,
        )
        if cancelled is not None:
            raise cancelled
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

    def _tx_to_payment_result(self, tx: Transaction) -> PaymentResult:
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
