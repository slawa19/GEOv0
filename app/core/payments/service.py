import json
import uuid
import hashlib
import logging
import asyncio
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone
from typing import List, Optional, Literal

from sqlalchemy import select, and_, or_, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.payments.engine import PaymentEngine
from app.core.payments.router import PaymentRouter
from app.config import settings
from app.db.models.transaction import Transaction
from app.db.models.participant import Participant
from app.db.models.equivalent import Equivalent
from app.schemas.payment import PaymentConstraints, PaymentCreateRequest, PaymentResult, PaymentRoute, PaymentError
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
from app.utils.validation import validate_equivalent_code, validate_idempotency_key

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
    ) -> PaymentResult:
        """Internal-only payment path for the simulator runner.

        IMPORTANT:
        - This must never be exposed via HTTP endpoints.
        - It bypasses signature verification and should only be used by trusted
          in-process code.
        """

        req = PaymentCreateRequest(
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
        )

    async def _create_payment_impl(
        self,
        sender_id: uuid.UUID,
        request: PaymentCreateRequest,
        *,
        idempotency_key: str | None = None,
        require_signature: bool,
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
            amount = Decimal(str(request.amount))
        except (InvalidOperation, ValueError):
            try:
                from app.utils.metrics import PAYMENT_EVENTS_TOTAL

                PAYMENT_EVENTS_TOTAL.labels(event="create", result="bad_request").inc()
            except Exception:
                pass
            raise BadRequestException("Invalid amount format")
        if amount <= 0:
            try:
                from app.utils.metrics import PAYMENT_EVENTS_TOTAL

                PAYMENT_EVENTS_TOTAL.labels(event="create", result="bad_request").inc()
            except Exception:
                pass
            raise BadRequestException("Amount must be positive")

        validate_equivalent_code(request.equivalent)

        # Optional idempotency: if provided, return the existing transaction result.
        request_fingerprint = None
        normalized_idempotency_key = None
        if idempotency_key is not None:
            normalized_idempotency_key = validate_idempotency_key(idempotency_key)
            fp_payload: dict = {
                "to": request.to,
                "equivalent": request.equivalent,
                "amount": request.amount,
            }
            if request.description is not None:
                fp_payload["description"] = request.description
            if request.constraints is not None:
                fp_payload["constraints"] = request.constraints.model_dump(exclude_unset=True)

            request_fingerprint = hashlib.sha256(canonical_json(fp_payload)).hexdigest()

            existing_tx = (
                await self.session.execute(
                    select(Transaction).where(
                        Transaction.initiator_id == sender_id,
                        Transaction.type == "PAYMENT",
                        Transaction.idempotency_key == normalized_idempotency_key,
                    )
                )
            ).scalar_one_or_none()
            if existing_tx is not None:
                existing_payload = existing_tx.payload or {}
                existing_fp = (existing_payload.get("idempotency") or {}).get("fingerprint")
                if existing_fp is not None and request_fingerprint is not None and existing_fp != request_fingerprint:
                    raise ConflictException("Idempotency-Key already used for a different request")

                if existing_tx.state in {"NEW", "ROUTED", "PREPARE_IN_PROGRESS", "PREPARED", "PROPOSED", "WAITING"}:
                    try:
                        from app.utils.metrics import PAYMENT_EVENTS_TOTAL

                        PAYMENT_EVENTS_TOTAL.labels(event="create", result="conflict_in_progress").inc()
                    except Exception:
                        pass
                    raise ConflictException("Payment with same Idempotency-Key is in progress")

                try:
                    from app.utils.metrics import PAYMENT_EVENTS_TOTAL

                    PAYMENT_EVENTS_TOTAL.labels(event="create", result="idempotent_hit").inc()
                except Exception:
                    pass
                return self._tx_to_payment_result(existing_tx)

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

                    PAYMENT_EVENTS_TOTAL.labels(event="create", result="bad_request").inc()
                except Exception:
                    pass
                raise InvalidSignatureException("Missing signature")

        receiver = (await self.session.execute(select(Participant).where(Participant.pid == request.to))).scalar_one_or_none()
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

        equivalent = (await self.session.execute(select(Equivalent).where(Equivalent.code == request.equivalent))).scalar_one_or_none()
        if not equivalent:
            try:
                from app.utils.metrics import PAYMENT_EVENTS_TOTAL

                PAYMENT_EVENTS_TOTAL.labels(event="create", result="not_found").inc()
            except Exception:
                pass
            raise NotFoundException(f"Equivalent {request.equivalent} not found")

        if require_signature:
            # Signature validation (proof-of-possession + binding of request fields).
            # Canonical message is part of the API contract for MVP.
            payload: dict = {
                "to": request.to,
                "equivalent": request.equivalent,
                "amount": request.amount,
            }
            if request.description is not None:
                payload["description"] = request.description
            if request.constraints is not None:
                payload["constraints"] = request.constraints.model_dump(exclude_unset=True)

            message = canonical_json(payload)
            try:
                verify_signature(sender.public_key, message, request.signature)
            except Exception:
                try:
                    from app.utils.metrics import PAYMENT_EVENTS_TOTAL

                    PAYMENT_EVENTS_TOTAL.labels(event="create", result="invalid_signature").inc()
                except Exception:
                    pass
                raise InvalidSignatureException("Invalid signature")

        # 2. Routing
        tx_uuid = uuid.uuid4()
        tx_id_str = str(tx_uuid)
        tx_persisted = False

        routing_timeout_s = float(getattr(settings, "ROUTING_PATH_FINDING_TIMEOUT_MS", 500) or 500) / 1000.0
        prepare_timeout_s = float(getattr(settings, "PREPARE_TIMEOUT_SECONDS", 3) or 3)
        commit_timeout_s = float(getattr(settings, "COMMIT_TIMEOUT_SECONDS", 5) or 5)
        total_timeout_s = float(getattr(settings, "PAYMENT_TOTAL_TIMEOUT_SECONDS", 10) or 10)

        try:
            async with asyncio.timeout(total_timeout_s):
                # Build routing graph + compute routes under spec-aligned timeout budget.
                await asyncio.wait_for(self.router.build_graph(equivalent.code), timeout=routing_timeout_s)

                routes_found = await asyncio.wait_for(
                    asyncio.to_thread(
                        self.router.find_flow_routes,
                        sender.pid,
                        receiver.pid,
                        amount,
                        max_hops=settings.ROUTING_MAX_HOPS,
                        max_paths=(
                            settings.ROUTING_MAX_PATHS
                            if bool(getattr(settings, "FEATURE_FLAGS_MULTIPATH_ENABLED", True))
                            else 1
                        ),
                    ),
                    timeout=routing_timeout_s,
                )

                if not routes_found:
                    try:
                        from app.utils.metrics import PAYMENT_EVENTS_TOTAL, ROUTING_FAILURES_TOTAL

                        PAYMENT_EVENTS_TOTAL.labels(event="create", result="routing_failed").inc()
                        ROUTING_FAILURES_TOTAL.labels(reason="insufficient_capacity").inc()
                    except Exception:
                        pass
                    raise RoutingException("No route found with sufficient capacity", insufficient_capacity=True)

                routes_payload = [
                    {"path": path, "amount": str(route_amount)}
                    for path, route_amount in routes_found
                ]

                # 3. Create Transaction
                new_tx = Transaction(
                    id=tx_uuid,
                    tx_id=tx_id_str,
                    idempotency_key=normalized_idempotency_key,
                    type='PAYMENT',
                    initiator_id=sender.id,
                    payload={
                        'from': sender.pid,
                        'to': receiver.pid,
                        'amount': str(amount),
                        'equivalent': equivalent.code,
                        'routes': routes_payload,
                        'idempotency': {
                            'key': normalized_idempotency_key,
                            'fingerprint': request_fingerprint,
                        } if normalized_idempotency_key is not None and request_fingerprint is not None else None,
                    },
                    state='NEW'
                )
                self.session.add(new_tx)
                await self.session.commit()
                tx_persisted = True

                # 4. Engine Prepare
                try:
                    if len(routes_found) == 1:
                        await asyncio.wait_for(
                            self.engine.prepare(tx_id_str, routes_found[0][0], amount, equivalent.id),
                            timeout=prepare_timeout_s,
                        )
                    else:
                        await asyncio.wait_for(
                            self.engine.prepare_routes(tx_id_str, routes_found, equivalent.id),
                            timeout=prepare_timeout_s,
                        )
                except asyncio.TimeoutError:
                    raise
                except Exception as e:
                    logger.info("event=payment.prepare_failed tx_id=%s error=%s", tx_id_str, str(e))
                    try:
                        from app.utils.metrics import PAYMENT_EVENTS_TOTAL

                        PAYMENT_EVENTS_TOTAL.labels(event="prepare", result="error").inc()
                    except Exception:
                        pass
                    # Abort is idempotent and also cleans up any partial locks.
                    await self.engine.abort(tx_id_str, reason=f"Prepare failed: {str(e)}")
                    raise BadRequestException(f"Payment preparation failed: {str(e)}")

                # 5. Engine Commit
                # In MVP we commit immediately. In real system, we might wait for receiver ACK.
                try:
                    await asyncio.wait_for(self.engine.commit(tx_id_str), timeout=commit_timeout_s)
                except asyncio.TimeoutError:
                    raise
                except Exception as e:
                    logger.info("event=payment.commit_failed tx_id=%s error=%s", tx_id_str, str(e))
                    try:
                        from app.utils.metrics import PAYMENT_EVENTS_TOTAL

                        PAYMENT_EVENTS_TOTAL.labels(event="commit", result="error").inc()
                    except Exception:
                        pass
                    # Abort is idempotent; if commit failed before applying changes, this releases locks.
                    await self.engine.abort(tx_id_str, reason=f"Commit failed: {str(e)}")
                    raise GeoException(f"Payment commit failed: {str(e)}")
        except asyncio.TimeoutError:
            if tx_persisted:
                # Ensure abort isn't cancelled due to the timeout cancellation context.
                await asyncio.shield(self.engine.abort(tx_id_str, reason="Payment timeout"))
            raise TimeoutException("Payment timed out")

        # Refresh tx to get server timestamps (created_at/updated_at)
        tx = await self.session.get(Transaction, tx_uuid)
        created_at = tx.created_at if tx else None
        committed_at = None
        if tx and tx.state == 'COMMITTED':
            committed_at = tx.updated_at

        routes = [PaymentRoute(path=path, amount=str(route_amount)) for path, route_amount in routes_found]
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
        tx = (await self.session.execute(select(Transaction).where(Transaction.tx_id == tx_id))).scalar_one_or_none()
        if not tx or tx.type != 'PAYMENT':
            raise NotFoundException(f"Payment {tx_id} not found")

        return self._tx_to_payment_result(tx)

    async def get_payment_for_participant(
        self,
        tx_id: str,
        *,
        requester_participant_id: uuid.UUID,
        requester_pid: str,
    ) -> PaymentResult:
        tx = (await self.session.execute(select(Transaction).where(Transaction.tx_id == tx_id))).scalar_one_or_none()
        if not tx or tx.type != 'PAYMENT':
            raise NotFoundException(f"Payment {tx_id} not found")

        payload = tx.payload or {}
        # Access rule (MVP): allow initiator or receiver; otherwise return 404 to avoid leaking existence.
        if tx.initiator_id != requester_participant_id and str(payload.get('to', '')) != requester_pid:
            raise NotFoundException(f"Payment {tx_id} not found")

        return self._tx_to_payment_result(tx)

    def _tx_to_payment_result(self, tx: Transaction) -> PaymentResult:
        payload = tx.payload or {}
        routes_payload = payload.get('routes')
        routes = None
        if routes_payload is not None:
            routes = [PaymentRoute.model_validate(r) for r in routes_payload] or None

        committed_at = tx.updated_at if tx.state == 'COMMITTED' else None
        error = None
        if tx.error:
            error = PaymentError(
                code=str(tx.error.get('code', 'ERROR')),
                message=str(tx.error.get('message', '')),
                details=tx.error.get('details'),
            )

        status = tx.state if tx.state in {'COMMITTED', 'ABORTED'} else 'ABORTED'
        return PaymentResult(
            tx_id=tx.tx_id,
            status=status,
            **{"from": str(payload.get('from', ''))},
            to=str(payload.get('to', '')),
            equivalent=str(payload.get('equivalent', '')),
            amount=str(payload.get('amount', '')),
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
        direction: Literal['sent', 'received', 'all'] = 'all',
        equivalent: str | None = None,
        status: Literal['COMMITTED', 'ABORTED', 'all'] = 'all',
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

        clauses = [Transaction.type == 'PAYMENT']
        if status != 'all':
            clauses.append(Transaction.state == status)
        if from_date is not None:
            if dialect_name == "sqlite":
                clauses.append(func.datetime(Transaction.created_at) >= func.datetime(from_date))
            else:
                clauses.append(Transaction.created_at >= from_date)
        if to_date is not None:
            if dialect_name == "sqlite":
                clauses.append(func.datetime(Transaction.created_at) <= func.datetime(to_date))
            else:
                clauses.append(Transaction.created_at <= to_date)

        # Direction filtering.
        payload = Transaction.payload
        to_expr = payload['to'].as_string()
        from_expr = payload['from'].as_string()
        eq_expr = payload['equivalent'].as_string()

        if direction == 'sent':
            clauses.append(or_(Transaction.initiator_id == requester_participant_id, from_expr == requester_pid))
        elif direction == 'received':
            clauses.append(to_expr == requester_pid)
        else:
            clauses.append(or_(Transaction.initiator_id == requester_participant_id, to_expr == requester_pid, from_expr == requester_pid))

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