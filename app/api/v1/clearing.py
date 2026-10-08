from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query, Request
from fastapi.exceptions import RequestValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from concurrent.futures.process import BrokenProcessPool

from app.api import deps
from app.core.clearing.flow_planner import PlanIntegrityError
from app.core.clearing.runner import (
    ClearingDiagnosticsUnavailable,
    ClearingPassCancelled,
    ClearingPassError,
    ClearingPassResult,
    atoms_text,
    planned_cycles_for_diagnostics,
    run_awaited_clearing,
)
from app.schemas.clearing import ClearingAutoResponse, ClearingCyclesResponse
from app.schemas.common import ErrorEnvelope
from app.utils.error_codes import ERROR_MESSAGES, ErrorCode
from app.utils.exceptions import GeoException
from app.utils.validation import validate_equivalent_code

logger = logging.getLogger(__name__)

router = APIRouter()

#: Programme 023, decision 8 / R2: execution has no depth. 035 A1 (owner decision П1-(а), 2026-10-08): neither has the
#: diagnostic `GET /cycles`, which answers with the cycles of the same flow plan.
_EXECUTION_DEPTH_REMOVED = (
    "max_depth is not accepted: clearing has no depth limit since programme 023 (the flow plan clears cycles of any "
    "length); the parameter was removed from POST /clearing/auto and from the diagnostic GET /clearing/cycles, "
    "which answers with the cycles of that plan"
)


@router.get(
    "/cycles",
    response_model=ClearingCyclesResponse,
    responses={
        # Declared here as well as in api/openapi.yaml so the generated schema states the same contract.
        500: {"model": ErrorEnvelope, "description": "The planner process could not answer (E010)"},
        503: {
            "model": ErrorEnvelope,
            "description": "Diagnostics cannot answer now (E007): another diagnostic plan is being computed "
            "(`details.reason = diagnostics_busy`) or this one ran past its time bound "
            "(`details.reason = diagnostics_timeout`); retry after `details.retry_after_seconds`",
        },
    },
)
async def list_cycles(
    request: Request,
    equivalent: str = Query(..., description="Equivalent code"),
    db: AsyncSession = Depends(deps.get_db),
    _current_participant=Depends(deps.get_current_participant),
):
    """The cycles of the plan a clearing pass would compute now, on a fresh snapshot (035 A1, П1-(а)).

    The plan is computed in the diagnostic planner process, never on the event loop and never in front of a pass's
    plan. Diagnostics that cannot answer say so with a `request_id` - busy or past the time bound is 503, a planner
    that failed is 500 - never an empty list and never another detector. The pass itself may still refuse to run
    (a stopped or held equivalent, clearing switched off): this route does not ask.
    """

    _refuse_execution_depth(request)
    validate_equivalent_code(equivalent)
    try:
        cycles = await planned_cycles_for_diagnostics(db, equivalent)
    except ClearingDiagnosticsUnavailable as unavailable:
        logger.warning("event=clearing.cycles.unavailable equivalent=%s reason=%s", equivalent, unavailable.reason)
        raise
    except (BrokenProcessPool, PlanIntegrityError) as failed:
        # The bare E010 (500) with the request id; which of the two it was stays in the log.
        logger.error("event=clearing.cycles.planner_failed equivalent=%s error=%s", equivalent, type(failed).__name__)
        raise GeoException() from failed
    return {"cycles": cycles}


def _refuse_execution_depth(request: Request) -> None:
    """R2: the KEY's presence is refused, whatever its value - bare, empty, repeated, valid or not.

    FastAPI ignores an undeclared query parameter, so deleting `max_depth` from the signature alone would accept
    and silently drop it - exactly the state decision 8 forbids. Both routes of this module refuse it (035 A1).
    """

    if "max_depth" in request.query_params:
        raise RequestValidationError(
            [
                {
                    "type": "extra_forbidden",
                    "loc": ("query", "max_depth"),
                    "msg": _EXECUTION_DEPTH_REMOVED,
                    "input": request.query_params.getlist("max_depth"),
                }
            ]
        )


def _is_internal(cause: BaseException) -> bool:
    """An internal failure: not a GeoException, or a GeoException with the internal code E010 (whose message may
    carry private detail - the retired `auto_clear` sanitised exactly these, and so does this route)."""

    return not isinstance(cause, GeoException) or cause.code == ErrorCode.E010.value


def _error_body(cause: BaseException) -> dict:
    """The cause as the error envelope renders it; an internal failure is sanitised to the bare E010."""

    if _is_internal(cause):
        return {"code": ErrorCode.E010.value, "message": ERROR_MESSAGES[ErrorCode.E010], "details": None}
    return {"code": cause.code, "message": cause.message, "details": dict(cause.details) or None}


def _answer(result: ClearingPassResult, *, error: BaseException | None = None) -> dict:
    return {
        "equivalent": result.equivalent,
        "cleared_cycles": len(result.committed),
        "status": result.status,
        "reason": None if result.reason is None else result.reason.value,
        "v_edge": atoms_text(result.v_edge_atoms),
        "v_cyc": atoms_text(result.v_cyc_atoms),
        "remaining_cycles": result.remaining_cycles,
        "remaining_v_edge": None if result.remaining_v_edge_atoms is None else atoms_text(result.remaining_v_edge_atoms),
        "committed": [
            {
                "occurrence_id": o.occurrence_id,
                "plan_id": str(o.plan_id),
                "ordinal": o.ordinal,
                "amount": atoms_text(o.amount_atoms),
                "edges": [
                    {"debt_id": str(e.debt_id), "debtor_id": str(e.debtor_id), "creditor_id": str(e.creditor_id)}
                    for e in o.edges
                ],
                "after_cancellation": o.after_cancellation,
            }
            for o in result.committed
        ],
        "error": None if error is None else _error_body(error),
    }


@router.post(
    "/auto",
    response_model=ClearingAutoResponse,
    responses={
        # T1544 / 023 R3: the operator stop (or an integrity hold) met BEFORE the first commit is 409/E008. Met after
        # progress, the pass answers 200 `interrupted` with `error.code = "E008"` instead (the progress is durable).
        # Declared here as well as in api/openapi.yaml so the generated schema states the same contract.
        409: {
            "model": ErrorEnvelope,
            "description": "Equivalent is not active (operator stop or integrity hold) before any occurrence "
            "committed, the equivalent's clearing lease is held by another pass, or clearing is switched off "
            "(`details.reason = clearing_disabled`)",
        },
    },
)
async def auto_clear(
    request: Request,
    equivalent: str = Query(..., description="Equivalent code"),
    _current_participant=Depends(deps.get_current_participant),
    redis_client=Depends(deps.get_redis_client),
    session_factory=Depends(deps.get_payment_session_factory),
):
    """One awaited pass of the common clearing runner (programme 023, decisions 7, 10; R3 outcome table)."""

    _refuse_execution_depth(request)
    validate_equivalent_code(equivalent)
    try:
        result = await run_awaited_clearing(session_factory, redis_client, equivalent)
    except ClearingPassError as failed:
        if not failed.result.committed:
            # Nothing is durable: the original error contract (409/E008 on a stop, the sanitised 500 otherwise).
            if isinstance(failed.cause, GeoException) and _is_internal(failed.cause):
                logger.error("event=clearing.auto.failed equivalent=%s error=GeoException(E010)", equivalent)
                raise GeoException() from failed.cause  # the bare E010: its private message stays in the log
            raise failed.cause
        logger.warning(
            "event=clearing.auto.interrupted_by_error equivalent=%s committed=%s error=%s",
            equivalent,
            len(failed.result.committed),
            type(failed.cause).__name__,
        )
        return _answer(failed.result, error=failed.cause)
    except ClearingPassCancelled as cancelled:
        # Accounted, then preserved: the durable progress is logged; the cancellation propagates unchanged.
        progress = cancelled.result
        logger.warning(
            "event=clearing.auto.cancelled equivalent=%s committed=%s v_edge=%s occurrences=%s",
            equivalent,
            len(progress.committed),
            atoms_text(progress.v_edge_atoms),
            ",".join(o.occurrence_id for o in progress.committed),
        )
        raise
    return _answer(result)
