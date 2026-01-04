import json
import uuid
import hashlib
import logging
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
from app.schemas.payment import PaymentCreateRequest, PaymentResult, PaymentRoute, PaymentError
from app.core.auth.crypto import verify_signature
from app.utils.exceptions import NotFoundException, BadRequestException, GeoException, ConflictException
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
            request_fingerprint = hashlib.sha256(
                json.dumps(
                    {
                        "to": request.to,
                        "equivalent": request.equivalent,
                        "amount": request.amount,
                        "description": request.description,
                        "constraints": request.constraints,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()

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

        if not isinstance(request.signature, str) or not request.signature:
            try:
                from app.utils.metrics import PAYMENT_EVENTS_TOTAL

                PAYMENT_EVENTS_TOTAL.labels(event="create", result="bad_request").inc()
            except Exception:
                pass
            raise BadRequestException("Missing signature")

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

        # Signature validation (proof-of-possession + binding of request fields).
        # Canonical message is part of the API contract for MVP.
        payload = {
            "to": request.to,
            "equivalent": request.equivalent,
            "amount": request.amount,
            "description": request.description,
            "constraints": request.constraints,
        }
        message = (
            f"geo:payment:request:{sender.pid}:"
            + json.dumps(payload, sort_keys=True, separators=(",", ":"))
        ).encode("utf-8")
        try:
            verify_signature(sender.public_key, message, request.signature)
        except Exception:
            try:
                from app.utils.metrics import PAYMENT_EVENTS_TOTAL

                PAYMENT_EVENTS_TOTAL.labels(event="create", result="invalid_signature").inc()
            except Exception:
                pass
            raise BadRequestException("Invalid signature")

        # 2. Routing
        # Build graph for this equivalent.
        await self.router.build_graph(equivalent.code)

        routes_found = self.router.find_flow_routes(
            sender.pid,
            receiver.pid,
            amount,
            max_hops=settings.ROUTING_MAX_HOPS,
            max_paths=settings.ROUTING_MAX_PATHS,
        )
        if not routes_found:
            try:
                from app.utils.metrics import PAYMENT_EVENTS_TOTAL, ROUTING_FAILURES_TOTAL

                PAYMENT_EVENTS_TOTAL.labels(event="create", result="routing_failed").inc()
                ROUTING_FAILURES_TOTAL.labels(reason="insufficient_capacity").inc()
            except Exception:
                pass
            raise BadRequestException("No route found with sufficient capacity")

        routes_payload = [
            {"path": path, "amount": str(route_amount)}
            for path, route_amount in routes_found
        ]
        
        # 3. Create Transaction
        tx_uuid = uuid.uuid4()
        # Ensure tx_id is unique string.
        tx_id_str = str(tx_uuid)
        
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
        
        # 4. Engine Prepare
        try:
            if len(routes_found) == 1:
                await self.engine.prepare(tx_id_str, routes_found[0][0], amount, equivalent.id)
            else:
                await self.engine.prepare_routes(tx_id_str, routes_found, equivalent.id)
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
            await self.engine.commit(tx_id_str)
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
        routes_payload = payload.get('routes') or []
        routes = [PaymentRoute(path=r.get('path', []), amount=str(r.get('amount'))) for r in routes_payload] or None

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