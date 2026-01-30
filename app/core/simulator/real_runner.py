from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import random
import secrets
import time
import uuid
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from typing import Any, Callable, Optional

from sqlalchemy import and_, func, or_, select

import app.db.session as db_session
import app.core.simulator.storage as simulator_storage
from app.config import settings
from app.core.clearing.service import ClearingService
from app.core.payments.service import PaymentService
from app.core.simulator.artifacts import ArtifactsManager
from app.core.simulator.models import RunRecord
from app.core.simulator.sse_broadcast import SseBroadcast
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine
from app.core.simulator.viz_patch_helper import VizPatchHelper
from app.schemas.simulator import (
    SimulatorClearingDoneEvent,
    SimulatorClearingPlanEvent,
    SimulatorTxFailedEvent,
    SimulatorTxUpdatedEvent,
)
from app.utils.exceptions import GeoException, TimeoutException


def map_rejection_code(err_details: Any) -> str:
    """Map structured error details to a stable simulator rejection code.

    This is intentionally a best-effort mapping for UI/analytics.
    Full diagnostics remain available in tx.failed.error.details.
    """

    default = "PAYMENT_REJECTED"
    if not isinstance(err_details, dict):
        return default

    exc_name = str(err_details.get("exc") or "")
    geo_code = str(err_details.get("geo_code") or "")

    if exc_name == "RoutingException":
        # E002 = insufficient capacity, E001 = generic routing not-found.
        if geo_code == "E002":
            return "ROUTING_NO_CAPACITY"
        if geo_code == "E001":
            return "ROUTING_NO_ROUTE"
        return "ROUTING_REJECTED"

    if exc_name == "TrustLineException":
        # E003 = limit exceeded, E004 = trust line inactive.
        if geo_code == "E003":
            return "TRUSTLINE_LIMIT_EXCEEDED"
        if geo_code == "E004":
            return "TRUSTLINE_NOT_ACTIVE"
        return "TRUSTLINE_REJECTED"

    return default


@dataclass(frozen=True)
class _RealPaymentAction:
    seq: int
    equivalent: str
    sender_pid: str
    receiver_pid: str
    amount: str


class RealRunner:
    def __init__(
        self,
        *,
        lock,
        get_run: Callable[[str], RunRecord],
        get_scenario_raw: Callable[[str], dict[str, Any]],
        sse: SseBroadcast,
        artifacts: ArtifactsManager,
        utc_now,
        publish_run_status: Callable[[str], None],
        db_enabled: Callable[[], bool],
        actions_per_tick_max: int,
        clearing_every_n_ticks: int,
        real_max_consec_tick_failures_default: int,
        real_max_timeouts_per_tick_default: int,
        real_max_errors_total_default: int,
        logger: logging.Logger,
    ) -> None:
        self._lock = lock
        self._get_run = get_run
        self._get_scenario_raw = get_scenario_raw
        self._sse = sse
        self._artifacts = artifacts
        self._utc_now = utc_now
        self._publish_run_status = publish_run_status
        self._db_enabled = db_enabled
        self._actions_per_tick_max = int(actions_per_tick_max)
        self._clearing_every_n_ticks = int(clearing_every_n_ticks)
        self._real_max_consec_tick_failures_default = int(real_max_consec_tick_failures_default)
        self._real_max_timeouts_per_tick_default = int(real_max_timeouts_per_tick_default)
        self._real_max_errors_total_default = int(real_max_errors_total_default)
        self._logger = logger

    async def tick_real_mode(self, run_id: str) -> None:
        run = self._get_run(run_id)
        scenario = self._get_scenario_raw(run.scenario_id)

        try:
            async with db_session.AsyncSessionLocal() as session:
                if not run._real_seeded:
                    await self._seed_scenario_into_db(session, scenario)
                    await session.commit()
                    run._real_seeded = True

                if run._real_participants is None or run._real_equivalents is None:
                    run._real_participants = await self._load_real_participants(session, scenario)
                    run._real_equivalents = [str(x) for x in (scenario.get("equivalents") or []) if str(x).strip()]

                participants = run._real_participants or []
                equivalents = run._real_equivalents or []
                if len(participants) < 2 or not equivalents:
                    return

                planned = self._plan_real_payments(run, scenario)
                with self._lock:
                    run.ops_sec = float(len(planned))
                    run.queue_depth = len(planned)
                    run._real_in_flight = 0
                    run.current_phase = "payments" if planned else None
                sender_id_by_pid = {pid: participant_id for (participant_id, pid) in participants}

                max_timeouts_per_tick = int(
                    os.getenv(
                        "SIMULATOR_REAL_MAX_TIMEOUTS_PER_TICK",
                        str(self._real_max_timeouts_per_tick_default),
                    )
                )
                max_errors_total = int(
                    os.getenv(
                        "SIMULATOR_REAL_MAX_ERRORS_TOTAL",
                        str(self._real_max_errors_total_default),
                    )
                )

                committed = 0
                rejected = 0
                errors = 0
                timeouts = 0

                sem = asyncio.Semaphore(max(1, int(run._real_max_in_flight)))

                per_eq: dict[str, dict[str, int]] = {
                    str(eq): {"committed": 0, "rejected": 0, "errors": 0, "timeouts": 0} for eq in equivalents
                }
                per_eq_route: dict[str, dict[str, float]] = {
                    str(eq): {"route_len_sum": 0.0, "route_len_n": 0.0} for eq in equivalents
                }
                per_eq_metric_values: dict[str, dict[str, float]] = {str(eq): {} for eq in equivalents}

                per_eq_edge_stats: dict[str, dict[tuple[str, str], dict[str, int]]] = {str(eq): {} for eq in equivalents}

                def _edge_inc(eq: str, src: str, dst: str, key: str, n: int = 1) -> None:
                    m = per_eq_edge_stats.setdefault(str(eq), {})
                    st = m.setdefault(
                        (str(src), str(dst)),
                        {"attempts": 0, "committed": 0, "rejected": 0, "errors": 0, "timeouts": 0},
                    )
                    st[key] = int(st.get(key, 0)) + int(n)

                async def _do_one(
                    action: _RealPaymentAction,
                ) -> tuple[
                    int,
                    str,
                    str,
                    str,
                    str | None,
                    str | None,
                    dict[str, Any] | None,
                    float,
                    list[tuple[str, str]],
                ]:
                    """Returns (seq, eq, sender_pid, receiver_pid, status, error_code, error_details, avg_route_len, route_edges)."""

                    sender_id = sender_id_by_pid.get(action.sender_pid)
                    if sender_id is None:
                        return (
                            action.seq,
                            action.equivalent,
                            action.sender_pid,
                            action.receiver_pid,
                            None,
                            "SENDER_NOT_FOUND",
                            {"reason": "SENDER_NOT_FOUND"},
                            0.0,
                            [],
                        )

                    idem = self._sim_idempotency_key(
                        run_id=run.run_id,
                        tick_ms=run.tick_index,
                        sender_pid=action.sender_pid,
                        receiver_pid=action.receiver_pid,
                        equivalent=action.equivalent,
                        amount=action.amount,
                        seq=action.seq,
                    )

                    async with sem:
                        with self._lock:
                            run._real_in_flight += 1

                        try:
                            async with db_session.AsyncSessionLocal() as s2:
                                service = PaymentService(s2)
                                res = await service.create_payment_internal(
                                    sender_id,
                                    to_pid=action.receiver_pid,
                                    equivalent=action.equivalent,
                                    amount=action.amount,
                                    idempotency_key=idem,
                                )
                                await s2.commit()

                            status = str(getattr(res, "status", None) or "")

                            route_edges: list[tuple[str, str]] = []
                            try:
                                routes = getattr(res, "routes", None)
                                if routes:
                                    for r in routes:
                                        path = getattr(r, "path", None)
                                        if not path or len(path) < 2:
                                            continue
                                        route_edges = [(str(a), str(b)) for a, b in zip(path, path[1:])]
                                        if route_edges:
                                            break
                            except Exception:
                                route_edges = []

                            avg_route_len = 0.0
                            try:
                                routes = getattr(res, "routes", None)
                                if routes:
                                    lens: list[float] = []
                                    for r in routes:
                                        path = getattr(r, "path", None)
                                        if not path or len(path) < 2:
                                            continue
                                        lens.append(float(max(1, int(len(path) - 1))))
                                    if lens:
                                        avg_route_len = float(sum(lens) / len(lens))
                            except Exception:
                                avg_route_len = 0.0

                            return (
                                action.seq,
                                action.equivalent,
                                action.sender_pid,
                                action.receiver_pid,
                                status,
                                None,
                                None,
                                float(avg_route_len),
                                route_edges,
                            )
                        except Exception as e:
                            # Classification:
                            # - Timeouts are errors.
                            # - GeoException with 4xx status are expected rejections (bad route, not found, etc).
                            # - Everything else is INTERNAL_ERROR.
                            code = "INTERNAL_ERROR"
                            status = None
                            err_details: dict[str, Any] | None = {
                                "exc": type(e).__name__,
                                "message": str(e),
                            }

                            if isinstance(e, TimeoutException):
                                code = "PAYMENT_TIMEOUT"
                                err_details = {
                                    "exc": type(e).__name__,
                                    "geo_code": getattr(e, "code", None),
                                    "message": getattr(e, "message", str(e)),
                                    "details": getattr(e, "details", None),
                                }
                            elif isinstance(e, GeoException):
                                geo_status = int(getattr(e, "status_code", 500) or 500)
                                # Treat client errors as clean rejection, not simulator errors_total.
                                if 400 <= geo_status < 500:
                                    status = "REJECTED"
                                    code = None
                                err_details = {
                                    "exc": type(e).__name__,
                                    "geo_code": getattr(e, "code", None),
                                    "message": getattr(e, "message", str(e)),
                                    "details": getattr(e, "details", None),
                                    "status_code": geo_status,
                                }
                            return (
                                action.seq,
                                action.equivalent,
                                action.sender_pid,
                                action.receiver_pid,
                                status,
                                code,
                                err_details,
                                0.0,
                                [],
                            )
                        finally:
                            with self._lock:
                                run._real_in_flight = max(0, run._real_in_flight - 1)

                tasks = [asyncio.create_task(_do_one(a)) for a in planned]

                next_seq = 0
                ready: dict[
                    int,
                    tuple[
                        str,
                        str,
                        str,
                        str | None,
                        str | None,
                        dict[str, Any] | None,
                        float,
                        list[tuple[str, str]],
                        list[dict[str, Any]] | None,
                        list[dict[str, Any]] | None,
                    ],
                ] = {}

                def _inc(eq: str, key: str, n: int = 1) -> None:
                    d = per_eq.setdefault(
                        str(eq),
                        {"committed": 0, "rejected": 0, "errors": 0, "timeouts": 0},
                    )
                    d[key] = int(d.get(key, 0)) + int(n)

                def _route_add(eq: str, route_len: float) -> None:
                    d = per_eq_route.setdefault(str(eq), {"route_len_sum": 0.0, "route_len_n": 0.0})
                    d["route_len_sum"] = float(d.get("route_len_sum", 0.0)) + float(route_len)
                    d["route_len_n"] = float(d.get("route_len_n", 0.0)) + 1.0

                def _emit_if_ready() -> None:
                    nonlocal next_seq, committed, rejected, errors, timeouts
                    while True:
                        item = ready.get(next_seq)
                        if item is None:
                            return
                        del ready[next_seq]
                        eq, sender_pid, receiver_pid, status, err_code, err_details, avg_route_len, route_edges, edge_patch, node_patch = item

                        edges_pairs = route_edges or [(sender_pid, receiver_pid)]

                        if err_code is not None:
                            errors += 1
                            _inc(eq, "errors")
                            for a, b in edges_pairs:
                                _edge_inc(eq, a, b, "attempts")
                                _edge_inc(eq, a, b, "errors")
                            if err_code == "PAYMENT_TIMEOUT":
                                timeouts += 1
                                _inc(eq, "timeouts")
                                for a, b in edges_pairs:
                                    _edge_inc(eq, a, b, "timeouts")
                            if err_code == "PAYMENT_REJECTED":
                                _inc(eq, "rejected")
                                for a, b in edges_pairs:
                                    _edge_inc(eq, a, b, "rejected")

                            with self._lock:
                                run.errors_total += 1
                                run._error_timestamps.append(time.time())
                                cutoff = time.time() - 60.0
                                while run._error_timestamps and run._error_timestamps[0] < cutoff:
                                    run._error_timestamps.popleft()
                                run.last_error = {
                                    "code": err_code,
                                    "message": str((err_details or {}).get("message") or err_code),
                                    "at": self._utc_now().isoformat(),
                                }
                                run.last_event_type = "tx.failed"

                            failed_evt = SimulatorTxFailedEvent(
                                event_id=self._sse.next_event_id(run),
                                ts=self._utc_now(),
                                type="tx.failed",
                                equivalent=eq,
                                from_=sender_pid,
                                to=receiver_pid,
                                error={
                                    "code": err_code,
                                    "message": str((err_details or {}).get("message") or err_code),
                                    "at": self._utc_now(),
                                    "details": err_details,
                                },
                            ).model_dump(mode="json", by_alias=True)
                            self._sse.broadcast(run_id, failed_evt)
                        else:
                            for a, b in edges_pairs:
                                _edge_inc(eq, a, b, "attempts")
                            if status == "COMMITTED":
                                committed += 1
                                _inc(eq, "committed")
                                for a, b in edges_pairs:
                                    _edge_inc(eq, a, b, "committed")
                                if float(avg_route_len) > 0:
                                    _route_add(eq, float(avg_route_len))

                                evt_dict = SimulatorTxUpdatedEvent(
                                    event_id=self._sse.next_event_id(run),
                                    ts=self._utc_now(),
                                    type="tx.updated",
                                    equivalent=eq,
                                    ttl_ms=1200,
                                    edges=[{"from": a, "to": b} for a, b in edges_pairs],
                                    node_badges=None,
                                ).model_dump(mode="json", by_alias=True)
                                if edge_patch:
                                    evt_dict["edge_patch"] = edge_patch
                                if node_patch:
                                    evt_dict["node_patch"] = node_patch
                                with self._lock:
                                    run.last_event_type = "tx.updated"
                                self._sse.broadcast(run_id, evt_dict)
                            else:
                                rejected += 1
                                _inc(eq, "rejected")
                                for a, b in edges_pairs:
                                    _edge_inc(eq, a, b, "rejected")

                                try:
                                    rejection_code = map_rejection_code(err_details)
                                except Exception:
                                    rejection_code = "PAYMENT_REJECTED"

                                with self._lock:
                                    run.last_event_type = "tx.failed"

                                failed_evt = SimulatorTxFailedEvent(
                                    event_id=self._sse.next_event_id(run),
                                    ts=self._utc_now(),
                                    type="tx.failed",
                                    equivalent=eq,
                                    from_=sender_pid,
                                    to=receiver_pid,
                                    error={
                                        "code": rejection_code,
                                        "message": rejection_code,
                                        "at": self._utc_now(),
                                        "details": err_details,
                                    },
                                ).model_dump(mode="json", by_alias=True)
                                self._sse.broadcast(run_id, failed_evt)

                        with self._lock:
                            run.queue_depth = max(0, run.queue_depth - 1)

                        next_seq += 1

                try:
                    if tasks:
                        for t in asyncio.as_completed(tasks):
                            seq, eq, sender_pid, receiver_pid, status, err_code, err_details, avg_route_len, route_edges = await t

                            if run.state != "running":
                                break

                            # Build edge_patch for committed transactions (Variant A: SSE patches)
                            edge_patch_list: list[dict[str, Any]] | None = None
                            node_patch_list: list[dict[str, Any]] | None = None
                            if status == "COMMITTED":
                                edges_pairs = route_edges or [(sender_pid, receiver_pid)]

                                edge_patch_list = []
                                try:
                                    async with db_session.AsyncSessionLocal() as patch_session:
                                        helper: VizPatchHelper | None
                                        with self._lock:
                                            helper = run._real_viz_by_eq.get(str(eq))

                                        if helper is None:
                                            helper = await VizPatchHelper.create(
                                                patch_session,
                                                equivalent_code=str(eq),
                                                refresh_every_ticks=int(
                                                    getattr(settings, "SIMULATOR_VIZ_QUANTILE_REFRESH_TICKS", 10) or 10
                                                ),
                                            )
                                            with self._lock:
                                                run._real_viz_by_eq[str(eq)] = helper

                                        eq_id = helper.equivalent_id

                                        participant_ids: list[uuid.UUID] = []
                                        if run._real_participants:
                                            participant_ids = [pid for (pid, _) in run._real_participants]
                                        await helper.maybe_refresh_quantiles(
                                            patch_session,
                                            tick_index=int(run.tick_index),
                                            participant_ids=participant_ids,
                                        )

                                        # Fetch participants in one query.
                                        pids = sorted({pid for ab in edges_pairs for pid in ab if pid})
                                        res = await patch_session.execute(select(Participant).where(Participant.pid.in_(pids)))
                                        pid_to_participant = {p.pid: p for p in res.scalars().all()}

                                        try:
                                            node_patch_list = await helper.compute_node_patches(
                                                patch_session,
                                                pid_to_participant=pid_to_participant,
                                                pids=pids,
                                            )
                                            if node_patch_list == []:
                                                node_patch_list = None
                                        except Exception as e_np:
                                            self._logger.warning(f"Failed to build node_patch: {e_np}")
                                            node_patch_list = None

                                        # Batch-load debts and trustlines for the affected edges (avoid N+1).
                                        id_pairs: list[tuple[uuid.UUID, uuid.UUID]] = []
                                        for src_pid, dst_pid in edges_pairs:
                                            src_part = pid_to_participant.get(src_pid)
                                            dst_part = pid_to_participant.get(dst_pid)
                                            if not src_part or not dst_part:
                                                continue
                                            id_pairs.append((src_part.id, dst_part.id))

                                        debt_by_pair: dict[tuple[uuid.UUID, uuid.UUID], Decimal] = {}
                                        tl_by_pair: dict[tuple[uuid.UUID, uuid.UUID], tuple[Decimal, str | None]] = {}

                                        if id_pairs:
                                            debt_cond = or_(*[and_(Debt.creditor_id == a, Debt.debtor_id == b) for a, b in id_pairs])
                                            debt_rows = (
                                                await patch_session.execute(
                                                    select(
                                                        Debt.creditor_id,
                                                        Debt.debtor_id,
                                                        func.coalesce(func.sum(Debt.amount), 0).label("amount"),
                                                    )
                                                    .where(Debt.equivalent_id == eq_id, debt_cond)
                                                    .group_by(Debt.creditor_id, Debt.debtor_id)
                                                )
                                            ).all()
                                            debt_by_pair = {
                                                (r.creditor_id, r.debtor_id): (r.amount or Decimal("0")) for r in debt_rows
                                            }

                                            tl_cond = or_(
                                                *[
                                                    and_(
                                                        TrustLine.from_participant_id == a,
                                                        TrustLine.to_participant_id == b,
                                                    )
                                                    for a, b in id_pairs
                                                ]
                                            )
                                            tl_rows = (
                                                await patch_session.execute(
                                                    select(
                                                        TrustLine.from_participant_id,
                                                        TrustLine.to_participant_id,
                                                        TrustLine.limit,
                                                        TrustLine.status,
                                                    ).where(TrustLine.equivalent_id == eq_id, tl_cond)
                                                )
                                            ).all()
                                            tl_by_pair = {
                                                (r.from_participant_id, r.to_participant_id): (r.limit or Decimal("0"), r.status)
                                                for r in tl_rows
                                            }

                                        for src_pid, dst_pid in edges_pairs:
                                            src_part = pid_to_participant.get(src_pid)
                                            dst_part = pid_to_participant.get(dst_pid)
                                            if not src_part or not dst_part:
                                                continue

                                            used_amt = debt_by_pair.get((src_part.id, dst_part.id), Decimal("0"))
                                            limit_amt, tl_status = tl_by_pair.get(
                                                (src_part.id, dst_part.id),
                                                (Decimal("0"), None),
                                            )
                                            available_amt = max(Decimal("0"), limit_amt - used_amt)

                                            edge_viz = helper.edge_viz(
                                                status=tl_status,
                                                used=used_amt,
                                                limit=limit_amt,
                                            )

                                            edge_patch_list.append(
                                                {
                                                    "source": src_pid,
                                                    "target": dst_pid,
                                                    "used": str(used_amt),
                                                    "available": str(available_amt),
                                                    **edge_viz,
                                                }
                                            )
                                except Exception as e_patch:
                                    self._logger.warning(f"Failed to build edge_patch: {e_patch}")
                                    edge_patch_list = None
                                    node_patch_list = None

                                if edge_patch_list == []:
                                    edge_patch_list = None

                            ready[seq] = (
                                eq,
                                sender_pid,
                                receiver_pid,
                                status,
                                err_code,
                                err_details,
                                avg_route_len,
                                route_edges,
                                edge_patch_list,
                                node_patch_list,
                            )
                            _emit_if_ready()

                            if max_timeouts_per_tick > 0 and timeouts >= max_timeouts_per_tick:
                                await self.fail_run(
                                    run_id,
                                    code="REAL_MODE_TOO_MANY_TIMEOUTS",
                                    message=f"Too many payment timeouts in one tick: {timeouts}",
                                )
                                break
                finally:
                    # Cancel any remaining tasks if we bailed early.
                    for task in tasks:
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)

                # Emit anything that completed but wasn't emitted yet.
                _emit_if_ready()

                with self._lock:
                    run._real_in_flight = 0
                    run.queue_depth = 0
                    run.current_phase = None
                    run._real_consec_tick_failures = 0

                    if max_errors_total > 0 and run.errors_total >= max_errors_total:
                        # Mark error; heartbeat task will be cancelled in _fail_run.
                        pass

                if max_errors_total > 0 and run.errors_total >= max_errors_total:
                    await self.fail_run(
                        run_id,
                        code="REAL_MODE_TOO_MANY_ERRORS",
                        message=f"Too many total errors: {run.errors_total}",
                    )
                    return

                # Best-effort clearing (optional MVP): once in a while, attempt clearing per equivalent.
                clearing_volume_by_eq: dict[str, float] = {str(eq): 0.0 for eq in equivalents}
                if (
                    self._clearing_every_n_ticks > 0
                    and run.tick_index % self._clearing_every_n_ticks == 0
                    and bool(getattr(settings, "CLEARING_ENABLED", True))
                ):
                    clearing_volume_by_eq = await self.tick_real_mode_clearing(session, run_id, run, equivalents)

                # Real total debt snapshot (sum of all debts for the equivalent).
                total_debt_by_eq: dict[str, float] = {str(eq): 0.0 for eq in equivalents}
                try:
                    eq_rows = (
                        await session.execute(select(Equivalent.id, Equivalent.code).where(Equivalent.code.in_(list(equivalents))))
                    ).all()
                    eq_id_by_code = {str(code): eq_id for (eq_id, code) in eq_rows}
                    for eq_code, eq_id in eq_id_by_code.items():
                        total = (
                            await session.execute(select(func.coalesce(func.sum(Debt.amount), 0)).where(Debt.equivalent_id == eq_id))
                        ).scalar_one()
                        total_debt_by_eq[str(eq_code)] = float(total)
                except Exception:
                    pass

                # Avg route length for this tick (successful payments).
                for eq in equivalents:
                    r = per_eq_route.get(str(eq), {})
                    n = float(r.get("route_len_n", 0.0) or 0.0)
                    s = float(r.get("route_len_sum", 0.0) or 0.0)
                    per_eq_metric_values[str(eq)]["avg_route_length"] = float(s / n) if n > 0 else 0.0
                    per_eq_metric_values[str(eq)]["total_debt"] = float(total_debt_by_eq.get(str(eq), 0.0) or 0.0)
                    per_eq_metric_values[str(eq)]["clearing_volume"] = float(clearing_volume_by_eq.get(str(eq), 0.0) or 0.0)

                await simulator_storage.write_tick_metrics(
                    run_id=run.run_id,
                    t_ms=int(run.sim_time_ms),
                    per_equivalent=per_eq,
                    metric_values_by_eq=per_eq_metric_values,
                    session=session,
                )

                # Persist bottlenecks snapshot derived from actual tick outcomes.
                if self._db_enabled():
                    computed_at = self._utc_now()
                    for eq in equivalents:
                        await simulator_storage.write_tick_bottlenecks(
                            run_id=run.run_id,
                            equivalent=str(eq),
                            computed_at=computed_at,
                            edge_stats=per_eq_edge_stats.get(str(eq), {}),
                            session=session,
                            limit=50,
                        )

                self._artifacts.write_real_tick_artifact(
                    run,
                    {
                        "tick_index": run.tick_index,
                        "sim_time_ms": run.sim_time_ms,
                        "budget": len(planned),
                        "committed": committed,
                        "rejected": rejected,
                        "errors": errors,
                        "timeouts": timeouts,
                    },
                )
                await simulator_storage.sync_artifacts(run)
        except Exception as e:
            with self._lock:
                run.errors_total += 1
                run._error_timestamps.append(time.time())
                cutoff = time.time() - 60.0
                while run._error_timestamps and run._error_timestamps[0] < cutoff:
                    run._error_timestamps.popleft()
                run._real_consec_tick_failures += 1
                run.last_error = {
                    "code": "REAL_MODE_TICK_FAILED",
                    "message": str(e),
                    "at": self._utc_now().isoformat(),
                }

            max_consec = int(
                os.getenv(
                    "SIMULATOR_REAL_MAX_CONSEC_TICK_FAILURES",
                    str(self._real_max_consec_tick_failures_default),
                )
            )
            if max_consec > 0 and run._real_consec_tick_failures >= max_consec:
                await self.fail_run(
                    run_id,
                    code="REAL_MODE_TICK_FAILED_REPEATED",
                    message=f"Real-mode tick failed {run._real_consec_tick_failures} times in a row",
                )

    async def fail_run(self, run_id: str, *, code: str, message: str) -> None:
        run = self._get_run(run_id)
        task = None
        with self._lock:
            if run.state in ("stopped", "stopping", "error"):
                return

            run.state = "error"
            run.stopped_at = self._utc_now()
            run.current_phase = None
            run.queue_depth = 0
            run._real_in_flight = 0
            run.errors_total += 1
            run._error_timestamps.append(time.time())
            cutoff = time.time() - 60.0
            while run._error_timestamps and run._error_timestamps[0] < cutoff:
                run._error_timestamps.popleft()
            run.last_error = {"code": code, "message": message, "at": self._utc_now().isoformat()}
            task = run._heartbeat_task

        self._publish_run_status(run_id)
        await simulator_storage.upsert_run(run)
        if task is not None:
            task.cancel()

    async def tick_real_mode_clearing(
        self,
        session,  # NOTE: Unused now; clearing uses its own isolated session
        run_id: str,
        run: RunRecord,
        equivalents: list[str],
    ) -> dict[str, float]:
        """Execute clearing for all equivalents using an isolated session.
        
        IMPORTANT: This method uses its own session to avoid poisoning the parent
        tick_real_mode session with commit/rollback side effects. PostgreSQL marks
        a transaction as "aborted" after any error, and subsequent queries fail
        with InFailedSQLTransactionError.
        """
        try:
            max_depth = int(os.getenv("SIMULATOR_CLEARING_MAX_DEPTH", "6"))
        except Exception:
            max_depth = 6
        cleared_amount_by_eq: dict[str, float] = {str(eq): 0.0 for eq in equivalents}
        for eq in equivalents:
            # Use isolated session per equivalent to prevent transaction poisoning
            try:
                async with db_session.AsyncSessionLocal() as clearing_session:
                    service = ClearingService(clearing_session)
                    
                    # Plan step: find at least one cycle to visualize.
                    cycles = await service.find_cycles(eq, max_depth=max_depth)
                    if not cycles:
                        continue

                    plan_id = f"plan_{secrets.token_hex(6)}"
                    plan_evt = SimulatorClearingPlanEvent(
                        event_id=self._sse.next_event_id(run),
                        ts=self._utc_now(),
                        type="clearing.plan",
                        equivalent=eq,
                        plan_id=plan_id,
                        steps=[
                            {
                                "at_ms": 0,
                                "intensity_key": "mid",
                                "flash": {"kind": "info", "title": "Clearing", "detail": "Auto clearing"},
                            }
                        ],
                    ).model_dump(mode="json")

                    with self._lock:
                        run.last_event_type = "clearing.plan"
                        run.current_phase = "clearing"
                    self._sse.broadcast(run_id, plan_evt)

                    # Execute with stats (volume = sum of cleared amounts).
                    cleared_cycles = 0
                    cleared_amount = 0.0
                    while True:
                        cycles = await service.find_cycles(eq, max_depth=max_depth)
                        if not cycles:
                            break

                        executed = False
                        for cycle in cycles:
                            # Clearing amount is min edge amount in cycle.
                            try:
                                amts: list[float] = []
                                for edge in cycle:
                                    if isinstance(edge, dict):
                                        amts.append(float(edge.get("amount")))
                                    else:
                                        amts.append(float(getattr(edge, "amount")))
                                clear_amount = float(min(amts)) if amts else 0.0
                            except Exception:
                                clear_amount = 0.0

                            success = await service.execute_clearing(cycle)
                            if success:
                                cleared_cycles += 1
                                cleared_amount += float(max(0.0, clear_amount))
                                executed = True
                                break

                        if not executed:
                            break
                        if cleared_cycles > 100:
                            break

                    # NOTE: ClearingService.execute_clearing already commits on success
                    # No need for additional commit here
                    cleared_amount_by_eq[str(eq)] = float(cleared_amount)

                    done_evt = SimulatorClearingDoneEvent(
                        event_id=self._sse.next_event_id(run),
                        ts=self._utc_now(),
                        type="clearing.done",
                        equivalent=eq,
                        plan_id=plan_id,
                    ).model_dump(mode="json")
                    with self._lock:
                        run.last_event_type = "clearing.done"
                        run.current_phase = None
                    self._sse.broadcast(run_id, done_evt)
            except Exception as e:
                with self._lock:
                    run.errors_total += 1
                    run._error_timestamps.append(time.time())
                    cutoff = time.time() - 60.0
                    while run._error_timestamps and run._error_timestamps[0] < cutoff:
                        run._error_timestamps.popleft()
                    run.last_error = {
                        "code": "CLEARING_ERROR",
                        "message": str(e),
                        "at": self._utc_now().isoformat(),
                    }
                continue

        return cleared_amount_by_eq

    def _real_candidates_from_scenario(self, scenario: dict[str, Any]) -> list[dict[str, Any]]:
        tls = scenario.get("trustlines") or []
        out: list[dict[str, Any]] = []
        for tl in tls:
            status = str(tl.get("status") or "active").strip().lower()
            if status != "active":
                continue

            eq = str(tl.get("equivalent") or "").strip()
            frm = str(tl.get("from") or "").strip()
            to = str(tl.get("to") or "").strip()
            if not eq or not frm or not to:
                continue

            try:
                limit = Decimal(str(tl.get("limit")))
            except Exception:
                continue
            if limit <= 0:
                continue

            # TrustLine direction is creditor->debtor. Payment from debtor->creditor.
            # TODO: Consider pre-filtering by DB-derived available capacity (taking current "used" into account)
            # to reduce rejected payment attempts under high load.
            out.append({"equivalent": eq, "sender_pid": to, "receiver_pid": frm, "limit": limit})

        out.sort(key=lambda x: (x["equivalent"], x["receiver_pid"], x["sender_pid"]))
        return out

    def _plan_real_payments(self, run: RunRecord, scenario: dict[str, Any]) -> list[_RealPaymentAction]:
        """Deterministic planner for Real Mode payment actions.

        Important property for SB-NF-04:
        - planning for a given (seed, tick_index, scenario) is deterministic.
        - changing intensity only changes *how many* actions we take from the same
          per-tick ordering (prefix-stable), so it doesn't affect later ticks.
        """

        intensity = max(0.0, min(1.0, float(run.intensity_percent) / 100.0))
        budget = int(self._actions_per_tick_max * intensity)
        if budget <= 0:
            return []

        candidates = self._real_candidates_from_scenario(scenario)
        if not candidates:
            return []

        tick_seed = (int(run.seed) * 1_000_003 + int(run.tick_index)) & 0xFFFFFFFF
        tick_rng = random.Random(tick_seed)

        order = list(candidates)
        tick_rng.shuffle(order)

        planned: list[_RealPaymentAction] = []
        for i in range(budget):
            c = order[i % len(order)]
            limit = c["limit"]

            action_seed = (tick_seed * 1_000_003 + i) & 0xFFFFFFFF
            action_rng = random.Random(action_seed)
            amount = self._real_pick_amount(action_rng, limit)
            if amount is None:
                continue

            planned.append(
                _RealPaymentAction(
                    seq=i,
                    equivalent=c["equivalent"],
                    sender_pid=c["sender_pid"],
                    receiver_pid=c["receiver_pid"],
                    amount=amount,
                )
            )

        return planned

    def _real_pick_amount(self, rng: random.Random, limit: Decimal) -> str | None:
        # Keep it small and <= limit.
        cap = min(limit, Decimal("3"))
        if cap <= 0:
            return None

        raw = Decimal(str(0.1 + rng.random() * float(cap)))
        amt = min(raw, cap).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
        if amt <= 0:
            return None
        return format(amt, "f")

    def _sim_idempotency_key(
        self,
        *,
        run_id: str,
        tick_ms: int,
        sender_pid: str,
        receiver_pid: str,
        equivalent: str,
        amount: str,
        seq: int,
    ) -> str:
        material = f"{run_id}|{tick_ms}|{sender_pid}|{receiver_pid}|{equivalent}|{amount}|{seq}"
        return "sim:" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]

    async def _load_real_participants(self, session, scenario: dict[str, Any]) -> list[tuple[uuid.UUID, str]]:
        pids = [str(p.get("id") or "").strip() for p in (scenario.get("participants") or [])]
        pids = [p for p in pids if p]
        if not pids:
            return []

        rows = (await session.execute(select(Participant).where(Participant.pid.in_(pids)))).scalars().all()
        by_pid = {p.pid: p for p in rows}
        out: list[tuple[uuid.UUID, str]] = []
        for pid in sorted(pids):
            rec = by_pid.get(pid)
            if rec is None:
                continue
            out.append((rec.id, rec.pid))
        return out

    async def _seed_scenario_into_db(self, session, scenario: dict[str, Any]) -> None:
        # Equivalents
        eq_codes = [str(x).strip().upper() for x in (scenario.get("equivalents") or [])]
        eq_codes = [c for c in eq_codes if c]

        if eq_codes:
            existing_eq = (await session.execute(select(Equivalent).where(Equivalent.code.in_(eq_codes)))).scalars().all()
            have = {e.code for e in existing_eq}
            for code in eq_codes:
                if code in have:
                    continue
                session.add(Equivalent(code=code, is_active=True, metadata_={}))

        # Participants
        participants = scenario.get("participants") or []
        pids = [str(p.get("id") or "").strip() for p in participants]
        pids = [p for p in pids if p]
        if pids:
            existing_p = (await session.execute(select(Participant).where(Participant.pid.in_(pids)))).scalars().all()
            have_p = {p.pid for p in existing_p}
            for p in participants:
                pid = str(p.get("id") or "").strip()
                if not pid or pid in have_p:
                    continue
                name = str(p.get("name") or pid)
                p_type = str(p.get("type") or "person").strip() or "person"
                status = str(p.get("status") or "active").strip().lower()
                if status == "frozen":
                    status = "suspended"
                elif status == "banned":
                    status = "deleted"
                elif status not in {"active", "suspended", "left", "deleted"}:
                    status = "active"
                public_key = hashlib.sha256(pid.encode("utf-8")).hexdigest()
                session.add(
                    Participant(
                        pid=pid,
                        display_name=name,
                        public_key=public_key,
                        type=p_type if p_type in {"person", "business", "hub"} else "person",
                        status=status,
                        profile={},
                    )
                )

        # NOTE: app.db.session.AsyncSessionLocal has autoflush=False.
        # We must flush pending inserts before querying IDs for trustlines.
        await session.flush()

        # Trustlines
        trustlines = scenario.get("trustlines") or []
        if trustlines and eq_codes and pids:
            default_policy = {
                "auto_clearing": True,
                "can_be_intermediate": True,
                "max_hop_usage": None,
                "daily_limit": None,
                "blocked_participants": [],
            }
            # Load ids
            eq_rows = (await session.execute(select(Equivalent).where(Equivalent.code.in_(eq_codes)))).scalars().all()
            eq_by_code = {e.code: e for e in eq_rows}

            p_rows = (await session.execute(select(Participant).where(Participant.pid.in_(pids)))).scalars().all()
            p_by_pid = {p.pid: p for p in p_rows}

            for tl in trustlines:
                eq = str(tl.get("equivalent") or "").strip().upper()
                if not eq or eq not in eq_by_code:
                    continue
                from_pid = str(tl.get("from") or "").strip()
                to_pid = str(tl.get("to") or "").strip()
                if not from_pid or not to_pid:
                    continue
                p_from = p_by_pid.get(from_pid)
                p_to = p_by_pid.get(to_pid)
                if p_from is None or p_to is None:
                    continue

                raw_limit = tl.get("limit")
                try:
                    limit = Decimal(str(raw_limit))
                except (InvalidOperation, ValueError):
                    continue
                if limit < 0:
                    continue

                existing = (
                    await session.execute(
                        select(TrustLine).where(
                            TrustLine.from_participant_id == p_from.id,
                            TrustLine.to_participant_id == p_to.id,
                            TrustLine.equivalent_id == eq_by_code[eq].id,
                        )
                    )
                ).scalar_one_or_none()
                if existing is not None:
                    continue

                status = str(tl.get("status") or "active").strip().lower()
                if status not in {"active", "frozen", "closed"}:
                    status = "active"

                policy = tl.get("policy")
                if not isinstance(policy, dict):
                    policy = default_policy

                session.add(
                    TrustLine(
                        from_participant_id=p_from.id,
                        to_participant_id=p_to.id,
                        equivalent_id=eq_by_code[eq].id,
                        limit=limit,
                        status=status,
                        policy=policy,
                    )
                )
