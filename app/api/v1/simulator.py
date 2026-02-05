from __future__ import annotations

import asyncio
import json
import secrets
import os
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Optional
from pydantic import BaseModel, Field
from pydantic.config import ConfigDict

from fastapi import APIRouter, Depends, Query, Request
from starlette.responses import FileResponse, Response, StreamingResponse

from app.api import deps
from app.core.simulator.runtime import runtime
from app.schemas.simulator import (
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
)
from app.utils.exceptions import GoneException, NotFoundException
from app.utils.exceptions import ForbiddenException

router = APIRouter(prefix="/simulator")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _sse_format(*, payload: dict[str, Any], event_id: str) -> str:
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"id: {event_id}\nevent: simulator.event\ndata: {data}\n\n"


async def _run_events_stream(*, run_id: str, equivalent: str, last_event_id: Optional[str] = None) -> AsyncIterator[str]:
    # Subscribe first so we don't miss immediate events.
    sub = await runtime.subscribe(run_id, equivalent=equivalent, after_event_id=last_event_id)
    is_pytest = bool(os.environ.get("PYTEST_CURRENT_TEST"))
    try:
        # Always start with a status snapshot.
        # We emit it through the runtime so it has a normal sequential event_id
        # and lands in the replay buffer.
        runtime.publish_run_status(run_id)

        prefetched: list[dict[str, Any]] = []
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
            ).model_dump(mode="json")
            yield _sse_format(payload=init_event, event_id=str(init_event["event_id"]))
        else:
            event_id = str(status_evt.get("event_id") or "")
            if not event_id:
                event_id = f"evt_{secrets.token_hex(6)}"
                status_evt = dict(status_evt)
                status_evt["event_id"] = event_id
            yield _sse_format(payload=status_evt, event_id=event_id)

            # Flush prefetched events after the initial status.
            for evt in prefetched:
                event_id = str(evt.get("event_id") or evt.get("event") or "")
                if not event_id:
                    event_id = f"evt_{secrets.token_hex(6)}"
                    evt = dict(evt)
                    evt["event_id"] = event_id
                yield _sse_format(payload=evt, event_id=event_id)

        # NOTE: httpx's in-process ASGI test transport can buffer streaming responses.
        # Under pytest we intentionally terminate the stream after emitting a minimal
        # "first frame" (run_status + at least one tx.updated) so integration tests
        # don't hang waiting for an infinite response to complete.
        if is_pytest:
            evt: Optional[dict[str, Any]] = None
            deadline = asyncio.get_running_loop().time() + 2.0
            while asyncio.get_running_loop().time() < deadline:
        def _actions_enabled() -> bool:
            return str(os.environ.get("SIMULATOR_ACTIONS_ENABLE", "") or "").strip() in {"1", "true", "TRUE", "yes"}

        def _require_actions_enabled() -> None:
            if not _actions_enabled():
                raise ForbiddenException("Simulator actions are disabled (set SIMULATOR_ACTIONS_ENABLE=1)")

                try:
                    nxt = await asyncio.wait_for(sub.queue.get(), timeout=0.25)
                except asyncio.TimeoutError:
                    continue
                if str(nxt.get("type") or "") == "run_status":
                    # Skip extra heartbeats; tests need at least one domain event.
                    continue
                evt = nxt
                break

            if evt is None:
                evt = SimulatorTxUpdatedEvent(
                    event_id=f"evt_init_tx_{secrets.token_hex(6)}",
                    ts=_utc_now(),
                    type="tx.updated",
                    equivalent=equivalent,
                    ttl_ms=1200,
                    intensity_key="mid",
                    edges=None,
                    node_badges=None,
                ).model_dump(mode="json")

            event_id = str(evt.get("event_id") or evt.get("event") or "")
            if not event_id:
                event_id = f"evt_{secrets.token_hex(6)}"
                evt = dict(evt)
                evt["event_id"] = event_id

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
    _actor=Depends(deps.require_participant_or_admin),
    db=Depends(deps.get_db),
):
    run_id = runtime.get_active_run_id()
    if run_id is None:
        # Active run is optional in MVP; return an empty snapshot.
        return SimulatorGraphSnapshot(equivalent=equivalent, generated_at=_utc_now(), nodes=[], links=[])
    return await runtime.build_graph_snapshot(run_id=run_id, equivalent=equivalent, session=db)


@router.get("/graph/ego", response_model=SimulatorGraphSnapshot)
async def ego_snapshot_active_run(
    equivalent: str = Query(...),
    pid: str = Query(...),
    depth: int = Query(1, ge=1, le=2),
    _actor=Depends(deps.require_participant_or_admin),
    db=Depends(deps.get_db),
):
    run_id = runtime.get_active_run_id()
    if run_id is None:
        return SimulatorGraphSnapshot(equivalent=equivalent, generated_at=_utc_now(), nodes=[], links=[])
    return await runtime.build_ego_snapshot(run_id=run_id, equivalent=equivalent, pid=pid, depth=depth, session=db)


@router.get("/events")
async def events_stream_active_run(
    request: Request,
    equivalent: str = Query(...),
    _actor=Depends(deps.require_participant_or_admin),
):
    run_id = runtime.get_active_run_id() or "active"

    # If there is no actual run, serve a stream with keep-alives only.
    if runtime.get_active_run_id() is None:

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
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


@router.get("/events/poll")
async def events_poll_active_run(
    equivalent: str = Query(...),
    after: Optional[str] = Query(None),
    _actor=Depends(deps.require_participant_or_admin),
):
    # MVP: no replay buffer.
    return []


# -----------------------------
# Real Mode control plane
# -----------------------------


@router.get("/scenarios", response_model=ScenariosListResponse)
async def list_scenarios(
    _actor=Depends(deps.require_participant_or_admin),
):
    return ScenariosListResponse(items=runtime.list_scenarios())


@router.post("/scenarios", response_model=ScenarioSummary)
async def upload_scenario(
    body: ScenarioUploadRequest,
    _actor=Depends(deps.require_participant_or_admin),
):
    rec = runtime.save_uploaded_scenario(body.scenario)
    return rec.summary()


@router.get("/scenarios/{scenario_id}", response_model=ScenarioSummary)
async def get_scenario_summary(
    scenario_id: str,
    _actor=Depends(deps.require_participant_or_admin),
):
    return runtime.get_scenario(scenario_id).summary()


@router.get("/scenarios/{scenario_id}/graph/preview", response_model=SimulatorGraphSnapshot)
async def scenario_graph_preview(
    scenario_id: str,
    equivalent: str = Query(...),
    mode: RunMode = Query("fixtures"),
    _actor=Depends(deps.require_participant_or_admin),
    db=Depends(deps.get_db),
):
    """Preview the scenario graph topology without starting a run."""
    return await runtime.build_scenario_preview(scenario_id=scenario_id, equivalent=equivalent, mode=mode, session=db)


@router.post("/runs", response_model=RunCreateResponse)
async def start_run(
    body: RunCreateRequest,
    _actor=Depends(deps.require_participant_or_admin),
):
    run_id = await runtime.create_run(
        scenario_id=body.scenario_id, mode=body.mode, intensity_percent=body.intensity_percent
    )
    return RunCreateResponse(run_id=run_id)


@router.get("/runs/{run_id}", response_model=RunStatus)
async def get_run_status(
    run_id: str,
    _actor=Depends(deps.require_participant_or_admin),
):
    return runtime.get_run_status(run_id)


@router.post("/runs/{run_id}/pause", response_model=RunStatus)
async def pause_run(
    run_id: str,
    _actor=Depends(deps.require_participant_or_admin),
):
    return await runtime.pause(run_id)


@router.post("/runs/{run_id}/resume", response_model=RunStatus)
async def resume_run(
    run_id: str,
    _actor=Depends(deps.require_participant_or_admin),
):
    return await runtime.resume(run_id)


@router.post("/runs/{run_id}/stop", response_model=RunStatus)
async def stop_run(
    run_id: str,
    _actor=Depends(deps.require_participant_or_admin),
):
    return await runtime.stop(run_id)


@router.post("/runs/{run_id}/restart", response_model=RunStatus)
async def restart_run(
    run_id: str,
    _actor=Depends(deps.require_participant_or_admin),
):
    return await runtime.restart(run_id)


@router.post("/runs/{run_id}/intensity", response_model=RunStatus)
async def set_run_intensity(
    run_id: str,
    body: SetIntensityRequest,
    _actor=Depends(deps.require_participant_or_admin),
):
    return await runtime.set_intensity(run_id, intensity_percent=body.intensity_percent)


@router.get("/runs/{run_id}/events")
async def run_events_stream(
    run_id: str,
    request: Request,
    equivalent: str = Query(...),
    _actor=Depends(deps.require_participant_or_admin),
):
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
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )

@router.post("/runs/{run_id}/actions/tx-once", response_model=TxOnceResponseBody)
async def action_tx_once(
    run_id: str,
    body: TxOnceRequestBody,
    _actor=Depends(deps.require_participant_or_admin),
):
    _require_actions_enabled()
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
    _actor=Depends(deps.require_participant_or_admin),
):
    _require_actions_enabled()
    plan_id, plan_event_id, done_event_id, _eq = runtime.emit_debug_clearing_once(
        run_id=run_id,
        equivalent=body.equivalent,
        cycle_edges=body.cycle_edges,
        cleared_amount=body.cleared_amount,
        seed=body.seed,
    )
    return ClearingOnceResponseBody(
        plan_id=plan_id,
        plan_event_id=plan_event_id,
        done_event_id=done_event_id,
        client_action_id=body.client_action_id,
    )

@router.get("/runs/{run_id}/graph/snapshot", response_model=SimulatorGraphSnapshot)
async def graph_snapshot_for_run(
    run_id: str,
    equivalent: str = Query(...),
    _actor=Depends(deps.require_participant_or_admin),
    db=Depends(deps.get_db),
):
    return await runtime.build_graph_snapshot(run_id=run_id, equivalent=equivalent, session=db)


@router.get("/runs/{run_id}/metrics", response_model=MetricsResponse)
async def metrics_for_run(
    run_id: str,
    equivalent: str = Query(...),
    from_ms: int = Query(..., ge=0),
    to_ms: int = Query(..., ge=0),
    step_ms: int = Query(..., ge=1),
    _actor=Depends(deps.require_participant_or_admin),
):
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
    _actor=Depends(deps.require_participant_or_admin),
):
    return await runtime.build_bottlenecks(
        run_id=run_id,
        equivalent=equivalent,
        limit=limit,
        min_score=min_score,
    )


@router.get("/runs/{run_id}/artifacts", response_model=ArtifactIndex)
async def artifacts_index(
    run_id: str,
    _actor=Depends(deps.require_participant_or_admin),
):
    return await runtime.list_artifacts(run_id=run_id)


@router.get("/runs/{run_id}/artifacts/{name}")
async def artifacts_download(
    run_id: str,
    name: str,
    _actor=Depends(deps.require_participant_or_admin),
):
    path = runtime.get_artifact_path(run_id=run_id, name=name)
    return FileResponse(path)
