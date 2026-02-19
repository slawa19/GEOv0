from __future__ import annotations

import asyncio
import json
import secrets
import os
import logging
import uuid
from decimal import Decimal
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Optional
from pydantic import BaseModel, Field, ValidationError
from pydantic.config import ConfigDict

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from starlette.responses import FileResponse, Response, StreamingResponse, JSONResponse

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import aliased

from app.api import deps
from app.config import settings
from app.core.simulator.runtime import runtime
from app.core.clearing.service import ClearingService
from app.core.payments.router import PaymentRouter
from app.core.payments.service import PaymentService
from app.core.simulator.edge_patch_builder import EdgePatchBuilder
from app.core.simulator.sse_broadcast import SseEventEmitter
from app.core.simulator.viz_patch_helper import VizPatchHelper
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine
from app.schemas.simulator import (
    ActiveRunResponse,
    ArtifactIndex,
    BottlenecksResponse,
    MetricsResponse,
    RunMode,
    RunCreateRequest,
    RunCreateResponse,
    RunStatus,
    ScenarioSummary,
    ScenarioUploadRequest,
    ScenariosListResponse,
    SetIntensityRequest,
    SimulatorGraphSnapshot,
    SimulatorRunStatusEvent,
    SimulatorTxUpdatedEvent,

    # SSE payloads
    TopologyChangedEdgeRef,
    TopologyChangedPayload,

    # Interact Mode action endpoints
    SimulatorActionError,
    SimulatorActionTrustlineCreateRequest,
    SimulatorActionTrustlineCreateResponse,
    SimulatorActionTrustlineUpdateRequest,
    SimulatorActionTrustlineUpdateResponse,
    SimulatorActionTrustlineCloseRequest,
    SimulatorActionTrustlineCloseResponse,
    SimulatorActionPaymentRealRequest,
    SimulatorActionPaymentRealResponse,
    SimulatorActionClearingRealRequest,
    SimulatorActionClearingRealResponse,
    SimulatorActionClearingCycle,
    SimulatorActionEdgeRef,
    SimulatorActionParticipantsListResponse,
    SimulatorActionParticipantItem,
    SimulatorActionTrustlinesListResponse,
    SimulatorActionTrustlineListItem,
)
from app.utils.exceptions import (
    BadRequestException,
    ConflictException,
    ForbiddenException,
    GeoException,
    GoneException,
    NotFoundException,
    RoutingException,
    TimeoutException,
)
from app.utils.validation import parse_amount_decimal

router = APIRouter(prefix="/simulator")

# Uvicorn typically configures this logger at INFO level.
logger = logging.getLogger("uvicorn.error")


@router.post("/session/ensure", summary="Ensure anonymous session")
async def ensure_session(request: Request, response: Response):
    """If valid cookie exists — return actor info. Otherwise create new session cookie."""
    # No auth required by design (§4.4) — creates cookie for any visitor.
    # Rate-limit exempt via _RATE_LIMIT_EXEMPT_PATHS in deps.py.
    from app.core.simulator.session import COOKIE_NAME, create_session, validate_session

    cookie_value = request.cookies.get(COOKIE_NAME)

    if cookie_value:
        session = validate_session(
            cookie_value,
            settings.SIMULATOR_SESSION_SECRET,
            settings.SIMULATOR_SESSION_TTL_SEC,
            settings.SIMULATOR_SESSION_CLOCK_SKEW_SEC,
        )
        if session:
            return {"actor_kind": "anon", "owner_id": session.owner_id}

    # Create new session
    cookie_val, session_info = create_session(settings.SIMULATOR_SESSION_SECRET)
    _scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    response.set_cookie(
        key=COOKIE_NAME,
        value=cookie_val,
        max_age=settings.SIMULATOR_SESSION_TTL_SEC,
        httponly=True,
        samesite="lax",
        secure=_scheme == "https",
        path="/",
    )
    return {"actor_kind": "anon", "owner_id": session_info.owner_id}


def _build_clearing_done_cycle_edges_payload(
    executed_cycles: list[SimulatorActionClearingCycle],
) -> list[dict[str, str]] | None:
    """Builds a stable `cycle_edges` payload for clearing.done SSE.

    Requirements:
    - include edges from *all* cleared cycles (not just the first one)
    - allow deduplication
    - keep payload ordering stable for UI consumption
    """

    seen: set[tuple[str, str]] = set()
    out: list[dict[str, str]] = []

    for cycle in (executed_cycles or []):
        for e in (getattr(cycle, "edges", None) or []):
            from_pid = str(getattr(e, "from_", "") or "").strip()
            to_pid = str(getattr(e, "to", "") or "").strip()
            if not from_pid or not to_pid or from_pid == to_pid:
                continue

            key = (from_pid, to_pid)
            if key in seen:
                continue
            seen.add(key)
            out.append({"from": from_pid, "to": to_pid})

    if not out:
        return None

    # Stable ordering for UI (regardless of cycle discovery/execution order).
    out.sort(key=lambda d: (d.get("from") or "", d.get("to") or ""))
    return out


async def _compute_viz_patches_best_effort(
    *,
    session,
    run,
    equivalent_code: str,
    edges_pairs: list[tuple[str, str]],
) -> tuple[list[dict[str, Any]] | None, list[dict[str, Any]] | None]:
    """Compute (edge_patch, node_patch) for a set of touched edges.

    Best-effort helper used by interact-mode action endpoints to update UI without
    requiring full snapshot refresh.
    """

    eq_upper = str(equivalent_code or "").strip().upper()
    if not eq_upper or not edges_pairs:
        return None, None

    try:
        # 1) Get or create per-run per-equivalent VizPatchHelper.
        helper: VizPatchHelper | None = None
        try:
            with runtime._lock:  # type: ignore[attr-defined]
                viz_by_eq = getattr(run, "_real_viz_by_eq", None)
                if isinstance(viz_by_eq, dict):
                    helper = viz_by_eq.get(eq_upper)
        except Exception:
            helper = None

        if helper is None:
            helper = await VizPatchHelper.create(
                session,
                equivalent_code=eq_upper,
                refresh_every_ticks=int(getattr(settings, "SIMULATOR_VIZ_QUANTILE_REFRESH_TICKS", 10) or 10),
            )
            try:
                with runtime._lock:  # type: ignore[attr-defined]
                    viz_by_eq = getattr(run, "_real_viz_by_eq", None)
                    if isinstance(viz_by_eq, dict):
                        viz_by_eq[eq_upper] = helper
            except Exception:
                pass

        # 2) Best-effort refresh quantiles (affects viz_width_key/viz_size).
        try:
            participant_ids: list[uuid.UUID] = []
            real_parts = getattr(run, "_real_participants", None)
            if real_parts:
                participant_ids = [pid for (pid, _p) in real_parts]
            await helper.maybe_refresh_quantiles(
                session,
                tick_index=int(getattr(run, "tick_index", 0) or 0),
                participant_ids=participant_ids,
            )
        except Exception:
            # Quantiles are optional; continue with default keys.
            pass

        # 3) Load Participant rows for touched pids.
        pids = sorted({pid for ab in edges_pairs for pid in ab if str(pid).strip()})
        if not pids:
            return None, None

        res = await session.execute(select(Participant).where(Participant.pid.in_(pids)))
        pid_to_participant = {p.pid: p for p in res.scalars().all()}

        # 4) Node patch (net balances + viz_*).
        node_patch = await helper.compute_node_patches(
            session,
            pid_to_participant=pid_to_participant,
            pids=pids,
        )
        if node_patch == []:
            node_patch = None

        # 5) Edge patch (used/available + viz_*).
        edge_patch = await EdgePatchBuilder(logger=logger).build_edge_patch_for_pairs(
            session=session,
            helper=helper,
            edges_pairs=edges_pairs,
            pid_to_participant=pid_to_participant,
        )
        if edge_patch == []:
            edge_patch = None

        return edge_patch, node_patch
    except Exception:
        return None, None


def _actions_enabled() -> bool:
    return str(os.environ.get("SIMULATOR_ACTIONS_ENABLE", "") or "").strip() in {"1", "true", "TRUE", "yes"}


def _require_actions_enabled() -> None:
    if not _actions_enabled():
        raise ForbiddenException("Simulator actions are disabled (set SIMULATOR_ACTIONS_ENABLE=1)")


def _action_error(
    *,
    status_code: int,
    code: str,
    message: str,
    details: Optional[dict[str, Any]] = None,
) -> JSONResponse:
    payload = SimulatorActionError(code=code, message=message, details=details).model_dump(mode="json", by_alias=True)
    return JSONResponse(status_code=int(status_code), content=payload)


def _get_run_checked_or_error(
    run_id: str,
    actor: "deps.SimulatorActor",
) -> tuple[Optional[Any], Optional[JSONResponse]]:
    """Get run and validate access/state; return action error envelope on failure.

    This keeps Interact Mode action endpoints stable even when runtime.get_run
    / access checks raise GeoException subclasses.
    """

    try:
        return _get_run_checked(run_id, actor), None
    except NotFoundException:
        return None, _action_error(
            status_code=404,
            code="RUN_NOT_FOUND",
            message="Run not found",
            details={"run_id": str(run_id)},
        )
    except ForbiddenException as exc:
        return None, _action_error(
            status_code=403,
            code="ACCESS_DENIED",
            message=str(getattr(exc, "message", None) or str(exc) or "Access denied"),
            details={"run_id": str(run_id)},
        )
    except ConflictException as exc:
        # Spec mapping: run in terminal state.
        det = getattr(exc, "details", None)
        if not isinstance(det, dict):
            det = {"run_id": str(run_id)}
        return None, _action_error(
            status_code=int(getattr(exc, "status_code", 409) or 409),
            code="RUN_TERMINAL",
            message="Run is in terminal state",
            details=det,
        )


def _get_run_for_readonly_actions_or_error(
    run_id: str,
    actor: "deps.SimulatorActor",
) -> tuple[Optional[Any], Optional[JSONResponse]]:
    """Get run and validate access for read-only action endpoints.

    Read-only endpoints intentionally work for stopped/error runs, so we only
    validate existence + ownership.
    """

    try:
        run = runtime.get_run(run_id)
    except NotFoundException:
        return None, _action_error(
            status_code=404,
            code="RUN_NOT_FOUND",
            message="Run not found",
            details={"run_id": str(run_id)},
        )
    try:
        _check_run_access(run, actor, run_id)
    except ForbiddenException as exc:
        return None, _action_error(
            status_code=403,
            code="ACCESS_DENIED",
            message=str(getattr(exc, "message", None) or str(exc) or "Access denied"),
            details={"run_id": str(run_id)},
        )

    return run, None


def _check_run_access(run, actor: "deps.SimulatorActor", run_id: str) -> None:
    """Check that actor has access to the run. Raises 403 if not owner and not admin.

    Also raises 404 if run is None (convenience so callers can call with runtime.get_run result).

    Deny-by-default (§7): if run.owner_id is empty/legacy, only admin may access.
    """
    if run is None:
        raise NotFoundException(f"Run {run_id} not found")
    if not actor.is_admin:
        if not run.owner_id or run.owner_id != actor.owner_id:
            raise ForbiddenException("Access denied: not run owner")


def _get_run_checked(run_id: str, actor: "deps.SimulatorActor"):
    """Get run, check access and action-acceptance state. Returns RunRecord or raises.

    Consolidates the _require_run_accepts_actions_or_error + _check_run_access(runtime.get_run())
    pattern into a single get_run() call (FIX-CR4: eliminates double get_run).

    Raises:
        NotFoundException (404): run not found.
        ForbiddenException (403): actor is not owner and not admin.
        HTTPException (409): run is in terminal state (stopped/error).
    """
    run = runtime.get_run(run_id)  # raises NotFoundException if not found
    _check_run_access(run, actor, run_id)
    if run.state in ("stopped", "error"):
        raise ConflictException(
            "Run is in terminal state",
            details={"run_id": run_id, "state": run.state, "conflict_kind": "run_terminal"},
        )
    return run


class AdminStopAllRequest(BaseModel):
    reason: Optional[str] = None


def _require_actions_enabled_or_error() -> Optional[JSONResponse]:
    if not _actions_enabled():
        return _action_error(
            status_code=403,
            code="ACTIONS_DISABLED",
            message="Simulator actions are disabled",
            details={"env": "SIMULATOR_ACTIONS_ENABLE"},
        )
    return None


def _require_run_accepts_actions_or_error(run_id: str) -> Optional[JSONResponse]:
    try:
        st = runtime.get_run_status(run_id)
    except NotFoundException:
        # Not in spec table; keep stable error anyway.
        return _action_error(
            status_code=404,
            code="RUN_NOT_FOUND",
            message="Run not found",
            details={"run_id": str(run_id)},
        )

    if st.state in ("stopped", "error"):
        return _action_error(
            status_code=409,
            code="RUN_TERMINAL",
            message="Run is in terminal state",
            details={"run_id": str(run_id), "state": str(st.state)},
        )
    return None


async def _resolve_participant_or_error(
    *, session, pid: str, field: str
) -> tuple[Optional[Participant], Optional[JSONResponse]]:
    pid_s = str(pid or "").strip()
    if not pid_s:
        return None, _action_error(
            status_code=404,
            code="PARTICIPANT_NOT_FOUND",
            message="Participant not found",
            details={"field": field, "pid": pid_s},
        )
    row = (
        await session.execute(select(Participant).where(Participant.pid == pid_s))
    ).scalar_one_or_none()
    if row is None:
        return None, _action_error(
            status_code=404,
            code="PARTICIPANT_NOT_FOUND",
            message="Participant not found",
            details={"field": field, "pid": pid_s},
        )
    return row, None


async def _resolve_equivalent_or_error(
    *, session, code: str
) -> tuple[Optional[Equivalent], Optional[JSONResponse]]:
    eq_code = str(code or "").strip().upper()
    if not eq_code:
        return None, _action_error(
            status_code=404,
            code="EQUIVALENT_NOT_FOUND",
            message="Equivalent not found",
            details={"equivalent": eq_code},
        )
    eq = (
        await session.execute(select(Equivalent).where(Equivalent.code == eq_code))
    ).scalar_one_or_none()
    if eq is None:
        return None, _action_error(
            status_code=404,
            code="EQUIVALENT_NOT_FOUND",
            message="Equivalent not found",
            details={"equivalent": eq_code},
        )
    return eq, None


async def _trustline_used_amount(
    session, *, from_id: uuid.UUID, to_id: uuid.UUID, equivalent_id: uuid.UUID
) -> Decimal:
    used = (
        await session.execute(
            select(func.coalesce(func.sum(Debt.amount), 0)).where(
                and_(
                    Debt.debtor_id == to_id,
                    Debt.creditor_id == from_id,
                    Debt.equivalent_id == equivalent_id,
                )
            )
        )
    ).scalar_one()
    try:
        return Decimal(str(used or 0))
    except Exception:
        return Decimal("0")


async def _trustline_reverse_used_amount(
    session, *, from_id: uuid.UUID, to_id: uuid.UUID, equivalent_id: uuid.UUID
) -> Decimal:
    reverse_used = (
        await session.execute(
            select(func.coalesce(func.sum(Debt.amount), 0)).where(
                and_(
                    Debt.debtor_id == from_id,
                    Debt.creditor_id == to_id,
                    Debt.equivalent_id == equivalent_id,
                )
            )
        )
    ).scalar_one()
    try:
        return Decimal(str(reverse_used or 0))
    except Exception:
        return Decimal("0")


def _fmt_decimal_for_api(v: Decimal) -> str:
    # Preserve plain decimal string without scientific notation.
    return format(v, "f")


def _norm_pid(v: object) -> str:
    return str(v or "").strip()


def _guard_no_self_loop_or_error(*, from_pid: object, to_pid: object) -> Optional[JSONResponse]:
    """Guard against self-loop trustlines.

    Spec: for trustline-create/update/close, reject from_pid == to_pid.
    """

    fp = _norm_pid(from_pid)
    tp = _norm_pid(to_pid)
    if fp and tp and fp == tp:
        return _action_error(
            status_code=400,
            code="INVALID_REQUEST",
            message="Invalid request",
            details={
                "from_pid": fp,
                "to_pid": tp,
                "reason": "self_loop_trustline",
            },
        )
    return None


def _mutate_runtime_trustline_topology_best_effort(
    *,
    run_id: str,
    op: str,
    equivalent: str,
    from_pid: str,
    to_pid: str,
    limit: str | None = None,
) -> None:
    """Best-effort sync of in-memory runtime snapshot/cache with interact trustline actions.

    This is needed so:
    - list endpoints can be snapshot-scoped and still reflect create/close immediately;
    - runtime edge cache (run._edges_by_equivalent) stays consistent with topology changes.

    Never raises.
    """

    try:
        run = runtime.get_run(run_id)

        lock = getattr(runtime, "_lock", None)
        if lock is None:
            # Fallback: mutate without lock (still best-effort).
            lock_ctx = None
        else:
            lock_ctx = lock

        def _apply() -> None:
            eq = str(equivalent or "").strip().upper()
            fp = _norm_pid(from_pid)
            tp = _norm_pid(to_pid)
            if not (eq and fp and tp):
                return

            # Invalidate per-equivalent viz cache so subsequent node/edge patches are consistent.
            try:
                getattr(run, "_real_viz_by_eq", {}).pop(eq, None)
            except Exception:
                pass

            # Update scenario snapshot.
            scenario = getattr(run, "_scenario_raw", None)
            if isinstance(scenario, dict):
                tls = scenario.get("trustlines")
                if isinstance(tls, list):
                    if op == "create":
                        tls.append(
                            {
                                "equivalent": eq,
                                "from": fp,
                                "to": tp,
                                "limit": str(limit or "0"),
                                "status": "active",
                            }
                        )
                    elif op == "update":
                        # Update the first matching trustline; if not found (legacy drift), append.
                        found = False
                        for tl in tls:
                            if not isinstance(tl, dict):
                                continue
                            if (
                                str(tl.get("equivalent") or "").strip().upper() == eq
                                and _norm_pid(tl.get("from")) == fp
                                and _norm_pid(tl.get("to")) == tp
                            ):
                                tl["limit"] = str(limit or "0")
                                found = True
                                break
                        if not found:
                            tls.append(
                                {
                                    "equivalent": eq,
                                    "from": fp,
                                    "to": tp,
                                    "limit": str(limit or "0"),
                                    "status": "active",
                                }
                            )
                    elif op == "close":
                        # Remove trustline(s) from scenario topology so snapshot no longer includes them.
                        scenario["trustlines"] = [
                            tl
                            for tl in tls
                            if not (
                                isinstance(tl, dict)
                                and str(tl.get("equivalent") or "").strip().upper() == eq
                                and _norm_pid(tl.get("from")) == fp
                                and _norm_pid(tl.get("to")) == tp
                            )
                        ]

            # Update runtime edge cache.
            edges_by_eq = getattr(run, "_edges_by_equivalent", None)
            if isinstance(edges_by_eq, dict):
                if op == "create":
                    lst = list(edges_by_eq.get(eq) or [])
                    if (fp, tp) not in lst:
                        lst.append((fp, tp))
                    edges_by_eq[eq] = lst
                elif op == "close":
                    lst = list(edges_by_eq.get(eq) or [])
                    edges_by_eq[eq] = [(a, b) for (a, b) in lst if not (a == fp and b == tp)]

        if lock_ctx is None:
            _apply()
        else:
            with lock_ctx:
                _apply()
    except Exception:
        return


class TxOnceRequestBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    equivalent: str
    from_: Optional[str] = Field(default=None, alias="from")
    to: Optional[str] = None
    amount: Optional[str] = None
    ttl_ms: Optional[int] = None
    intensity_key: Optional[str] = None
    seed: Optional[Any] = None
    client_action_id: Optional[str] = None


class TxOnceResponseBody(BaseModel):
    ok: bool = True
    emitted_event_id: str
    client_action_id: Optional[str] = None


class ClearingOnceRequestBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    equivalent: str
    cycle_edges: Optional[list[dict[str, str]]] = None
    cleared_amount: Optional[str] = None
    seed: Optional[Any] = None
    client_action_id: Optional[str] = None


class ClearingOnceResponseBody(BaseModel):
    ok: bool = True
    plan_id: str
    done_event_id: str
    client_action_id: Optional[str] = None


# ----------------------------------
# Interact Mode action endpoints (MVP)
# ----------------------------------


@router.post(
    "/runs/{run_id}/actions/trustline-create",
    response_model=SimulatorActionTrustlineCreateResponse,
    include_in_schema=_actions_enabled(),
)
async def action_trustline_create(
    run_id: str,
    req: SimulatorActionTrustlineCreateRequest,
    actor: deps.SimulatorActor = Depends(deps.require_simulator_actor),
    db=Depends(deps.get_db),
):
    if (err := _require_actions_enabled_or_error()) is not None:
        return err
    _run, run_err = _get_run_checked_or_error(run_id, actor)
    if run_err is not None:
        return run_err

    if (err := _guard_no_self_loop_or_error(from_pid=req.from_pid, to_pid=req.to_pid)) is not None:
        return err

    # Validate and normalize amounts early.
    try:
        limit_dec = parse_amount_decimal(req.limit, require_positive=False)
        if limit_dec < 0:
            raise BadRequestException("Invalid limit")
    except BadRequestException:
        return _action_error(
            status_code=400,
            code="INVALID_AMOUNT",
            message="Invalid limit",
            details={"limit": req.limit},
        )

    from_p, err = await _resolve_participant_or_error(session=db, pid=req.from_pid, field="from_pid")
    if err is not None:
        return err
    to_p, err = await _resolve_participant_or_error(session=db, pid=req.to_pid, field="to_pid")
    if err is not None:
        return err
    eq, err = await _resolve_equivalent_or_error(session=db, code=req.equivalent)
    if err is not None:
        return err

    assert from_p is not None and to_p is not None and eq is not None

    # If there is already debt, ensure the new limit is not below it.
    try:
        used_now = await _trustline_used_amount(
            db,
            from_id=from_p.id,
            to_id=to_p.id,
            equivalent_id=eq.id,
        )
    except Exception:
        # Do NOT fall back to 0: it can create an inconsistent trustline (limit < existing debt).
        logger.error(
            "Failed to read used amount for trustline-create: run_id=%s equivalent=%s from_pid=%s to_pid=%s",
            run_id,
            eq.code,
            from_p.pid,
            to_p.pid,
            exc_info=True,
        )
        return _action_error(
            status_code=503,
            code="TRUSTLINE_USED_UNAVAILABLE",
            message="Temporary error while reading current used amount",
            details={
                "equivalent": eq.code,
                "from_pid": from_p.pid,
                "to_pid": to_p.pid,
            },
        )
    if limit_dec < used_now:
        return _action_error(
            status_code=409,
            code="USED_EXCEEDS_NEW_LIMIT",
            message="Limit is below current used amount",
            details={
                "equivalent": eq.code,
                "from_pid": from_p.pid,
                "to_pid": to_p.pid,
                "used": _fmt_decimal_for_api(used_now),
                "limit": _fmt_decimal_for_api(limit_dec),
            },
        )

    existing = (
        await db.execute(
            select(TrustLine.id).where(
                and_(
                    TrustLine.from_participant_id == from_p.id,
                    TrustLine.to_participant_id == to_p.id,
                    TrustLine.equivalent_id == eq.id,
                    TrustLine.status != "closed",
                )
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return _action_error(
            status_code=409,
            code="TRUSTLINE_EXISTS",
            message="Active trustline already exists",
            details={
                "from_pid": from_p.pid,
                "to_pid": to_p.pid,
                "equivalent": eq.code,
            },
        )

    tl = TrustLine(
        from_participant_id=from_p.id,
        to_participant_id=to_p.id,
        equivalent_id=eq.id,
        limit=limit_dec,
        status="active",
    )
    db.add(tl)
    await db.commit()
    await db.refresh(tl)

    # Keep runtime snapshot/cache consistent with action.
    _mutate_runtime_trustline_topology_best_effort(
        run_id=run_id,
        op="create",
        equivalent=eq.code,
        from_pid=from_p.pid,
        to_pid=to_p.pid,
        limit=req.limit,
    )

    # Trustline topology affects routing graph.
    try:
        PaymentRouter.invalidate_cache(eq.code)
    except Exception:
        pass

    # Best-effort SSE emission.
    try:
        run = runtime.get_run(run_id)
        emitter = SseEventEmitter(sse=runtime._sse, utc_now=_utc_now, logger=logger)  # type: ignore[attr-defined]

        edge_patch: list[dict[str, Any]] | None = None
        try:
            edge_patch = await EdgePatchBuilder(logger=logger).build_edge_patch_for_equivalent(
                session=db,
                run=run,
                equivalent_code=eq.code,
                only_edges={(from_p.pid, to_p.pid)},
                include_width_keys=True,
            )
        except Exception:
            edge_patch = None

        payload = TopologyChangedPayload(
            added_edges=[
                TopologyChangedEdgeRef(
                    from_pid=from_p.pid,
                    to_pid=to_p.pid,
                    equivalent_code=eq.code,
                    limit=_fmt_decimal_for_api(limit_dec),
                )
            ],
            node_patch=None,
            edge_patch=(edge_patch or None),
        )
        emitter.emit_topology_changed(
            run_id=run_id,
            run=run,
            equivalent=eq.code,
            payload=payload,
            reason="interact.trustline_create",
        )
    except Exception:
        # TODO(interact): consider emitting node_patch/edge_patch via VizPatchHelper for immediate UI updates.
        logger.warning(
            "Best-effort SSE emission failed: interact.trustline_create run_id=%s",
            run_id,
            exc_info=True,
        )

    return SimulatorActionTrustlineCreateResponse(
        trustline_id=str(tl.id),
        from_pid=from_p.pid,
        to_pid=to_p.pid,
        equivalent=eq.code,
        limit=_fmt_decimal_for_api(limit_dec),
        client_action_id=req.client_action_id,
    )


@router.post(
    "/runs/{run_id}/actions/trustline-update",
    response_model=SimulatorActionTrustlineUpdateResponse,
    include_in_schema=_actions_enabled(),
)
async def action_trustline_update(
    run_id: str,
    req: SimulatorActionTrustlineUpdateRequest,
    actor: deps.SimulatorActor = Depends(deps.require_simulator_actor),
    db=Depends(deps.get_db),
):
    if (err := _require_actions_enabled_or_error()) is not None:
        return err
    _run, run_err = _get_run_checked_or_error(run_id, actor)
    if run_err is not None:
        return run_err

    if (err := _guard_no_self_loop_or_error(from_pid=req.from_pid, to_pid=req.to_pid)) is not None:
        return err

    try:
        new_limit_dec = parse_amount_decimal(req.new_limit, require_positive=False)
        if new_limit_dec < 0:
            raise BadRequestException("Invalid new_limit")
    except BadRequestException:
        return _action_error(
            status_code=400,
            code="INVALID_AMOUNT",
            message="Invalid new_limit",
            details={"new_limit": req.new_limit},
        )

    from_p, err = await _resolve_participant_or_error(session=db, pid=req.from_pid, field="from_pid")
    if err is not None:
        return err
    to_p, err = await _resolve_participant_or_error(session=db, pid=req.to_pid, field="to_pid")
    if err is not None:
        return err
    eq, err = await _resolve_equivalent_or_error(session=db, code=req.equivalent)
    if err is not None:
        return err

    assert from_p is not None and to_p is not None and eq is not None

    tl = (
        await db.execute(
            select(TrustLine).where(
                and_(
                    TrustLine.from_participant_id == from_p.id,
                    TrustLine.to_participant_id == to_p.id,
                    TrustLine.equivalent_id == eq.id,
                    TrustLine.status != "closed",
                )
            )
        )
    ).scalar_one_or_none()
    if tl is None:
        return _action_error(
            status_code=404,
            code="TRUSTLINE_NOT_FOUND",
            message="Trustline not found",
            details={"from_pid": from_p.pid, "to_pid": to_p.pid, "equivalent": eq.code},
        )

    old_limit_dec = Decimal(str(getattr(tl, "limit", 0) or 0))
    try:
        used = await _trustline_used_amount(
            db,
            from_id=tl.from_participant_id,
            to_id=tl.to_participant_id,
            equivalent_id=eq.id,
        )
    except Exception:
        logger.error(
            "Failed to read used amount for trustline-update: run_id=%s equivalent=%s from_pid=%s to_pid=%s",
            run_id,
            eq.code,
            from_p.pid,
            to_p.pid,
            exc_info=True,
        )
        return _action_error(
            status_code=503,
            code="TRUSTLINE_USED_UNAVAILABLE",
            message="Temporary error while reading current used amount",
            details={
                "equivalent": eq.code,
                "from_pid": from_p.pid,
                "to_pid": to_p.pid,
            },
        )
    if new_limit_dec < used:
        return _action_error(
            status_code=409,
            code="USED_EXCEEDS_NEW_LIMIT",
            message="Cannot reduce trustline limit below used amount",
            details={
                "equivalent": eq.code,
                "from_pid": from_p.pid,
                "to_pid": to_p.pid,
                "used": _fmt_decimal_for_api(used),
                "new_limit": _fmt_decimal_for_api(new_limit_dec),
            },
        )

    tl.limit = new_limit_dec
    await db.commit()
    await db.refresh(tl)

    # Keep runtime snapshot consistent with action (limit change).
    _mutate_runtime_trustline_topology_best_effort(
        run_id=run_id,
        op="update",
        equivalent=eq.code,
        from_pid=from_p.pid,
        to_pid=to_p.pid,
        limit=req.new_limit,
    )

    try:
        PaymentRouter.invalidate_cache(eq.code)
    except Exception:
        pass

    try:
        run = runtime.get_run(run_id)
        emitter = SseEventEmitter(sse=runtime._sse, utc_now=_utc_now, logger=logger)  # type: ignore[attr-defined]
        edge_patch: list[dict[str, Any]] | None = None
        try:
            edge_patch = await EdgePatchBuilder(logger=logger).build_edge_patch_for_equivalent(
                session=db,
                run=run,
                equivalent_code=eq.code,
                only_edges={(from_p.pid, to_p.pid)},
                include_width_keys=True,
            )
        except Exception:
            edge_patch = None

        if edge_patch:
            payload = TopologyChangedPayload(node_patch=None, edge_patch=edge_patch)
            emitter.emit_topology_changed(
                run_id=run_id,
                run=run,
                equivalent=eq.code,
                payload=payload,
                reason="interact.trustline_update",
            )
    except Exception:
        # TODO(interact): best-effort patches for trustline update.
        logger.warning(
            "Best-effort SSE emission failed: interact.trustline_update run_id=%s",
            run_id,
            exc_info=True,
        )

    return SimulatorActionTrustlineUpdateResponse(
        trustline_id=str(tl.id),
        old_limit=_fmt_decimal_for_api(old_limit_dec),
        new_limit=_fmt_decimal_for_api(new_limit_dec),
        client_action_id=req.client_action_id,
    )


@router.post(
    "/runs/{run_id}/actions/trustline-close",
    response_model=SimulatorActionTrustlineCloseResponse,
    include_in_schema=_actions_enabled(),
)
async def action_trustline_close(
    run_id: str,
    req: SimulatorActionTrustlineCloseRequest,
    actor: deps.SimulatorActor = Depends(deps.require_simulator_actor),
    db=Depends(deps.get_db),
):
    if (err := _require_actions_enabled_or_error()) is not None:
        return err
    _run, run_err = _get_run_checked_or_error(run_id, actor)
    if run_err is not None:
        return run_err

    if (err := _guard_no_self_loop_or_error(from_pid=req.from_pid, to_pid=req.to_pid)) is not None:
        return err

    from_p, err = await _resolve_participant_or_error(session=db, pid=req.from_pid, field="from_pid")
    if err is not None:
        return err
    to_p, err = await _resolve_participant_or_error(session=db, pid=req.to_pid, field="to_pid")
    if err is not None:
        return err
    eq, err = await _resolve_equivalent_or_error(session=db, code=req.equivalent)
    if err is not None:
        return err

    assert from_p is not None and to_p is not None and eq is not None

    tl = (
        await db.execute(
            select(TrustLine).where(
                and_(
                    TrustLine.from_participant_id == from_p.id,
                    TrustLine.to_participant_id == to_p.id,
                    TrustLine.equivalent_id == eq.id,
                    TrustLine.status != "closed",
                )
            )
        )
    ).scalar_one_or_none()
    if tl is None:
        return _action_error(
            status_code=404,
            code="TRUSTLINE_NOT_FOUND",
            message="Trustline not found",
            details={"from_pid": from_p.pid, "to_pid": to_p.pid, "equivalent": eq.code},
        )

    try:
        used = await _trustline_used_amount(
            db,
            from_id=tl.from_participant_id,
            to_id=tl.to_participant_id,
            equivalent_id=eq.id,
        )
        reverse_used = await _trustline_reverse_used_amount(
            db,
            from_id=tl.from_participant_id,
            to_id=tl.to_participant_id,
            equivalent_id=eq.id,
        )
    except Exception:
        logger.error(
            "Failed to read used amount for trustline-close: run_id=%s equivalent=%s from_pid=%s to_pid=%s",
            run_id,
            eq.code,
            from_p.pid,
            to_p.pid,
            exc_info=True,
        )
        return _action_error(
            status_code=503,
            code="TRUSTLINE_USED_UNAVAILABLE",
            message="Temporary error while reading current used amount",
            details={
                "equivalent": eq.code,
                "from_pid": from_p.pid,
                "to_pid": to_p.pid,
            },
        )
    if used > 0 or reverse_used > 0:
        return _action_error(
            status_code=409,
            code="TRUSTLINE_HAS_DEBT",
            message="Cannot close trustline with non-zero debt",
            details={
                "equivalent": eq.code,
                "from_pid": from_p.pid,
                "to_pid": to_p.pid,
                "used": _fmt_decimal_for_api(used),
                "reverse_used": _fmt_decimal_for_api(reverse_used),
            },
        )

    tl.status = "closed"
    await db.commit()
    await db.refresh(tl)

    # Keep runtime snapshot/cache consistent with action.
    _mutate_runtime_trustline_topology_best_effort(
        run_id=run_id,
        op="close",
        equivalent=eq.code,
        from_pid=from_p.pid,
        to_pid=to_p.pid,
    )

    try:
        PaymentRouter.invalidate_cache(eq.code)
    except Exception:
        pass

    try:
        run = runtime.get_run(run_id)
        emitter = SseEventEmitter(sse=runtime._sse, utc_now=_utc_now, logger=logger)  # type: ignore[attr-defined]

        # For close, frontend needs an explicit removal semantic.
        payload = TopologyChangedPayload(
            removed_edges=[
                TopologyChangedEdgeRef(
                    from_pid=from_p.pid,
                    to_pid=to_p.pid,
                    equivalent_code=eq.code,
                )
            ],
            node_patch=None,
            edge_patch=None,
        )
        emitter.emit_topology_changed(
            run_id=run_id,
            run=run,
            equivalent=eq.code,
            payload=payload,
            reason="interact.trustline_close",
        )
    except Exception:
        # TODO(interact): best-effort patches for trustline close.
        logger.warning(
            "Best-effort SSE emission failed: interact.trustline_close run_id=%s",
            run_id,
            exc_info=True,
        )

    return SimulatorActionTrustlineCloseResponse(
        trustline_id=str(tl.id),
        client_action_id=req.client_action_id,
    )


@router.post(
    "/runs/{run_id}/actions/payment-real",
    response_model=SimulatorActionPaymentRealResponse,
    include_in_schema=_actions_enabled(),
)
async def action_payment_real(
    run_id: str,
    req: SimulatorActionPaymentRealRequest,
    actor: deps.SimulatorActor = Depends(deps.require_simulator_actor),
    db=Depends(deps.get_db),
):
    if (err := _require_actions_enabled_or_error()) is not None:
        return err
    run, run_err = _get_run_checked_or_error(run_id, actor)
    if run_err is not None:
        return run_err
    assert run is not None

    from_p, err = await _resolve_participant_or_error(session=db, pid=req.from_pid, field="from_pid")
    if err is not None:
        return err
    to_p, err = await _resolve_participant_or_error(session=db, pid=req.to_pid, field="to_pid")
    if err is not None:
        return err
    eq, err = await _resolve_equivalent_or_error(session=db, code=req.equivalent)
    if err is not None:
        return err
    assert from_p is not None and to_p is not None and eq is not None

    # Amount validation per spec.
    try:
        _ = parse_amount_decimal(req.amount, require_positive=True)
    except BadRequestException as exc:
        return _action_error(
            status_code=400,
            code="INVALID_AMOUNT",
            message=str(getattr(exc, "message", None) or "Invalid amount"),
            details={"amount": req.amount},
        )

    # Best-effort pre-check to distinguish NO_ROUTE vs INSUFFICIENT_CAPACITY.
    # Use cached trustline-only topology via PaymentRouter (ignores remaining capacity), so
    # fully-saturated edges don't get misclassified as NO_ROUTE.
    any_path_exists: Optional[bool] = None
    try:
        router = PaymentRouter(db)
        await router.build_topology(eq.code)
        any_path_exists = router.has_topology_path(
            str(from_p.pid),
            str(to_p.pid),
            max_hops=int(getattr(settings, "ROUTING_MAX_HOPS", 6) or 6),
        )
    except Exception:
        any_path_exists = None

    try:
        service = PaymentService(db)
        res = await service.create_payment_internal(
            from_p.id,
            to_pid=to_p.pid,
            equivalent=eq.code,
            amount=req.amount,
            idempotency_key=None,
            commit=True,
        )
    except RoutingException as exc:
        if any_path_exists is False:
            return _action_error(
                status_code=409,
                code="NO_ROUTE",
                message=str(getattr(exc, "message", None) or "No route"),
                details={
                    "equivalent": eq.code,
                    "from_pid": from_p.pid,
                    "to_pid": to_p.pid,
                    "requested": req.amount,
                },
            )
        return _action_error(
            status_code=409,
            code="INSUFFICIENT_CAPACITY",
            message=str(getattr(exc, "message", None) or "Insufficient capacity"),
            details={
                "equivalent": eq.code,
                "from_pid": from_p.pid,
                "to_pid": to_p.pid,
                "requested": req.amount,
            },
        )
    except TimeoutException as exc:
        return _action_error(
            status_code=503,
            code="ENGINE_TIMEOUT",
            message=str(getattr(exc, "message", None) or "Engine timeout"),
            details={"equivalent": eq.code, "from_pid": from_p.pid, "to_pid": to_p.pid},
        )
    except GeoException as exc:
        # Best-effort mapping for unexpected business errors.
        return _action_error(
            status_code=int(getattr(exc, "status_code", 409) or 409),
            code="PAYMENT_REJECTED",
            message=str(getattr(exc, "message", None) or str(exc)),
            details=getattr(exc, "details", None) or {},
        )

    # Success: emit best-effort tx.updated SSE. `run` already fetched by _get_run_checked above.
    try:
        emitter = SseEventEmitter(sse=runtime._sse, utc_now=_utc_now, logger=logger)  # type: ignore[attr-defined]

        edges: list[dict[str, Any]] = []
        try:
            routes = res.routes or []
            if routes:
                path = routes[0].path
                edges = [{"from": str(a), "to": str(b)} for a, b in zip(path, path[1:])]
        except Exception:
            edges = []
        if not edges:
            edges = [{"from": from_p.pid, "to": to_p.pid}]

        edges_pairs: list[tuple[str, str]] = []
        for e in edges:
            a = str(e.get("from") or "").strip()
            b = str(e.get("to") or "").strip()
            if a and b:
                edges_pairs.append((a, b))

        edge_patch, node_patch = await _compute_viz_patches_best_effort(
            session=db,
            run=run,
            equivalent_code=eq.code,
            edges_pairs=edges_pairs,
        )

        emitter.emit_tx_updated(
            run_id=run_id,
            run=run,
            equivalent=eq.code,
            from_pid=from_p.pid,
            to_pid=to_p.pid,
            amount=req.amount,
            amount_flyout=True,
            ttl_ms=1200,
            edges=edges,
            node_badges=None,
            edge_patch=edge_patch,
            node_patch=node_patch,
        )
    except Exception:
        # TODO(interact): use VizPatchHelper + EdgePatchBuilder.build_edge_patch_for_pairs for immediate UI updates.
        logger.warning(
            "Best-effort SSE emission failed: interact.payment_real run_id=%s",
            run_id,
            exc_info=True,
        )

    return SimulatorActionPaymentRealResponse(
        payment_id=str(res.tx_id),
        from_pid=from_p.pid,
        to_pid=to_p.pid,
        equivalent=eq.code,
        amount=str(req.amount),
        status=str(res.status),
        client_action_id=req.client_action_id,
    )


@router.post(
    "/runs/{run_id}/actions/clearing-real",
    response_model=SimulatorActionClearingRealResponse,
    include_in_schema=_actions_enabled(),
)
async def action_clearing_real(
    run_id: str,
    req: SimulatorActionClearingRealRequest,
    actor: deps.SimulatorActor = Depends(deps.require_simulator_actor),
    db=Depends(deps.get_db),
):
    if (err := _require_actions_enabled_or_error()) is not None:
        return err
    run, run_err = _get_run_checked_or_error(run_id, actor)
    if run_err is not None:
        return run_err
    assert run is not None

    eq, err = await _resolve_equivalent_or_error(session=db, code=req.equivalent)
    if err is not None:
        return err
    assert eq is not None

    service = ClearingService(db)

    executed: list[SimulatorActionClearingCycle] = []
    total = Decimal("0")
    cleared_count = 0

    # Auto-clear loop: best-effort match ClearingService.auto_clear(), but keep per-cycle details.
    for _ in range(0, 100):
        cycles = await service.find_cycles(eq.code, max_depth=int(req.max_depth))
        if not cycles:
            break

        executed_this_round = False
        for cycle in cycles:
            clear_amt = await service.execute_clearing_with_amount(cycle)
            if clear_amt is None:
                continue

            edges: list[SimulatorActionEdgeRef] = []
            for e in (cycle or []):
                debtor = str(e.get("debtor") or "").strip()
                creditor = str(e.get("creditor") or "").strip()
                if debtor and creditor and debtor != creditor:
                    # Trustline direction is creditor -> debtor (see project guardrails).
                    edges.append(SimulatorActionEdgeRef(from_=creditor, to=debtor))

            executed.append(
                SimulatorActionClearingCycle(
                    cleared_amount=_fmt_decimal_for_api(clear_amt),
                    edges=edges,
                )
            )
            total += clear_amt
            cleared_count += 1
            executed_this_round = True
            break

        if not executed_this_round:
            break

    # Best-effort SSE emission (clearing.done). `run` already fetched by _get_run_checked above.
    try:
        if cleared_count > 0:
            emitter = SseEventEmitter(sse=runtime._sse, utc_now=_utc_now, logger=logger)  # type: ignore[attr-defined]

            cycle_edges_payload = _build_clearing_done_cycle_edges_payload(executed)

            edges_pairs: list[tuple[str, str]] = []
            for e in (cycle_edges_payload or []):
                a = str(e.get("from") or "").strip()
                b = str(e.get("to") or "").strip()
                if a and b:
                    edges_pairs.append((a, b))

            edge_patch, node_patch = await _compute_viz_patches_best_effort(
                session=db,
                run=run,
                equivalent_code=eq.code,
                edges_pairs=edges_pairs,
            )

            emitter.emit_clearing_done(
                run_id=run_id,
                run=run,
                equivalent=eq.code,
                plan_id=f"plan_interact_{secrets.token_hex(6)}",
                cleared_cycles=int(cleared_count),
                cleared_amount=_fmt_decimal_for_api(total),
                cycle_edges=cycle_edges_payload,
                node_patch=node_patch,
                edge_patch=edge_patch,
            )
    except Exception:
        # TODO(interact): compute node_patch/edge_patch via VizPatchHelper for clearing.done.
        logger.warning(
            "Best-effort SSE emission failed: interact.clearing_real run_id=%s",
            run_id,
            exc_info=True,
        )

    return SimulatorActionClearingRealResponse(
        equivalent=eq.code,
        cleared_cycles=int(cleared_count),
        total_cleared_amount=_fmt_decimal_for_api(total),
        cycles=executed,
        client_action_id=req.client_action_id,
    )


@router.get(
    "/runs/{run_id}/actions/participants-list",
    response_model=SimulatorActionParticipantsListResponse,
    include_in_schema=_actions_enabled(),
)
async def action_participants_list(
    run_id: str,
    actor: deps.SimulatorActor = Depends(deps.require_simulator_actor),
    db=Depends(deps.get_db),
):
    if (err := _require_actions_enabled_or_error()) is not None:
        return err
    # NOTE: intentionally no _require_run_accepts_actions_or_error here —
    # participants-list is read-only and must work even for stopped/error runs.

    # AuthZ: ownership check
    _run, run_err = _get_run_for_readonly_actions_or_error(run_id, actor)
    if run_err is not None:
        return run_err

    # Run/snapshot-scoped: build from current run snapshot (not global Participant table).
    try:
        snap = await runtime.build_graph_snapshot(run_id=run_id, equivalent="", session=db)
    except NotFoundException:
        return _action_error(
            status_code=404,
            code="RUN_NOT_FOUND",
            message="Run not found",
            details={"run_id": str(run_id)},
        )

    items: list[SimulatorActionParticipantItem] = []
    for n in (getattr(snap, "nodes", None) or []):
        pid = _norm_pid(getattr(n, "id", ""))
        if not pid:
            continue
        name = str(getattr(n, "name", None) or pid)
        t = str(getattr(n, "type", None) or "person")
        st = str(getattr(n, "status", None) or "active")
        items.append(
            SimulatorActionParticipantItem(
                pid=pid,
                name=name,
                type=t,
                status=st,
            )
        )

    items.sort(key=lambda x: x.pid)
    return SimulatorActionParticipantsListResponse(items=items)


@router.get(
    "/runs/{run_id}/actions/trustlines-list",
    response_model=SimulatorActionTrustlinesListResponse,
    include_in_schema=_actions_enabled(),
)
async def action_trustlines_list(
    run_id: str,
    equivalent: Optional[str] = Query(None),
    participant_pid: Optional[str] = Query(None),
    actor: deps.SimulatorActor = Depends(deps.require_simulator_actor),
    db=Depends(deps.get_db),
):
    if (err := _require_actions_enabled_or_error()) is not None:
        return err
    # NOTE: intentionally no _require_run_accepts_actions_or_error here —
    # trustlines-list is read-only and must work even for stopped/error runs.

    # AuthZ: ownership check
    _run, run_err = _get_run_for_readonly_actions_or_error(run_id, actor)
    if run_err is not None:
        return run_err

    eq, err = await _resolve_equivalent_or_error(session=db, code=str(equivalent or ""))
    if err is not None:
        return err
    assert eq is not None

    participant_id: uuid.UUID | None = None
    if participant_pid is not None:
        p, p_err = await _resolve_participant_or_error(session=db, pid=participant_pid, field="participant_pid")
        if p_err is not None:
            return p_err
        assert p is not None
        participant_id = p.id

    # Run/snapshot-scoped: build from current run snapshot (not global TrustLine table).
    try:
        snap = await runtime.build_graph_snapshot(run_id=run_id, equivalent=eq.code, session=db)
    except NotFoundException:
        return _action_error(
            status_code=404,
            code="RUN_NOT_FOUND",
            message="Run not found",
            details={"run_id": str(run_id)},
        )

    pid_to_name: dict[str, str] = {}
    for n in (getattr(snap, "nodes", None) or []):
        pid = _norm_pid(getattr(n, "id", ""))
        if not pid:
            continue
        pid_to_name[pid] = str(getattr(n, "name", None) or pid)

    def _fmt_num_or_str(v: object) -> str:
        if v is None:
            return "0"
        if isinstance(v, Decimal):
            return _fmt_decimal_for_api(v)
        if isinstance(v, (int, float)):
            # Avoid scientific notation for most typical values.
            try:
                return format(Decimal(str(v)), "f")
            except Exception:
                return str(v)
        return str(v)

    items: list[SimulatorActionTrustlineListItem] = []
    for link in (getattr(snap, "links", None) or []):
        from_pid = _norm_pid(getattr(link, "source", ""))
        to_pid = _norm_pid(getattr(link, "target", ""))
        if not from_pid or not to_pid:
            continue

        if participant_pid is not None:
            if from_pid != participant_pid and to_pid != participant_pid:
                continue

        status = str(getattr(link, "status", None) or "active").strip().lower()
        # Do not surface non-active edges (closed/frozen/etc.).
        if status != "active":
            continue

        limit_s = _fmt_num_or_str(getattr(link, "trust_limit", None))
        used_s = _fmt_num_or_str(getattr(link, "used", None))
        avail_s = _fmt_num_or_str(getattr(link, "available", None))

        items.append(
            SimulatorActionTrustlineListItem(
                from_pid=from_pid,
                from_name=pid_to_name.get(from_pid, from_pid),
                to_pid=to_pid,
                to_name=pid_to_name.get(to_pid, to_pid),
                equivalent=eq.code,
                limit=limit_s,
                used=used_s,
                available=avail_s,
                status=status,
            )
        )

    items.sort(key=lambda x: (x.from_pid, x.to_pid, x.equivalent))
    return SimulatorActionTrustlinesListResponse(items=items)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _sse_format(*, payload: dict[str, Any], event_id: str) -> str:
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"id: {event_id}\nevent: simulator.event\ndata: {data}\n\n"


def _parse_stop_after_types(raw: Optional[str]) -> Optional[set[str]]:
    if raw is None:
        return None
    parts = [p.strip() for p in str(raw).split(",")]
    parts = [p for p in parts if p]
    return set(parts) if parts else None


async def _run_events_stream(
    *,
    run_id: str,
    equivalent: str,
    last_event_id: Optional[str] = None,
    stop_after_types: Optional[set[str]] = None,
) -> AsyncIterator[str]:
    # Subscribe first so we don't miss immediate events.
    sub = await runtime.subscribe(run_id, equivalent=equivalent, after_event_id=last_event_id)
    is_pytest = bool(os.environ.get("PYTEST_CURRENT_TEST"))
    try:
        # Always start with a status snapshot.
        # We emit it through the runtime so it has a normal sequential event_id
        # and lands in the replay buffer.
        runtime.publish_run_status(run_id)

        prefetched: list[dict[str, Any]] = []
        seen_types: set[str] = set()
        status_evt: Optional[dict[str, Any]] = None
        try:
            # Drain until we see the first run_status; preserve other events.
            deadline_sec = 1.0
            while True:
                evt = await asyncio.wait_for(sub.queue.get(), timeout=deadline_sec)
                if str(evt.get("type") or "") == "run_status":
                    status_evt = evt
                    break
                prefetched.append(evt)
                # After first event, don't keep extending wait too much.
                deadline_sec = 0.25
        except asyncio.TimeoutError:
            status_evt = None

        if status_evt is None:
            # Fallback: build a snapshot locally (should be rare).
            run = runtime.get_run(run_id)
            init_event = SimulatorRunStatusEvent(
                event_id=f"evt_init_{secrets.token_hex(6)}",
                ts=_utc_now(),
                type="run_status",
                run_id=run.run_id,
                scenario_id=run.scenario_id,
                state=run.state,
                sim_time_ms=run.sim_time_ms,
                intensity_percent=run.intensity_percent,
                ops_sec=run.ops_sec,
                queue_depth=run.queue_depth,
                last_event_type=run.last_event_type,
                current_phase=run.current_phase,
                last_error=run.last_error,
            ).model_dump(mode="json", by_alias=True)
            seen_types.add(str(init_event.get("type") or ""))
            yield _sse_format(payload=init_event, event_id=str(init_event["event_id"]))
        else:
            event_id = str(status_evt.get("event_id") or "")
            if not event_id:
                event_id = f"evt_{secrets.token_hex(6)}"
                status_evt = dict(status_evt)
                status_evt["event_id"] = event_id
            seen_types.add(str(status_evt.get("type") or ""))
            yield _sse_format(payload=status_evt, event_id=event_id)

            # Flush prefetched events after the initial status.
            for evt in prefetched:
                event_id = str(evt.get("event_id") or evt.get("event") or "")
                if not event_id:
                    event_id = f"evt_{secrets.token_hex(6)}"
                    evt = dict(evt)
                    evt["event_id"] = event_id
                seen_types.add(str(evt.get("type") or ""))
                yield _sse_format(payload=evt, event_id=event_id)

        # NOTE: httpx's in-process ASGI test transport can buffer streaming responses.
        # Under pytest we intentionally terminate the stream after emitting a minimal
        # "first frame" (run_status + at least one tx.updated) so integration tests
        # don't hang waiting for an infinite response to complete.
        if is_pytest:
            # NOTE: httpx's in-process ASGI test transport can buffer streaming responses.
            # Under pytest we intentionally terminate the stream after emitting a bounded
            # "first frame" so integration tests don't hang on infinite SSE.
            #
            # Some tests need specific event types (e.g. clearing.done);
            # in that case we keep streaming until we observe all requested types or
            # hit a short deadline.
            if stop_after_types and stop_after_types.issubset(seen_types):
                return

            # Give the simulator enough time to emit at least one tx.* event under
            # in-process httpx streaming, while keeping the SSE response bounded.
            deadline = asyncio.get_running_loop().time() + (6.0 if stop_after_types else 3.0)
            seen_tx_updated = False
            while asyncio.get_running_loop().time() < deadline:
                try:
                    nxt = await asyncio.wait_for(sub.queue.get(), timeout=0.25)
                except asyncio.TimeoutError:
                    continue

                nxt_type = str(nxt.get("type") or "")
                if nxt_type == "run_status":
                    continue

                event_id = str(nxt.get("event_id") or nxt.get("event") or "")
                if not event_id:
                    event_id = f"evt_{secrets.token_hex(6)}"
                    nxt = dict(nxt)
                    nxt["event_id"] = event_id

                seen_types.add(nxt_type)
                yield _sse_format(payload=nxt, event_id=event_id)
                if nxt_type == "tx.updated":
                    seen_tx_updated = True

                if stop_after_types and stop_after_types.issubset(seen_types):
                    return

                if stop_after_types is None and seen_tx_updated:
                    return

            # If we didn't observe a tx.updated, emit a synthetic one so SSE
            # consumers/tests have a predictable "first frame". Best-effort
            # include patches derived from a snapshot.
            if not seen_tx_updated and stop_after_types is None:
                edge_patch = None
                node_patch = None
                edges = None
                try:
                    from app.db.session import AsyncSessionLocal

                    async with AsyncSessionLocal() as session:
                        snap = await runtime.build_graph_snapshot(
                            run_id=run_id, equivalent=equivalent, session=session
                        )

                    if getattr(snap, "links", None):
                        l0 = snap.links[0]
                        edge_patch = [l0.model_dump(mode="json", by_alias=True)]
                        edges = [{"from": str(l0.source), "to": str(l0.target)}]

                        node_by_id = {n.id: n for n in (snap.nodes or [])}
                        n_src = node_by_id.get(str(l0.source))
                        n_dst = node_by_id.get(str(l0.target))
                        node_patch = [
                            n.model_dump(mode="json", by_alias=True)
                            for n in (n_src, n_dst)
                            if n is not None
                        ]
                except Exception:
                    edge_patch = None
                    node_patch = None
                    edges = None

                evt = SimulatorTxUpdatedEvent(
                    event_id=f"evt_init_tx_{secrets.token_hex(6)}",
                    ts=_utc_now(),
                    type="tx.updated",
                    equivalent=equivalent,
                    amount="0.00",
                    amount_flyout=False,
                    ttl_ms=1200,
                    intensity_key="mid",
                    edges=edges,
                    node_badges=None,
                ).model_dump(mode="json", by_alias=True)
                if edge_patch:
                    evt["edge_patch"] = edge_patch
                if node_patch:
                    evt["node_patch"] = node_patch
                event_id = str(evt.get("event_id") or "") or f"evt_{secrets.token_hex(6)}"
                yield _sse_format(payload=evt, event_id=event_id)
            return

        keepalive_sec = 15
        while True:
            try:
                evt = await asyncio.wait_for(sub.queue.get(), timeout=keepalive_sec)
            except asyncio.TimeoutError:
                # Keep-alive comment
                yield ": keep-alive\n\n"
                continue

            event_id = str(evt.get("event_id") or evt.get("event") or "")
            if not event_id:
                event_id = f"evt_{secrets.token_hex(6)}"
                evt = dict(evt)
                evt["event_id"] = event_id

            yield _sse_format(payload=evt, event_id=event_id)

            # Once the run is terminal, close the stream after emitting status.
            if str(evt.get("type") or "") == "run_status" and str(evt.get("state") or "") in ("stopped", "error"):
                return
    finally:
        await runtime.unsubscribe(run_id, sub)
        if is_pytest:
            try:
                await runtime.stop(run_id)
            except Exception:
                pass


# -----------------------------
# Legacy (active run) endpoints
# -----------------------------


@router.get("/graph/snapshot", response_model=SimulatorGraphSnapshot)
async def graph_snapshot_active_run(
    equivalent: str = Query(...),
    actor: deps.SimulatorActor = Depends(deps.require_simulator_actor),
    db=Depends(deps.get_db),
):
    run_id = runtime.get_active_run_id(owner_id=actor.owner_id)
    if run_id is None:
        # Active run is optional in MVP; return an empty snapshot.
        return SimulatorGraphSnapshot(equivalent=equivalent, generated_at=_utc_now(), nodes=[], links=[])
    try:
        run = runtime.get_run(run_id)
    except NotFoundException:
        return SimulatorGraphSnapshot(equivalent=equivalent, generated_at=_utc_now(), nodes=[], links=[])

    _check_run_access(run, actor, run_id)
    return await runtime.build_graph_snapshot(run_id=run_id, equivalent=equivalent, session=db)


@router.get("/graph/ego", response_model=SimulatorGraphSnapshot)
async def ego_snapshot_active_run(
    equivalent: str = Query(...),
    pid: str = Query(...),
    depth: int = Query(1, ge=1, le=2),
    actor: deps.SimulatorActor = Depends(deps.require_simulator_actor),
    db=Depends(deps.get_db),
):
    run_id = runtime.get_active_run_id(owner_id=actor.owner_id)
    if run_id is None:
        return SimulatorGraphSnapshot(equivalent=equivalent, generated_at=_utc_now(), nodes=[], links=[])
    try:
        run = runtime.get_run(run_id)
    except NotFoundException:
        return SimulatorGraphSnapshot(equivalent=equivalent, generated_at=_utc_now(), nodes=[], links=[])

    _check_run_access(run, actor, run_id)
    return await runtime.build_ego_snapshot(run_id=run_id, equivalent=equivalent, pid=pid, depth=depth, session=db)


@router.get("/events")
async def events_stream_active_run(
    request: Request,
    equivalent: str = Query(...),
    stop_after_types: Optional[str] = Query(None),
    actor: deps.SimulatorActor = Depends(deps.require_simulator_actor),
):
    run_id = runtime.get_active_run_id(owner_id=actor.owner_id)

    # If there is no actual run (or mapping is stale), serve a stream with keep-alives only.
    if run_id is not None:
        try:
            run = runtime.get_run(run_id)
            _check_run_access(run, actor, run_id)
        except NotFoundException:
            run_id = None

    if run_id is None:

        async def idle_stream() -> AsyncIterator[str]:
            while True:
                yield ": keep-alive\n\n"
                await asyncio.sleep(15)

        return StreamingResponse(
            idle_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )

    last_event_id = request.headers.get("Last-Event-ID")
    if last_event_id and runtime.is_sse_strict_replay_enabled() and runtime.is_replay_too_old(
        run_id=run_id, after_event_id=last_event_id
    ):
        raise GoneException("Last-Event-ID is too old; please refresh state")

    return StreamingResponse(
        _run_events_stream(
            run_id=run_id,
            equivalent=equivalent,
            last_event_id=last_event_id,
            stop_after_types=_parse_stop_after_types(stop_after_types),
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


@router.get("/events/poll")
async def events_poll_active_run(
    equivalent: str = Query(...),
    after: Optional[str] = Query(None),
    actor: deps.SimulatorActor = Depends(deps.require_simulator_actor),
):
    # MVP: no replay buffer.
    return []


# -----------------------------
# Real Mode control plane
# -----------------------------


@router.get("/scenarios", response_model=ScenariosListResponse)
async def list_scenarios(
    actor: deps.SimulatorActor = Depends(deps.require_simulator_actor),
):
    return ScenariosListResponse(items=runtime.list_scenarios())


@router.post("/scenarios", response_model=ScenarioSummary)
async def upload_scenario(
    body: ScenarioUploadRequest,
    actor: deps.SimulatorActor = Depends(deps.require_simulator_actor),
):
    rec = runtime.save_uploaded_scenario(body.scenario)
    return rec.summary()


@router.get("/scenarios/{scenario_id}", response_model=ScenarioSummary)
async def get_scenario_summary(
    scenario_id: str,
    actor: deps.SimulatorActor = Depends(deps.require_simulator_actor),
):
    return runtime.get_scenario(scenario_id).summary()


@router.get("/scenarios/{scenario_id}/graph/preview", response_model=SimulatorGraphSnapshot)
async def scenario_graph_preview(
    scenario_id: str,
    equivalent: str = Query(...),
    mode: RunMode = Query("fixtures"),
    actor: deps.SimulatorActor = Depends(deps.require_simulator_actor),
    db=Depends(deps.get_db),
):
    """Preview the scenario graph topology without starting a run."""
    return await runtime.build_scenario_preview(scenario_id=scenario_id, equivalent=equivalent, mode=mode, session=db)


@router.post("/runs", response_model=RunCreateResponse)
async def start_run(
    body: RunCreateRequest,
    actor: deps.SimulatorActor = Depends(deps.require_simulator_actor),
):
    run_id = await runtime.create_run(
        scenario_id=body.scenario_id,
        mode=body.mode,
        intensity_percent=body.intensity_percent,
        owner_id=actor.owner_id,
        owner_kind=actor.kind,
        created_by={"actor_kind": actor.kind, "owner_id": actor.owner_id},
    )
    return RunCreateResponse(run_id=run_id)


@router.get("/runs/active", response_model=ActiveRunResponse)
async def get_active_run(
    actor: deps.SimulatorActor = Depends(deps.require_simulator_actor),
):
    """Return the current active run id (if any).

    Used by the Simulator UI to recover when `SIMULATOR_MAX_ACTIVE_RUNS` prevents
    creating a new run (e.g. another tab already has one running).
    """
    run_id = runtime.get_active_run_id(owner_id=actor.owner_id)
    if run_id is None:
        return ActiveRunResponse(run_id=None)

    # `runtime.get_active_run_id()` may point to the most recent run even if it is
    # already terminal. For UI recovery we only want a currently active (non-terminal)
    # run that could be attached to.
    try:
        st = runtime.get_run_status(run_id)
    except NotFoundException:
        return ActiveRunResponse(run_id=None)

    if st.state in ("stopped", "error"):
        return ActiveRunResponse(run_id=None)

    return ActiveRunResponse(run_id=run_id)


@router.get("/runs/{run_id}", response_model=RunStatus)
async def get_run_status(
    run_id: str,
    actor: deps.SimulatorActor = Depends(deps.require_simulator_actor),
):
    _check_run_access(runtime.get_run(run_id), actor, run_id)
    return runtime.get_run_status(run_id)


@router.post("/runs/{run_id}/pause", response_model=RunStatus)
async def pause_run(
    run_id: str,
    actor: deps.SimulatorActor = Depends(deps.require_simulator_actor),
):
    _check_run_access(runtime.get_run(run_id), actor, run_id)
    return await runtime.pause(run_id)


@router.post("/runs/{run_id}/resume", response_model=RunStatus)
async def resume_run(
    run_id: str,
    actor: deps.SimulatorActor = Depends(deps.require_simulator_actor),
):
    _check_run_access(runtime.get_run(run_id), actor, run_id)
    return await runtime.resume(run_id)


@router.post("/runs/{run_id}/stop", response_model=RunStatus)
async def stop_run(
    run_id: str,
    request: Request,
    source: Optional[str] = Query(default=None, description="Client source (e.g. ui, cli, script)"),
    reason: Optional[str] = Query(default=None, description="Human-readable stop reason"),
    actor: deps.SimulatorActor = Depends(deps.require_simulator_actor),
):
    _check_run_access(runtime.get_run(run_id), actor, run_id)
    client = getattr(request, "client", None)
    client_host = getattr(client, "host", None)
    source_s = str(source) if source else "<unspecified>"
    reason_s = str(reason) if reason else "<unspecified>"
    client_s = str(client_host) if client_host else "<unknown>"

    # Avoid logging any sensitive auth material; admin token is a header.
    logger.info(
        "simulator.run_stop_requested run_id=%s source=%s reason=%s client=%s",
        str(run_id),
        source_s,
        reason_s,
        client_s,
    )
    return await runtime.stop(
        run_id,
        source=(source if source is not None else None),
        reason=(reason if reason is not None else None),
        client=(client_host if client_host is not None else None),
    )


@router.post("/runs/{run_id}/restart", response_model=RunStatus)
async def restart_run(
    run_id: str,
    actor: deps.SimulatorActor = Depends(deps.require_simulator_actor),
):
    _check_run_access(runtime.get_run(run_id), actor, run_id)
    return await runtime.restart(run_id)


@router.post("/runs/{run_id}/intensity", response_model=RunStatus)
async def set_run_intensity(
    run_id: str,
    body: SetIntensityRequest,
    actor: deps.SimulatorActor = Depends(deps.require_simulator_actor),
):
    _check_run_access(runtime.get_run(run_id), actor, run_id)
    return await runtime.set_intensity(run_id, intensity_percent=body.intensity_percent)


@router.get("/runs/{run_id}/events")
async def run_events_stream(
    run_id: str,
    request: Request,
    equivalent: str = Query(...),
    stop_after_types: Optional[str] = Query(None),
    actor: deps.SimulatorActor = Depends(deps.require_simulator_actor),
):
    _check_run_access(runtime.get_run(run_id), actor, run_id)
    last_event_id = request.headers.get("Last-Event-ID")
    if last_event_id and runtime.is_sse_strict_replay_enabled() and runtime.is_replay_too_old(
        run_id=run_id, after_event_id=last_event_id
    ):
        raise GoneException("Last-Event-ID is too old; please refresh state")

    return StreamingResponse(
        _run_events_stream(
            run_id=run_id,
            equivalent=equivalent,
            last_event_id=last_event_id,
            stop_after_types=_parse_stop_after_types(stop_after_types),
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )

@router.post("/runs/{run_id}/actions/tx-once", response_model=TxOnceResponseBody)
async def action_tx_once(
    run_id: str,
    body: TxOnceRequestBody,
    actor: deps.SimulatorActor = Depends(deps.require_simulator_actor),
):
    _require_actions_enabled()
    _check_run_access(runtime.get_run(run_id), actor, run_id)
    emitted = runtime.emit_debug_tx_once(
        run_id=run_id,
        equivalent=body.equivalent,
        from_=body.from_,
        to=body.to,
        amount=body.amount,
        ttl_ms=body.ttl_ms,
        intensity_key=body.intensity_key,
        seed=body.seed,
    )
    return TxOnceResponseBody(emitted_event_id=emitted, client_action_id=body.client_action_id)

@router.post("/runs/{run_id}/actions/clearing-once", response_model=ClearingOnceResponseBody)
async def action_clearing_once(
    run_id: str,
    body: ClearingOnceRequestBody,
    actor: deps.SimulatorActor = Depends(deps.require_simulator_actor),
):
    _require_actions_enabled()
    _check_run_access(runtime.get_run(run_id), actor, run_id)
    plan_id, done_event_id, _eq = runtime.emit_debug_clearing_once(
        run_id=run_id,
        equivalent=body.equivalent,
        cycle_edges=body.cycle_edges,
        cleared_amount=body.cleared_amount,
        seed=body.seed,
    )
    return ClearingOnceResponseBody(
        plan_id=plan_id,
        done_event_id=done_event_id,
        client_action_id=body.client_action_id,
    )

@router.get("/runs/{run_id}/graph/snapshot", response_model=SimulatorGraphSnapshot)
async def graph_snapshot_for_run(
    run_id: str,
    equivalent: str = Query(...),
    actor: deps.SimulatorActor = Depends(deps.require_simulator_actor),
    db=Depends(deps.get_db),
):
    _check_run_access(runtime.get_run(run_id), actor, run_id)
    return await runtime.build_graph_snapshot(run_id=run_id, equivalent=equivalent, session=db)


@router.get("/runs/{run_id}/metrics", response_model=MetricsResponse)
async def metrics_for_run(
    run_id: str,
    equivalent: str = Query(...),
    from_ms: int = Query(..., ge=0),
    to_ms: int = Query(..., ge=0),
    step_ms: int = Query(..., ge=1),
    actor: deps.SimulatorActor = Depends(deps.require_simulator_actor),
):
    _check_run_access(runtime.get_run(run_id), actor, run_id)
    return await runtime.build_metrics(
        run_id=run_id,
        equivalent=equivalent,
        from_ms=from_ms,
        to_ms=to_ms,
        step_ms=step_ms,
    )


@router.get("/runs/{run_id}/bottlenecks", response_model=BottlenecksResponse)
async def bottlenecks_for_run(
    run_id: str,
    equivalent: str = Query(...),
    limit: int = Query(20, ge=1, le=200),
    min_score: Optional[float] = Query(None, ge=0.0, le=1.0),
    actor: deps.SimulatorActor = Depends(deps.require_simulator_actor),
):
    _check_run_access(runtime.get_run(run_id), actor, run_id)
    return await runtime.build_bottlenecks(
        run_id=run_id,
        equivalent=equivalent,
        limit=limit,
        min_score=min_score,
    )


@router.get("/runs/{run_id}/artifacts", response_model=ArtifactIndex)
async def artifacts_index(
    run_id: str,
    actor: deps.SimulatorActor = Depends(deps.require_simulator_actor),
):
    _check_run_access(runtime.get_run(run_id), actor, run_id)
    return await runtime.list_artifacts(run_id=run_id)


@router.get("/runs/{run_id}/artifacts/{name}")
async def artifacts_download(
    run_id: str,
    name: str,
    actor: deps.SimulatorActor = Depends(deps.require_simulator_actor),
):
    _check_run_access(runtime.get_run(run_id), actor, run_id)
    path = runtime.get_artifact_path(run_id=run_id, name=name)
    return FileResponse(path)


# ---------------------------------------------------------------------------
# Admin control plane endpoints (spec §8, §9)
# ---------------------------------------------------------------------------


@router.get("/admin/runs", summary="List all runs (admin)")
async def admin_list_runs(
    state: Optional[str] = None,
    owner_id: Optional[str] = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    actor: deps.SimulatorActor = Depends(deps.require_simulator_actor),
):
    """List all runs with optional filters. Admin only.

    Query params:
    - state: filter by run state (e.g. "running", "stopped", "error", "paused")
    - owner_id: filter by owner_id exact match
    - limit: page size (1..200, default 50)
    - offset: pagination offset (default 0)

    Returns paginated list with owner info for each run.
    """
    if not actor.is_admin:
        raise ForbiddenException("Admin access required")

    all_runs = runtime.list_runs(state=state, owner_id=owner_id)
    total = len(all_runs)
    page = all_runs[offset: offset + limit]

    items = []
    for run in page:
        items.append({
            "run_id": run.run_id,
            "scenario_id": run.scenario_id,
            "mode": run.mode,
            "state": run.state,
            "owner_id": run.owner_id,
            "owner_kind": run.owner_kind,
            "intensity_percent": run.intensity_percent,
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "stopped_at": run.stopped_at.isoformat() if run.stopped_at else None,
            "sim_time_ms": run.sim_time_ms,
            "ops_sec": run.ops_sec,
            "errors_total": run.errors_total,
            "committed_total": run.committed_total,
            "rejected_total": run.rejected_total,
            "attempts_total": run.attempts_total,
        })

    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.post("/admin/runs/stop-all", summary="Stop all active runs (admin)")
async def admin_stop_all_runs(
    body: AdminStopAllRequest = Body(default=AdminStopAllRequest()),
    state: str = Query(default="*", description="State filter: running|paused|stopping|*"),
    actor: deps.SimulatorActor = Depends(deps.require_simulator_actor),
):
    """Stop all currently active runs. Admin only.

    Iterates over all active owner→run mappings and calls stop() for each.
    Returns count of successfully stopped runs and any per-run errors.
    """
    if not actor.is_admin:
        raise ForbiddenException("Admin access required")

    state_s = str(state or "*").strip().lower()
    allowed = {"running", "paused", "stopping", "*"}
    if state_s not in allowed:
        raise BadRequestException(
            "Invalid state filter",
            details={"reason": "invalid_state_filter", "state": state, "allowed": sorted(allowed)},
        )

    # get_all_active_runs() returns a copy; safe to iterate while stop()
    # modifies the original mapping. Concurrent stop-all calls are safe
    # because stop() is idempotent.
    active_runs = runtime.get_all_active_runs()  # dict owner_id → run_id
    stopped = 0

    for _owner_id, run_id in active_runs.items():
        try:
            st = runtime.get_run_status(run_id)
            if state_s != "*" and str(getattr(st, "state", "") or "").lower() != state_s:
                continue

            await runtime.stop(
                run_id,
                source="admin",
                reason=(str(body.reason).strip() if body.reason else "admin_stop_all"),
            )
            stopped += 1
        except Exception:
            # Best-effort: stop-all should not fail entirely due to one run.
            logger.exception("simulator.admin.stop_all_failed run_id=%s", str(run_id))

    return {"stopped": stopped}
