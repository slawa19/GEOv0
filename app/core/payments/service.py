import uuid
import hashlib
import logging
import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, List, Literal

from sqlalchemy import select, and_, or_, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import DBAPIError, IntegrityError

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
from app.utils.error_codes import ErrorCode
from app.utils.validation import validate_equivalent_code, validate_tx_id, parse_amount_decimal

logger = logging.getLogger(__name__)


_RETRYABLE_PAYMENT_SQLSTATES = frozenset({"40001", "40P01"})


def _iter_exception_chain(exc: BaseException):
    pending = [exc]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        marker = id(current)
        if marker in seen:
            continue
        seen.add(marker)
        yield current

        for related in (
            getattr(current, "orig", None),
            current.__cause__,
            current.__context__,
        ):
            if isinstance(related, BaseException):
                pending.append(related)


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


def _classify_payment_db_error(exc: BaseException) -> GeoException:
    """Map database concurrency failures without exposing driver details."""

    for current in _iter_exception_chain(exc):
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
        self.router = PaymentRouter(session)

    def _resolve_existing_payment(
        self,
        existing_tx: Transaction,
        *,
        sender_id: uuid.UUID,
        request_fingerprint: str,
    ) -> PaymentResult:
        """Apply one idempotency policy to both lookup and insert-race rows."""
        if existing_tx.type != "PAYMENT":
            raise ConflictException("tx_id already used")
        if existing_tx.initiator_id != sender_id:
            raise ConflictException("tx_id already used")

        existing_payload = existing_tx.payload or {}
        existing_fp = (existing_payload.get("idempotency") or {}).get("fingerprint")
        if existing_fp is not None and existing_fp != request_fingerprint:
            raise ConflictException("tx_id already used for a different request")

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
        return await self._create_payment_impl(
            sender_id,
            request,
            idempotency_key=idempotency_key,
            require_signature=True,
            commit=True,
        )

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
        return await self._create_payment_impl(
            sender_id,
            req,
            idempotency_key=idempotency_key,
            require_signature=False,
            commit=commit,
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
    ) -> StagedPaymentResult:
        """Flush an internal payment into the caller transaction without publishing it."""

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
        deferred_effects: list[PaymentPostCommitEffects] = []
        result = await self._create_payment_impl(
            sender_id,
            req,
            idempotency_key=idempotency_key,
            require_signature=False,
            commit=False,
            deferred_effects=deferred_effects,
        )
        return StagedPaymentResult(
            result=result,
            post_commit_effects=(deferred_effects[0] if deferred_effects else None),
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
        if not codes or not self.engine._is_postgres():
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
            await self.engine.acquire_staged_equivalent_owner_locks(
                resolved_ids
            )
        except DBAPIError as exc:
            if self.engine._get_pgcode(exc) == "55P03":
                raise asyncio.TimeoutError(
                    "Payment equivalent owner lock timed out"
                ) from exc
            raise _classify_payment_db_error(exc) from exc

    async def _create_payment_impl(
        self,
        sender_id: uuid.UUID,
        request: PaymentCreateRequest,
        *,
        idempotency_key: str | None = None,
        require_signature: bool,
        commit: bool,
        deferred_effects: list[PaymentPostCommitEffects] | None = None,
    ) -> PaymentResult:
        """
        Create and execute a payment.
        1. Validate sender/receiver/equivalent.
        2. Find path.
        3. Create Transaction(NEW).
        4. Engine.prepare().
        5. Engine.commit().
        """
        try:
            from app.utils.metrics import PAYMENT_EVENTS_TOTAL

            PAYMENT_EVENTS_TOTAL.labels(event="create", result="start").inc()
        except Exception:
            pass

        try:
            amount = parse_amount_decimal(request.amount, require_positive=True)
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
        existing_tx = (
            await self.session.execute(
                select(Transaction).where(Transaction.tx_id == tx_id_str)
            )
        ).scalar_one_or_none()
        if existing_tx is not None:
            return self._resolve_existing_payment(
                existing_tx,
                sender_id=sender_id,
                request_fingerprint=request_fingerprint,
            )

        # 2. Routing
        tx_uuid = uuid.uuid4()
        tx_may_be_persisted = False

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
            async with asyncio.timeout(total_timeout_s):
                # Build routing graph + compute routes under spec-aligned timeout budget.
                try:
                    if deferred_effects is None:
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

                routes_payload = [
                    {"path": path, "amount": str(route_amount)}
                    for path, route_amount in routes_found
                ]

                # 3. Create Transaction
                new_tx = Transaction(
                    id=tx_uuid,
                    tx_id=tx_id_str,
                    # Legacy header is ignored for new requests; tx_id is the single source of truth.
                    idempotency_key=None,
                    type="PAYMENT",
                    initiator_id=sender_id,
                    payload={
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
                    state="NEW",
                )
                tx_may_be_persisted = True
                self.session.add(new_tx)
                try:
                    if commit:
                        await self.session.commit()
                    else:
                        # When running under an outer transaction context manager (e.g. simulator
                        # real-mode tick using session.begin_nested()), avoid calling
                        # session.rollback()/commit() here. Use an inner SAVEPOINT instead so an
                        # IntegrityError doesn't poison/close the outer context.
                        async with self.session.begin_nested():
                            await self.session.flush()
                except IntegrityError:
                    # tx_id is globally unique; handle race by re-loading and applying idempotency rules.
                    if commit:
                        await self.session.rollback()
                    existing_tx = (
                        await self.session.execute(
                            select(Transaction).where(Transaction.tx_id == tx_id_str)
                        )
                    ).scalar_one_or_none()
                    if existing_tx is not None:
                        return self._resolve_existing_payment(
                            existing_tx,
                            sender_id=sender_id,
                            request_fingerprint=request_fingerprint,
                        )
                    raise
                except DBAPIError as exc:
                    public_error = _classify_payment_db_error(exc)
                    if not isinstance(
                        public_error, RetryablePaymentConflictException
                    ):
                        raise
                    if commit:
                        try:
                            await self.session.rollback()
                        except Exception as rollback_error:
                            logger.error(
                                "event=payment.insert_rollback_failed "
                                "tx_id=%s error_type=%s",
                                tx_id_str,
                                type(rollback_error).__name__,
                            )
                            raise GeoException() from rollback_error
                    # In staged mode the caller owns the outer transaction. A
                    # serialization failure invalidates that scope, so propagate
                    # the typed conflict and let the tick rollback/replay it.
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
                                commit=commit,
                            ),
                            timeout=prepare_timeout_s,
                        )
                    else:
                        await asyncio.wait_for(
                            self.engine.prepare_routes(
                                tx_id_str,
                                routes_found,
                                equivalent_id,
                                commit=commit,
                            ),
                            timeout=prepare_timeout_s,
                        )

                    # Routing graph may incorporate debts/locks; invalidate any TTL cache.
                    if deferred_effects is None:
                        PaymentRouter.invalidate_cache(str(request.equivalent))
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

                        PAYMENT_EVENTS_TOTAL.labels(
                            event="prepare", result="error"
                        ).inc()
                    except Exception:
                        pass

                    is_client_error = isinstance(e, GeoException) and 400 <= int(
                        getattr(e, "status_code", 500) or 500
                    ) < 500
                    public_error = (
                        e if is_client_error else _classify_payment_db_error(e)
                    )
                    abort_reason = str(public_error.message)
                    abort_code = getattr(public_error, "code", ErrorCode.E010)
                    abort_details = getattr(public_error, "details", None) or {}

                    # Abort is idempotent and also cleans up any partial locks.
                    if commit:
                        try:
                            await self.session.rollback()
                        except Exception as rollback_error:
                            logger.error(
                                "event=payment.prepare_rollback_failed tx_id=%s error_type=%s",
                                tx_id_str,
                                type(rollback_error).__name__,
                            )
                            raise GeoException() from rollback_error

                        try:
                            await self.engine.abort(
                                tx_id_str,
                                reason=abort_reason,
                                error_code=abort_code,
                                details=abort_details,
                                commit=True,
                            )
                        except Exception as abort_error:
                            logger.error(
                                "event=payment.prepare_abort_failed tx_id=%s error_type=%s",
                                tx_id_str,
                                type(abort_error).__name__,
                            )
                            raise GeoException() from abort_error
                    else:
                        try:
                            await self.engine.abort(
                                tx_id_str,
                                reason=abort_reason,
                                error_code=abort_code,
                                details=abort_details,
                                commit=False,
                            )
                        except Exception as abort_error:
                            logger.error(
                                "event=payment.prepare_nested_abort_failed tx_id=%s error_type=%s",
                                tx_id_str,
                                type(abort_error).__name__,
                            )

                    if is_client_error:
                        raise
                    raise public_error from e

                # 5. Engine Commit
                # In MVP we commit immediately. In real system, we might wait for receiver ACK.
                try:
                    await asyncio.wait_for(
                        self.engine.commit(tx_id_str, commit=commit),
                        timeout=commit_timeout_s,
                    )

                    # Debts/locks changed — never serve stale routing graphs.
                    if deferred_effects is None:
                        PaymentRouter.invalidate_cache(str(request.equivalent))
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

                        PAYMENT_EVENTS_TOTAL.labels(
                            event="commit", result="error"
                        ).inc()
                    except Exception:
                        pass

                    # If the engine raised a user-level GeoException (4xx), preserve it so
                    # simulator real-mode can classify it as REJECTED instead of INTERNAL_ERROR.
                    if isinstance(e, GeoException) and 400 <= int(
                        getattr(e, "status_code", 500) or 500
                    ) < 500:
                        if commit:
                            try:
                                await self.session.rollback()
                            except Exception as rollback_error:
                                logger.error(
                                    "event=payment.commit_rollback_failed tx_id=%s error_type=%s",
                                    tx_id_str,
                                    type(rollback_error).__name__,
                                )
                                raise GeoException() from rollback_error

                            try:
                                await self.engine.abort(
                                    tx_id_str,
                                    reason=str(e.message),
                                    error_code=e.code,
                                    details=e.details or {},
                                    commit=True,
                                )
                            except Exception as abort_error:
                                logger.error(
                                    "event=payment.commit_abort_failed tx_id=%s error_type=%s",
                                    tx_id_str,
                                    type(abort_error).__name__,
                                )
                                raise GeoException() from abort_error
                        else:
                            try:
                                await self.engine.abort(
                                    tx_id_str,
                                    reason=str(e.message),
                                    error_code=e.code,
                                    details=e.details or {},
                                    commit=False,
                                )
                            except Exception as abort_error:
                                logger.error(
                                    "event=payment.commit_nested_abort_failed tx_id=%s error_type=%s",
                                    tx_id_str,
                                    type(abort_error).__name__,
                                )
                        raise

                    # Under uncertainty (e.g. DB/network errors), commit may have succeeded even if
                    # the caller sees an exception. Read-before-abort to avoid COMMITTED -> ABORTED.
                    public_error = _classify_payment_db_error(e)
                    if commit:
                        try:
                            await self.session.rollback()
                        except Exception as rollback_error:
                            logger.error(
                                "event=payment.commit_rollback_failed tx_id=%s error_type=%s",
                                tx_id_str,
                                type(rollback_error).__name__,
                            )
                            raise public_error from rollback_error

                        try:
                            tx_latest = (
                                await self.session.execute(
                                    select(Transaction).where(
                                        Transaction.tx_id == tx_id_str
                                    )
                                )
                            ).scalar_one_or_none()
                        except Exception as read_error:
                            logger.error(
                                "event=payment.commit_recovery_read_failed tx_id=%s error_type=%s",
                                tx_id_str,
                                type(read_error).__name__,
                            )
                            raise public_error from read_error
                        if tx_latest is not None and tx_latest.state == "COMMITTED":
                            return self._tx_to_payment_result(tx_latest)

                    # Abort is idempotent; if commit failed before applying changes, this releases locks.
                    if commit:
                        try:
                            await self.engine.abort(
                                tx_id_str,
                                reason=public_error.message,
                                error_code=public_error.code,
                                details=public_error.details,
                                commit=True,
                            )
                        except Exception as abort_error:
                            logger.error(
                                "event=payment.commit_abort_failed tx_id=%s error_type=%s",
                                tx_id_str,
                                type(abort_error).__name__,
                            )
                            raise public_error from abort_error
                    else:
                        try:
                            await self.engine.abort(
                                tx_id_str,
                                reason=public_error.message,
                                error_code=public_error.code,
                                details=public_error.details,
                                commit=False,
                            )
                        except Exception as abort_error:
                            logger.error(
                                "event=payment.commit_nested_abort_failed tx_id=%s error_type=%s",
                                tx_id_str,
                                type(abort_error).__name__,
                            )
                    raise public_error from e
        except asyncio.CancelledError:
            if tx_may_be_persisted:
                committed_after_cancellation = False

                async def terminalize_after_cancellation() -> None:
                    nonlocal committed_after_cancellation
                    if commit:
                        await self.session.rollback()
                        tx_latest = (
                            await self.session.execute(
                                select(Transaction).where(
                                    Transaction.tx_id == tx_id_str
                                ).execution_options(populate_existing=True)
                            )
                        ).scalar_one_or_none()
                        committed_after_cancellation = (
                            tx_latest is not None and tx_latest.state == "COMMITTED"
                        )
                    if not committed_after_cancellation:
                        await self.engine.abort(
                            tx_id_str,
                            reason="Payment cancelled",
                            commit=commit,
                            error_code=ErrorCode.E007,
                        )

                cleanup_error = await _drain_payment_cleanup(
                    terminalize_after_cancellation
                )

                if cleanup_error is not None:
                    logger.error(
                        "event=payment.cancellation_cleanup_failed "
                        "tx_id=%s error_type=%s",
                        tx_id_str,
                        type(cleanup_error).__name__,
                    )
            raise
        except asyncio.TimeoutError:
            if tx_may_be_persisted:
                # Timeout is ambiguous: commit may have succeeded. Read-after-timeout to avoid
                # COMMITTED -> ABORTED.
                committed_after_timeout: PaymentResult | None = None
                cleanup_stage = "abort"

                async def terminalize_after_timeout() -> None:
                    nonlocal committed_after_timeout, cleanup_stage
                    if commit:
                        cleanup_stage = "rollback"
                        await self.session.rollback()
                        cleanup_stage = "recovery_read"
                        tx_latest = (
                            await self.session.execute(
                                select(Transaction).where(
                                    Transaction.tx_id == tx_id_str
                                ).execution_options(populate_existing=True)
                            )
                        ).scalar_one_or_none()
                        if tx_latest is not None and tx_latest.state == "COMMITTED":
                            committed_after_timeout = self._tx_to_payment_result(tx_latest)
                            return

                    cleanup_stage = "abort"
                    await self.engine.abort(
                        tx_id_str,
                        reason="Payment timeout",
                        commit=commit,
                        error_code=ErrorCode.E007,
                    )

                cleanup_error = await _drain_payment_cleanup(terminalize_after_timeout)
                if cleanup_error is not None:
                    event_name = (
                        "payment.timeout_recovery_read_failed"
                        if cleanup_stage == "recovery_read"
                        else f"payment.timeout_{cleanup_stage}_failed"
                    )
                    logger.error(
                        "event=%s tx_id=%s error_type=%s",
                        event_name,
                        tx_id_str,
                        type(cleanup_error).__name__,
                    )
                    if isinstance(cleanup_error, asyncio.CancelledError):
                        raise cleanup_error
                    if commit:
                        raise GeoException() from cleanup_error
                if committed_after_timeout is not None:
                    return committed_after_timeout
            raise TimeoutException("Payment timed out")

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
            invalidate_routing_cache=deferred_effects is not None,
            include_engine_success_metrics=deferred_effects is not None,
        )
        if deferred_effects is None:
            effects.apply_once()
        else:
            deferred_effects.append(effects)
        return result

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
        bind = None
        try:
            bind = self.session.get_bind()
        except Exception:
            bind = getattr(self.session, "bind", None)

        dialect_name = None
        try:
            dialect_name = bind.dialect.name if bind is not None else None
        except Exception:
            dialect_name = None

        def _normalize_dt(value: datetime | None) -> datetime | None:
            if value is None:
                return None
            if dialect_name == "sqlite":
                if value.tzinfo is None:
                    return value
                return value.astimezone(timezone.utc).replace(tzinfo=None)
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
            if dialect_name == "sqlite":
                clauses.append(
                    func.datetime(Transaction.created_at) >= func.datetime(from_date)
                )
            else:
                clauses.append(Transaction.created_at >= from_date)
        if to_date is not None:
            if dialect_name == "sqlite":
                clauses.append(
                    func.datetime(Transaction.created_at) <= func.datetime(to_date)
                )
            else:
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
