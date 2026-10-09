from __future__ import annotations

import asyncio
import json
import secrets
import logging
import uuid
from dataclasses import dataclass
from decimal import Decimal
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Callable, Optional
from pydantic import BaseModel, Field
from pydantic.config import ConfigDict

from fastapi import APIRouter, Body, Depends, Header, Query, Request
from starlette.responses import FileResponse, Response, StreamingResponse, JSONResponse

from sqlalchemy import and_, func, select
from sqlalchemy.exc import IntegrityError

from app.core.trustlines.service import (
    TrustLineService,
    _is_live_trustline_uniqueness_violation,
)

from app.api import deps
from app.config import settings
from app.core.simulator.runtime import runtime
from app.core.clearing.service import OCCURRENCE_AMOUNT_NOT_IN_STEP
from app.core.clearing.runner import (
    ClearingPassCancelled,
    ClearingPassError,
    atoms_text,
    run_clearing_pass,
)
from app.core.money_boundary import MoneyBoundary
from app.core.payments.router import PaymentRouter
from app.core.payments.service import PaymentService, public_refusal_details, public_refusal_message
from app.core.simulator.edge_patch_builder import EdgePatchBuilder, line_pairs_of_payment_hops
from app.core.simulator.inject_executor import SIMULATED_TRUSTLINE_POLICY
from app.core.simulator.real_scenario_seeder import (
    RealScenarioSeeder,
    ScenarioTrustLineRefused,
    SimulatorPidTakenError,
)
from app.core.simulator.scenario_equivalent import effective_equivalent
from app.core.simulator.models import _Subscription, bump_topology_epoch
from app.core.simulator.sse_broadcast import (
    SSE_SUBSCRIPTION_CLOSED_TYPE,
    SseEventEmitter,
    SseReplayUnavailable,
    publish_closed_trustlines,
)
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

    # Phase 2.5 payment targets
    SimulatorPaymentTargetsResponse,
    SimulatorPaymentTargetsItem,
)
from app.schemas.common import ErrorEnvelope
from app.utils.exceptions import (
    BadRequestException,
    ConflictException,
    ForbiddenException,
    GeoException,
    GoneException,
    NotFoundException,
    RetryablePaymentConflictException,
    RoutingException,
    TimeoutException,
)
from app.schemas.trustline import (
    TrustLineCloseRequest,
    TrustLineCreateRequest,
    TrustLineUpdateRequest,
)
from app.core.simulator.helpers import artifact_content_type
from app.utils.error_codes import ErrorCode
from app.utils.money import to_money_str
from app.utils.validation import parse_money_amount, require_money_step

router = APIRouter(prefix="/simulator")

# Programme 021, stage 2: what the trust-line request models need in their required `signature` field when an
# Interact action calls the service's internal path. `require_signature=False` is what makes the service skip the
# check - never this value, which the service does not read on that path. Nothing of the action's request reaches
# either: the flag is a literal at the call, and the placeholder is this constant.
_UNSIGNED = "__internal__"

# The application's logger (034 S3, F-034-7), like every other module: level from `LOG_LEVEL`, the one format of
# `app/main.py`. Until then this was `uvicorn.error`, whose lines the server prints through its own handler - without
# time and logger name, and at the server's level.
logger = logging.getLogger(__name__)


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


async def _emit_interact_clearing_done_best_effort(
    *,
    run_id: str,
    run,
    db,
    equivalent_code: str,
    executed: list[SimulatorActionClearingCycle],
    cleared_count: int,
    total: Decimal,
    precision: int,
    on_emitted: Callable[[], None] | None = None,
) -> None:
    """`on_emitted` is called right after `clearing.done` WAS PUBLISHED (the emitter returned its event id), with
    no await in between: the caller's cancellation ending must know the event is already out (034 S2b). An emit the
    emitter swallowed is not a publication."""
    if cleared_count <= 0:
        return

    closed: set[tuple[str, str]] = set()
    try:
        emitter = SseEventEmitter(
            sse=runtime._sse,  # type: ignore[attr-defined]
            utc_now=_utc_now,
            logger=logger,
        )
        cycle_edges_payload = _build_clearing_done_cycle_edges_payload(executed)

        edges_pairs: list[tuple[str, str]] = []
        for edge in cycle_edges_payload or []:
            from_pid = str(edge.get("from") or "").strip()
            to_pid = str(edge.get("to") or "").strip()
            if from_pid and to_pid:
                edges_pairs.append((from_pid, to_pid))

        edge_patch, node_patch = await _compute_viz_patches_best_effort(
            session=db,
            run=run,
            equivalent_code=equivalent_code,
            edges_pairs=edges_pairs,
            closed=closed,
        )

        event_id = emitter.emit_clearing_done(
            run_id=run_id,
            run=run,
            equivalent=equivalent_code,
            plan_id=f"plan_interact_{secrets.token_hex(6)}",
            cleared_cycles=int(cleared_count),
            cleared_amount=_fmt_decimal_for_api(total, precision),
            cycle_edges=cycle_edges_payload,
            node_patch=node_patch,
            edge_patch=edge_patch,
        )
        # The emitter catches its own failure and answers None: only an event that has an id was published.
        if event_id is not None and on_emitted is not None:
            on_emitted()
    except Exception:
        logger.warning(
            "Best-effort SSE emission failed: interact.clearing_real run_id=%s",
            run_id,
            exc_info=True,
        )
    # 026 `T2603.2`: every occurrence above is committed (`on_committed`); the lines it closed leave the run.
    await _publish_closed_best_effort(run_id=run_id, run=run, equivalent=equivalent_code, pairs=closed)


async def _publish_closed_best_effort(*, run_id: str, run, equivalent: str, pairs) -> None:
    emitter = SseEventEmitter(sse=runtime._sse, utc_now=_utc_now, logger=logger)  # type: ignore[attr-defined]
    await publish_closed_trustlines(emitter=emitter, lock=runtime._lock, run_id=run_id, run=run,  # type: ignore[attr-defined]
                                    equivalent=equivalent, pairs=pairs)


def _emit_interact_clearing_done_without_patches_best_effort(
    *,
    run_id: str,
    run,
    equivalent_code: str,
    executed: list[SimulatorActionClearingCycle],
    cleared_count: int,
    total: Decimal,
    precision: int,
) -> None:
    """Publish durable clearing progress without entering another await."""
    if cleared_count <= 0:
        return

    try:
        emitter = SseEventEmitter(
            sse=runtime._sse,  # type: ignore[attr-defined]
            utc_now=_utc_now,
            logger=logger,
        )
        emitter.emit_clearing_done(
            run_id=run_id,
            run=run,
            equivalent=equivalent_code,
            plan_id=f"plan_interact_{secrets.token_hex(6)}",
            cleared_cycles=int(cleared_count),
            cleared_amount=_fmt_decimal_for_api(total, precision),
            cycle_edges=_build_clearing_done_cycle_edges_payload(executed),
            node_patch=None,
            edge_patch=None,
        )
    except Exception:
        logger.warning(
            "Best-effort cancellation SSE emission failed: "
            "interact.clearing_real run_id=%s",
            run_id,
            exc_info=True,
        )


async def _compute_viz_patches_best_effort(
    *,
    session,
    run,
    equivalent_code: str,
    edges_pairs: list[tuple[str, str]],
    closed: set[tuple[str, str]] | None = None,
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
                refresh_every_ticks=int(settings.SIMULATOR_VIZ_QUANTILE_REFRESH_TICKS or 10),
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
            # Quantiles are optional; continue with default keys - and say so (034 `F-034-9`).
            logger.warning(
                "simulator.actions.viz_quantiles_failed run_id=%s eq=%s",
                str(getattr(run, "run_id", "")),
                eq_upper,
                exc_info=True,
            )

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
            closed=closed,
        )
        if edge_patch == []:
            edge_patch = None

        return edge_patch, node_patch
    except Exception:
        # Best-effort: the event goes out without patches. Logged, so that a missing patch can be found
        # (034 `F-034-9`; until then this handler was silent).
        logger.warning(
            "simulator.actions.viz_patch_failed run_id=%s eq=%s",
            str(getattr(run, "run_id", "")),
            eq_upper,
            exc_info=True,
        )
        return None, None


# 011/T1109: 403 really has two shapes on the Interact Mode routes, and declaring one of them
# would be the same defect this program catalogues. `_require_actions_enabled_or_error` and the
# access checks answer with the flat `SimulatorActionError` (ACTIONS_DISABLED, ACCESS_DENIED),
# while `require_simulator_actor` raises `ForbiddenException` before the handler runs - bad admin
# token, inactive participant, failed CSRF origin check - and the global handler wraps that in
# `ErrorEnvelope`. Verified by response, not by reading: a bad X-Admin-Token returns
# {"error": {"code": "E006", ...}} and a disabled flag returns {"code": "ACTIONS_DISABLED", ...}.
_ACTION_FORBIDDEN_RESPONSE: dict[str, Any] = {
    "description": (
        "Actions disabled or access denied (flat SimulatorActionError), or the identity was "
        "rejected before the handler ran (ErrorEnvelope)"
    ),
    "content": {
        "application/json": {
            "schema": {
                "oneOf": [
                    {"$ref": "#/components/schemas/SimulatorActionError"},
                    {"$ref": "#/components/schemas/ErrorEnvelope"},
                ]
            }
        }
    },
}


def _actions_enabled() -> bool:
    return settings.SIMULATOR_ACTIONS_ENABLE


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


#: 028 `T2864`: `resume` and `restart` re-enter `running` under the limits of create (`F-028-5`).
_ENTRY_LIMIT_CONFLICT = {"model": ErrorEnvelope, "description": "The owner already has an active run, or the global "
                         "limit of active runs is reached (E008, `details.conflict_kind`)"}


def _flat_trustline_conflict(exc: ConflictException, details: dict, reasons: tuple[str, ...]) -> JSONResponse:
    """A trust-line action's own conflict in its flat 409 body (`SimulatorActionError`); any other one propagates.

    The service names it in `details.reason` (`TRUSTLINE_CLOSE_REQUESTED`, 026 `T2603.1`; `TRUSTLINE_CLOSED`, 028
    `F-028-6` - a line closed by another writer while the action waited for its row lock). Call after the rollback.
    """

    reason = (exc.details or {}).get("reason")
    if reason not in reasons:
        raise exc
    return _action_error(status_code=409, code=str(reason), message=exc.message, details=details)


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

    One get_run() call for existence, access and action acceptance (FIX-CR4: eliminates double get_run).

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


_real_scenario_seeder = RealScenarioSeeder()


async def _ensure_run_seeded(run_id: str, session) -> Optional[JSONResponse]:
    """Lazily seed scenario participants/equivalents/trustlines into the DB for a real-mode run.

    In Interact Mode the run starts paused (intensity=0), so the real tick orchestrator's
    'if not run._real_seeded: seed…' branch may never execute before the first interact
    action arrives.  This helper ensures seeding happens on demand, before any action
    that reads trust_lines from the DB.

    Safe to call multiple times: RealScenarioSeeder.seed_scenario_into_db is idempotent
    (skips already-existing rows) and run._real_seeded acts as a fast-path guard.

    Returns:
        None on success/already-seeded, or a JSONResponse with the standard action
        error envelope on failure.

    Race-safety: uses a per-run asyncio.Lock with double-checked locking to prevent
    concurrent requests from seeding the same run simultaneously.
    """
    try:
        run = runtime.get_run(run_id)
    except Exception:
        return _action_error(
            status_code=404,
            code="RUN_NOT_FOUND",
            message="Run not found",
            details={"run_id": str(run_id)},
        )

    # Fast-path: already seeded, no lock needed
    if run._real_seeded:
        return None

    # Acquire per-run lock (create if missing). Use runtime lock to avoid
    # concurrent overwrites that would break coordination with the tick seeder.
    with runtime._lock:
        if getattr(run, "_real_seeding_lock", None) is None:
            run._real_seeding_lock = asyncio.Lock()
        lock = run._real_seeding_lock

    async with lock:
        # Re-check after acquiring lock (another coroutine may have seeded while we waited)
        if run._real_seeded:
            return None

        scenario = getattr(run, "_scenario_raw", None)
        if not scenario:
            try:
                scenario = runtime.get_scenario(run.scenario_id).raw
            except Exception:
                return _action_error(
                    status_code=503,
                    code="SEEDING_FAILED",
                    message="Failed to seed scenario into database. Please retry.",
                    details={
                        "run_id": str(run_id),
                        "scenario_id": str(getattr(run, "scenario_id", "") or ""),
                        "reason": "scenario_unavailable",
                    },
                )

        try:
            try:
                await _real_scenario_seeder.seed_scenario_into_db(session=session, scenario=scenario)
                await session.commit()
            except SimulatorPidTakenError:
                # The 024 perimeter refuses before the seeder stages anything (`seed_scenario_into_db`
                # checks every scenario pid first), so there is nothing of the seeding to roll back.
                raise
            except Exception:
                # Programme 021 (T2100 P2-4): this handler owns the seeding transaction, so it rolls back
                # what the seeding staged BEFORE it translates the failure into a response - the session is
                # the request's, and nothing staged here may reach a later commit on it.
                await session.rollback()
                raise
            run._real_seeded = True
            logger.debug(
                "interact.ensure_seeded: seeded scenario for run_id=%s scenario_id=%s",
                run_id,
                run.scenario_id,
            )
        except SimulatorPidTakenError as exc:
            # Programme 024, F-024-4b: the scenario names a real participant. Not transient, so
            # not SEEDING_FAILED/503 ("retry"): 409 with the code, the pid and the request id.
            from app.utils.request_id import request_id_var

            request_id = request_id_var.get()
            logger.warning(
                "interact.ensure_seeded: refused code=%s run_id=%s pid=%s request_id=%s",
                exc.details["code"],
                run_id,
                exc.pid,
                request_id,
            )
            return _action_error(
                status_code=409,
                code=str(exc.details["code"]),
                message=exc.message,
                details={
                    "run_id": str(run_id),
                    "pid": exc.pid,
                    "request_id": request_id,
                },
            )
        except ScenarioTrustLineRefused as exc:
            # 028 `F-028-3`/`F-028-24`: a line of the scenario breaks the trust-line rules - not transient.
            logger.warning("interact.ensure_seeded: refused run_id=%s line=%s reason=%s", run_id, exc.line,
                           exc.details["reason"])
            return _action_error(status_code=409, code=str(exc.details["code"]), message=exc.message,
                                 details={"run_id": str(run_id), "line": exc.line, "reason": exc.details["reason"]})
        except Exception:
            logger.error(
                "interact.ensure_seeded: seeding failed for run_id=%s",
                run_id,
                exc_info=True,
            )
            return _action_error(
                status_code=503,
                code="SEEDING_FAILED",
                message="Failed to seed scenario into database. Please retry.",
                details={
                    "run_id": str(run_id),
                    "scenario_id": str(getattr(run, "scenario_id", "") or ""),
                },
            )

    return None


async def _run_perimeter(*, run_id: str, session) -> tuple[set[str], bool]:
    """The PIDs the run contains, and whether the perimeter could be measured at all.

    The read side of the interact family is scoped to the run snapshot
    (`participants-list`).  Mutating actions must see the same perimeter, so they resolve
    participants against this set — see `_resolve_participant_or_error`.

    The perimeter is the run's own participant list and nothing else.  `build_graph_snapshot`
    derives its nodes from `run._scenario_raw` via `scenario_to_snapshot`
    (`app/core/simulator/snapshot_builder.py:65-66`), and with an empty `equivalent` the
    DB-enrichment step returns immediately (`:88-90`).  Seeding therefore has no influence
    on this value at all.

    2026-08-21 / p009: an earlier revision justified the fail-closed decision by "every
    caller runs `_ensure_run_seeded` first".  That argument is wrong — seeding is irrelevant
    here, as above — even though the decision it defended is right.  The correct reason is
    simpler: `_scenario_raw` is set when the run is created, so a run always has a
    participant list, and an empty one means an empty run, which contains nobody.

    2026-08-22 / p010: the second value exists because those two situations are not the
    same ANSWER even though they are the same authorisation.  An empty set admits nobody
    either way, but a caller that reports an outcome must be able to say "the perimeter
    could not be measured" instead of resolving every participant to 404 and telling the
    user that somebody the run contains is not in it.  There is deliberately no variant of
    this function that returns None: on this surface None reads as "no restriction", and a
    P1 authorisation guard once really was switched off by an exception that way.
    """
    try:
        snap = await runtime.build_graph_snapshot(run_id=run_id, equivalent="", session=session)
    except Exception:
        logger.warning(
            "simulator.actions.perimeter_unavailable run_id=%s", str(run_id), exc_info=True
        )
        return set(), False
    nodes = getattr(snap, "nodes", None) or []
    pids = {str(getattr(n, "id", "") or "").strip() for n in nodes}
    pids.discard("")
    return pids, True


def _perimeter_unavailable_error(run_id: str) -> JSONResponse:
    return _action_error(
        status_code=503,
        code="RUN_PERIMETER_UNAVAILABLE",
        message="Run perimeter could not be established",
        details={"run_id": str(run_id)},
    )


async def _resolve_participant_or_error(
    *, session, pid: str, field: str, scoped_pids: Optional[set[str]] = None, reason: Optional[str] = None
) -> tuple[Optional[Participant], Optional[JSONResponse]]:
    """Resolve a participant, optionally restricted to the perimeter of one run.

    `scoped_pids` closes finding F-009-1 (`C-A1a-003`, P1): without it this resolver read
    the GLOBAL `Participant` table, so a mutating interact action accepted a participant
    the run does not contain while `participants-list` of the same family hid it.  A
    foreign participant now produces exactly the response the read side implies —
    `PARTICIPANT_NOT_FOUND` — rather than a successful mutation.
    """
    pid_s = str(pid or "").strip()
    not_found = _action_error(
        status_code=404,
        code="PARTICIPANT_NOT_FOUND",
        message="Participant not found",
        details={"field": field, "pid": pid_s, **({"reason": reason} if reason else {})},
    )
    if not pid_s:
        return None, not_found
    if scoped_pids is not None and pid_s not in scoped_pids:
        return None, not_found
    row = (
        await session.execute(select(Participant).where(Participant.pid == pid_s))
    ).scalar_one_or_none()
    if row is None:
        return None, not_found
    return row, None


async def _resolve_equivalent_or_error(
    *, session, code: str, reason: Optional[str] = None
) -> tuple[Optional[Equivalent], Optional[JSONResponse]]:
    eq_code = str(code or "").strip().upper()
    not_found = {"equivalent": eq_code, **({"reason": reason} if reason else {})}
    if not eq_code:
        return None, _action_error(
            status_code=404,
            code="EQUIVALENT_NOT_FOUND",
            message="Equivalent not found",
            details=not_found,
        )
    eq = (
        await session.execute(select(Equivalent).where(Equivalent.code == eq_code))
    ).scalar_one_or_none()
    if eq is None:
        return None, _action_error(
            status_code=404,
            code="EQUIVALENT_NOT_FOUND",
            message="Equivalent not found",
            details=not_found,
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


_INTERACT_ROUTING_CODE = {"no_route": "NO_ROUTE", "insufficient_capacity": "INSUFFICIENT_CAPACITY"}


def _fmt_decimal_for_api(v: Decimal, precision: int) -> str:
    # 029 F-029-5: a state amount is written in its equivalent's step, as the snapshot and the tick write it.
    return to_money_str(v, precision)


def _norm_pid(v: object) -> str:
    return str(v or "").strip()


def _step_error_or_none(value: Decimal, eq: Any, *, field: str, raw: object) -> Optional[JSONResponse]:
    """028 `F-028-23` (owner В-4): an amount or limit finer than the equivalent's step - the action's own 400."""

    try:
        require_money_step(value, precision=eq.precision, equivalent=eq.code, field=field)
    except BadRequestException as exc:
        return _action_error(status_code=400, code="INVALID_AMOUNT", message=exc.message,
                             details={field: raw, **(exc.details or {})})
    return None


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
            if op in ("create", "update"):  # 026 `T2603.2`: a closure publication begun before this skips the pair.
                bump_topology_epoch(run, eq, fp, tp)

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
                    if op in ("create", "update"):
                        # Update the first matching trustline; if not found (legacy drift), append. A create over a
                        # stale entry of a line closed outside the run replaces it (026 `T2603.2`), not duplicates.
                        found = False
                        for tl in tls:
                            if not isinstance(tl, dict):
                                continue
                            if (
                                str(effective_equivalent(scenario, tl) or "")
                                .strip()
                                .upper()
                                == eq
                                and _norm_pid(tl.get("from")) == fp
                                and _norm_pid(tl.get("to")) == tp
                            ):
                                tl["limit"] = str(limit or "0")
                                if op == "create":
                                    tl["status"] = "active"
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
                                and str(effective_equivalent(scenario, tl) or "")
                                .strip()
                                .upper()
                                == eq
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
        # Never raises - the action itself is committed. But the run's in-memory topology (`_scenario_raw`,
        # `_edges_by_equivalent`) may now disagree with the database until the next snapshot, and until 034
        # `F-034-9` nothing said so.
        logger.warning(
            "simulator.actions.runtime_topology_sync_failed run_id=%s op=%s eq=%s from_pid=%s to_pid=%s",
            str(run_id),
            str(op),
            str(equivalent),
            str(from_pid),
            str(to_pid),
            exc_info=True,
        )


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
#
# Programme 021, stage 4 (`T2106`): every mutating action is "perimeter -> service -> SSE". The perimeter half is
# `_action_parties_or_error`; the domain write is the owning service's (trust lines: `TrustLineService`'s internal
# path in the handler's transaction, stage 2; payments: `PaymentService.create_payment_internal`; clearing: the
# common runner, 023(d)); what follows the commit is best effort (`_publish_trustline_change_best_effort`). The
# checks a handler keeps before the service are the ones that produce the action's own wire codes - the existing-
# debt check of create is STRONGER than the service's (spec, "Решения" item 5) - and they are not a second
# implementation of the service's rules.


@dataclass(frozen=True)
class _ActionParties:
    """What the perimeter half of a mutating action resolved: the run's PIDs, both participants, the equivalent."""

    scoped_pids: set[str]
    from_p: Participant
    to_p: Participant
    eq: Equivalent


async def _action_parties_or_error(
    *, run_id: str, db, from_pid: str, to_pid: str, equivalent: str, payment: bool = False
) -> tuple[Optional[_ActionParties], Optional[JSONResponse]]:
    """The perimeter half of a mutating action, in its fixed order of refusals.

    Lazy seeding first (in Interact Mode the run starts paused, so the tick-level seeding may not have run yet);
    then the run's perimeter - an unmeasurable one is 503 `RUN_PERIMETER_UNAVAILABLE`, because an empty perimeter
    would resolve every participant to 404 and tell the caller that somebody the run contains is not in it, a
    failed measurement of authority dressed as a fact about the data; then `from_pid`, `to_pid` (both only
    within the perimeter, F-009-1) and the equivalent.
    """

    if (seed_err := await _ensure_run_seeded(run_id, db)) is not None:
        return None, seed_err
    scoped_pids, perimeter_available = await _run_perimeter(run_id=run_id, session=db)
    if not perimeter_available:
        return None, _perimeter_unavailable_error(run_id)
    # `payment`: the refusals carry the payment's machine reason (028 `F-028-42`, §15 review `T2899.4` #3).
    from_p, err = await _resolve_participant_or_error(
        session=db, pid=from_pid, field="from_pid", scoped_pids=scoped_pids, reason="other" if payment else None
    )
    if err is not None:
        return None, err
    to_p, err = await _resolve_participant_or_error(session=db, pid=to_pid, field="to_pid", scoped_pids=scoped_pids,
                                                     reason="recipient_not_found" if payment else None)
    if err is not None:
        return None, err
    eq, err = await _resolve_equivalent_or_error(session=db, code=equivalent,
                                                 reason="equivalent_not_found" if payment else None)
    if err is not None:
        return None, err
    assert from_p is not None and to_p is not None and eq is not None
    return _ActionParties(scoped_pids=scoped_pids, from_p=from_p, to_p=to_p, eq=eq), None


async def _live_trustline(db, parties: _ActionParties) -> Optional[TrustLine]:
    """The LIVE line of the triple, if any.

    Only a live line counts - protocol precondition of TRUST_LINE_CREATE, docs/ru/02-protocol-spec.md:333. Since
    migration 019_trust_lines_partial_unique_live the database agrees: uniqueness is over `status <> 'closed'`.
    """

    return (
        await db.execute(
            select(TrustLine).where(
                and_(
                    TrustLine.from_participant_id == parties.from_p.id,
                    TrustLine.to_participant_id == parties.to_p.id,
                    TrustLine.equivalent_id == parties.eq.id,
                    TrustLine.status != "closed",
                )
            )
        )
    ).scalar_one_or_none()


async def _pair_debts_or_error(
    *, run_id: str, action: str, db, parties: _ActionParties, with_reverse: bool
) -> tuple[Decimal, Decimal, Optional[JSONResponse]]:
    """(used, reverse_used) of the triple, or the 503 `TRUSTLINE_USED_UNAVAILABLE` answer.

    `used` is the debt `to -> from` the line covers; `reverse_used` (read only `with_reverse`, else 0) the debt the
    other way. A failed read is NOT taken as 0: that could create or keep a line whose limit is below its debt.
    """

    ids = {"from_id": parties.from_p.id, "to_id": parties.to_p.id, "equivalent_id": parties.eq.id}
    try:
        used = await _trustline_used_amount(db, **ids)
        reverse_used = await _trustline_reverse_used_amount(db, **ids) if with_reverse else Decimal("0")
    except Exception:
        logger.error(
            "Failed to read used amount for %s: run_id=%s equivalent=%s from_pid=%s to_pid=%s",
            action,
            run_id,
            parties.eq.code,
            parties.from_p.pid,
            parties.to_p.pid,
            exc_info=True,
        )
        return (
            Decimal("0"),
            Decimal("0"),
            _action_error(
                status_code=503,
                code="TRUSTLINE_USED_UNAVAILABLE",
                message="Temporary error while reading current used amount",
                details={
                    "equivalent": parties.eq.code,
                    "from_pid": parties.from_p.pid,
                    "to_pid": parties.to_p.pid,
                },
            ),
        )
    return used, reverse_used, None


async def _publish_trustline_change_best_effort(
    *,
    run_id: str,
    db,
    op: str,
    parties: _ActionParties,
    limit_raw: str | None = None,
    limit_dec: Decimal | None = None,
) -> None:
    """After a committed trust-line action: the run's in-memory topology, the router cache, `topology.changed`.

    All of it is best effort and never raises: the mutation is durable already, and nothing here may report it
    as failed. `op` is `create` (the added edge with its limit and an edge patch), `update` (published only when
    an edge patch could be built) or `close` (the removed edge, no patch - the UI needs the explicit removal).
    """

    eq_code, from_pid, to_pid = parties.eq.code, parties.from_p.pid, parties.to_p.pid
    _mutate_runtime_trustline_topology_best_effort(
        run_id=run_id, op=op, equivalent=eq_code, from_pid=from_pid, to_pid=to_pid, limit=limit_raw
    )
    # Trust-line topology is the routing graph.
    try:
        PaymentRouter.invalidate_cache(eq_code)
    except Exception:
        pass

    try:
        run = runtime.get_run(run_id)
        emitter = SseEventEmitter(sse=runtime._sse, utc_now=_utc_now, logger=logger)  # type: ignore[attr-defined]
        edge_patch: list[dict[str, Any]] | None = None
        if op in ("create", "update"):
            try:
                edge_patch = await EdgePatchBuilder(logger=logger).build_edge_patch_for_equivalent(
                    session=db,
                    run=run,
                    equivalent_code=eq_code,
                    only_edges={(from_pid, to_pid)},
                    include_width_keys=True,
                )
            except Exception:
                edge_patch = None

        if op == "create":
            assert limit_dec is not None
            payload = TopologyChangedPayload(
                added_edges=[
                    TopologyChangedEdgeRef(
                        from_pid=from_pid,
                        to_pid=to_pid,
                        equivalent_code=eq_code,
                        limit=_fmt_decimal_for_api(limit_dec, parties.eq.precision),
                    )
                ],
                node_patch=None,
                edge_patch=(edge_patch or None),
            )
        elif op == "update":
            if not edge_patch:
                return
            payload = TopologyChangedPayload(node_patch=None, edge_patch=edge_patch)
        else:
            payload = TopologyChangedPayload(
                removed_edges=[TopologyChangedEdgeRef(from_pid=from_pid, to_pid=to_pid, equivalent_code=eq_code)],
                node_patch=None,
                edge_patch=None,
            )
        emitter.emit_topology_changed(
            run_id=run_id,
            run=run,
            equivalent=eq_code,
            payload=payload,
            reason=f"interact.trustline_{op}",
        )
    except Exception:
        logger.warning(
            "Best-effort SSE emission failed: interact.trustline_%s run_id=%s",
            op,
            run_id,
            exc_info=True,
        )


@router.post(
    "/runs/{run_id}/actions/trustline-create",
    response_model=SimulatorActionTrustlineCreateResponse,
    responses={
        400: {"model": SimulatorActionError, "description": "Invalid request payload for the action (flat envelope)"},
        401: {"model": ErrorEnvelope, "description": "Missing or invalid simulator identity"},
        403: _ACTION_FORBIDDEN_RESPONSE,
        404: {"model": SimulatorActionError, "description": "Run, participant, equivalent or trustline not found (flat envelope)"},
        409: {"model": SimulatorActionError, "description": "Run is terminal, or the action conflicts with current state (flat envelope)"},
        422: {"model": ErrorEnvelope, "description": "Invalid simulator identity transport (for example, X-Simulator-Owner)"},
        503: {"model": SimulatorActionError, "description": "Run perimeter, trustline usage, seeding or engine unavailable (flat envelope)"},
    },
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
        limit_dec = parse_money_amount(req.limit, field="limit")
        if limit_dec < 0:
            raise BadRequestException("Invalid limit")
    except BadRequestException:
        return _action_error(
            status_code=400,
            code="INVALID_AMOUNT",
            message="Invalid limit",
            details={"limit": req.limit},
        )

    parties, err = await _action_parties_or_error(
        run_id=run_id, db=db, from_pid=req.from_pid, to_pid=req.to_pid, equivalent=req.equivalent
    )
    if err is not None:
        return err
    assert parties is not None
    from_p, to_p, eq = parties.from_p, parties.to_p, parties.eq
    if (err := _step_error_or_none(limit_dec, eq, field="limit", raw=req.limit)) is not None:
        return err

    # If there is already debt, the new limit may not be below it: the action's own check, STRONGER than the
    # service's create (spec, "Решения" item 5).
    used_now, _, err = await _pair_debts_or_error(
        run_id=run_id, action="trustline-create", db=db, parties=parties, with_reverse=False
    )
    if err is not None:
        return err
    if limit_dec < used_now:
        return _action_error(
            status_code=409,
            code="USED_EXCEEDS_NEW_LIMIT",
            message="Limit is below current used amount",
            details={
                "equivalent": eq.code,
                "from_pid": from_p.pid,
                "to_pid": to_p.pid,
                "used": _fmt_decimal_for_api(used_now, eq.precision),
                "limit": _fmt_decimal_for_api(limit_dec, eq.precision),
            },
        )

    # The action's own copy of the create guard (finding F-009-4 / B-A1a-016): it answers the action's
    # `TRUSTLINE_EXISTS` code. Since 021 stage 2 the write below goes through TrustLineService, whose own check
    # is then the second one.
    if await _live_trustline(db, parties) is not None:
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

    # Programme 021, stage 2: the write, its audit row and its checkpoint pair are the trust-line service's
    # (internal path, this handler's transaction). The checks above stay: the existing-debt check is STRONGER than
    # the service's create ("Решения" item 5), and the codes they answer are the action's wire contract.
    #
    # Guard and INSERT are not atomic; the partial unique index rejects a concurrent
    # duplicate, and that rejection must surface as a declared 409 rather than as an
    # unhandled database error (fail-closed).  It surfaces at the service's flush (translated
    # there into a `CONCURRENT_TRUSTLINE_CREATE` conflict) or at the commit below.
    # 2026-08-22 / p009_t905 (`F-009-6`).  The readback happens INSIDE the transaction and
    # nothing after the commit performs a mandatory database read.  This route is one of
    # the three aggravated ones: the runtime snapshot mutation and the router cache
    # invalidation below run only after the readback, so a failure there used to leave the
    # database and the run's in-memory topology permanently out of step -- not until the
    # next read, but for the lifetime of the run.  `RT-009-5` shows the failure is
    # reachable.
    trust_lines = TrustLineService(db)
    batch = trust_lines.begin_internal_batch()
    concurrent_create = False
    try:
        tl = await trust_lines.execute_create(
            batch,
            from_p.id,
            TrustLineCreateRequest(
                to=to_p.pid,
                equivalent=eq.code,
                limit=format(limit_dec, "f"),
                policy=dict(SIMULATED_TRUSTLINE_POLICY),
                signature=_UNSIGNED,
            ),
            require_signature=False,
        )
        await batch.finish()
        await db.refresh(tl)
        await db.commit()
    except ConflictException:
        # This handler owns the transaction: roll back BEFORE translating (spec, "Решения" item 7).
        # The service's only conflict on create is a live line of the triple. The check above already
        # found none, so whatever the service found - committed after that check, or clashing at its
        # flush - is a concurrent create.
        await db.rollback()
        concurrent_create = True
    except IntegrityError as exc:
        await db.rollback()
        # Identity check, not a blanket rename: a CHECK or foreign-key violation must not
        # be reported as "trustline already exists".  The service-side classifier is
        # reused so both call sites answer the same question the same way.
        if not _is_live_trustline_uniqueness_violation(exc):
            raise
        concurrent_create = True
    except Exception:
        await db.rollback()
        raise
    if concurrent_create:
        return _action_error(
            status_code=409,
            code="TRUSTLINE_EXISTS",
            message="Active trustline already exists",
            details={
                "from_pid": from_p.pid,
                "to_pid": to_p.pid,
                "equivalent": eq.code,
                "reason": "CONCURRENT_TRUSTLINE_CREATE",
            },
        )

    await _publish_trustline_change_best_effort(
        run_id=run_id, db=db, op="create", parties=parties, limit_raw=req.limit, limit_dec=limit_dec
    )

    return SimulatorActionTrustlineCreateResponse(
        trustline_id=str(tl.id),
        from_pid=from_p.pid,
        to_pid=to_p.pid,
        equivalent=eq.code,
        limit=_fmt_decimal_for_api(limit_dec, eq.precision),
        client_action_id=req.client_action_id,
    )


@router.post(
    "/runs/{run_id}/actions/trustline-update",
    response_model=SimulatorActionTrustlineUpdateResponse,
    responses={
        400: {"model": SimulatorActionError, "description": "Invalid request payload for the action (flat envelope)"},
        401: {"model": ErrorEnvelope, "description": "Missing or invalid simulator identity"},
        403: _ACTION_FORBIDDEN_RESPONSE,
        404: {"model": SimulatorActionError, "description": "Run, participant, equivalent or trustline not found (flat envelope)"},
        409: {"model": SimulatorActionError, "description": "Run is terminal, or the action conflicts with current state (flat envelope)"},
        422: {"model": ErrorEnvelope, "description": "Invalid simulator identity transport (for example, X-Simulator-Owner)"},
        503: {"model": SimulatorActionError, "description": "Run perimeter, seeding or engine unavailable (flat envelope)"},
    },
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
        new_limit_dec = parse_money_amount(req.new_limit, field="new_limit")
        if new_limit_dec < 0:
            raise BadRequestException("Invalid new_limit")
    except BadRequestException:
        return _action_error(
            status_code=400,
            code="INVALID_AMOUNT",
            message="Invalid new_limit",
            details={"new_limit": req.new_limit},
        )

    parties, err = await _action_parties_or_error(
        run_id=run_id, db=db, from_pid=req.from_pid, to_pid=req.to_pid, equivalent=req.equivalent
    )
    if err is not None:
        return err
    assert parties is not None
    tl = await _live_trustline(db, parties)
    if tl is None:
        return _action_error(
            status_code=404,
            code="TRUSTLINE_NOT_FOUND",
            message="Trustline not found",
            details={"from_pid": parties.from_p.pid, "to_pid": parties.to_p.pid, "equivalent": parties.eq.code},
        )

    if (err := _step_error_or_none(new_limit_dec, parties.eq, field="new_limit", raw=req.new_limit)) is not None:
        return err
    old_limit_dec = Decimal(str(getattr(tl, "limit", 0) or 0))
    # 026 `T2602`: no debt floor - a limit below `used` is a trust change, same rule as the public PATCH; the
    # service takes the row lock (`TrustLineService.execute_update`).

    # Programme 021, stage 2: the write goes through the trust-line service's internal path in this handler's
    # transaction; the handler rolls back on any failure before it propagates (spec, "Решения" item 7). See the
    # note in `action_trustline_create`: readback before commit.
    # Read before the write: a rollback expires the ORM rows, and the refusal below names the line after it.
    refusal_details = {
        "from_pid": parties.from_p.pid,
        "to_pid": parties.to_p.pid,
        "equivalent": parties.eq.code,
        "trustline_id": str(tl.id),
    }
    trust_lines = TrustLineService(db)
    batch = trust_lines.begin_internal_batch()
    try:
        await trust_lines.execute_update(
            batch,
            tl.id,
            tl.from_participant_id,
            TrustLineUpdateRequest(limit=format(new_limit_dec, "f"), signature=_UNSIGNED),
            require_signature=False,
        )
        await batch.finish()
        await db.refresh(tl)
        await db.commit()
    except ConflictException as exc:
        # Roll back BEFORE translating (spec 021, "Решения" item 7). A positive limit on a line whose close is
        # requested (026 `T2603.1`) is this action's own refusal and answers in its flat body; any other conflict
        # propagates unchanged.
        await db.rollback()
        return _flat_trustline_conflict(exc, refusal_details, ("TRUSTLINE_CLOSE_REQUESTED", "TRUSTLINE_CLOSED"))
    except Exception:
        await db.rollback()
        raise

    await _publish_trustline_change_best_effort(
        run_id=run_id, db=db, op="update", parties=parties, limit_raw=req.new_limit
    )

    return SimulatorActionTrustlineUpdateResponse(
        trustline_id=str(tl.id),
        old_limit=_fmt_decimal_for_api(old_limit_dec, parties.eq.precision),
        new_limit=_fmt_decimal_for_api(new_limit_dec, parties.eq.precision),
        client_action_id=req.client_action_id,
    )


@router.post(
    "/runs/{run_id}/actions/trustline-close",
    response_model=SimulatorActionTrustlineCloseResponse,
    responses={
        400: {"model": SimulatorActionError, "description": "Invalid request payload for the action (flat envelope)"},
        401: {"model": ErrorEnvelope, "description": "Missing or invalid simulator identity"},
        403: _ACTION_FORBIDDEN_RESPONSE,
        404: {"model": SimulatorActionError, "description": "Run, participant, equivalent or trustline not found (flat envelope)"},
        409: {"model": SimulatorActionError, "description": "Run is terminal, or the action conflicts with current state (flat envelope)"},
        422: {"model": ErrorEnvelope, "description": "Invalid simulator identity transport (for example, X-Simulator-Owner)"},
        503: {"model": SimulatorActionError, "description": "Run perimeter, seeding or engine unavailable (flat envelope)"},
    },
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

    parties, err = await _action_parties_or_error(
        run_id=run_id, db=db, from_pid=req.from_pid, to_pid=req.to_pid, equivalent=req.equivalent
    )
    if err is not None:
        return err
    assert parties is not None
    tl = await _live_trustline(db, parties)
    if tl is None:
        return _action_error(
            status_code=404,
            code="TRUSTLINE_NOT_FOUND",
            message="Trustline not found",
            details={"from_pid": parties.from_p.pid, "to_pid": parties.to_p.pid, "equivalent": parties.eq.code},
        )

    # 026 `T2603.1` (owner В1): no debt refusal here - the service closes at once when the debt the line supports
    # is 0 and otherwise records the request (limit 0, the line stays live until `Book` closes it).
    # Programme 021, stage 2: as in `action_trustline_update`.
    refusal_details = {"from_pid": parties.from_p.pid, "to_pid": parties.to_p.pid, "equivalent": parties.eq.code,
                       "trustline_id": str(tl.id)}
    trust_lines = TrustLineService(db)
    batch = trust_lines.begin_internal_batch()
    try:
        await trust_lines.execute_close(
            batch,
            tl.id,
            tl.from_participant_id,
            TrustLineCloseRequest(signature=_UNSIGNED),
            require_signature=False,
        )
        await batch.finish()
        await db.refresh(tl)
        await db.commit()
    except ConflictException as exc:
        await db.rollback()
        return _flat_trustline_conflict(exc, refusal_details, ("TRUSTLINE_CLOSED",))
    except Exception:
        await db.rollback()
        raise

    # A pending request is NOT a removal: publish it as the limit change it is (its edge patch carries
    # `close_requested_at`). The later completion leaves the run after its commit (026 `T2603.2`,
    # `publish_closed_trustlines`).
    if str(tl.status) == "closed":
        await _publish_trustline_change_best_effort(run_id=run_id, db=db, op="close", parties=parties)
    else:
        await _publish_trustline_change_best_effort(run_id=run_id, db=db, op="update", parties=parties, limit_raw="0")

    return SimulatorActionTrustlineCloseResponse(
        trustline_id=str(tl.id),
        status=str(tl.status),
        close_requested_at=tl.close_requested_at,
        client_action_id=req.client_action_id,
    )


@router.post(
    "/runs/{run_id}/actions/payment-real",
    response_model=SimulatorActionPaymentRealResponse,
    responses={
        400: {"model": SimulatorActionError, "description": "Invalid request payload for the action (flat envelope)"},
        401: {"model": ErrorEnvelope, "description": "Missing or invalid simulator identity"},
        403: _ACTION_FORBIDDEN_RESPONSE,
        404: {"model": SimulatorActionError, "description": "Run, participant, equivalent or trustline not found (flat envelope)"},
        409: {"model": SimulatorActionError, "description": "Run is terminal, or the action conflicts with current state (flat envelope)"},
        422: {"model": ErrorEnvelope, "description": "Invalid simulator identity transport (for example, X-Simulator-Owner)"},
        503: {"model": SimulatorActionError, "description": "Run perimeter, trustline usage, seeding or engine unavailable (flat envelope)"},
    },
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

    parties, err = await _action_parties_or_error(
        run_id=run_id, db=db, from_pid=req.from_pid, to_pid=req.to_pid, equivalent=req.equivalent, payment=True
    )
    if err is not None:
        return err
    assert parties is not None
    scoped_pids, from_p, to_p, eq = parties.scoped_pids, parties.from_p, parties.to_p, parties.eq

    # Amount validation per spec.
    try:
        amount_dec = parse_money_amount(req.amount, field="amount", require_positive=True)
    except BadRequestException as exc:
        return _action_error(
            status_code=400,
            code="INVALID_AMOUNT",
            message=str(getattr(exc, "message", None) or "Invalid amount"),
            details={"amount": req.amount, "reason": public_refusal_details(exc.details, exc.code)["reason"]},
        )
    if (err := _step_error_or_none(amount_dec, eq, field="amount", raw=req.amount)) is not None:
        return err

    try:
        service = PaymentService(db)
        res = await service.create_payment_internal(
            from_p.id,
            to_pid=to_p.pid,
            equivalent=eq.code,
            amount=req.amount,
            idempotency_key=None,
            allowed_participant_pids=scoped_pids,
        )
    except RetryablePaymentConflictException as exc:
        return _action_error(
            status_code=exc.status_code,
            code="CONFLICT",
            message=exc.message,
            details=public_refusal_details(exc.details, exc.code),
        )
    except RoutingException as exc:
        # 028 `F-028-42`: the refusal's machine reason (and `max_available`) beside the action's own fields.
        reason = {k: v for k, v in public_refusal_details(exc.details, exc.code).items()
                  if k in ("reason", "max_available")}
        # 029 F-029-8: the code is a function of the core's reason - one source for one fact. A routing refusal
        # with another reason answers by its own code (`E002` is capacity, `E001` is no route).
        code = _INTERACT_ROUTING_CODE.get(reason.get("reason")) or (
            "INSUFFICIENT_CAPACITY" if exc.code == ErrorCode.E002.value else "NO_ROUTE")
        return _action_error(
            status_code=409,
            code=code,
            message=exc.message,
            details={
                "equivalent": eq.code,
                "from_pid": from_p.pid,
                "to_pid": to_p.pid,
                "requested": req.amount,
                **reason,
            },
        )
    except TimeoutException as exc:
        return _action_error(
            status_code=503,
            code="ENGINE_TIMEOUT",
            message=str(getattr(exc, "message", None) or "Engine timeout"),
            details={"equivalent": eq.code, "from_pid": from_p.pid, "to_pid": to_p.pid, "reason": "timeout"},
        )
    except GeoException as exc:
        # Best-effort mapping for unexpected business errors.
        return _action_error(
            status_code=int(getattr(exc, "status_code", 409) or 409),
            code="PAYMENT_REJECTED",
            message=public_refusal_message(exc.code, exc.message),
            details=public_refusal_details(exc.details, exc.code),
        )

    # Success: emit best-effort tx.updated SSE. `run` already fetched by _get_run_checked above.
    closed: set[tuple[str, str]] = set()
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
            # The LINES of the route's hops (payee -> payer and its reverse), not the hops themselves: 034 S1b.
            edges_pairs=line_pairs_of_payment_hops(edges_pairs),
            closed=closed,
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
    # 026 `T2603.2`: the payment is committed (`create_payment_internal`); a line its book operation closed leaves.
    await _publish_closed_best_effort(run_id=run_id, run=run, equivalent=eq.code, pairs=closed)

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
    responses={
        400: {"model": SimulatorActionError, "description": "Invalid request payload for the action (flat envelope)"},
        401: {"model": ErrorEnvelope, "description": "Missing or invalid simulator identity"},
        403: _ACTION_FORBIDDEN_RESPONSE,
        404: {"model": SimulatorActionError, "description": "Run, participant, equivalent or trustline not found (flat envelope)"},
        409: {"model": SimulatorActionError, "description": "Run is terminal, or the action conflicts with current state (flat envelope)"},
        422: {"model": ErrorEnvelope, "description": "Invalid simulator identity transport (for example, X-Simulator-Owner)"},
        500: {"model": SimulatorActionError, "description": "Clearing execution failed (flat envelope)"},
        503: {"model": SimulatorActionError, "description": "Run perimeter, trustline usage, seeding or engine unavailable (flat envelope)"},
    },
)
async def action_clearing_real(
    run_id: str,
    req: SimulatorActionClearingRealRequest,
    actor: deps.SimulatorActor = Depends(deps.require_simulator_actor),
    db=Depends(deps.get_db),
    session_factory=Depends(deps.get_payment_session_factory),
):
    if (err := _require_actions_enabled_or_error()) is not None:
        return err
    run, run_err = _get_run_checked_or_error(run_id, actor)
    if run_err is not None:
        return run_err
    assert run is not None

    # Ensure scenario is seeded into DB so trustlines/participants exist for clearing.
    # In Interact Mode the run starts paused so the tick-level seeding may not have run yet.
    if (seed_err := await _ensure_run_seeded(run_id, db)) is not None:
        return seed_err

    eq, err = await _resolve_equivalent_or_error(session=db, code=req.equivalent)
    if err is not None:
        return err
    assert eq is not None
    # A skipped clearing rolls back its service-owned attempt, which expires ORM
    # instances. Keep the wire identifier independent of that session state.
    eq_code, eq_precision = str(eq.code), int(eq.precision)

    # 2026-08-22 / p010 (`F-010-3`).  This route used to hand `ClearingService` an
    # equivalent code and nothing else, so a run could clear a cycle made entirely of
    # another run's participants -- and be told the amount as its own result.  The
    # perimeter is computed once here and given to BOTH detection and execution: detection
    # so the cycle is never found, execution so a cycle that arrives by any other route is
    # still refused.
    scoped_pids, perimeter_available = await _run_perimeter(run_id=run_id, session=db)
    if not perimeter_available:
        # Reporting "nothing to clear" here would hide a failed authorisation measurement
        # behind a successful-looking empty result.
        return _perimeter_unavailable_error(run_id)

    # Programme 023, slice (d): ONE pass of the common runner in the run's perimeter (decisions 7, 10; R3). The route
    # no longer detects and executes cycles itself. The runner hands every durable occurrence to `_account` right
    # after its commit, with no await in between; it is recorded here as is and shaped for the wire afterwards.
    # UUIDs -> PIDs through the perimeter's own participants, resolved BEFORE the pass so that no lookup (no await)
    # stands between a commit and its record.
    pid_by_id = await _perimeter_pid_by_id(db, scoped_pids)
    # The request session has only READ since the seeding committed; end its transaction before the runner's own
    # sessions write, so no snapshot or lock of this request is held across the pass (the tick does the same
    # before clearing, `tick.py::RealTick._execute_clearing_with_timeout`). Nothing is pending here to commit.
    await db.commit()
    committed: list = []
    executed: list[SimulatorActionClearingCycle] = []
    total = Decimal("0")
    cleared_count = 0

    def _account(occurrence) -> None:
        nonlocal total, cleared_count
        committed.append(occurrence)
        total += occurrence.amount
        cleared_count += 1

    def _shape() -> None:
        executed[:] = [_interact_cycle_of(occurrence, pid_by_id, eq_precision) for occurrence in committed]

    done_emitted = False

    def _mark_done_emitted() -> None:
        nonlocal done_emitted
        done_emitted = True

    async def _emit_known_progress() -> None:
        _shape()
        try:
            await _emit_interact_clearing_done_best_effort(
                run_id=run_id,
                run=run,
                db=db,
                equivalent_code=eq_code,
                executed=executed,
                cleared_count=cleared_count,
                total=total,
                precision=eq_precision,
                on_emitted=_mark_done_emitted,
            )
        except asyncio.CancelledError:
            # Cancellation may arrive while patches are being computed, before
            # the synchronous broadcast. Publish already-durable progress
            # without entering another await, then preserve cancellation.
            # 034 S2b: it may also arrive AFTER the broadcast, while the lines the clearing closed are being
            # published - then the event is out already and is not published a second time (the tick's
            # `done_emitted`, `RealTick._run_clearing`).
            if done_emitted:
                raise
            _emit_interact_clearing_done_without_patches_best_effort(
                run_id=run_id,
                run=run,
                equivalent_code=eq_code,
                executed=executed,
                cleared_count=cleared_count,
                total=total,
                precision=eq_precision,
            )
            raise

    def _progress_details() -> dict[str, Any]:
        return {
            "partial_cleared_cycles": int(cleared_count),
            "partial_cleared_amount": _fmt_decimal_for_api(total, eq_precision),
        }

    try:
        result = await run_clearing_pass(
            session_factory,
            eq_code,
            allowed_participant_pids=scoped_pids,
            on_committed=_account,
        )
    except ClearingPassCancelled:
        if cleared_count > 0:
            _shape()
            _emit_interact_clearing_done_without_patches_best_effort(
                run_id=run_id,
                run=run,
                equivalent_code=eq_code,
                executed=executed,
                cleared_count=cleared_count,
                total=total,
                precision=eq_precision,
            )
        raise
    except ClearingPassError as failed:
        exc = failed.cause
        logger.error(
            "event=simulator.interact.clearing_failed run_id=%s "
            "equivalent=%s cleared_cycles=%s error=%s",
            run_id,
            eq_code,
            cleared_count,
            type(exc).__name__,
            exc_info=(type(exc), exc, exc.__traceback__),
        )
        details = dict(exc.details or {}) if isinstance(exc, GeoException) else {}
        if cleared_count > 0:
            details.update(_progress_details())
            await _emit_known_progress()
        if (
            isinstance(exc, ConflictException)
            and not isinstance(exc, RetryablePaymentConflictException)
            and (exc.details or {}).get("reason") in MoneyBoundary.MONEY_STOP_REASONS
        ):
            # T1544: the operator's stop is a conflict with the equivalent's state, not a failed
            # execution. This route already declares 409 for exactly that. Step 5c: the integrity
            # hold is the same kind of refusal, with its own reason.
            return _action_error(
                status_code=409,
                code="CONFLICT",
                message=exc.message,
                details=details or None,
            )
        if isinstance(exc, ConflictException) and (exc.details or {}).get("reason") == OCCURRENCE_AMOUNT_NOT_IN_STEP:
            # 030 S2 (`F-030-1`, §15 `T3092`): the executor refused an amount finer than the step - debts finer than the
            # step are in the database, which is reseeded. A logical refusal, not a failed execution; not a money stop.
            return _action_error(
                status_code=409,
                code="CLEARING_REFUSED",
                message=exc.message,
                details=details or None,
            )
        return _action_error(
            status_code=500,
            code="CLEARING_FAILED",
            message="Clearing failed",
            details=details or None,
        )

    await _emit_known_progress()

    if result.status != "complete":
        # R3: an interrupted pass is not a success. Its durable progress is published above and reported here with
        # the reason and the remainder of the last known plan (None: no plan was known - not measured, not zero).
        return _action_error(
            status_code=409,
            code="CLEARING_INTERRUPTED",
            message="Clearing pass interrupted before the plan was exhausted",
            details={
                "reason": None if result.reason is None else result.reason.value,
                **_progress_details(),
                "remaining_cycles": result.remaining_cycles,
                "remaining_v_edge": (
                    None if result.remaining_v_edge_atoms is None else atoms_text(result.remaining_v_edge_atoms)
                ),
            },
        )

    return SimulatorActionClearingRealResponse(
        equivalent=eq_code,
        cleared_cycles=int(cleared_count),
        total_cleared_amount=_fmt_decimal_for_api(total, eq_precision),
        cycles=executed,
        client_action_id=req.client_action_id,
    )


async def _perimeter_pid_by_id(db, scoped_pids) -> dict:
    """Participant id -> PID for the run's perimeter (one read, before the pass)."""

    if not scoped_pids:
        return {}
    rows = (
        await db.execute(select(Participant.id, Participant.pid).where(Participant.pid.in_(sorted(scoped_pids))))
    ).all()
    return {participant_id: str(pid) for participant_id, pid in rows}


def _interact_cycle_of(occurrence, pid_by_id: dict, precision: int) -> SimulatorActionClearingCycle:
    """One committed occurrence on the wire: its amount and its edges in the trust-line direction creditor -> debtor.

    The runner's progress edge is debtor -> creditor by participant UUID (decision R3); the Interact wire and
    `clearing.done.cycle_edges` carry `from` = creditor, `to` = debtor by PID.
    """

    edges: list[SimulatorActionEdgeRef] = []
    for edge in occurrence.edges:
        creditor = pid_by_id.get(edge.creditor_id)
        debtor = pid_by_id.get(edge.debtor_id)
        if creditor and debtor and creditor != debtor:
            edges.append(SimulatorActionEdgeRef(from_=creditor, to=debtor))
    return SimulatorActionClearingCycle(cleared_amount=_fmt_decimal_for_api(occurrence.amount, precision), edges=edges)


@router.get(
    "/runs/{run_id}/actions/participants-list",
    response_model=SimulatorActionParticipantsListResponse,
    responses={
        401: {"model": ErrorEnvelope, "description": "Missing or invalid simulator identity"},
        403: _ACTION_FORBIDDEN_RESPONSE,
        404: {"model": SimulatorActionError, "description": "Run, participant, equivalent or trustline not found (flat envelope)"},
        422: {"model": ErrorEnvelope, "description": "Invalid simulator identity transport (for example, X-Simulator-Owner)"},
    },
)
async def action_participants_list(
    run_id: str,
    actor: deps.SimulatorActor = Depends(deps.require_simulator_actor),
    db=Depends(deps.get_db),
):
    if (err := _require_actions_enabled_or_error()) is not None:
        return err
    # NOTE: intentionally not `_get_run_checked_or_error` (it refuses terminal runs) —
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
    responses={
        401: {"model": ErrorEnvelope, "description": "Missing or invalid simulator identity"},
        403: _ACTION_FORBIDDEN_RESPONSE,
        404: {"model": SimulatorActionError, "description": "Run, participant, equivalent or trustline not found (flat envelope)"},
        409: {"model": SimulatorActionError, "description": "The run's scenario names a participant the simulator did not create - SIMULATOR_PID_TAKEN; or a scenario trust line breaks the trust-line rules - SCENARIO_TRUSTLINE_REFUSED (flat envelope)"},
        422: {"model": ErrorEnvelope, "description": "Invalid simulator identity transport (for example, X-Simulator-Owner)"},
        503: {"model": SimulatorActionError, "description": "Run perimeter, trustline usage, seeding or engine unavailable (flat envelope)"},
    },
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
    # NOTE: intentionally not `_get_run_checked_or_error` (it refuses terminal runs) —
    # trustlines-list is read-only and must work even for stopped/error runs.

    # AuthZ: ownership check
    _run, run_err = _get_run_for_readonly_actions_or_error(run_id, actor)
    if run_err is not None:
        return run_err

    # Ensure scenario is seeded into DB so equivalent/participant resolution works.
    # In Interact Mode the run starts paused so the tick-level seeding may not have run yet.
    if (seed_err := await _ensure_run_seeded(run_id, db)) is not None:
        return seed_err

    eq, err = await _resolve_equivalent_or_error(session=db, code=str(equivalent or ""))
    if err is not None:
        return err
    assert eq is not None

    if participant_pid is not None:
        p, p_err = await _resolve_participant_or_error(session=db, pid=participant_pid, field="participant_pid")
        if p_err is not None:
            return p_err
        assert p is not None

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

    # NOTE: `reverse_used` is the debt the other way: debtor = from_pid, creditor = to_pid. It no longer gates
    # a close (026 `T2603.1`: the other line supports it).
    # For this read-only list we can compute it from DB using the participants referenced
    # in the snapshot links.

    def _fmt_num_or_str(v: object) -> str:
        if v is None:
            return "0"
        if isinstance(v, Decimal):
            return _fmt_decimal_for_api(v, eq.precision)
        if isinstance(v, (int, float)):
            # Avoid scientific notation for most typical values.
            try:
                return format(Decimal(str(v)), "f")
            except Exception:
                return str(v)
        return str(v)

    active_links: list[tuple[str, str, object]] = []
    pids_needed: set[str] = set()
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

        active_links.append((from_pid, to_pid, link))
        pids_needed.add(from_pid)
        pids_needed.add(to_pid)

    # Programme 024 (external review P2): only the run's participants are resolved against the
    # database - the snapshot's nodes, which are the perimeter - so a link whose end lies outside
    # it never reads a `Debt`, even if a snapshot were to carry one.
    pids_needed &= set(pid_to_name)
    pid_to_id: dict[str, uuid.UUID] = {}
    if pids_needed:
        rows = (
            await db.execute(select(Participant.pid, Participant.id).where(Participant.pid.in_(sorted(pids_needed))))
        ).all()
        pid_to_id = {str(pid): pid_id for pid, pid_id in rows if pid and pid_id}

    # Bulk fetch reverse debts for all participant pairs that appear in the list.
    reverse_debt_map: dict[tuple[uuid.UUID, uuid.UUID], Decimal] = {}
    if pid_to_id:
        ids = sorted(set(pid_to_id.values()))
        debt_rows = (
            await db.execute(
                select(Debt.debtor_id, Debt.creditor_id, Debt.amount).where(
                    and_(
                        Debt.equivalent_id == eq.id,
                        Debt.debtor_id.in_(ids),
                        Debt.creditor_id.in_(ids),
                    )
                )
            )
        ).all()
        for debtor_id, creditor_id, amount in debt_rows:
            if debtor_id is None or creditor_id is None:
                continue
            if amount is None:
                continue
            reverse_debt_map[(debtor_id, creditor_id)] = amount

    items: list[SimulatorActionTrustlineListItem] = []
    for from_pid, to_pid, link in active_links:
        limit_s = _fmt_num_or_str(getattr(link, "trust_limit", None))
        used_s = _fmt_num_or_str(getattr(link, "used", None))
        avail_s = _fmt_num_or_str(getattr(link, "available", None))

        from_id = pid_to_id.get(from_pid)
        to_id = pid_to_id.get(to_pid)
        reverse_used = Decimal("0")
        if from_id is not None and to_id is not None:
            reverse_used = reverse_debt_map.get((from_id, to_id), Decimal("0"))
        reverse_used_s = _fmt_num_or_str(reverse_used)

        items.append(
            SimulatorActionTrustlineListItem(
                from_pid=from_pid,
                from_name=pid_to_name.get(from_pid, from_pid),
                to_pid=to_pid,
                to_name=pid_to_name.get(to_pid, to_pid),
                equivalent=eq.code,
                limit=limit_s,
                used=used_s,
                reverse_used=reverse_used_s,
                available=avail_s,
                status="active",
                close_requested_at=getattr(link, "close_requested_at", None),
            )
        )

    items.sort(key=lambda x: (x.from_pid, x.to_pid, x.equivalent))
    return SimulatorActionTrustlinesListResponse(items=items)


@router.get(
    "/runs/{run_id}/payment-targets",
    response_model=SimulatorPaymentTargetsResponse,
    responses={
        401: {"model": ErrorEnvelope, "description": "Missing or invalid simulator identity"},
        403: _ACTION_FORBIDDEN_RESPONSE,
        404: {"model": SimulatorActionError, "description": "Run, participant, equivalent or trustline not found (flat envelope)"},
        409: {"model": SimulatorActionError, "description": "The run's scenario names a participant the simulator did not create - SIMULATOR_PID_TAKEN; or a scenario trust line breaks the trust-line rules - SCENARIO_TRUSTLINE_REFUSED (flat envelope)"},
        422: {"model": ErrorEnvelope, "description": "Invalid simulator identity transport (for example, X-Simulator-Owner)"},
        503: {"model": SimulatorActionError, "description": "Run perimeter, trustline usage, seeding or engine unavailable (flat envelope)"},
    },
)
async def payment_targets(
    run_id: str,
    equivalent: str = Query(...),
    from_pid: str = Query(...),
    max_hops: int = Query(6, ge=1, le=8),
    limit: int = Query(200, ge=1, le=1000),
    include_max_available: bool = Query(False),
    actor: deps.SimulatorActor = Depends(deps.require_simulator_actor),
    db=Depends(deps.get_db),
):
    """Phase 2.5: backend-first reachable payment targets.

    Returns receivers that are reachable via multi-hop routes where every edge has capacity > 0.
    Uses existing PaymentRouter to keep routing semantics consistent.
    """

    if (err := _require_actions_enabled_or_error()) is not None:
        return err
    # Read-only: do not require run accepts actions.

    # AuthZ: ownership check
    _run, run_err = _get_run_for_readonly_actions_or_error(run_id, actor)
    if run_err is not None:
        return run_err

    # Ensure scenario is seeded into DB so equivalent/participant resolution works.
    if (seed_err := await _ensure_run_seeded(run_id, db)) is not None:
        return seed_err

    eq, err = await _resolve_equivalent_or_error(session=db, code=str(equivalent or ""))
    if err is not None:
        return err
    assert eq is not None

    # Programme 024 (external review P2): a read of the run, confined like its money path.
    # `from_pid` is resolved within the run perimeter, and the router instance is narrowed to it
    # with the mechanism the payment service uses for `allowed_participant_pids`
    # (`PaymentRouter.confine_to_participants`), so no target, hop or capacity outside the run - and no
    # route THROUGH a participant outside it - is computed at all.
    scoped_pids, perimeter_available = await _run_perimeter(run_id=run_id, session=db)
    if not perimeter_available:
        return _perimeter_unavailable_error(run_id)
    from_p, err = await _resolve_participant_or_error(
        session=db, pid=from_pid, field="from_pid", scoped_pids=scoped_pids
    )
    if err is not None:
        return err
    assert from_p is not None

    # Build the capacity graph (edges included only if capacity > 0).
    router = PaymentRouter(db)
    await router.build_graph(eq.code)
    router.confine_to_participants(scoped_pids)

    # 034 S2 (F-034-11): the router's own public answer (035 A6) - policy-aware reachability, nearest first then by
    # pid, cut by `limit`. Until then this route ran that loop itself over the router's private search. `limit` is
    # at least 1 here (the query refuses 0), so the old loop's "zero means no cut" has no caller.
    src = str(from_p.pid)
    targets = router.payment_targets(src, max_hops=int(max_hops), limit=int(limit))

    items: list[SimulatorPaymentTargetsItem] = []
    for target in targets:
        hops, dst = target.hops, target.to_pid
        max_avail: str | None = None
        if include_max_available:
            # Best-effort: use existing max-flow implementation.
            # NOTE: may be expensive; guarded by include_max_available + limit.
            try:
                mf = router.calculate_max_flow(src, dst)
                max_avail = str(getattr(mf, "max_amount", None) or "0")
            except Exception:
                max_avail = None

        items.append(
            SimulatorPaymentTargetsItem(
                to_pid=dst,
                hops=int(hops),
                max_available=max_avail,
            )
        )

    return SimulatorPaymentTargetsResponse(items=items)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _sse_format(*, payload: dict[str, Any], event_id: str) -> str:
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"id: {event_id}\nevent: simulator.event\ndata: {data}\n\n"


async def _run_events_stream(
    *,
    run_id: str,
    equivalent: str,
    last_event_id: Optional[str] = None,
    subscription: Optional[_Subscription] = None,
    initial_status_event_id: Optional[str] = None,
) -> AsyncIterator[str]:
    # Replay and the initial status are atomically queued before subscription exposure.
    if subscription is None:
        sub, status_event_id = await runtime.subscribe_with_status(
            run_id, equivalent=equivalent, after_event_id=last_event_id
        )
    else:
        sub = subscription
        status_event_id = initial_status_event_id or runtime.publish_run_status(run_id)
    try:
        # Always start with a status snapshot.
        # We emit it through the runtime so it has a normal sequential event_id
        # and lands in the replay buffer.
        prefetched: list[dict[str, Any]] = []
        status_evt: Optional[dict[str, Any]] = None
        try:
            # Drain until we see the status emitted above. Older replay statuses are
            # part of the replay prefix and must not be mistaken for this snapshot.
            deadline_sec = 1.0
            while True:
                evt = await asyncio.wait_for(sub.queue.get(), timeout=deadline_sec)
                if str(evt.get("type") or "") == SSE_SUBSCRIPTION_CLOSED_TYPE:
                    return
                if str(evt.get("event_id") or "") == status_event_id:
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
            yield _sse_format(payload=init_event, event_id=str(init_event["event_id"]))
            bootstrap_tail = (
                runtime.finish_replay_bootstrap(run_id, sub)
                if sub.replay_bootstrap_pending
                else []
            )
            if bootstrap_tail is None:
                return
            if str(init_event.get("state") or "") in ("stopped", "error"):
                return
        else:
            status_id = str(status_evt.get("event_id") or "")
            if not status_id:
                status_id = f"evt_{secrets.token_hex(6)}"
                status_evt = dict(status_evt)
                status_evt["event_id"] = status_id
            # Replay and any already queued live tail must precede the new
            # authoritative status snapshot.
            for evt in prefetched:
                event_id = str(evt.get("event_id") or evt.get("event") or "")
                if not event_id:
                    event_id = f"evt_{secrets.token_hex(6)}"
                    evt = dict(evt)
                    evt["event_id"] = event_id
                yield _sse_format(payload=evt, event_id=event_id)

            yield _sse_format(payload=status_evt, event_id=status_id)
            bootstrap_tail = (
                runtime.finish_replay_bootstrap(run_id, sub)
                if sub.replay_bootstrap_pending
                else []
            )
            if bootstrap_tail is None:
                return

            if str(status_evt.get("state") or "") in ("stopped", "error"):
                return

        # This list is the exact pre-finalization live tail. Future broadcasts
        # now enter the normal queue and cannot be consumed in this flush.
        for evt in bootstrap_tail:
            event_id = str(evt.get("event_id") or evt.get("event") or "")
            if not event_id:
                event_id = f"evt_{secrets.token_hex(6)}"
                evt = dict(evt)
                evt["event_id"] = event_id
            yield _sse_format(payload=evt, event_id=event_id)
            if str(evt.get("type") or "") == "run_status" and str(
                evt.get("state") or ""
            ) in ("stopped", "error"):
                return

        keepalive_sec = 15
        while True:
            try:
                evt = await asyncio.wait_for(sub.queue.get(), timeout=keepalive_sec)
            except asyncio.TimeoutError:
                # Keep-alive comment
                yield ": keep-alive\n\n"
                continue

            if str(evt.get("type") or "") == SSE_SUBSCRIPTION_CLOSED_TYPE:
                return

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


@router.get(
    "/events",
    responses={
        410: {
            "model": ErrorEnvelope,
            "description": "Replay cursor is invalid, unavailable, or cannot fit the subscriber queue",
        }
    },
)
async def events_stream_active_run(
    equivalent: str = Query(...),
    last_event_id: Optional[str] = Header(None, alias="Last-Event-ID"),
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
        if last_event_id is not None:
            raise GoneException(
                "Last-Event-ID replay is unavailable; please refresh state"
            )

        async def idle_stream() -> AsyncIterator[str]:
            while True:
                yield ": keep-alive\n\n"
                await asyncio.sleep(15)

        return StreamingResponse(
            idle_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )

    try:
        sub, status_event_id = await runtime.subscribe_with_status(
            run_id, equivalent=equivalent, after_event_id=last_event_id
        )
    except SseReplayUnavailable as exc:
        raise GoneException("Last-Event-ID replay is unavailable; please refresh state") from exc

    return StreamingResponse(
        _run_events_stream(
            run_id=run_id,
            equivalent=equivalent,
            last_event_id=last_event_id,
            subscription=sub,
            initial_status_event_id=status_event_id,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


# 011/`F-011-7`: the polling fallback has never had a replay buffer, so the handler below returns
# an empty array unconditionally and reads neither query parameter. The canon used to promise an
# array of SimulatorEvent - six event variants a client would write parsing code for and never
# receive. The owner's decision (delegated to external review, verdict A) is that the contract
# states what exists: an array that is always empty. `maxItems: 0` says exactly that, and it says
# it in a way a future replay buffer cannot inherit by accident - lifting the cap will be a
# deliberate contract change rather than the belated delivery of an old promise.
#
# Declared as a raw schema rather than a `model`, because "empty array" is not a pydantic type; and
# through `responses=`, never `response_model=`, which would make FastAPI filter the reply.
_EVENTS_POLL_EMPTY_RESPONSE: dict[str, Any] = {
    "description": (
        "Always an empty array: the MVP has no replay buffer, so no event is ever returned and "
        "the `equivalent` and `after` parameters are not read"
    ),
    "content": {
        "application/json": {
            "schema": {"type": "array", "maxItems": 0, "items": {}},
            "example": [],
        }
    },
}


@router.get("/events/poll", responses={200: _EVENTS_POLL_EMPTY_RESPONSE})
async def events_poll_active_run(
    equivalent: str = Query(
        ...,
        description=(
            "Ignored in the MVP: the handler returns an empty array without reading it "
            "(`F-011-7`). Required so the eventual replay buffer keeps the same call shape."
        ),
    ),
    after: Optional[str] = Query(
        None,
        description=(
            "Ignored in the MVP. Reserved for the cursor semantics a replay buffer would need "
            "(`F-011-7`)."
        ),
    ),
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


@router.post("/runs/{run_id}/resume", response_model=RunStatus, responses={409: _ENTRY_LIMIT_CONFLICT})
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


@router.post("/runs/{run_id}/restart", response_model=RunStatus, responses={409: _ENTRY_LIMIT_CONFLICT})
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


@router.get(
    "/runs/{run_id}/events",
    responses={
        410: {
            "model": ErrorEnvelope,
            "description": "Replay cursor is invalid, unavailable, or cannot fit the subscriber queue",
        }
    },
)
async def run_events_stream(
    run_id: str,
    equivalent: str = Query(...),
    last_event_id: Optional[str] = Header(None, alias="Last-Event-ID"),
    actor: deps.SimulatorActor = Depends(deps.require_simulator_actor),
):
    _check_run_access(runtime.get_run(run_id), actor, run_id)
    try:
        sub, status_event_id = await runtime.subscribe_with_status(
            run_id, equivalent=equivalent, after_event_id=last_event_id
        )
    except SseReplayUnavailable as exc:
        raise GoneException("Last-Event-ID replay is unavailable; please refresh state") from exc

    return StreamingResponse(
        _run_events_stream(
            run_id=run_id,
            equivalent=equivalent,
            last_event_id=last_event_id,
            subscription=sub,
            initial_status_event_id=status_event_id,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )

@router.post(
    "/runs/{run_id}/actions/tx-once",
    response_model=TxOnceResponseBody,
    responses={
        400: {"model": SimulatorActionError},
        403: {"model": ErrorEnvelope},
        409: {"model": ErrorEnvelope},
        422: {
            "model": ErrorEnvelope,
            "description": "Invalid simulator identity transport",
        },
    },
)
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

@router.post(
    "/runs/{run_id}/actions/clearing-once",
    response_model=ClearingOnceResponseBody,
    responses={
        400: {"model": SimulatorActionError},
        403: {"model": ErrorEnvelope},
        409: {"model": ErrorEnvelope},
        422: {
            "model": ErrorEnvelope,
            "description": "Invalid simulator identity transport",
        },
    },
)
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


@router.get(
    "/runs/{run_id}/metrics",
    response_model=MetricsResponse,
    responses={503: {"model": ErrorEnvelope, "description": "Measured data is unavailable"}},
)
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


@router.get(
    "/runs/{run_id}/bottlenecks",
    response_model=BottlenecksResponse,
    responses={503: {"model": ErrorEnvelope, "description": "Measured data is unavailable"}},
)
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
    # 029 F-029-7: the type the index names for this artifact; None leaves the file response's own guess.
    return FileResponse(path, media_type=artifact_content_type(name))


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
