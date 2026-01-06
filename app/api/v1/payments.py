from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Optional, Literal

from fastapi import APIRouter, Depends, Query
from fastapi import Header
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.core.payments.router import PaymentRouter
from app.core.payments.service import PaymentService
from app.utils.distributed_lock import redis_distributed_lock
from app.schemas.payment import (
    CapacityResponse, MaxFlowResponse,
    PaymentCreateRequest, PaymentResult, PaymentsList
)
from app.db.models.participant import Participant
from app.utils.exceptions import BadRequestException

router = APIRouter()

@router.get("/capacity", response_model=CapacityResponse)
async def check_capacity(
    to: str,
    equivalent: str,
    amount: str, # Decimal string
    session: AsyncSession = Depends(deps.get_db),
    current_participant: Participant = Depends(deps.get_current_participant)
):
    """
    Check capacity for a concrete payment amount from current user to recipient.
    """
    payment_router = PaymentRouter(session)
    await payment_router.build_graph(equivalent)
    
    try:
        amount_decimal = Decimal(amount)
    except (InvalidOperation, ValueError):
        raise BadRequestException("Invalid amount format")

    if amount_decimal <= 0:
        raise BadRequestException("Amount must be positive")

    return payment_router.check_capacity(current_participant.pid, to, amount_decimal)

@router.get("/max-flow", response_model=MaxFlowResponse)
async def get_max_flow(
    to: str,
    equivalent: str,
    session: AsyncSession = Depends(deps.get_db),
    current_participant: Participant = Depends(deps.get_current_participant)
):
    """
    Estimate maximum transferable amount and diagnostics.
    """
    payment_router = PaymentRouter(session)
    await payment_router.build_graph(equivalent)
    
    return payment_router.calculate_max_flow(current_participant.pid, to)

@router.post("", response_model=PaymentResult)
async def create_payment(
    payment_in: PaymentCreateRequest,
    session: AsyncSession = Depends(deps.get_db),
    current_participant: Participant = Depends(deps.get_current_participant),
    redis_client=Depends(deps.get_redis_client),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
):
    """
    Create and execute a payment.
    """
    service = PaymentService(session)

    lock_key = f"dlock:payment:{current_participant.id}:{payment_in.equivalent}"
    async with redis_distributed_lock(
        redis_client,
        lock_key,
        ttl_seconds=15,
        wait_timeout_seconds=2.0,
    ):
        return await service.create_payment(
            current_participant.id,
            payment_in,
            idempotency_key=idempotency_key,
        )

@router.get("/{tx_id}", response_model=PaymentResult)
async def get_payment(
    tx_id: str,
    session: AsyncSession = Depends(deps.get_db),
    current_participant: Participant = Depends(deps.get_current_participant)
):
    """
    Get payment details by Transaction ID.
    """
    service = PaymentService(session)
    return await service.get_payment_for_participant(
        tx_id,
        requester_participant_id=current_participant.id,
        requester_pid=current_participant.pid,
    )

@router.get("", response_model=PaymentsList)
async def list_payments(
    direction: Literal['sent', 'received', 'all'] = Query('all'),
    equivalent: Optional[str] = Query(None),
    status: Literal['COMMITTED', 'ABORTED', 'all'] = Query('all'),
    from_date: Optional[datetime] = Query(None),
    to_date: Optional[datetime] = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=200),
    session: AsyncSession = Depends(deps.get_db),
    current_participant: Participant = Depends(deps.get_current_participant)
):
    """
    List payments for current user.
    """
    service = PaymentService(session)
    items = await service.list_payments(
        requester_participant_id=current_participant.id,
        requester_pid=current_participant.pid,
        direction=direction,
        equivalent=equivalent,
        status=status,
        from_date=from_date,
        to_date=to_date,
        page=page,
        per_page=per_page,
    )
    return PaymentsList(items=items)
