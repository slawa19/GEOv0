from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from decimal import Decimal
from typing import List

from app.api import deps
from app.core.payments.router import PaymentRouter
from app.core.payments.service import PaymentService
from app.schemas.payment import (
    CapacityResponse, MaxFlowResponse, 
    PaymentCreateRequest, PaymentResult, PaymentDetail, PaymentsList
)
from app.db.models.participant import Participant
from app.utils.exceptions import GeoException

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
    except:
        raise HTTPException(status_code=400, detail="Invalid amount format")

    if amount_decimal <= 0:
        raise HTTPException(status_code=400, detail="Amount must be positive")

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
    current_participant: Participant = Depends(deps.get_current_participant)
):
    """
    Create and execute a payment.
    """
    service = PaymentService(session)
    return await service.create_payment(current_participant.id, payment_in)

@router.get("/{tx_id}", response_model=PaymentDetail)
async def get_payment(
    tx_id: str,
    session: AsyncSession = Depends(deps.get_db),
    current_participant: Participant = Depends(deps.get_current_participant)
):
    """
    Get payment details by Transaction ID.
    """
    service = PaymentService(session)
    # Check permission? PaymentService.get_payment currently fetches by ID.
    # Ideally verify current user is participant.
    # Service layer or API layer check.
    # For MVP, simple fetch.
    return await service.get_payment(tx_id)

@router.get("", response_model=PaymentsList)
async def list_payments(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(deps.get_db),
    current_participant: Participant = Depends(deps.get_current_participant)
):
    """
    List payments for current user.
    """
    service = PaymentService(session)
    items = await service.list_payments(current_participant.id, limit=limit, offset=offset)
    return PaymentsList(items=items)
