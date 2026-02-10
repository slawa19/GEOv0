import uuid
import hashlib
import logging
import asyncio
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone
from typing import List, Literal

from sqlalchemy import select, and_, or_, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

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
    InvalidSignatureException,
    RoutingException,
    TimeoutException,
)
from app.utils.error_codes import ErrorCode
from app.utils.validation import validate_equivalent_code, validate_tx_id, parse_amount_decimal

logger = logging.getLogger(__name__)


class PaymentService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.engine = PaymentEngine(session)
        self.router = PaymentRouter(session)

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

    async def _create_payment_impl(
        self,
        sender_id: uuid.UUID,
        request: PaymentCreateRequest,
        *,
        idempotency_key: str | None = None,
        require_signature: bool,
        commit: bool,
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

        # 2. Routing
        tx_uuid = uuid.uuid4()
        tx_persisted = False

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
                    await asyncio.wait_for(
                        self.router.build_graph(equivalent.code), timeout=routing_timeout_s
                    )
                except asyncio.TimeoutError:
                    raise TimeoutException("Routing timed out")

                try:
                    routes_found = await asyncio.wait_for(
                        asyncio.to_thread(
                            self.router.find_flow_routes,
                            sender.pid,
                            receiver.pid,
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
                    initiator_id=sender.id,
                    payload={
                        "from": sender.pid,
                        "to": receiver.pid,
                        "amount": str(amount),
                        "equivalent": equivalent.code,
                        "routes": routes_payload,
                        "idempotency": {
                            "key": tx_id_str,
                            "fingerprint": request_fingerprint,
                        },
                    },
                    state="NEW",
                )
                self.session.add(new_tx)
                try:
                    if commit:
                        await self.session.commit()
                    else:
                        await self.session.flush()
                except IntegrityError:
                    # tx_id is globally unique; handle race by re-loading and applying idempotency rules.
                    await self.session.rollback()
                    existing_tx = (
                        await self.session.execute(
                            select(Transaction).where(Transaction.tx_id == tx_id_str)
                        )
                    ).scalar_one_or_none()
                    if existing_tx is not None:
                        if existing_tx.type != "PAYMENT":
                            raise ConflictException("tx_id already used")
                        existing_payload = existing_tx.payload or {}
                        existing_fp = (existing_payload.get("idempotency") or {}).get(
                            "fingerprint"
                        )
                        if existing_fp is not None and existing_fp != request_fingerprint:
                            raise ConflictException(
                                "tx_id already used for a different request"
                            )
                        return self._tx_to_payment_result(existing_tx)
                    raise
                tx_persisted = True

                # 4. Engine Prepare
                try:
                    if len(routes_found) == 1:
                        await asyncio.wait_for(
                            self.engine.prepare(
                                tx_id_str,
                                routes_found[0][0],
                                amount,
                                equivalent.id,
                                commit=commit,
                            ),
                            timeout=prepare_timeout_s,
                        )
                    else:
                        await asyncio.wait_for(
                            self.engine.prepare_routes(
                                tx_id_str,
                                routes_found,
                                equivalent.id,
                                commit=commit,
                            ),
                            timeout=prepare_timeout_s,
                        )
                except asyncio.TimeoutError:
                    raise
                except Exception as e:
                    logger.info(
                        "event=payment.prepare_failed tx_id=%s error=%s",
                        tx_id_str,
                        str(e),
                    )
                    try:
                        from app.utils.metrics import PAYMENT_EVENTS_TOTAL

                        PAYMENT_EVENTS_TOTAL.labels(
                            event="prepare", result="error"
                        ).inc()
                    except Exception:
                        pass
                    # Abort is idempotent and also cleans up any partial locks.
                    await self.engine.abort(
                        tx_id_str,
                        reason=f"Prepare failed: {str(e)}",
                        error_code=ErrorCode.E010,
                    )
                    raise BadRequestException(f"Payment preparation failed: {str(e)}")

                # 5. Engine Commit
                # In MVP we commit immediately. In real system, we might wait for receiver ACK.
                try:
                    await asyncio.wait_for(
                        self.engine.commit(tx_id_str, commit=commit),
                        timeout=commit_timeout_s,
                    )
                except asyncio.TimeoutError:
                    raise
                except Exception as e:
                    logger.info(
                        "event=payment.commit_failed tx_id=%s error=%s",
                        tx_id_str,
                        str(e),
                    )
                    try:
                        from app.utils.metrics import PAYMENT_EVENTS_TOTAL

                        PAYMENT_EVENTS_TOTAL.labels(
                            event="commit", result="error"
                        ).inc()
                    except Exception:
                        pass
                    # Under uncertainty (e.g. DB/network errors), commit may have succeeded even if
                    # the caller sees an exception. Read-before-abort to avoid COMMITTED -> ABORTED.
                    try:
                        await self.session.rollback()
                    except Exception:
                        pass

                    tx_latest = (
                        await self.session.execute(
                            select(Transaction).where(Transaction.tx_id == tx_id_str)
                        )
                    ).scalar_one_or_none()
                    if tx_latest is not None and tx_latest.state == "COMMITTED":
                        return self._tx_to_payment_result(tx_latest)

                    # Abort is idempotent; if commit failed before applying changes, this releases locks.
                    await self.engine.abort(
                        tx_id_str,
                        reason=f"Commit failed: {str(e)}",
                        error_code=ErrorCode.E010,
                    )
                    raise GeoException(f"Payment commit failed: {str(e)}")
        except asyncio.TimeoutError:
            if tx_persisted:
                # Timeout is ambiguous: commit may have succeeded. Read-after-timeout to avoid
                # COMMITTED -> ABORTED.
                try:
                    await self.session.rollback()
                except Exception:
                    pass

                tx_latest = (
                    await self.session.execute(
                        select(Transaction).where(Transaction.tx_id == tx_id_str)
                    )
                ).scalar_one_or_none()
                if tx_latest is not None and tx_latest.state == "COMMITTED":
                    return self._tx_to_payment_result(tx_latest)

                # Ensure abort isn't cancelled due to the timeout cancellation context.
                await asyncio.shield(
                    self.engine.abort(
                        tx_id_str,
                        reason="Payment timeout",
                        commit=commit,
                        error_code=ErrorCode.E007,
                    )
                )
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
        try:
            from app.utils.metrics import PAYMENT_EVENTS_TOTAL

            PAYMENT_EVENTS_TOTAL.labels(event="create", result="success").inc()
            PAYMENT_EVENTS_TOTAL.labels(event="prepare", result="success").inc()
            PAYMENT_EVENTS_TOTAL.labels(event="commit", result="success").inc()
        except Exception:
            pass

        # Best-effort real-time event for receivers.
        try:
            from app.utils.event_bus import event_bus

            event_bus.publish(
                recipient_pid=receiver.pid,
                event="payment.received",
                payload={
                    "tx_id": tx_id_str,
                    "from": sender.pid,
                    "to": receiver.pid,
                    "equivalent": equivalent.code,
                    "amount": str(amount),
                },
            )
        except Exception:
            pass
        return PaymentResult(
            tx_id=tx_id_str,
            status="COMMITTED",
            **{"from": sender.pid},
            to=receiver.pid,
            equivalent=equivalent.code,
            amount=str(amount),
            routes=routes,
            created_at=created_at,
            committed_at=committed_at,
        )

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
