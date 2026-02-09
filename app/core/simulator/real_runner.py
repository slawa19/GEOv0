from __future__ import annotations

import asyncio
import hashlib
import math
import logging
import os
import random
import secrets
import time
import uuid
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from typing import Any, Callable

from sqlalchemy import and_, func, or_, select

import app.db.session as db_session
import app.core.simulator.storage as simulator_storage
from app.config import settings
from app.core.clearing.service import ClearingService
from app.core.payments.service import PaymentService
from app.core.simulator.artifacts import ArtifactsManager
from app.core.simulator.models import RunRecord
from app.core.simulator.runtime_utils import safe_int_env as _safe_int_env
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


def _safe_decimal_env(name: str, default: Decimal) -> Decimal:
    try:
        raw = os.getenv(name, "")
        if not str(raw).strip():
            return default
        v = Decimal(str(raw))
        if v.is_nan() or v <= 0:
            return default
        return v.quantize(Decimal("0.01"), rounding=ROUND_DOWN)
    except (InvalidOperation, Exception):
        return default


def _safe_optional_decimal_env(name: str) -> Decimal | None:
    try:
        raw = os.getenv(name, "")
        if not str(raw).strip():
            return None
        v = Decimal(str(raw))
        if v.is_nan() or v <= 0:
            return None
        return v.quantize(Decimal("0.01"), rounding=ROUND_DOWN)
    except (InvalidOperation, Exception):
        return None


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
    msg = str(err_details.get("message") or "")

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

    # Generic HTTP-ish application errors (GeoException subclasses)
    # Mapped to stable UI/analytics labels.
    if exc_name == "NotFoundException":
        # Best-effort parsing by message. Keep this intentionally simple and
        # stable (tests rely on these exact outcomes).
        m = msg.lower()
        if "equivalent" in m:
            return "EQUIVALENT_NOT_FOUND"
        if "participants not found" in m or "participant" in m:
            return "PARTICIPANT_NOT_FOUND"
        if "transaction" in m or "tx" in m:
            return "TX_NOT_FOUND"
        return default

    if exc_name == "BadRequestException":
        # E009 = validation error
        if geo_code == "E009":
            return "INVALID_INPUT"
        return "INVALID_INPUT"

    if exc_name == "ConflictException":
        return "CONFLICT"

    if exc_name == "UnauthorizedException":
        return "UNAUTHORIZED"

    if exc_name == "ForbiddenException":
        return "FORBIDDEN"

    if exc_name in {"InvalidSignatureException", "CryptoException"}:
        return "INVALID_SIGNATURE"

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
        self._real_max_consec_tick_failures_default = int(
            real_max_consec_tick_failures_default
        )
        self._real_max_timeouts_per_tick_default = int(
            real_max_timeouts_per_tick_default
        )
        self._real_max_errors_total_default = int(real_max_errors_total_default)
        self._logger = logger

        # Cache env-derived limits (avoid getenv on every tick).
        self._real_max_consec_tick_failures_limit = _safe_int_env(
            "SIMULATOR_REAL_MAX_CONSEC_TICK_FAILURES",
            int(self._real_max_consec_tick_failures_default),
        )
        self._real_max_timeouts_per_tick_limit = _safe_int_env(
            "SIMULATOR_REAL_MAX_TIMEOUTS_PER_TICK",
            int(self._real_max_timeouts_per_tick_default),
        )
        self._real_max_errors_total_limit = _safe_int_env(
            "SIMULATOR_REAL_MAX_ERRORS_TOTAL",
            int(self._real_max_errors_total_default),
        )
        self._clearing_max_depth_limit = _safe_int_env(
            "SIMULATOR_CLEARING_MAX_DEPTH", 6
        )
        self._clearing_max_fx_edges_limit = _safe_int_env(
            "SIMULATOR_CLEARING_MAX_EDGES_FOR_FX", 30
        )
        # Amount cap is opt-in. Default must not override scenario amount_model bounds.
        self._real_amount_cap_limit = _safe_optional_decimal_env(
            "SIMULATOR_REAL_AMOUNT_CAP"
        )
        self._real_enable_inject = bool(
            _safe_int_env("SIMULATOR_REAL_ENABLE_INJECT", 0)
        )

        # Cache env-derived throttling knobs (avoid getenv on every tick).
        self._real_db_metrics_every_n_ticks = _safe_int_env(
            "SIMULATOR_REAL_DB_METRICS_EVERY_N_TICKS", 5
        )
        self._real_db_bottlenecks_every_n_ticks = _safe_int_env(
            "SIMULATOR_REAL_DB_BOTTLENECKS_EVERY_N_TICKS", 10
        )
        self._real_last_tick_write_every_ms = _safe_int_env(
            "SIMULATOR_REAL_LAST_TICK_WRITE_EVERY_MS", 500
        )
        self._real_artifacts_sync_every_ms = _safe_int_env(
            "SIMULATOR_REAL_ARTIFACTS_SYNC_EVERY_MS", 5000
        )

        # Clearing loop throttling: keep default behavior, but avoid long event-loop stalls.
        # If budget is exceeded, clearing will continue on the next tick.
        self._real_clearing_time_budget_ms = _safe_int_env(
            "SIMULATOR_REAL_CLEARING_TIME_BUDGET_MS", 250
        )

    def _parse_event_time_ms(self, evt: Any) -> int | None:
        if not isinstance(evt, dict):
            return None
        t = evt.get("time")
        if isinstance(t, int):
            return max(0, int(t))
        # MVP: token-based times are future.
        return None

    def _compute_stress_multipliers(
        self,
        *,
        events: Any,
        sim_time_ms: int,
    ) -> tuple[float, dict[str, float], dict[str, float]]:
        """Returns (mult_all, mult_by_group, mult_by_profile) for tx_rate.

        Best-effort: ignores unknown ops/fields/scopes.
        Deterministic: depends only on (events, sim_time_ms).
        """

        mult_all = 1.0
        mult_by_group: dict[str, float] = {}
        mult_by_profile: dict[str, float] = {}

        if not isinstance(events, list) or not events:
            return mult_all, mult_by_group, mult_by_profile

        for evt in events:
            if not isinstance(evt, dict):
                continue
            if str(evt.get("type") or "").strip() != "stress":
                continue
            t0 = self._parse_event_time_ms(evt)
            if t0 is None:
                continue

            duration_ms = 0
            try:
                md = evt.get("metadata")
                if isinstance(md, dict) and md.get("duration_ms") is not None:
                    duration_ms = int(md.get("duration_ms"))
                elif evt.get("duration_ms") is not None:
                    duration_ms = int(evt.get("duration_ms"))
            except Exception:
                duration_ms = 0
            duration_ms = max(0, int(duration_ms))

            if duration_ms <= 0:
                # Interpret as an instantaneous event at t0.
                if int(sim_time_ms) != int(t0):
                    continue
            else:
                if not (int(t0) <= int(sim_time_ms) < int(t0) + int(duration_ms)):
                    continue

            effects = evt.get("effects")
            if not isinstance(effects, list) or not effects:
                continue

            for eff in effects:
                if not isinstance(eff, dict):
                    continue
                if str(eff.get("op") or "").strip() != "mult":
                    continue
                if str(eff.get("field") or "").strip() != "tx_rate":
                    continue
                try:
                    v = float(eff.get("value"))
                except Exception:
                    continue
                if v <= 0:
                    continue
                # Soft clamp to avoid accidental huge multipliers; tx_rate is clamped later anyway.
                v = max(0.0, min(10.0, float(v)))

                scope = str(eff.get("scope") or "all").strip()
                if scope == "all" or not scope:
                    mult_all *= v
                    continue
                if scope.startswith("group:"):
                    g = scope.split(":", 1)[1].strip()
                    if g:
                        mult_by_group[g] = float(mult_by_group.get(g, 1.0)) * v
                    continue
                if scope.startswith("profile:"):
                    p = scope.split(":", 1)[1].strip()
                    if p:
                        mult_by_profile[p] = float(mult_by_profile.get(p, 1.0)) * v
                    continue

        return float(mult_all), mult_by_group, mult_by_profile

    async def _apply_due_scenario_events(
        self, session, *, run_id: str, run: RunRecord, scenario: dict[str, Any]
    ) -> None:
        events = scenario.get("events")
        if not isinstance(events, list) or not events:
            return

        # Build pid->id map once.
        pid_to_participant_id: dict[str, uuid.UUID] = {}
        if run._real_participants:
            pid_to_participant_id = {
                str(pid): participant_id
                for (participant_id, pid) in run._real_participants
            }

        for idx, evt in enumerate(events):
            if idx in run._real_fired_scenario_event_indexes:
                continue

            t0 = self._parse_event_time_ms(evt)
            if t0 is None or int(run.sim_time_ms) < int(t0):
                continue

            evt_type = str((evt or {}).get("type") or "").strip()

            if evt_type == "note":
                payload = {
                    "type": "note",
                    "ts": self._utc_now().isoformat(),
                    "sim_time_ms": int(run.sim_time_ms),
                    "tick_index": int(run.tick_index),
                    "scenario": {
                        "event_index": int(idx),
                        "time": t0,
                        "description": str((evt or {}).get("description") or ""),
                        "metadata": (
                            (evt or {}).get("metadata")
                            if isinstance((evt or {}).get("metadata"), dict)
                            else None
                        ),
                    },
                }
                self._artifacts.enqueue_event_artifact(run_id, payload)
                run._real_fired_scenario_event_indexes.add(idx)
                continue

            if evt_type == "inject":
                if not self._real_enable_inject:
                    self._artifacts.enqueue_event_artifact(
                        run_id,
                        {
                            "type": "note",
                            "ts": self._utc_now().isoformat(),
                            "sim_time_ms": int(run.sim_time_ms),
                            "tick_index": int(run.tick_index),
                            "scenario": {
                                "event_index": int(idx),
                                "time": t0,
                                "description": "inject skipped (SIMULATOR_REAL_ENABLE_INJECT=0)",
                            },
                        },
                    )
                    run._real_fired_scenario_event_indexes.add(idx)
                    continue

                effects = (evt or {}).get("effects")
                if not isinstance(effects, list) or not effects:
                    run._real_fired_scenario_event_indexes.add(idx)
                    continue

                max_edges = 500
                max_total_amount: Decimal | None = None
                try:
                    md = (evt or {}).get("metadata")
                    if isinstance(md, dict) and md.get("max_total_amount") is not None:
                        max_total_amount = Decimal(str(md.get("max_total_amount")))
                except Exception:
                    max_total_amount = None
                if max_total_amount is not None and max_total_amount <= 0:
                    max_total_amount = None

                applied = 0
                skipped = 0
                total_applied = Decimal("0")

                # Resolve equivalents lazily.
                eq_id_by_code: dict[str, uuid.UUID] = {}

                for eff in effects[:max_edges]:
                    if not isinstance(eff, dict):
                        skipped += 1
                        continue
                    if str(eff.get("op") or "").strip() != "inject_debt":
                        continue

                    eq = str(eff.get("equivalent") or "").strip().upper()
                    debtor_pid = str(eff.get("debtor") or "").strip()
                    creditor_pid = str(eff.get("creditor") or "").strip()
                    if not eq or not debtor_pid or not creditor_pid:
                        skipped += 1
                        continue

                    try:
                        amount = Decimal(str(eff.get("amount")))
                        amount = amount.quantize(Decimal("0.01"), rounding=ROUND_DOWN)
                    except Exception:
                        skipped += 1
                        continue
                    if amount <= 0:
                        skipped += 1
                        continue

                    if (
                        max_total_amount is not None
                        and total_applied + amount > max_total_amount
                    ):
                        skipped += 1
                        continue

                    debtor_id = pid_to_participant_id.get(debtor_pid)
                    creditor_id = pid_to_participant_id.get(creditor_pid)
                    if debtor_id is None or creditor_id is None:
                        skipped += 1
                        continue

                    eq_id = eq_id_by_code.get(eq)
                    if eq_id is None:
                        row = (
                            await session.execute(
                                select(Equivalent.id).where(Equivalent.code == eq)
                            )
                        ).scalar_one_or_none()
                        if row is None:
                            skipped += 1
                            continue
                        eq_id = row
                        eq_id_by_code[eq] = eq_id

                    tl = (
                        await session.execute(
                            select(TrustLine.limit, TrustLine.status).where(
                                TrustLine.from_participant_id == creditor_id,
                                TrustLine.to_participant_id == debtor_id,
                                TrustLine.equivalent_id == eq_id,
                            )
                        )
                    ).one_or_none()
                    if tl is None:
                        skipped += 1
                        continue
                    tl_limit, tl_status = tl
                    if str(tl_status or "").strip().lower() != "active":
                        skipped += 1
                        continue
                    try:
                        tl_limit_amt = Decimal(str(tl_limit))
                    except Exception:
                        skipped += 1
                        continue
                    if tl_limit_amt <= 0:
                        skipped += 1
                        continue

                    existing = (
                        await session.execute(
                            select(Debt).where(
                                Debt.debtor_id == debtor_id,
                                Debt.creditor_id == creditor_id,
                                Debt.equivalent_id == eq_id,
                            )
                        )
                    ).scalar_one_or_none()

                    if existing is None:
                        new_amt = amount
                        if new_amt > tl_limit_amt:
                            skipped += 1
                            continue
                        session.add(
                            Debt(
                                debtor_id=debtor_id,
                                creditor_id=creditor_id,
                                equivalent_id=eq_id,
                                amount=new_amt,
                            )
                        )
                    else:
                        new_amt = (Decimal(str(existing.amount)) + amount).quantize(
                            Decimal("0.01"), rounding=ROUND_DOWN
                        )
                        if new_amt > tl_limit_amt:
                            skipped += 1
                            continue
                        existing.amount = new_amt

                    applied += 1
                    total_applied += amount

                try:
                    await session.commit()
                except Exception:
                    await session.rollback()
                    self._artifacts.enqueue_event_artifact(
                        run_id,
                        {
                            "type": "note",
                            "ts": self._utc_now().isoformat(),
                            "sim_time_ms": int(run.sim_time_ms),
                            "tick_index": int(run.tick_index),
                            "scenario": {
                                "event_index": int(idx),
                                "time": t0,
                                "description": "inject failed (db error)",
                            },
                        },
                    )
                    run._real_fired_scenario_event_indexes.add(idx)
                    continue

                self._artifacts.enqueue_event_artifact(
                    run_id,
                    {
                        "type": "note",
                        "ts": self._utc_now().isoformat(),
                        "sim_time_ms": int(run.sim_time_ms),
                        "tick_index": int(run.tick_index),
                        "scenario": {
                            "event_index": int(idx),
                            "time": t0,
                            "description": "inject applied",
                            "stats": {
                                "applied": int(applied),
                                "skipped": int(skipped),
                                "total_amount": format(total_applied, "f"),
                            },
                        },
                    },
                )

                run._real_fired_scenario_event_indexes.add(idx)
                continue

            # Unknown / unsupported event types are ignored, but we still mark them fired once due.
            run._real_fired_scenario_event_indexes.add(idx)

    def _should_warn_this_tick(self, run: RunRecord, *, key: str) -> bool:
        with self._lock:
            tick = int(run.tick_index)
            if int(run._real_warned_tick) != tick:
                run._real_warned_tick = tick
                run._real_warned_keys.clear()

            if key in run._real_warned_keys:
                return False
            run._real_warned_keys.add(key)
            return True

    async def flush_pending_storage(self, run_id: str) -> None:
        """Best-effort flush of the last computed tick metrics/bottlenecks.

        Used on stop/error to avoid losing the last batch when DB writes are throttled.

        NOTE: Stop/error marks the run state first, then calls this flush. The cached
        payload is stable because no further ticks will update it after stop/fail.
        """

        if not self._db_enabled():
            return

        run = self._get_run(run_id)
        if str(run.mode) != "real":
            return

        payload = run._real_last_tick_storage_payload
        if not isinstance(payload, dict):
            return

        last_tick = int(payload.get("tick_index", -1) or -1)
        if last_tick < 0:
            return

        flushed_tick = int(run._real_last_tick_storage_flushed_tick or -1)
        if flushed_tick >= last_tick:
            return

        try:
            async with db_session.AsyncSessionLocal() as session:
                try:
                    await simulator_storage.write_tick_metrics(
                        run_id=str(payload.get("run_id") or run.run_id),
                        t_ms=int(payload.get("t_ms") or 0),
                        per_equivalent=payload.get("per_equivalent") or {},
                        metric_values_by_eq=payload.get("metric_values_by_eq") or {},
                        session=session,
                    )
                    if self._db_enabled() and isinstance(
                        payload.get("bottlenecks"), dict
                    ):
                        computed_at = (
                            payload.get("bottlenecks", {}).get("computed_at")
                            or self._utc_now()
                        )
                        edge_stats_by_eq = (
                            payload.get("bottlenecks", {}).get("edge_stats_by_eq") or {}
                        )
                        equivalents = (
                            payload.get("bottlenecks", {}).get("equivalents") or []
                        )
                        for eq in equivalents:
                            await simulator_storage.write_tick_bottlenecks(
                                run_id=str(payload.get("run_id") or run.run_id),
                                equivalent=str(eq),
                                computed_at=computed_at,
                                edge_stats=edge_stats_by_eq.get(str(eq), {}) or {},
                                session=session,
                                limit=50,
                            )
                except Exception:
                    try:
                        await session.rollback()
                    except Exception:
                        pass
                    raise

            with self._lock:
                run._real_last_tick_storage_flushed_tick = int(last_tick)
        except Exception:
            self._logger.warning(
                "simulator.real.flush_pending_storage_failed run_id=%s",
                str(run_id),
                exc_info=True,
            )

    async def tick_real_mode(self, run_id: str) -> None:
        run = self._get_run(run_id)
        scenario = self._get_scenario_raw(run.scenario_id)

        tick_t0 = time.monotonic()
        # NOTE: keep this at DEBUG to avoid log spam; we add WARNING logs around
        # potentially blocking stages (clearing/commit).
        self._logger.debug(
            "simulator.real.tick_start run_id=%s tick=%s sim_time_ms=%s",
            str(run.run_id),
            int(run.tick_index or 0),
            int(run.sim_time_ms or 0),
        )

        try:
            async with db_session.AsyncSessionLocal() as session:
                if not run._real_seeded:
                    await self._seed_scenario_into_db(session, scenario)
                    await session.commit()
                    run._real_seeded = True

                if run._real_participants is None or run._real_equivalents is None:
                    run._real_participants = await self._load_real_participants(
                        session, scenario
                    )
                    run._real_equivalents = [
                        str(x)
                        for x in (scenario.get("equivalents") or [])
                        if str(x).strip()
                    ]

                participants = run._real_participants or []
                equivalents = run._real_equivalents or []
                if len(participants) < 2 or not equivalents:
                    return

                # Apply due scenario timeline events (note/stress/inject). Best-effort.
                # IMPORTANT: inject modifies DB state and must happen before payments.
                await self._apply_due_scenario_events(
                    session, run_id=run_id, run=run, scenario=scenario
                )

                planned = self._plan_real_payments(run, scenario)
                with self._lock:
                    run.ops_sec = float(len(planned))
                    run.queue_depth = len(planned)
                    run._real_in_flight = 0
                    run.current_phase = "payments" if planned else None
                sender_id_by_pid = {
                    pid: participant_id for (participant_id, pid) in participants
                }

                max_timeouts_per_tick = int(self._real_max_timeouts_per_tick_limit)
                max_errors_total = int(self._real_max_errors_total_limit)

                committed = 0
                rejected = 0
                errors = 0
                timeouts = 0

                sem = asyncio.Semaphore(max(1, int(run._real_max_in_flight)))
                action_db_lock = asyncio.Lock()

                per_eq: dict[str, dict[str, int]] = {
                    str(eq): {"committed": 0, "rejected": 0, "errors": 0, "timeouts": 0}
                    for eq in equivalents
                }
                per_eq_route: dict[str, dict[str, float]] = {
                    str(eq): {"route_len_sum": 0.0, "route_len_n": 0.0}
                    for eq in equivalents
                }
                per_eq_metric_values: dict[str, dict[str, float]] = {
                    str(eq): {} for eq in equivalents
                }

                per_eq_edge_stats: dict[str, dict[tuple[str, str], dict[str, int]]] = {
                    str(eq): {} for eq in equivalents
                }

                def _edge_inc(
                    eq: str, src: str, dst: str, key: str, n: int = 1
                ) -> None:
                    m = per_eq_edge_stats.setdefault(str(eq), {})
                    st = m.setdefault(
                        (str(src), str(dst)),
                        {
                            "attempts": 0,
                            "committed": 0,
                            "rejected": 0,
                            "errors": 0,
                            "timeouts": 0,
                        },
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
                            None,  # amount
                            None,  # status
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

                    # Single-session real-mode tick:
                    # - One AsyncSession for the whole tick (this `session`)
                    # - Per-action SAVEPOINT via begin_nested()
                    # - No per-action commit (service/engine run with commit=False)
                    # IMPORTANT: AsyncSession is not safe for concurrent use, so we serialize
                    # DB work for actions with a lock.

                    async with sem:
                        with self._lock:
                            run._real_in_flight += 1

                        try:
                            async with action_db_lock:
                                async with session.begin_nested():
                                    service = PaymentService(session)
                                    res = await service.create_payment_internal(
                                        sender_id,
                                        to_pid=action.receiver_pid,
                                        equivalent=action.equivalent,
                                        amount=action.amount,
                                        idempotency_key=idem,
                                        commit=False,
                                    )

                            status = str(res.status or "")

                            routes = res.routes or []

                            route_edges: list[tuple[str, str]] = []
                            for r in routes:
                                path = r.path
                                if len(path) < 2:
                                    continue
                                route_edges = [
                                    (str(a), str(b)) for a, b in zip(path, path[1:])
                                ]
                                if route_edges:
                                    break

                            avg_route_len = 0.0
                            lens = [
                                float(len(r.path) - 1)
                                for r in routes
                                if len(r.path) >= 2
                            ]
                            if lens:
                                avg_route_len = float(sum(lens) / len(lens))

                            return (
                                action.seq,
                                action.equivalent,
                                action.sender_pid,
                                action.receiver_pid,
                                action.amount,
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
                                action.amount,
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
                    d = per_eq_route.setdefault(
                        str(eq), {"route_len_sum": 0.0, "route_len_n": 0.0}
                    )
                    d["route_len_sum"] = float(d.get("route_len_sum", 0.0)) + float(
                        route_len
                    )
                    d["route_len_n"] = float(d.get("route_len_n", 0.0)) + 1.0

                def _emit_if_ready() -> None:
                    nonlocal next_seq, committed, rejected, errors, timeouts
                    while True:
                        item = ready.get(next_seq)
                        if item is None:
                            return
                        del ready[next_seq]
                        (
                            eq,
                            sender_pid,
                            receiver_pid,
                            amount,
                            status,
                            err_code,
                            err_details,
                            avg_route_len,
                            route_edges,
                            edge_patch,
                            node_patch,
                        ) = item

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
                                run.attempts_total += 1
                                run.errors_total += 1
                                if err_code == "PAYMENT_TIMEOUT":
                                    run.timeouts_total += 1
                                run._error_timestamps.append(time.time())
                                cutoff = time.time() - 60.0
                                while (
                                    run._error_timestamps
                                    and run._error_timestamps[0] < cutoff
                                ):
                                    run._error_timestamps.popleft()
                                run.last_error = {
                                    "code": err_code,
                                    "message": str(
                                        (err_details or {}).get("message") or err_code
                                    ),
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
                                    "message": str(
                                        (err_details or {}).get("message") or err_code
                                    ),
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
                                    from_=sender_pid,
                                    to=receiver_pid,
                                    amount=amount,
                                    amount_flyout=True,
                                    ttl_ms=1200,
                                    edges=[
                                        {"from": a, "to": b} for a, b in edges_pairs
                                    ],
                                    node_badges=None,
                                ).model_dump(mode="json", by_alias=True)
                                if edge_patch:
                                    evt_dict["edge_patch"] = edge_patch
                                if node_patch:
                                    evt_dict["node_patch"] = node_patch
                                with self._lock:
                                    run.last_event_type = "tx.updated"
                                    run.attempts_total += 1
                                    run.committed_total += 1
                                self._sse.broadcast(run_id, evt_dict)
                            else:
                                rejected += 1
                                _inc(eq, "rejected")
                                for a, b in edges_pairs:
                                    _edge_inc(eq, a, b, "rejected")

                                try:
                                    rejection_code = map_rejection_code(err_details)
                                except Exception:
                                    if self._should_warn_this_tick(
                                        run, key="map_rejection_code_failed"
                                    ):
                                        self._logger.debug(
                                            "simulator.real.map_rejection_code_failed run_id=%s tick=%s",
                                            str(run.run_id),
                                            int(run.tick_index),
                                            exc_info=True,
                                        )
                                    rejection_code = "PAYMENT_REJECTED"

                                with self._lock:
                                    run.last_event_type = "tx.failed"
                                    run.attempts_total += 1
                                    run.rejected_total += 1

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
                        patch_session = session

                        # Per-tick caches to avoid repeated heavy work when multiple tx touch the same pids.
                        per_tick_pid_to_participant_by_eq_and_pids: dict[
                            tuple[str, tuple[str, ...]],
                            dict[str, Participant],
                        ] = {}
                        per_tick_node_patch_by_eq_and_pids: dict[
                            tuple[str, tuple[str, ...]],
                            list[dict[str, Any]] | None,
                        ] = {}
                        per_tick_quantiles_refreshed_by_eq: set[str] = set()

                        emitted_since_yield = 0

                        for t in asyncio.as_completed(tasks):
                            (
                                seq,
                                eq,
                                sender_pid,
                                receiver_pid,
                                amount,
                                status,
                                err_code,
                                err_details,
                                avg_route_len,
                                route_edges,
                            ) = await t

                            if run.state != "running":
                                break

                            # Build edge_patch for committed transactions (Variant A: SSE patches)
                            edge_patch_list: list[dict[str, Any]] | None = None
                            node_patch_list: list[dict[str, Any]] | None = None

                            # Serialize patch DB reads with action DB writes: one session, one connection.
                            async with action_db_lock:
                                try:
                                    if status == "COMMITTED":
                                        edges_pairs = route_edges or [
                                            (sender_pid, receiver_pid)
                                        ]

                                        edge_patch_list = []
                                        helper: VizPatchHelper | None
                                        with self._lock:
                                            helper = run._real_viz_by_eq.get(str(eq))

                                        if helper is None:
                                            helper = await VizPatchHelper.create(
                                                patch_session,
                                                equivalent_code=str(eq),
                                                refresh_every_ticks=int(
                                                    getattr(
                                                        settings,
                                                        "SIMULATOR_VIZ_QUANTILE_REFRESH_TICKS",
                                                        10,
                                                    )
                                                    or 10
                                                ),
                                            )
                                            with self._lock:
                                                run._real_viz_by_eq[str(eq)] = helper

                                        eq_id = helper.equivalent_id

                                        participant_ids: list[uuid.UUID] = []
                                        if run._real_participants:
                                            participant_ids = [
                                                pid
                                                for (pid, _) in run._real_participants
                                            ]
                                        if (
                                            str(eq)
                                            not in per_tick_quantiles_refreshed_by_eq
                                        ):
                                            await helper.maybe_refresh_quantiles(
                                                patch_session,
                                                tick_index=int(run.tick_index),
                                                participant_ids=participant_ids,
                                            )
                                            per_tick_quantiles_refreshed_by_eq.add(
                                                str(eq)
                                            )

                                        # Fetch participants in one query.
                                        pids = sorted(
                                            {
                                                pid
                                                for ab in edges_pairs
                                                for pid in ab
                                                if pid
                                            }
                                        )
                                        pids_key = (str(eq), tuple(pids))
                                        pid_to_participant = per_tick_pid_to_participant_by_eq_and_pids.get(
                                            pids_key
                                        )
                                        if pid_to_participant is None:
                                            res = await patch_session.execute(
                                                select(Participant).where(
                                                    Participant.pid.in_(pids)
                                                )
                                            )
                                            pid_to_participant = {
                                                p.pid: p for p in res.scalars().all()
                                            }
                                            per_tick_pid_to_participant_by_eq_and_pids[
                                                pids_key
                                            ] = pid_to_participant

                                        try:
                                            if (
                                                pids_key
                                                in per_tick_node_patch_by_eq_and_pids
                                            ):
                                                node_patch_list = (
                                                    per_tick_node_patch_by_eq_and_pids[
                                                        pids_key
                                                    ]
                                                )
                                            else:
                                                node_patch_list = await helper.compute_node_patches(
                                                    patch_session,
                                                    pid_to_participant=pid_to_participant,
                                                    pids=pids,
                                                )
                                                if node_patch_list == []:
                                                    node_patch_list = None
                                                per_tick_node_patch_by_eq_and_pids[
                                                    pids_key
                                                ] = node_patch_list
                                        except Exception:
                                            if self._should_warn_this_tick(
                                                run, key=f"node_patch_failed:{eq}"
                                            ):
                                                self._logger.warning(
                                                    "simulator.real.node_patch_failed run_id=%s tick=%s eq=%s",
                                                    str(run.run_id),
                                                    int(run.tick_index),
                                                    str(eq),
                                                    exc_info=True,
                                                )
                                            node_patch_list = None

                                        # Batch-load debts and trustlines for the affected edges (avoid N+1).
                                        id_pairs: list[tuple[uuid.UUID, uuid.UUID]] = []
                                        for src_pid, dst_pid in edges_pairs:
                                            src_part = pid_to_participant.get(src_pid)
                                            dst_part = pid_to_participant.get(dst_pid)
                                            if not src_part or not dst_part:
                                                continue
                                            id_pairs.append((src_part.id, dst_part.id))

                                        debt_by_pair: dict[
                                            tuple[uuid.UUID, uuid.UUID], Decimal
                                        ] = {}
                                        tl_by_pair: dict[
                                            tuple[uuid.UUID, uuid.UUID],
                                            tuple[Decimal, str | None],
                                        ] = {}

                                        if id_pairs:
                                            debt_cond = or_(
                                                *[
                                                    and_(
                                                        Debt.creditor_id == a,
                                                        Debt.debtor_id == b,
                                                    )
                                                    for a, b in id_pairs
                                                ]
                                            )
                                            debt_rows = (
                                                await patch_session.execute(
                                                    select(
                                                        Debt.creditor_id,
                                                        Debt.debtor_id,
                                                        func.coalesce(
                                                            func.sum(Debt.amount), 0
                                                        ).label("amount"),
                                                    )
                                                    .where(
                                                        Debt.equivalent_id == eq_id,
                                                        debt_cond,
                                                    )
                                                    .group_by(
                                                        Debt.creditor_id, Debt.debtor_id
                                                    )
                                                )
                                            ).all()
                                            debt_by_pair = {
                                                (r.creditor_id, r.debtor_id): (
                                                    r.amount or Decimal("0")
                                                )
                                                for r in debt_rows
                                            }

                                            tl_cond = or_(
                                                *[
                                                    and_(
                                                        TrustLine.from_participant_id
                                                        == a,
                                                        TrustLine.to_participant_id
                                                        == b,
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
                                                    ).where(
                                                        TrustLine.equivalent_id
                                                        == eq_id,
                                                        tl_cond,
                                                    )
                                                )
                                            ).all()
                                            tl_by_pair = {
                                                (
                                                    r.from_participant_id,
                                                    r.to_participant_id,
                                                ): (r.limit or Decimal("0"), r.status)
                                                for r in tl_rows
                                            }

                                        for src_pid, dst_pid in edges_pairs:
                                            src_part = pid_to_participant.get(src_pid)
                                            dst_part = pid_to_participant.get(dst_pid)
                                            if not src_part or not dst_part:
                                                continue

                                            used_amt = debt_by_pair.get(
                                                (src_part.id, dst_part.id), Decimal("0")
                                            )
                                            limit_amt, tl_status = tl_by_pair.get(
                                                (src_part.id, dst_part.id),
                                                (Decimal("0"), None),
                                            )
                                            available_amt = max(
                                                Decimal("0"), limit_amt - used_amt
                                            )

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
                                except Exception:
                                    if self._should_warn_this_tick(
                                        run, key=f"edge_patch_failed:{eq}"
                                    ):
                                        self._logger.warning(
                                            "simulator.real.edge_patch_failed run_id=%s tick=%s eq=%s",
                                            str(run.run_id),
                                            int(run.tick_index),
                                            str(eq),
                                            exc_info=True,
                                        )
                                    edge_patch_list = None
                                    node_patch_list = None

                            if edge_patch_list == []:
                                edge_patch_list = None

                            ready[seq] = (
                                eq,
                                sender_pid,
                                receiver_pid,
                                amount,
                                status,
                                err_code,
                                err_details,
                                avg_route_len,
                                route_edges,
                                edge_patch_list,
                                node_patch_list,
                            )
                            _emit_if_ready()

                            emitted_since_yield += 1
                            if emitted_since_yield % 5 == 0:
                                # Give the event loop a chance to flush SSE writes and timers.
                                await asyncio.sleep(0)

                            if (
                                max_timeouts_per_tick > 0
                                and timeouts >= max_timeouts_per_tick
                            ):
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

                    # Track consecutive all-rejection ticks for stall detection.
                    if len(planned) > 0 and committed == 0 and errors == 0:
                        run._real_consec_all_rejected_ticks += 1
                    else:
                        run._real_consec_all_rejected_ticks = 0

                    stall_ticks = run._real_consec_all_rejected_ticks

                # Log stall warning (throttled: every 5 consecutive stall ticks).
                if stall_ticks > 0 and stall_ticks % 5 == 0:
                    self._logger.warning(
                        "simulator.real.all_rejected_stall run_id=%s tick=%s "
                        "consec_stall_ticks=%d planned=%d rejected=%d",
                        str(run.run_id),
                        int(run.tick_index),
                        stall_ticks,
                        len(planned),
                        rejected,
                    )

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
                clearing_volume_by_eq: dict[str, float] = {
                    str(eq): 0.0 for eq in equivalents
                }
                if (
                    self._clearing_every_n_ticks > 0
                    and run.tick_index % self._clearing_every_n_ticks == 0
                    and bool(getattr(settings, "CLEARING_ENABLED", True))
                ):
                    # Commit payments BEFORE clearing to release the DB write lock.
                    # Clearing uses an isolated session (separate connection) that needs
                    # write access. On SQLite (single-writer) the clearing session would
                    # deadlock if the parent session still holds an uncommitted transaction.
                    try:
                        commit_t0 = time.monotonic()
                        await session.commit()
                        commit_ms = (time.monotonic() - commit_t0) * 1000.0
                        if commit_ms > 500.0:
                            self._logger.warning(
                                "simulator.real.tick_commit_slow run_id=%s tick=%s commit_ms=%s total_tick_ms=%s",
                                str(run.run_id),
                                int(run.tick_index),
                                int(commit_ms),
                                int((time.monotonic() - tick_t0) * 1000.0),
                            )
                    except Exception:
                        await session.rollback()
                        raise

                    self._logger.warning(
                        "simulator.real.tick_clearing_enter run_id=%s tick=%s eqs=%s planned=%s",
                        str(run.run_id),
                        int(run.tick_index),
                        ",".join([str(x) for x in (equivalents or [])]),
                        int(len(planned or [])),
                    )
                    clearing_t0 = time.monotonic()
                    # Hard timeout: clearing must not block the heartbeat loop indefinitely.
                    # Note: asyncio.wait_for() relies on cancellation being delivered. Some
                    # DB awaits (or driver edge cases) may delay cancellation, so we run
                    # clearing in a separate task and time out without waiting for teardown.
                    clearing_hard_timeout_sec = max(
                        2.0,
                        float(self._real_clearing_time_budget_ms) / 1000.0 * 4.0,
                    )
                    env_timeout_cap = float(
                        _safe_int_env("SIMULATOR_REAL_CLEARING_HARD_TIMEOUT_SEC", 8)
                    )
                    if env_timeout_cap > 0:
                        clearing_hard_timeout_sec = min(
                            clearing_hard_timeout_sec, env_timeout_cap
                        )
                    clearing_hard_timeout_sec = max(0.1, float(clearing_hard_timeout_sec))

                    clearing_task: asyncio.Task[dict[str, float]] | None = None
                    with self._lock:
                        existing = run._real_clearing_task
                        if existing is not None and existing.done():
                            run._real_clearing_task = None
                            existing = None
                        clearing_task = existing

                    if clearing_task is None:
                        clearing_task = asyncio.create_task(
                            self.tick_real_mode_clearing(
                                session, run_id, run, equivalents
                            )
                        )
                        with self._lock:
                            run._real_clearing_task = clearing_task
                    else:
                        self._logger.warning(
                            "simulator.real.tick_clearing_already_running run_id=%s tick=%s",
                            str(run.run_id),
                            int(run.tick_index),
                        )
                    try:
                        clearing_volume_by_eq = await asyncio.wait_for(
                            asyncio.shield(clearing_task),
                            timeout=clearing_hard_timeout_sec,
                        )
                        with self._lock:
                            if run._real_clearing_task is clearing_task:
                                run._real_clearing_task = None
                    except asyncio.TimeoutError:
                        self._logger.warning(
                            "simulator.real.tick_clearing_hard_timeout run_id=%s tick=%s timeout_sec=%s",
                            str(run.run_id),
                            int(run.tick_index),
                            clearing_hard_timeout_sec,
                        )
                        # Clearing timed out — proceed with the rest of the tick.
                        # Best-effort: request cancellation, but do not await it here.
                        # We keep the task reference to avoid overlapping clearing runs.
                        try:
                            clearing_task.cancel()
                        except Exception:
                            pass
                        with self._lock:
                            run.current_phase = None
                    except Exception:
                        # Clearing is best-effort; do not fail the whole tick.
                        with self._lock:
                            if run._real_clearing_task is clearing_task:
                                run._real_clearing_task = None
                        self._logger.warning(
                            "simulator.real.tick_clearing_failed run_id=%s tick=%s",
                            str(run.run_id),
                            int(run.tick_index),
                            exc_info=True,
                        )
                    self._logger.warning(
                        "simulator.real.tick_clearing_done run_id=%s tick=%s elapsed_ms=%s",
                        str(run.run_id),
                        int(run.tick_index),
                        int((time.monotonic() - clearing_t0) * 1000.0),
                    )

                # Real total debt snapshot (sum of all debts for the equivalent).
                # Throttled: aggregate SUM can become hot on large Debt tables.
                total_debt_by_eq: dict[str, float] = {str(eq): 0.0 for eq in equivalents}

                metrics_every_n = int(self._real_db_metrics_every_n_ticks)
                should_refresh_total_debt = metrics_every_n <= 1 or (
                    int(run.tick_index) % int(metrics_every_n) == 0
                )

                if not should_refresh_total_debt:
                    with self._lock:
                        cached = dict(run._real_total_debt_by_eq or {})
                    for eq in equivalents:
                        total_debt_by_eq[str(eq)] = float(cached.get(str(eq), 0.0) or 0.0)

                if should_refresh_total_debt:
                    try:
                        eq_rows = (
                            await session.execute(
                                select(Equivalent.id, Equivalent.code).where(
                                    Equivalent.code.in_(list(equivalents))
                                )
                            )
                        ).all()
                        eq_id_by_code = {str(code): eq_id for (eq_id, code) in eq_rows}
                        for eq_code, eq_id in eq_id_by_code.items():
                            total = (
                                await session.execute(
                                    select(func.coalesce(func.sum(Debt.amount), 0)).where(
                                        Debt.equivalent_id == eq_id
                                    )
                                )
                            ).scalar_one()
                            total_debt_by_eq[str(eq_code)] = float(total)

                        with self._lock:
                            run._real_total_debt_by_eq = dict(total_debt_by_eq)
                            run._real_total_debt_tick = int(run.tick_index)
                    except Exception:
                        if self._should_warn_this_tick(run, key="total_debt_snapshot_failed"):
                            self._logger.debug(
                                "simulator.real.total_debt_snapshot_failed run_id=%s tick=%s",
                                str(run.run_id),
                                int(run.tick_index),
                                exc_info=True,
                            )

                        # Fallback to the last cached values if available.
                        with self._lock:
                            cached = dict(run._real_total_debt_by_eq or {})
                        for eq in equivalents:
                            total_debt_by_eq[str(eq)] = float(cached.get(str(eq), 0.0) or 0.0)

                # Avg route length for this tick (successful payments).
                for eq in equivalents:
                    r = per_eq_route.get(str(eq), {})
                    n = float(r.get("route_len_n", 0.0) or 0.0)
                    s = float(r.get("route_len_sum", 0.0) or 0.0)
                    per_eq_metric_values[str(eq)]["avg_route_length"] = (
                        float(s / n) if n > 0 else 0.0
                    )
                    per_eq_metric_values[str(eq)]["total_debt"] = float(
                        total_debt_by_eq.get(str(eq), 0.0) or 0.0
                    )
                    per_eq_metric_values[str(eq)]["clearing_volume"] = float(
                        clearing_volume_by_eq.get(str(eq), 0.0) or 0.0
                    )

                # Cache the last computed payload to allow best-effort flush on stop/error.
                # Keep it in-memory only (no API changes).
                computed_at = self._utc_now()
                with self._lock:
                    run._real_last_tick_storage_payload = {
                        "run_id": str(run.run_id),
                        "tick_index": int(run.tick_index),
                        "t_ms": int(run.sim_time_ms),
                        "per_equivalent": per_eq,
                        "metric_values_by_eq": per_eq_metric_values,
                        "bottlenecks": {
                            "computed_at": computed_at,
                            "equivalents": list(equivalents),
                            "edge_stats_by_eq": per_eq_edge_stats,
                        },
                    }

                # Throttle DB writes for metrics/bottlenecks to reduce IO.
                metrics_every_n = int(self._real_db_metrics_every_n_ticks)
                bottlenecks_every_n = int(self._real_db_bottlenecks_every_n_ticks)

                should_write_metrics = metrics_every_n <= 1 or (
                    int(run.tick_index) % int(metrics_every_n) == 0
                )
                should_write_bottlenecks = bottlenecks_every_n <= 1 or (
                    int(run.tick_index) % int(bottlenecks_every_n) == 0
                )

                if should_write_metrics:
                    await simulator_storage.write_tick_metrics(
                        run_id=run.run_id,
                        t_ms=int(run.sim_time_ms),
                        per_equivalent=per_eq,
                        metric_values_by_eq=per_eq_metric_values,
                        session=session,
                        commit=False,
                    )

                # Persist bottlenecks snapshot derived from actual tick outcomes.
                if should_write_bottlenecks and self._db_enabled():
                    for eq in equivalents:
                        await simulator_storage.write_tick_bottlenecks(
                            run_id=run.run_id,
                            equivalent=str(eq),
                            computed_at=computed_at,
                            edge_stats=per_eq_edge_stats.get(str(eq), {}),
                            session=session,
                            limit=50,
                            commit=False,
                        )

                if should_write_metrics or should_write_bottlenecks:
                    with self._lock:
                        run._real_last_tick_storage_flushed_tick = int(run.tick_index)

                # One commit for the whole tick DB state (payments + optional snapshots).
                try:
                    commit_t0 = time.monotonic()
                    await session.commit()
                    commit_ms = (time.monotonic() - commit_t0) * 1000.0
                    if commit_ms > 500.0:
                        self._logger.warning(
                            "simulator.real.tick_commit_slow run_id=%s tick=%s commit_ms=%s total_tick_ms=%s",
                            str(run.run_id),
                            int(run.tick_index),
                            int(commit_ms),
                            int((time.monotonic() - tick_t0) * 1000.0),
                        )
                except Exception:
                    await session.rollback()
                    raise

                # Artifacts are useful for diagnostics, but syncing them every tick is very IO-heavy.
                # Throttle filesystem writes and DB sync to reduce HDD/SSD churn in interactive runs.
                now_ms = int(time.time() * 1000)
                tick_write_every_ms = int(self._real_last_tick_write_every_ms)
                artifacts_sync_every_ms = int(self._real_artifacts_sync_every_ms)

                if (
                    tick_write_every_ms > 0
                    and (
                        now_ms
                        - int(run._artifact_last_tick_written_at_ms or 0)
                    )
                    >= tick_write_every_ms
                ):
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
                    run._artifact_last_tick_written_at_ms = now_ms

                if (
                    artifacts_sync_every_ms > 0
                    and (
                        now_ms - int(run._artifact_last_sync_at_ms or 0)
                    )
                    >= artifacts_sync_every_ms
                ):
                    await simulator_storage.sync_artifacts(run)
                    run._artifact_last_sync_at_ms = now_ms
        except Exception as e:
            self._logger.warning(
                "simulator.real.tick_failed run_id=%s tick=%s",
                str(run.run_id),
                int(run.tick_index or 0),
                exc_info=True,
            )
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

            max_consec = int(self._real_max_consec_tick_failures_limit)
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
            run.last_error = {
                "code": code,
                "message": message,
                "at": self._utc_now().isoformat(),
            }
            task = run._heartbeat_task

        self._publish_run_status(run_id)
        await simulator_storage.upsert_run(run)

        # Best-effort final flush for throttled tick metrics/bottlenecks.
        try:
            await self.flush_pending_storage(run_id)
        except Exception:
            self._logger.warning(
                "simulator.real.fail_run.flush_pending_storage_failed run_id=%s",
                str(run_id),
                exc_info=True,
            )
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
        max_depth = int(self._clearing_max_depth_limit)
        max_fx_edges = int(self._clearing_max_fx_edges_limit)
        cleared_amount_by_eq: dict[str, float] = {str(eq): 0.0 for eq in equivalents}
        for eq in equivalents:
            # Use isolated session per equivalent to prevent transaction poisoning
            try:
                async with db_session.AsyncSessionLocal() as clearing_session:
                    service = ClearingService(clearing_session)

                    eq_t0 = time.monotonic()
                    self._logger.warning(
                        "simulator.real.clearing_eq_enter run_id=%s tick=%s eq=%s",
                        str(run.run_id),
                        int(run.tick_index),
                        str(eq),
                    )

                    # Plan step: find at least one cycle to visualize.
                    self._logger.warning(
                        "simulator.real.clearing_find_cycles_start run_id=%s tick=%s eq=%s max_depth=%s",
                        str(run.run_id),
                        int(run.tick_index),
                        str(eq),
                        int(max_depth),
                    )
                    _fc_t0 = time.monotonic()
                    cycles = await service.find_cycles(eq, max_depth=max_depth)
                    _fc_ms = int((time.monotonic() - _fc_t0) * 1000.0)
                    if _fc_ms > 500:
                        self._logger.warning(
                            "simulator.real.clearing_find_cycles_slow run_id=%s tick=%s eq=%s elapsed_ms=%s",
                            str(run.run_id),
                            int(run.tick_index),
                            str(eq),
                            int(_fc_ms),
                        )
                    self._logger.warning(
                        "simulator.real.clearing_find_cycles_done run_id=%s tick=%s eq=%s cycles_n=%s elapsed_ms=%s",
                        str(run.run_id),
                        int(run.tick_index),
                        str(eq),
                        int(len(cycles or [])),
                        int(_fc_ms),
                    )
                    if not cycles:
                        continue

                    plan_id = f"plan_{secrets.token_hex(6)}"

                    # Extract edges from first cycle for visualization.
                    # Each edge has: {"debtor": pid, "creditor": pid, "amount": ..., "debt_id": ...}
                    # UI expects: {"from": pid, "to": pid}
                    cycle_edges: list[dict[str, str]] = []
                    try:
                        for edge in cycles[0]:
                            debtor_pid = (
                                str(edge.get("debtor") or "")
                                if isinstance(edge, dict)
                                else str(getattr(edge, "debtor", ""))
                            )
                            creditor_pid = (
                                str(edge.get("creditor") or "")
                                if isinstance(edge, dict)
                                else str(getattr(edge, "creditor", ""))
                            )
                            if debtor_pid and creditor_pid:
                                cycle_edges.append(
                                    {"from": debtor_pid, "to": creditor_pid}
                                )
                    except Exception:
                        if self._should_warn_this_tick(
                            run, key=f"clearing_cycle_edges_parse_failed:{eq}"
                        ):
                            self._logger.debug(
                                "simulator.real.clearing_cycle_edges_parse_failed run_id=%s tick=%s eq=%s",
                                str(run.run_id),
                                int(run.tick_index),
                                str(eq),
                                exc_info=True,
                            )
                        cycle_edges = []

                    if max_fx_edges > 0 and len(cycle_edges) > max_fx_edges:
                        cycle_edges = cycle_edges[:max_fx_edges]

                    # Build steps with highlight_edges for visible clearing animation.
                    plan_steps: list[dict[str, Any]] = []
                    if cycle_edges:
                        plan_steps.append(
                            {
                                "at_ms": 0,
                                "highlight_edges": cycle_edges,
                                "intensity_key": "hi",
                            }
                        )
                        plan_steps.append(
                            {
                                "at_ms": 400,
                                "particles_edges": cycle_edges,
                                "intensity_key": "mid",
                            }
                        )
                        plan_steps.append({"at_ms": 900, "flash": {"kind": "clearing"}})
                    else:
                        # Fallback if no edges extracted.
                        plan_steps.append(
                            {
                                "at_ms": 0,
                                "intensity_key": "mid",
                                "flash": {
                                    "kind": "info",
                                    "title": "Clearing",
                                    "detail": "Auto clearing",
                                },
                            }
                        )

                    plan_evt = SimulatorClearingPlanEvent(
                        event_id=self._sse.next_event_id(run),
                        ts=self._utc_now(),
                        type="clearing.plan",
                        equivalent=eq,
                        plan_id=plan_id,
                        steps=plan_steps,
                    ).model_dump(mode="json", by_alias=True)

                    with self._lock:
                        run.last_event_type = "clearing.plan"
                        run.current_phase = "clearing"
                    self._sse.broadcast(run_id, plan_evt)

                    # Execute with stats (volume = sum of cleared amounts).
                    cleared_cycles = 0
                    cleared_amount_dec = Decimal("0")
                    touched_nodes: set[str] = set()
                    # Edge direction in graph is creditor -> debtor.
                    touched_edges: set[tuple[str, str]] = set()
                    clearing_started = time.monotonic()
                    progress_last_log = 0.0
                    while True:
                        now = time.monotonic()
                        if progress_last_log <= 0.0:
                            progress_last_log = now
                        elif (now - progress_last_log) >= 5.0:
                            self._logger.warning(
                                "simulator.real.clearing_progress run_id=%s tick=%s eq=%s elapsed_ms=%s cleared_cycles=%s",
                                str(run.run_id),
                                int(run.tick_index),
                                str(eq),
                                int((now - clearing_started) * 1000.0),
                                int(cleared_cycles),
                            )
                            progress_last_log = now

                        # Soft throttle: yield to event loop during long clearing bursts.
                        if cleared_cycles and (cleared_cycles % 5 == 0):
                            await asyncio.sleep(0)

                        # Optional time budget to avoid multi-second stalls.
                        budget_ms = int(
                            getattr(self, "_real_clearing_time_budget_ms", 0) or 0
                        )
                        if budget_ms > 0:
                            elapsed_ms = (time.monotonic() - clearing_started) * 1000.0
                            if elapsed_ms >= float(budget_ms):
                                if self._should_warn_this_tick(
                                    run, key=f"clearing_time_budget_exceeded:{eq}"
                                ):
                                    self._logger.warning(
                                        "simulator.real.clearing_time_budget_exceeded run_id=%s tick=%s eq=%s budget_ms=%s elapsed_ms=%s",
                                        str(run.run_id),
                                        int(run.tick_index),
                                        str(eq),
                                        int(budget_ms),
                                        int(elapsed_ms),
                                    )
                                break

                        self._logger.debug(
                            "simulator.real.clearing_find_cycles_loop_start run_id=%s tick=%s eq=%s cleared_cycles=%s",
                            str(run.run_id),
                            int(run.tick_index),
                            str(eq),
                            int(cleared_cycles),
                        )
                        _loop_fc_t0 = time.monotonic()
                        cycles = await service.find_cycles(eq, max_depth=max_depth)
                        _loop_fc_ms = int((time.monotonic() - _loop_fc_t0) * 1000.0)
                        if _loop_fc_ms > 500:
                            self._logger.warning(
                                "simulator.real.clearing_find_cycles_loop_slow run_id=%s tick=%s eq=%s elapsed_ms=%s",
                                str(run.run_id),
                                int(run.tick_index),
                                str(eq),
                                int(_loop_fc_ms),
                            )
                        if not cycles:
                            break

                        executed = False
                        for cycle in cycles:
                            # Clearing amount is min edge amount in cycle.
                            try:
                                amts: list[Decimal] = []
                                for edge in cycle:
                                    if isinstance(edge, dict):
                                        amts.append(Decimal(str(edge.get("amount"))))
                                    else:
                                        amts.append(
                                            Decimal(str(getattr(edge, "amount")))
                                        )
                                clear_amount = min(amts) if amts else Decimal("0")
                            except Exception:
                                if self._should_warn_this_tick(
                                    run,
                                    key=f"clearing_clear_amount_parse_failed:{eq}",
                                ):
                                    self._logger.debug(
                                        "simulator.real.clearing_clear_amount_parse_failed run_id=%s tick=%s eq=%s",
                                        str(run.run_id),
                                        int(run.tick_index),
                                        str(eq),
                                        exc_info=True,
                                    )
                                clear_amount = Decimal("0")

                            self._logger.warning(
                                "simulator.real.clearing_execute_start run_id=%s tick=%s eq=%s clear_amount=%s cycle_len=%s",
                                str(run.run_id),
                                int(run.tick_index),
                                str(eq),
                                str(clear_amount),
                                int(len(cycle or [])),
                            )
                            _exec_t0 = time.monotonic()
                            success = await service.execute_clearing(cycle)
                            _exec_ms = int((time.monotonic() - _exec_t0) * 1000.0)
                            self._logger.warning(
                                "simulator.real.clearing_execute_done run_id=%s tick=%s eq=%s success=%s elapsed_ms=%s",
                                str(run.run_id),
                                int(run.tick_index),
                                str(eq),
                                bool(success),
                                int(_exec_ms),
                            )
                            if _exec_ms > 500:
                                self._logger.warning(
                                    "simulator.real.clearing_execute_slow run_id=%s tick=%s eq=%s elapsed_ms=%s",
                                    str(run.run_id),
                                    int(run.tick_index),
                                    str(eq),
                                    int(_exec_ms),
                                )
                            if success:
                                cleared_cycles += 1
                                if clear_amount > 0:
                                    cleared_amount_dec += clear_amount

                                try:
                                    for edge in cycle:
                                        if not isinstance(edge, dict):
                                            continue
                                        debtor_pid = str(
                                            edge.get("debtor") or ""
                                        ).strip()
                                        creditor_pid = str(
                                            edge.get("creditor") or ""
                                        ).strip()
                                        if debtor_pid:
                                            touched_nodes.add(debtor_pid)
                                        if creditor_pid:
                                            touched_nodes.add(creditor_pid)
                                        if creditor_pid and debtor_pid:
                                            touched_edges.add(
                                                (creditor_pid, debtor_pid)
                                            )
                                except Exception:
                                    if self._should_warn_this_tick(
                                        run, key=f"clearing_touched_parse_failed:{eq}"
                                    ):
                                        self._logger.debug(
                                            "simulator.real.clearing_touched_parse_failed run_id=%s tick=%s eq=%s",
                                            str(run.run_id),
                                            int(run.tick_index),
                                            str(eq),
                                            exc_info=True,
                                        )
                                executed = True
                                break

                        if not executed:
                            break
                        if cleared_cycles > 100:
                            break

                    # NOTE: ClearingService.execute_clearing already commits on success
                    # No need for additional commit here
                    cleared_amount_by_eq[str(eq)] = float(cleared_amount_dec)

                    # Best-effort: compute patches for touched nodes/edges.
                    node_patch_list: list[dict[str, Any]] | None = None
                    edge_patch_list: list[dict[str, Any]] | None = None

                    # Compute cleared_amount BEFORE the patch try-block so a
                    # patch-computation failure cannot wipe the amount.
                    # The amount is derived from already-executed clearing
                    # cycles and does not need VizPatchHelper.
                    cleared_amount_str: str | None = None
                    if cleared_amount_dec > 0:
                        try:
                            cleared_amount_str = format(
                                cleared_amount_dec.quantize(
                                    Decimal("0.01"), rounding=ROUND_DOWN
                                ),
                                "f",
                            )
                        except Exception:
                            cleared_amount_str = str(cleared_amount_dec)

                    self._logger.warning(
                        "simulator.real.clearing_patch_start run_id=%s tick=%s eq=%s touched_nodes=%s touched_edges=%s cleared_cycles=%s",
                        str(run.run_id),
                        int(run.tick_index),
                        str(eq),
                        int(len(touched_nodes)),
                        int(len(touched_edges)),
                        int(cleared_cycles),
                    )
                    _patch_t0 = time.monotonic()
                    try:
                        helper: VizPatchHelper | None
                        with self._lock:
                            helper = run._real_viz_by_eq.get(str(eq))

                        if helper is None:
                            helper = await VizPatchHelper.create(
                                clearing_session,
                                equivalent_code=str(eq),
                                refresh_every_ticks=int(
                                    getattr(
                                        settings,
                                        "SIMULATOR_VIZ_QUANTILE_REFRESH_TICKS",
                                        10,
                                    )
                                    or 10
                                ),
                            )
                            with self._lock:
                                run._real_viz_by_eq[str(eq)] = helper

                        # Refine cleared_amount_str with helper's precision if available.
                        if cleared_amount_dec > 0:
                            try:
                                precision = int(getattr(helper, "precision", 2) or 2)
                                money_quant = Decimal(1) / (Decimal(10) ** precision)
                                cleared_amount_str = format(
                                    cleared_amount_dec.quantize(
                                        money_quant, rounding=ROUND_DOWN
                                    ),
                                    "f",
                                )
                            except Exception:
                                pass  # keep the pre-computed value

                        # Refresh quantiles using all participants when available.
                        participant_ids: list[uuid.UUID] = []
                        if run._real_participants:
                            participant_ids = [
                                pid for (pid, _) in run._real_participants
                            ]
                        await helper.maybe_refresh_quantiles(
                            clearing_session,
                            tick_index=int(run.tick_index),
                            participant_ids=participant_ids,
                        )

                        # Node patches for touched nodes.
                        pids = sorted(
                            {str(x).strip() for x in touched_nodes if str(x).strip()}
                        )
                        if pids:
                            res = await clearing_session.execute(
                                select(Participant).where(Participant.pid.in_(pids))
                            )
                            pid_to_participant = {p.pid: p for p in res.scalars().all()}
                            node_patch_list = await helper.compute_node_patches(
                                clearing_session,
                                pid_to_participant=pid_to_participant,
                                pids=pids,
                            )
                            if node_patch_list == []:
                                node_patch_list = None

                            # Edge patches for touched trustlines.
                            pairs = sorted(touched_edges)
                            id_pairs: list[tuple[uuid.UUID, uuid.UUID]] = []
                            for a_pid, b_pid in pairs:
                                a = pid_to_participant.get(a_pid)
                                b = pid_to_participant.get(b_pid)
                                if not a or not b:
                                    continue
                                id_pairs.append((a.id, b.id))

                            debt_by_pair: dict[tuple[uuid.UUID, uuid.UUID], Decimal] = (
                                {}
                            )
                            tl_by_pair: dict[
                                tuple[uuid.UUID, uuid.UUID], tuple[Decimal, str | None]
                            ] = {}
                            if id_pairs:
                                debt_cond = or_(
                                    *[
                                        and_(Debt.creditor_id == a, Debt.debtor_id == b)
                                        for a, b in id_pairs
                                    ]
                                )
                                debt_rows = (
                                    await clearing_session.execute(
                                        select(
                                            Debt.creditor_id,
                                            Debt.debtor_id,
                                            func.coalesce(
                                                func.sum(Debt.amount), 0
                                            ).label("amount"),
                                        )
                                        .where(
                                            Debt.equivalent_id == helper.equivalent_id,
                                            debt_cond,
                                        )
                                        .group_by(Debt.creditor_id, Debt.debtor_id)
                                    )
                                ).all()
                                debt_by_pair = {
                                    (r.creditor_id, r.debtor_id): (
                                        r.amount or Decimal("0")
                                    )
                                    for r in debt_rows
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
                                    await clearing_session.execute(
                                        select(
                                            TrustLine.from_participant_id,
                                            TrustLine.to_participant_id,
                                            TrustLine.limit,
                                            TrustLine.status,
                                        ).where(
                                            TrustLine.equivalent_id
                                            == helper.equivalent_id,
                                            tl_cond,
                                        )
                                    )
                                ).all()
                                tl_by_pair = {
                                    (r.from_participant_id, r.to_participant_id): (
                                        (r.limit or Decimal("0")),
                                        (
                                            str(r.status)
                                            if r.status is not None
                                            else None
                                        ),
                                    )
                                    for r in tl_rows
                                }

                            edge_patch_list = []
                            for a_pid, b_pid in pairs:
                                a = pid_to_participant.get(a_pid)
                                b = pid_to_participant.get(b_pid)
                                if not a or not b:
                                    continue

                                used_amt = debt_by_pair.get((a.id, b.id), Decimal("0"))
                                limit_amt, tl_status = tl_by_pair.get(
                                    (a.id, b.id), (Decimal("0"), None)
                                )
                                available_amt = max(Decimal("0"), limit_amt - used_amt)

                                edge_viz = helper.edge_viz(
                                    status=tl_status, used=used_amt, limit=limit_amt
                                )
                                edge_patch_list.append(
                                    {
                                        "source": a_pid,
                                        "target": b_pid,
                                        "used": str(used_amt),
                                        "available": str(available_amt),
                                        **edge_viz,
                                    }
                                )
                            if edge_patch_list == []:
                                edge_patch_list = None
                    except Exception:
                        if self._should_warn_this_tick(
                            run, key=f"clearing_done_patch_failed:{eq}"
                        ):
                            self._logger.debug(
                                "simulator.real.clearing_done_patch_failed run_id=%s tick=%s eq=%s",
                                str(run.run_id),
                                int(run.tick_index),
                                str(eq),
                                exc_info=True,
                            )
                        node_patch_list = None
                        edge_patch_list = None
                        # NOTE: cleared_amount_str is NOT reset here; it was
                        # computed before the patch try-block and remains valid.

                    _patch_ms = int((time.monotonic() - _patch_t0) * 1000.0)
                    if _patch_ms > 500:
                        self._logger.warning(
                            "simulator.real.clearing_patch_slow run_id=%s tick=%s eq=%s elapsed_ms=%s",
                            str(run.run_id),
                            int(run.tick_index),
                            str(eq),
                            int(_patch_ms),
                        )
                    self._logger.warning(
                        "simulator.real.clearing_patch_done run_id=%s tick=%s eq=%s elapsed_ms=%s",
                        str(run.run_id),
                        int(run.tick_index),
                        str(eq),
                        int(_patch_ms),
                    )

                    done_evt = SimulatorClearingDoneEvent(
                        event_id=self._sse.next_event_id(run),
                        ts=self._utc_now(),
                        type="clearing.done",
                        equivalent=eq,
                        plan_id=plan_id,
                        cleared_cycles=cleared_cycles,
                        cleared_amount=cleared_amount_str,
                        node_patch=node_patch_list,
                        edge_patch=edge_patch_list,
                    ).model_dump(mode="json", by_alias=True)
                    with self._lock:
                        run.last_event_type = "clearing.done"
                        run.current_phase = None
                    self._sse.broadcast(run_id, done_evt)

                    self._logger.warning(
                        "simulator.real.clearing_eq_done run_id=%s tick=%s eq=%s elapsed_ms=%s cleared_cycles=%s",
                        str(run.run_id),
                        int(run.tick_index),
                        str(eq),
                        int((time.monotonic() - eq_t0) * 1000.0),
                        int(cleared_cycles),
                    )
            except Exception as e:
                if self._should_warn_this_tick(run, key=f"clearing_failed:{eq}"):
                    self._logger.warning(
                        "simulator.real.clearing_failed run_id=%s tick=%s eq=%s",
                        str(run.run_id),
                        int(run.tick_index),
                        str(eq),
                        exc_info=True,
                    )
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

    def _real_candidates_from_scenario(
        self, scenario: dict[str, Any]
    ) -> list[dict[str, Any]]:
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
            out.append(
                {
                    "equivalent": eq,
                    "sender_pid": to,
                    "receiver_pid": frm,
                    "limit": limit,
                }
            )

        out.sort(key=lambda x: (x["equivalent"], x["receiver_pid"], x["sender_pid"]))
        return out

    def _plan_real_payments(
        self, run: RunRecord, scenario: dict[str, Any]
    ) -> list[_RealPaymentAction]:
        """Deterministic planner for Real Mode payment actions.

        Important property for SB-NF-04:
        - planning for a given (seed, tick_index, scenario) is deterministic.
        - changing intensity only changes *how many* actions we take from the same
          per-tick ordering (prefix-stable), so it doesn't affect later ticks.
        """

        intensity = max(0.0, min(1.0, float(run.intensity_percent) / 100.0))
        target_actions = int(self._actions_per_tick_max * intensity)
        if target_actions <= 0:
            return []

        candidates = self._real_candidates_from_scenario(scenario)
        if not candidates:
            return []

        profiles_props_by_id: dict[str, dict[str, Any]] = {}
        for bp in scenario.get("behaviorProfiles") or []:
            if not isinstance(bp, dict):
                continue
            bp_id = str(bp.get("id") or "").strip()
            if not bp_id:
                continue
            props = bp.get("props")
            profiles_props_by_id[bp_id] = props if isinstance(props, dict) else {}

        participant_profile_id_by_pid: dict[str, str] = {}
        participant_group_by_pid: dict[str, str] = {}
        for p in scenario.get("participants") or []:
            if not isinstance(p, dict):
                continue
            pid = str(p.get("id") or p.get("participant_id") or "").strip()
            if not pid:
                continue
            profile_id = str(p.get("behaviorProfileId") or "").strip()
            if profile_id:
                participant_profile_id_by_pid[pid] = profile_id
            group_id = str(p.get("groupId") or "").strip()
            if group_id:
                participant_group_by_pid[pid] = group_id

        tick_seed = (int(run.seed) * 1_000_003 + int(run.tick_index)) & 0xFFFFFFFF
        tick_rng = random.Random(tick_seed)

        order = list(candidates)
        tick_rng.shuffle(order)

        # Active stress multipliers (tx_rate) for this tick.
        mult_all, mult_by_group, mult_by_profile = self._compute_stress_multipliers(
            events=scenario.get("events"),
            sim_time_ms=int(run.sim_time_ms),
        )

        def _clamp01(v: Any, default: float) -> float:
            try:
                f = float(v)
            except Exception:
                return default
            if f < 0.0:
                return 0.0
            if f > 1.0:
                return 1.0
            return f

        def _norm_weight(weights: Any, key: str) -> float:
            if not isinstance(weights, dict) or not weights:
                return 1.0
            try:
                values = [float(x) for x in weights.values() if float(x) > 0]
                if not values:
                    return 0.0
                max_w = max(values)
                w = float(weights.get(key, 0.0))
                if max_w <= 0 or w <= 0:
                    return 0.0
                return min(1.0, w / max_w)
            except Exception:
                return 1.0

        # Build adjacency (payment direction debtor->creditor) and per-sender/receiver limit hints.
        # NOTE: TrustLine direction is creditor->debtor, but candidates are already inverted to debtor->creditor.
        adjacency_by_eq: dict[str, dict[str, list[tuple[str, Decimal]]]] = {}
        max_outgoing_limit: dict[tuple[str, str], Decimal] = {}
        max_incoming_limit: dict[tuple[str, str], Decimal] = {}
        direct_edge_limit: dict[tuple[str, str, str], Decimal] = {}
        for c in candidates:
            eq = str(c.get("equivalent") or "").strip()
            sender = str(c.get("sender_pid") or "").strip()
            receiver = str(c.get("receiver_pid") or "").strip()
            limit = c.get("limit")
            if not eq or not sender or not receiver:
                continue
            if not isinstance(limit, Decimal):
                continue
            adjacency_by_eq.setdefault(eq, {}).setdefault(sender, []).append(
                (receiver, limit)
            )

            direct_edge_limit[(sender, receiver, eq)] = limit

            k = (sender, eq)
            prev = max_outgoing_limit.get(k)
            if prev is None or limit > prev:
                max_outgoing_limit[k] = limit

            k_in = (receiver, eq)
            prev_in = max_incoming_limit.get(k_in)
            if prev_in is None or limit > prev_in:
                max_incoming_limit[k_in] = limit

        for eq, m in adjacency_by_eq.items():
            for sender, edges in m.items():
                # Deterministic neighbor order.
                edges.sort(key=lambda x: x[0])

        all_group_ids = sorted({g for g in participant_group_by_pid.values() if g})

        def _pick_group(rng: random.Random, sender_props: dict[str, Any]) -> str | None:
            weights = sender_props.get("recipient_group_weights")
            if not isinstance(weights, dict) or not weights:
                return None
            try:
                items = [(str(k), float(v)) for k, v in weights.items()]
                items = [(k, v) for (k, v) in items if k and v > 0]
                if not items:
                    return None
                total = sum(v for _, v in items)
                if total <= 0:
                    return None
                r = rng.random() * total
                acc = 0.0
                for k, v in items:
                    acc += v
                    if r <= acc:
                        return k
                return items[-1][0]
            except Exception:
                return None

        def _reachable_nodes(
            eq: str, sender: str, *, max_depth: int = 3, max_nodes: int = 200
        ) -> list[str]:
            graph = adjacency_by_eq.get(eq) or {}
            if sender not in graph:
                return []

            visited: set[str] = {sender}
            # (node, depth)
            queue: list[tuple[str, int]] = [(sender, 0)]
            qi = 0
            while qi < len(queue) and len(visited) < max_nodes:
                node, depth = queue[qi]
                qi += 1
                if depth >= max_depth:
                    continue
                for nxt, _lim in graph.get(node) or []:
                    if nxt in visited:
                        continue
                    visited.add(nxt)
                    queue.append((nxt, depth + 1))
                    if len(visited) >= max_nodes:
                        break

            visited.discard(sender)
            return sorted(visited)

        def _choose_receiver(
            *, rng: random.Random, eq: str, sender: str, sender_props: dict[str, Any]
        ) -> str | None:
            reachable = _reachable_nodes(eq, sender)
            if not reachable:
                # Fallback to direct neighbors.
                direct = [
                    pid
                    for (pid, _lim) in (adjacency_by_eq.get(eq) or {}).get(sender, [])
                ]
                reachable = sorted({p for p in direct if p and p != sender})
            if not reachable:
                return None

            target_group = _pick_group(rng, sender_props)
            if target_group:
                in_group = [
                    pid
                    for pid in reachable
                    if participant_group_by_pid.get(pid) == target_group
                ]
                if in_group:
                    return rng.choice(in_group)

            # If no group match (or no group weights), try any known group match, then any reachable.
            if all_group_ids:
                rng.shuffle(all_group_ids)
                for g in all_group_ids:
                    in_group = [
                        pid
                        for pid in reachable
                        if participant_group_by_pid.get(pid) == g
                    ]
                    if in_group:
                        return rng.choice(in_group)

            return rng.choice(reachable)

        planned: list[_RealPaymentAction] = []
        i = 0
        max_iters = max(1, target_actions) * 50
        while len(planned) < target_actions and i < max_iters:
            c = order[i % len(order)]

            eq = str(c["equivalent"])
            sender_pid = c["sender_pid"]
            sender_profile_id = participant_profile_id_by_pid.get(sender_pid, "")
            sender_props = profiles_props_by_id.get(sender_profile_id, {})

            tx_rate_base = _clamp01(sender_props.get("tx_rate", 1.0), 1.0)
            sender_group = participant_group_by_pid.get(sender_pid, "")
            tx_rate_mult = float(mult_all)
            if sender_group:
                tx_rate_mult *= float(mult_by_group.get(sender_group, 1.0))
            if sender_profile_id:
                tx_rate_mult *= float(mult_by_profile.get(sender_profile_id, 1.0))
            tx_rate = _clamp01(float(tx_rate_base) * float(tx_rate_mult), 1.0)
            eq_weight = _norm_weight(sender_props.get("equivalent_weights"), eq)

            accept_prob = tx_rate * eq_weight
            if accept_prob <= 0.0:
                i += 1
                continue

            action_seed = (tick_seed * 1_000_003 + i) & 0xFFFFFFFF
            action_rng = random.Random(action_seed)

            if action_rng.random() > accept_prob:
                i += 1
                continue

            receiver_pid = _choose_receiver(
                rng=action_rng, eq=eq, sender=sender_pid, sender_props=sender_props
            )
            if receiver_pid is None:
                i += 1
                continue

            # Bound by sender-side outgoing capacity upper bound, plus receiver-side incoming upper bound.
            # For direct neighbors, also bound by the concrete direct edge limit.
            limit = max_outgoing_limit.get((sender_pid, eq), c["limit"])
            recv_cap = max_incoming_limit.get((receiver_pid, eq))
            if recv_cap is not None and recv_cap > 0:
                limit = min(limit, recv_cap)
            direct_cap = direct_edge_limit.get((sender_pid, receiver_pid, eq))
            if direct_cap is not None and direct_cap > 0:
                limit = min(limit, direct_cap)

            amount_model = None
            raw_amount_model = sender_props.get("amount_model")
            if isinstance(raw_amount_model, dict):
                maybe = raw_amount_model.get(eq)
                if isinstance(maybe, dict):
                    amount_model = maybe

            amount = self._real_pick_amount(
                action_rng, limit, amount_model=amount_model
            )
            if amount is None:
                i += 1
                continue

            planned.append(
                _RealPaymentAction(
                    # `seq` must be contiguous within a tick for ordered SSE emission.
                    seq=len(planned),
                    equivalent=eq,
                    sender_pid=sender_pid,
                    receiver_pid=receiver_pid,
                    amount=amount,
                )
            )

            i += 1

        return planned

    def _real_pick_amount(
        self,
        rng: random.Random,
        limit: Decimal,
        *,
        amount_model: dict[str, Any] | None = None,
    ) -> str | None:
        cap = limit
        if self._real_amount_cap_limit is not None:
            cap = min(cap, self._real_amount_cap_limit)
        if cap <= 0:
            return None

        model_min: Decimal | None = None
        if isinstance(amount_model, dict) and amount_model:
            try:
                m_max = amount_model.get("max")
                if m_max is not None:
                    cap = min(cap, Decimal(str(m_max)))
                m_min = amount_model.get("min")
                if m_min is not None:
                    model_min = Decimal(str(m_min))
            except Exception:
                model_min = None

        if cap <= 0:
            return None
        if model_min is not None and model_min > cap:
            return None

        if isinstance(amount_model, dict) and amount_model:
            low = model_min if model_min is not None else Decimal("0.10")

            # If p50+p90 are provided, prefer a lognormal model for more realistic variability.
            # (p90 was previously ignored; this makes scenarios with p90 behave as intended.)
            try:
                p50_raw = amount_model.get("p50")
                p90_raw = amount_model.get("p90")
                if p50_raw is not None and p90_raw is not None:
                    p50 = Decimal(str(p50_raw))
                    p90 = Decimal(str(p90_raw))

                    if p50 <= 0 or p90 <= 0:
                        raise ValueError("non_positive_percentiles")

                    if p50 < low:
                        p50 = low
                    if p50 > cap:
                        p50 = cap

                    if p90 < p50:
                        p90 = p50
                    if p90 > cap:
                        p90 = cap

                    ratio = float(p90 / p50) if p50 > 0 else 1.0
                    if ratio <= 1.0:
                        raise ValueError("degenerate_p90")

                    z90 = 1.281551565545  # approx Normal(0,1) quantile at 0.90
                    mu = math.log(float(p50))
                    sigma = math.log(ratio) / z90
                    if not (math.isfinite(mu) and math.isfinite(sigma) and sigma > 0):
                        raise ValueError("bad_lognormal_params")

                    raw_f = rng.lognormvariate(mu, sigma)
                    raw = Decimal(str(raw_f))
                else:
                    raise ValueError("missing_percentiles")
            except Exception:
                # Fallback: triangular distribution biased towards p50 (mode).
                try:
                    mode_raw = amount_model.get("p50")
                    mode = (
                        Decimal(str(mode_raw))
                        if mode_raw is not None
                        else (low + cap) / 2
                    )
                    if mode < low:
                        mode = low
                    if mode > cap:
                        mode = cap
                    raw_f = rng.triangular(float(low), float(cap), float(mode))
                    raw = Decimal(str(raw_f))
                except Exception:
                    raw = Decimal(str(0.1 + rng.random() * float(cap)))
        else:
            raw = Decimal(str(0.1 + rng.random() * float(cap)))

        amt = min(raw, cap).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
        if model_min is not None and amt < model_min:
            amt = model_min.quantize(Decimal("0.01"), rounding=ROUND_DOWN)
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

    async def _load_real_participants(
        self, session, scenario: dict[str, Any]
    ) -> list[tuple[uuid.UUID, str]]:
        pids = [
            str(p.get("id") or "").strip() for p in (scenario.get("participants") or [])
        ]
        pids = [p for p in pids if p]
        if not pids:
            return []

        rows = (
            (
                await session.execute(
                    select(Participant).where(Participant.pid.in_(pids))
                )
            )
            .scalars()
            .all()
        )
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
            existing_eq = (
                (
                    await session.execute(
                        select(Equivalent).where(Equivalent.code.in_(eq_codes))
                    )
                )
                .scalars()
                .all()
            )
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
            existing_p = (
                (
                    await session.execute(
                        select(Participant).where(Participant.pid.in_(pids))
                    )
                )
                .scalars()
                .all()
            )
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
                        type=(
                            p_type
                            if p_type in {"person", "business", "hub"}
                            else "person"
                        ),
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
            eq_rows = (
                (
                    await session.execute(
                        select(Equivalent).where(Equivalent.code.in_(eq_codes))
                    )
                )
                .scalars()
                .all()
            )
            eq_by_code = {e.code: e for e in eq_rows}

            p_rows = (
                (
                    await session.execute(
                        select(Participant).where(Participant.pid.in_(pids))
                    )
                )
                .scalars()
                .all()
            )
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
