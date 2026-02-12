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

from sqlalchemy import and_, func, or_, select, update

import app.db.session as db_session
import app.core.simulator.storage as simulator_storage
from app.config import settings
from app.core.clearing.service import ClearingService
from app.core.payments.router import PaymentRouter
from app.core.payments.service import PaymentService
from app.core.simulator import viz_rules
from app.core.simulator.artifacts import ArtifactsManager
from app.core.simulator.edge_patch_builder import EdgePatchBuilder
from app.core.simulator.inject_executor import InjectExecutor
from app.core.simulator.models import EdgeClearingHistory, RunRecord, TrustDriftConfig
from app.core.simulator.real_clearing_engine import RealClearingEngine
from app.core.simulator.real_debt_snapshot_loader import RealDebtSnapshotLoader
from app.core.simulator.real_payment_planner import RealPaymentPlanner
from app.core.simulator.real_payments_executor import RealPaymentsExecutor
from app.core.simulator.real_tick_persistence import RealTickPersistence
from app.core.simulator.trust_drift_engine import TrustDriftEngine
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
    SimulatorTopologyChangedEvent,
    SimulatorTxFailedEvent,
    SimulatorTxUpdatedEvent,
    TopologyChangedEdgeRef,
    TopologyChangedNodeRef,
    TopologyChangedPayload,
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
        self._real_enable_inject = (
            int(_safe_int_env("SIMULATOR_REAL_ENABLE_INJECT", 0)) >= 1
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

    def _get_real_payments_executor(self) -> RealPaymentsExecutor:
        executor = getattr(self, "_real_payments_executor", None)
        if executor is not None:
            return executor

        edge_patch_builder = getattr(self, "_edge_patch_builder", None)
        if edge_patch_builder is None:
            edge_patch_builder = EdgePatchBuilder(logger=self._logger)
            setattr(self, "_edge_patch_builder", edge_patch_builder)

        executor = RealPaymentsExecutor(
            lock=self._lock,
            sse=self._sse,
            utc_now=self._utc_now,
            logger=self._logger,
            edge_patch_builder=edge_patch_builder,
            should_warn_this_tick=self._should_warn_this_tick,
            sim_idempotency_key=self._sim_idempotency_key,
        )
        setattr(self, "_real_payments_executor", executor)
        return executor

    def _get_trust_drift_engine(self) -> TrustDriftEngine:
        engine = getattr(self, "_trust_drift_engine", None)
        if engine is not None:
            return engine

        engine = TrustDriftEngine(
            sse=self._sse,
            utc_now=self._utc_now,
            logger=self._logger,
            get_scenario_raw=self._get_scenario_raw,
        )
        setattr(self, "_trust_drift_engine", engine)
        return engine

    def _get_inject_executor(self) -> InjectExecutor:
        executor = getattr(self, "_inject_executor", None)
        if executor is not None:
            return executor

        executor = InjectExecutor(
            sse=self._sse,
            artifacts=self._artifacts,
            utc_now=self._utc_now,
            logger=self._logger,
        )
        setattr(self, "_inject_executor", executor)
        return executor

    def _get_real_debt_snapshot_loader(self) -> RealDebtSnapshotLoader:
        loader = getattr(self, "_real_debt_snapshot_loader", None)
        if loader is not None:
            return loader

        loader = RealDebtSnapshotLoader()
        setattr(self, "_real_debt_snapshot_loader", loader)
        return loader

    def _get_real_payment_planner(self) -> RealPaymentPlanner:
        planner = getattr(self, "_real_payment_planner", None)
        if planner is not None:
            return planner

        planner = RealPaymentPlanner(
            actions_per_tick_max=int(getattr(self, "_actions_per_tick_max", 0) or 0),
            amount_cap_limit=getattr(self, "_real_amount_cap_limit", None),
            logger=self._logger,
            action_factory=lambda seq, eq, sender_pid, receiver_pid, amount: _RealPaymentAction(
                seq=int(seq),
                equivalent=str(eq),
                sender_pid=str(sender_pid),
                receiver_pid=str(receiver_pid),
                amount=str(amount),
            ),
        )
        setattr(self, "_real_payment_planner", planner)
        return planner

    def _get_real_clearing_engine(self) -> RealClearingEngine:
        engine = getattr(self, "_real_clearing_engine", None)
        if engine is not None:
            return engine

        edge_patch_builder = getattr(self, "_edge_patch_builder", None)
        if edge_patch_builder is None:
            edge_patch_builder = EdgePatchBuilder(logger=self._logger)
            setattr(self, "_edge_patch_builder", edge_patch_builder)

        engine = RealClearingEngine(
            lock=self._lock,
            sse=self._sse,
            utc_now=self._utc_now,
            logger=self._logger,
            edge_patch_builder=edge_patch_builder,
            clearing_max_depth_limit=int(getattr(self, "_clearing_max_depth_limit", 0) or 0),
            clearing_max_fx_edges_limit=int(getattr(self, "_clearing_max_fx_edges_limit", 0) or 0),
            real_clearing_time_budget_ms=int(getattr(self, "_real_clearing_time_budget_ms", 0) or 0),
            should_warn_this_tick=lambda run, key: self._should_warn_this_tick(run, key=key),
        )
        setattr(self, "_real_clearing_engine", engine)
        return engine

    def _get_real_tick_persistence(self) -> RealTickPersistence:
        persistence = getattr(self, "_real_tick_persistence", None)
        if persistence is not None:
            return persistence

        persistence = RealTickPersistence(
            lock=self._lock,
            artifacts=self._artifacts,
            utc_now=self._utc_now,
            db_enabled=self._db_enabled,
            logger=self._logger,
            real_db_metrics_every_n_ticks=int(getattr(self, "_real_db_metrics_every_n_ticks", 0) or 0),
            real_db_bottlenecks_every_n_ticks=int(getattr(self, "_real_db_bottlenecks_every_n_ticks", 0) or 0),
            real_last_tick_write_every_ms=int(getattr(self, "_real_last_tick_write_every_ms", 0) or 0),
            real_artifacts_sync_every_ms=int(getattr(self, "_real_artifacts_sync_every_ms", 0) or 0),
        )
        setattr(self, "_real_tick_persistence", persistence)
        return persistence

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
                await self._get_inject_executor().apply_inject_event(
                    session,
                    run_id=run_id,
                    run=run,
                    scenario=scenario,
                    event_index=idx,
                    event_time_ms=t0,
                    event=evt,
                    pid_to_participant_id=pid_to_participant_id,
                    inject_enabled=bool(self._real_enable_inject),
                    build_edge_patch_for_equivalent=self._build_edge_patch_for_equivalent,
                    broadcast_topology_edge_patch=self._broadcast_topology_edge_patch,
                )
                continue

            # Unknown / unsupported event types are ignored, but we still mark them fired once due.
            run._real_fired_scenario_event_indexes.add(idx)

    async def _apply_inject_event(
        self,
        session,
        *,
        run_id: str,
        run: RunRecord,
        scenario: dict[str, Any],
        event_index: int,
        event_time_ms: int,
        event: dict[str, Any] | None,
        pid_to_participant_id: dict[str, uuid.UUID],
    ) -> None:
        if not self._real_enable_inject:
            self._artifacts.enqueue_event_artifact(
                run_id,
                {
                    "type": "note",
                    "ts": self._utc_now().isoformat(),
                    "sim_time_ms": int(run.sim_time_ms),
                    "tick_index": int(run.tick_index),
                    "scenario": {
                        "event_index": int(event_index),
                        "time": event_time_ms,
                        "description": "inject skipped (SIMULATOR_REAL_ENABLE_INJECT=0)",
                    },
                },
            )
            run._real_fired_scenario_event_indexes.add(event_index)
            return

        effects = (event or {}).get("effects")
        if not isinstance(effects, list) or not effects:
            run._real_fired_scenario_event_indexes.add(event_index)
            return

        max_edges = 500
        max_total_amount: Decimal | None = None
        try:
            md = (event or {}).get("metadata")
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

        # Track new entities for cache invalidation after commit.
        affected_equivalents: set[str] = set()
        new_participants_for_cache: list[tuple[uuid.UUID, str]] = []
        new_participants_for_scenario: list[dict[str, Any]] = []
        new_trustlines_for_scenario: list[dict[str, Any]] = []
        frozen_participant_pids: list[str] = []
        inject_debt_equivalents: set[str] = set()
        inject_debt_edges_by_eq: dict[str, set[tuple[str, str]]] = {}

        async def resolve_eq_id(eq_code: str) -> uuid.UUID | None:
            """Lazily resolve equivalent code → UUID from DB."""
            eq_upper = eq_code.strip().upper()
            cached = eq_id_by_code.get(eq_upper)
            if cached is not None:
                return cached
            row = (
                await session.execute(
                    select(Equivalent.id).where(Equivalent.code == eq_upper)
                )
            ).scalar_one_or_none()
            if row is not None:
                eq_id_by_code[eq_upper] = row
            return row

        default_tl_policy = {
            "auto_clearing": True,
            "can_be_intermediate": True,
            "max_hop_usage": None,
            "daily_limit": None,
            "blocked_participants": [],
        }

        async def op_inject_debt(eff: dict[str, Any]) -> bool:
            nonlocal applied, skipped, total_applied

            eq = str(eff.get("equivalent") or "").strip().upper()
            # Contract: prefer from/to (creditor->debtor), but keep
            # backward-compatible debtor/creditor keys.
            creditor_pid = str(eff.get("creditor") or eff.get("from") or "").strip()
            debtor_pid = str(eff.get("debtor") or eff.get("to") or "").strip()
            if not eq or not debtor_pid or not creditor_pid:
                skipped += 1
                return False

            try:
                amount = Decimal(str(eff.get("amount")))
                amount = amount.quantize(Decimal("0.01"), rounding=ROUND_DOWN)
            except Exception:
                skipped += 1
                return False
            if amount <= 0:
                skipped += 1
                return False

            if max_total_amount is not None and total_applied + amount > max_total_amount:
                skipped += 1
                return False

            debtor_id = pid_to_participant_id.get(debtor_pid)
            creditor_id = pid_to_participant_id.get(creditor_pid)
            if debtor_id is None or creditor_id is None:
                skipped += 1
                return False

            eq_id = await resolve_eq_id(eq)
            if eq_id is None:
                skipped += 1
                return False

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
                return False
            tl_limit, tl_status = tl
            if str(tl_status or "").strip().lower() != "active":
                skipped += 1
                return False
            try:
                tl_limit_amt = Decimal(str(tl_limit))
            except Exception:
                skipped += 1
                return False
            if tl_limit_amt <= 0:
                skipped += 1
                return False

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
                    return False
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
                    return False
                existing.amount = new_amt

            applied += 1
            total_applied += amount
            affected_equivalents.add(eq)
            inject_debt_equivalents.add(eq)
            inject_debt_edges_by_eq.setdefault(eq, set()).add((creditor_pid, debtor_pid))
            return True

        async def op_add_participant(eff: dict[str, Any]) -> bool:
            nonlocal applied, skipped

            try:
                p_data = eff.get("participant")
                if not isinstance(p_data, dict):
                    self._logger.warning(
                        "simulator.real.inject.add_participant: missing participant dict"
                    )
                    skipped += 1
                    return False

                pid = str(p_data.get("id") or "").strip()
                if not pid:
                    skipped += 1
                    return False

                # Idempotency: skip if participant already exists.
                existing_p = (
                    await session.execute(
                        select(Participant.id).where(Participant.pid == pid)
                    )
                ).scalar_one_or_none()
                if existing_p is not None:
                    self._logger.info(
                        "simulator.real.inject.add_participant.skip_exists pid=%s",
                        pid,
                    )
                    skipped += 1
                    return False

                name = str(p_data.get("name") or pid)
                p_type = str(p_data.get("type") or "person").strip()
                if p_type not in {"person", "business", "hub"}:
                    p_type = "person"
                status = str(p_data.get("status") or "active").strip().lower()
                if status not in {"active", "suspended", "left", "deleted"}:
                    status = "active"
                public_key = hashlib.sha256(pid.encode("utf-8")).hexdigest()

                new_p = Participant(
                    pid=pid,
                    display_name=name,
                    public_key=public_key,
                    type=p_type,
                    status=status,
                    profile={},
                )
                session.add(new_p)
                await session.flush()  # materialise new_p.id

                new_participants_for_cache.append((new_p.id, pid))
                new_participants_for_scenario.append(
                    {
                        "id": pid,
                        "name": name,
                        "type": p_type,
                        "status": status,
                        "groupId": str(p_data.get("groupId") or ""),
                        "behaviorProfileId": str(p_data.get("behaviorProfileId") or ""),
                    }
                )
                pid_to_participant_id[pid] = new_p.id

                # Create initial trustlines (sponsor ↔ new participant).
                initial_tls = eff.get("initial_trustlines")
                if isinstance(initial_tls, list):
                    for itl in initial_tls:
                        if not isinstance(itl, dict):
                            continue
                        sponsor_pid = str(itl.get("sponsor") or "").strip()
                        eq_code = str(itl.get("equivalent") or "").strip().upper()
                        raw_limit = itl.get("limit")
                        direction = str(itl.get("direction") or "sponsor_credits_new").strip()

                        if not sponsor_pid or not eq_code:
                            continue
                        try:
                            tl_limit_val = Decimal(str(raw_limit))
                        except Exception:
                            continue
                        if tl_limit_val <= 0:
                            continue

                        # Resolve sponsor participant ID.
                        sponsor_id = pid_to_participant_id.get(sponsor_pid)
                        if sponsor_id is None:
                            sponsor_row = (
                                await session.execute(
                                    select(Participant.id).where(Participant.pid == sponsor_pid)
                                )
                            ).scalar_one_or_none()
                            if sponsor_row is None:
                                self._logger.warning(
                                    "simulator.real.inject.add_participant.sponsor_not_found sponsor=%s",
                                    sponsor_pid,
                                )
                                continue
                            sponsor_id = sponsor_row
                            pid_to_participant_id[sponsor_pid] = sponsor_id

                        eq_id = await resolve_eq_id(eq_code)
                        if eq_id is None:
                            continue

                        # Determine trustline direction.
                        if direction == "sponsor_credits_new":
                            from_id = sponsor_id
                            to_id = new_p.id
                            from_pid_str = sponsor_pid
                            to_pid_str = pid
                        else:
                            from_id = new_p.id
                            to_id = sponsor_id
                            from_pid_str = pid
                            to_pid_str = sponsor_pid

                        # Idempotency: skip if trustline already exists.
                        existing_tl = (
                            await session.execute(
                                select(TrustLine.id).where(
                                    TrustLine.from_participant_id == from_id,
                                    TrustLine.to_participant_id == to_id,
                                    TrustLine.equivalent_id == eq_id,
                                )
                            )
                        ).scalar_one_or_none()
                        if existing_tl is not None:
                            continue

                        session.add(
                            TrustLine(
                                from_participant_id=from_id,
                                to_participant_id=to_id,
                                equivalent_id=eq_id,
                                limit=tl_limit_val,
                                status="active",
                                policy=dict(default_tl_policy),
                            )
                        )
                        affected_equivalents.add(eq_code)
                        new_trustlines_for_scenario.append(
                            {
                                "from": from_pid_str,
                                "to": to_pid_str,
                                "equivalent": eq_code,
                                "limit": str(tl_limit_val),
                                "status": "active",
                            }
                        )

                applied += 1
                return True
            except Exception as exc:
                self._logger.warning(
                    "simulator.real.inject.add_participant.error: %s",
                    exc,
                    exc_info=True,
                )
                skipped += 1
                return False

        async def op_create_trustline(eff: dict[str, Any]) -> bool:
            nonlocal applied, skipped

            try:
                from_pid_val = str(eff.get("from") or "").strip()
                to_pid_val = str(eff.get("to") or "").strip()
                eq_code = str(eff.get("equivalent") or "").strip().upper()
                raw_limit = eff.get("limit")

                if not from_pid_val or not to_pid_val or not eq_code:
                    skipped += 1
                    return False

                try:
                    tl_limit_val = Decimal(str(raw_limit))
                except Exception:
                    skipped += 1
                    return False
                if tl_limit_val <= 0:
                    skipped += 1
                    return False

                # Resolve participant IDs.
                from_id = pid_to_participant_id.get(from_pid_val)
                if from_id is None:
                    row = (
                        await session.execute(
                            select(Participant.id).where(Participant.pid == from_pid_val)
                        )
                    ).scalar_one_or_none()
                    if row is None:
                        self._logger.warning(
                            "simulator.real.inject.create_trustline.from_not_found pid=%s",
                            from_pid_val,
                        )
                        skipped += 1
                        return False
                    from_id = row
                    pid_to_participant_id[from_pid_val] = from_id

                to_id = pid_to_participant_id.get(to_pid_val)
                if to_id is None:
                    row = (
                        await session.execute(
                            select(Participant.id).where(Participant.pid == to_pid_val)
                        )
                    ).scalar_one_or_none()
                    if row is None:
                        self._logger.warning(
                            "simulator.real.inject.create_trustline.to_not_found pid=%s",
                            to_pid_val,
                        )
                        skipped += 1
                        return False
                    to_id = row
                    pid_to_participant_id[to_pid_val] = to_id

                eq_id = await resolve_eq_id(eq_code)
                if eq_id is None:
                    skipped += 1
                    return False

                # Idempotency: skip if trustline already exists.
                existing_tl = (
                    await session.execute(
                        select(TrustLine.id).where(
                            TrustLine.from_participant_id == from_id,
                            TrustLine.to_participant_id == to_id,
                            TrustLine.equivalent_id == eq_id,
                        )
                    )
                ).scalar_one_or_none()
                if existing_tl is not None:
                    self._logger.info(
                        "simulator.real.inject.create_trustline.skip_exists from=%s to=%s eq=%s",
                        from_pid_val,
                        to_pid_val,
                        eq_code,
                    )
                    skipped += 1
                    return False

                session.add(
                    TrustLine(
                        from_participant_id=from_id,
                        to_participant_id=to_id,
                        equivalent_id=eq_id,
                        limit=tl_limit_val,
                        status="active",
                        policy=dict(default_tl_policy),
                    )
                )
                affected_equivalents.add(eq_code)
                new_trustlines_for_scenario.append(
                    {
                        "from": from_pid_val,
                        "to": to_pid_val,
                        "equivalent": eq_code,
                        "limit": str(tl_limit_val),
                        "status": "active",
                    }
                )
                applied += 1
                return True
            except Exception as exc:
                self._logger.warning(
                    "simulator.real.inject.create_trustline.error: %s",
                    exc,
                    exc_info=True,
                )
                skipped += 1
                return False

        async def op_freeze_participant(eff: dict[str, Any]) -> bool:
            nonlocal applied, skipped

            try:
                freeze_pid = str(eff.get("participant_id") or "").strip()
                freeze_tls = bool(eff.get("freeze_trustlines", True))

                if not freeze_pid:
                    skipped += 1
                    return False

                # Update participant status → suspended.
                p_row = (
                    await session.execute(select(Participant).where(Participant.pid == freeze_pid))
                ).scalar_one_or_none()
                if p_row is None:
                    self._logger.warning(
                        "simulator.real.inject.freeze_participant.not_found pid=%s",
                        freeze_pid,
                    )
                    skipped += 1
                    return False

                if p_row.status == "suspended":
                    self._logger.info(
                        "simulator.real.inject.freeze_participant.already_suspended pid=%s",
                        freeze_pid,
                    )
                    skipped += 1
                    return False

                p_row.status = "suspended"

                if freeze_tls:
                    incident_tls = (
                        await session.execute(
                            select(TrustLine).where(
                                or_(
                                    TrustLine.from_participant_id == p_row.id,
                                    TrustLine.to_participant_id == p_row.id,
                                ),
                                TrustLine.status == "active",
                            )
                        )
                    ).scalars().all()
                    for frozen_tl in incident_tls:
                        frozen_tl.status = "frozen"

                # Invalidate only incident equivalents (best-effort).
                # Freezing a participant affects routing; avoid evicting all equivalents.
                incident_eqs: set[str] = set()
                s_tls = scenario.get("trustlines")
                if isinstance(s_tls, list):
                    for tl in s_tls:
                        if not isinstance(tl, dict):
                            continue
                        frm = str(tl.get("from") or "").strip()
                        to = str(tl.get("to") or "").strip()
                        if frm != freeze_pid and to != freeze_pid:
                            continue
                        eq = str(tl.get("equivalent") or "").strip().upper()
                        if eq:
                            incident_eqs.add(eq)

                if incident_eqs:
                    affected_equivalents.update(incident_eqs)

                frozen_participant_pids.append(freeze_pid)
                applied += 1
                return True
            except Exception as exc:
                self._logger.warning(
                    "simulator.real.inject.freeze_participant.error: %s",
                    exc,
                    exc_info=True,
                )
                skipped += 1
                return False

        for eff in effects[:max_edges]:
            if not isinstance(eff, dict):
                skipped += 1
                continue

            op = str(eff.get("op") or "").strip()

            # Unknown inject op — skip silently.
            if op == "inject_debt":
                await op_inject_debt(eff)
                continue
            if op == "add_participant":
                await op_add_participant(eff)
                continue
            if op == "create_trustline":
                await op_create_trustline(eff)
                continue
            if op == "freeze_participant":
                await op_freeze_participant(eff)
                continue

        # ---- commit inject effects --------------------------------
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
                        "event_index": int(event_index),
                        "time": event_time_ms,
                        "description": "inject failed (db error)",
                    },
                },
            )
            run._real_fired_scenario_event_indexes.add(event_index)
            return

        # ---- collect frozen edges before cache invalidation ------
        frozen_edges_for_sse: list[dict[str, str]] = []
        if frozen_participant_pids:
            frozen_set = set(frozen_participant_pids)
            s_tls = scenario.get("trustlines")
            if isinstance(s_tls, list):
                for tl in s_tls:
                    if not isinstance(tl, dict):
                        continue
                    frm = str(tl.get("from") or "").strip()
                    to = str(tl.get("to") or "").strip()
                    eq = str(tl.get("equivalent") or "").strip()
                    st = str(tl.get("status") or "").strip().lower()
                    if (frm in frozen_set or to in frozen_set) and st == "active":
                        frozen_edges_for_sse.append(
                            {
                                "from_pid": frm,
                                "to_pid": to,
                                "equivalent_code": eq.upper(),
                            }
                        )

        # ---- cache invalidation after successful commit -----------
        self._invalidate_caches_after_inject(
            run=run,
            scenario=scenario,
            affected_equivalents=affected_equivalents,
            new_participants=new_participants_for_cache,
            new_participants_scenario=new_participants_for_scenario,
            new_trustlines_scenario=new_trustlines_for_scenario,
            frozen_pids=frozen_participant_pids,
        )

        # ---- SSE topology.changed (per affected equivalent) -------
        self._broadcast_topology_changed(
            run_id=run_id,
            run=run,
            affected_equivalents=affected_equivalents,
            new_participants_scenario=new_participants_for_scenario,
            new_trustlines_scenario=new_trustlines_for_scenario,
            frozen_pids=frozen_participant_pids,
            frozen_edges=frozen_edges_for_sse,
        )

        # inject_debt affects DB debts and therefore routing capacity; emit an
        # edge_patch so the frontend updates used/available/viz without refresh.
        if inject_debt_equivalents:
            try:
                for eq in inject_debt_equivalents:
                    edges = inject_debt_edges_by_eq.get(eq) or set()
                    edge_patch = await self._build_edge_patch_for_equivalent(
                        session=session,
                        run=run,
                        equivalent_code=str(eq),
                        only_edges=edges,
                        include_width_keys=False,
                    )
                    self._broadcast_topology_edge_patch(
                        run_id=run_id,
                        run=run,
                        equivalent=str(eq),
                        edge_patch=edge_patch,
                        reason="inject_debt",
                    )
            except Exception:
                self._logger.warning(
                    "simulator.real.inject.inject_debt_edge_patch_failed",
                    exc_info=True,
                )

        self._artifacts.enqueue_event_artifact(
            run_id,
            {
                "type": "note",
                "ts": self._utc_now().isoformat(),
                "sim_time_ms": int(run.sim_time_ms),
                "tick_index": int(run.tick_index),
                "scenario": {
                    "event_index": int(event_index),
                    "time": event_time_ms,
                    "description": "inject applied",
                    "stats": {
                        "applied": int(applied),
                        "skipped": int(skipped),
                        "total_amount": format(total_applied, "f"),
                    },
                },
            },
        )

        run._real_fired_scenario_event_indexes.add(event_index)

    def _invalidate_caches_after_inject(
        self,
        *,
        run: RunRecord,
        scenario: dict[str, Any],
        affected_equivalents: set[str],
        new_participants: list[tuple[uuid.UUID, str]],
        new_participants_scenario: list[dict[str, Any]],
        new_trustlines_scenario: list[dict[str, Any]],
        frozen_pids: list[str],
    ) -> None:
        """Invalidate in-memory caches after a successful inject commit.

        Uses Variant A (mutate shared dicts in-place) so that the running tick
        picks up the topology changes immediately.

        Best-effort: failures here are logged but do not crash the tick.
        """
        try:
            # 1. PaymentRouter graph cache — evict affected equivalents.
            for eq in affected_equivalents:
                PaymentRouter._graph_cache.pop(eq, None)

            # 2. run._real_viz_by_eq — evict so VizPatchHelper is recreated.
            for eq in affected_equivalents:
                run._real_viz_by_eq.pop(eq, None)

            # 3. run._real_participants — append new participants.
            if new_participants and run._real_participants is not None:
                for p_tuple in new_participants:
                    run._real_participants.append(p_tuple)

            # 4. scenario["participants"] — append new participant dicts.
            if new_participants_scenario:
                s_participants = scenario.get("participants")
                if isinstance(s_participants, list):
                    for p_dict in new_participants_scenario:
                        s_participants.append(p_dict)

            # 5. scenario["trustlines"] — append new trustline dicts.
            if new_trustlines_scenario:
                s_trustlines = scenario.get("trustlines")
                if isinstance(s_trustlines, list):
                    for tl_dict in new_trustlines_scenario:
                        s_trustlines.append(tl_dict)

            # 6. run._edges_by_equivalent — add edges for new trustlines.
            if new_trustlines_scenario and run._edges_by_equivalent is not None:
                for tl_dict in new_trustlines_scenario:
                    eq = str(tl_dict.get("equivalent") or "").strip()
                    src = str(tl_dict.get("from") or "").strip()
                    dst = str(tl_dict.get("to") or "").strip()
                    if eq and src and dst:
                        run._edges_by_equivalent.setdefault(eq, []).append(
                            (src, dst)
                        )

            # 7. Frozen participants — update scenario dicts in-place.
            if frozen_pids:
                frozen_set = set(frozen_pids)

                # Update scenario["participants"][i]["status"].
                s_participants = scenario.get("participants")
                if isinstance(s_participants, list):
                    for p_dict in s_participants:
                        if isinstance(p_dict, dict):
                            pid = str(p_dict.get("id") or "").strip()
                            if pid in frozen_set:
                                p_dict["status"] = "suspended"

                # Update scenario["trustlines"][i]["status"] for incident edges.
                s_trustlines = scenario.get("trustlines")
                if isinstance(s_trustlines, list):
                    for tl_dict in s_trustlines:
                        if isinstance(tl_dict, dict):
                            frm = str(tl_dict.get("from") or "").strip()
                            to = str(tl_dict.get("to") or "").strip()
                            if frm in frozen_set or to in frozen_set:
                                prev = str(tl_dict.get("status") or "active").strip().lower()
                                if prev == "active":
                                    tl_dict["status"] = "frozen"

                # Remove frozen edges from run._edges_by_equivalent.
                if run._edges_by_equivalent is not None:
                    for eq, edges in run._edges_by_equivalent.items():
                        run._edges_by_equivalent[eq] = [
                            (s, d)
                            for s, d in edges
                            if s not in frozen_set and d not in frozen_set
                        ]

        except Exception:
            self._logger.warning(
                "simulator.real.inject.cache_invalidation_error",
                exc_info=True,
            )

    def _broadcast_topology_changed(
        self,
        *,
        run_id: str,
        run: RunRecord,
        affected_equivalents: set[str],
        new_participants_scenario: list[dict[str, Any]],
        new_trustlines_scenario: list[dict[str, Any]],
        frozen_pids: list[str],
        frozen_edges: list[dict[str, str]],
    ) -> None:
        """Broadcast SSE topology.changed events per affected equivalent.

        Only emits if there are actual topology changes. Best-effort: errors
        are logged but do not crash the tick.
        """
        try:
            if not affected_equivalents:
                return

            # Build node refs for added nodes.
            added_nodes = [
                TopologyChangedNodeRef(
                    pid=str(p.get("id") or ""),
                    name=str(p.get("name") or "") or None,
                    type=str(p.get("type") or "") or None,
                )
                for p in new_participants_scenario
                if str(p.get("id") or "").strip()
            ]

            # Build removed nodes list.
            frozen_nodes = [pid for pid in frozen_pids if pid.strip()]

            # Build edge refs for added edges, grouped by equivalent.
            added_edges_by_eq: dict[str, list[TopologyChangedEdgeRef]] = {}
            for tl in new_trustlines_scenario:
                eq = str(tl.get("equivalent") or "").strip().upper()
                if not eq:
                    continue
                ref = TopologyChangedEdgeRef(
                    from_pid=str(tl.get("from") or ""),
                    to_pid=str(tl.get("to") or ""),
                    equivalent_code=eq,
                    limit=str(tl.get("limit") or "") or None,
                )
                added_edges_by_eq.setdefault(eq, []).append(ref)

            # Build edge refs for frozen edges, grouped by equivalent.
            frozen_edges_by_eq: dict[str, list[TopologyChangedEdgeRef]] = {}
            for fe in frozen_edges:
                eq = str(fe.get("equivalent_code") or "").strip().upper()
                if not eq:
                    continue
                ref = TopologyChangedEdgeRef(
                    from_pid=str(fe.get("from_pid") or ""),
                    to_pid=str(fe.get("to_pid") or ""),
                    equivalent_code=eq,
                )
                frozen_edges_by_eq.setdefault(eq, []).append(ref)

            # Emit one topology.changed event per affected equivalent.
            for eq in affected_equivalents:
                eq_upper = eq.strip().upper()
                payload = TopologyChangedPayload(
                    added_nodes=added_nodes,
                    removed_nodes=[],
                    frozen_nodes=frozen_nodes,
                    added_edges=added_edges_by_eq.get(eq_upper, []),
                    removed_edges=[],
                    frozen_edges=frozen_edges_by_eq.get(eq_upper, []),
                )

                # Skip empty payloads.
                if (
                    not payload.added_nodes
                    and not payload.removed_nodes
                    and not payload.frozen_nodes
                    and not payload.added_edges
                    and not payload.removed_edges
                    and not payload.frozen_edges
                ):
                    continue

                evt = SimulatorTopologyChangedEvent(
                    event_id=self._sse.next_event_id(run),
                    ts=self._utc_now(),
                    type="topology.changed",
                    equivalent=eq_upper,
                    payload=payload,
                ).model_dump(mode="json", by_alias=True)

                self._sse.broadcast(run_id, evt)
                self._logger.info(
                    "simulator.real.inject.topology_changed eq=%s added_nodes=%d removed_nodes=%d added_edges=%d removed_edges=%d",
                    eq_upper,
                    len(payload.added_nodes),
                    len(payload.removed_nodes),
                    len(payload.added_edges),
                    len(payload.removed_edges),
                )

        except Exception:
            self._logger.warning(
                "simulator.real.inject.topology_changed_broadcast_error",
                exc_info=True,
            )

    def _broadcast_trust_drift_changed(
        self,
        *,
        run_id: str,
        run: RunRecord,
        reason: str,
        equivalents: list[str] | set[str],
        edge_patches_by_eq: dict[str, list[dict]] | None = None,
    ) -> None:
        """Broadcast SSE topology.changed for trust-drift limit changes.

        When *edge_patches_by_eq* is provided, each event carries a non-empty
        ``payload.edge_patch`` so the frontend can apply incremental updates
        **without** a full snapshot refresh.  If no edge_patch is available for
        a given equivalent the event is **skipped** — sending an empty payload
        would trigger ``refreshSnapshot()`` on every tick and cause visible
        jitter / "sticking" in the UI.

        ``reason`` is added as an extra field on the event (the schema uses
        ``extra="allow"``); possible values: ``trust_drift_growth``,
        ``trust_drift_decay``.
        """
        try:
            for eq in equivalents:
                eq_upper = str(eq).strip().upper()
                if not eq_upper:
                    continue

                edge_patch = (edge_patches_by_eq or {}).get(eq_upper) or []
                if not edge_patch:
                    # Skip: empty topology.changed would trigger full
                    # refreshSnapshot() on the frontend and cause jitter.
                    self._logger.debug(
                        "simulator.real.trust_drift.topology_changed_skipped_empty eq=%s reason=%s",
                        eq_upper,
                        reason,
                    )
                    continue

                payload = TopologyChangedPayload(edge_patch=edge_patch)

                evt = SimulatorTopologyChangedEvent(
                    event_id=self._sse.next_event_id(run),
                    ts=self._utc_now(),
                    type="topology.changed",
                    equivalent=eq_upper,
                    payload=payload,
                    reason=reason,
                ).model_dump(mode="json", by_alias=True)

                self._sse.broadcast(run_id, evt)
                self._logger.info(
                    "simulator.real.trust_drift.topology_changed eq=%s reason=%s edges=%d",
                    eq_upper,
                    reason,
                    len(edge_patch),
                )
        except Exception:
            self._logger.warning(
                "simulator.real.trust_drift.topology_changed_broadcast_error reason=%s",
                reason,
                exc_info=True,
            )

    async def _build_edge_patch_for_equivalent(
        self,
        *,
        session,
        run: RunRecord,
        equivalent_code: str,
        only_edges: set[tuple[str, str]] | None = None,
        include_width_keys: bool = True,
    ) -> list[dict[str, Any]]:
        """Compute backend-authoritative edge patches from DB state.

        - When *only_edges* is provided, returns patches only for those (source_pid, target_pid).
        - When *include_width_keys* is False, skips width-key recomputation (useful when limits don't change).
        """

        eq_upper = str(equivalent_code or "").strip().upper()
        if not eq_upper:
            return []

        pid_pairs: set[tuple[str, str]] | None = None
        if only_edges:
            pid_pairs = {(str(s).strip(), str(d).strip()) for s, d in only_edges if str(s).strip() and str(d).strip()}
            if not pid_pairs:
                return []

        uuid_to_pid: dict[uuid.UUID, str] = {uid: pid for uid, pid in (run._real_participants or []) if pid}

        eq_row = (
            await session.execute(
                select(Equivalent.id, Equivalent.precision).where(Equivalent.code == eq_upper)
            )
        ).one_or_none()
        if not eq_row:
            return []
        eq_id = eq_row[0]
        precision = int(eq_row[1] or 2)

        scale10 = Decimal(10) ** precision
        money_quant = Decimal(1) / scale10

        def _to_money_str(v: Decimal) -> str:
            return format(v.quantize(money_quant, rounding=ROUND_DOWN), "f")

        # Load trustlines for this equivalent.
        tl_rows = (
            await session.execute(
                select(
                    TrustLine.from_participant_id,
                    TrustLine.to_participant_id,
                    TrustLine.limit,
                    TrustLine.status,
                ).where(TrustLine.equivalent_id == eq_id)
            )
        ).all()

        # Load aggregated debts for this equivalent.
        debt_rows = (
            await session.execute(
                select(
                    Debt.creditor_id,
                    Debt.debtor_id,
                    func.coalesce(func.sum(Debt.amount), 0).label("amount"),
                )
                .where(Debt.equivalent_id == eq_id)
                .group_by(Debt.creditor_id, Debt.debtor_id)
            )
        ).all()
        debt_by_pair: dict[tuple[uuid.UUID, uuid.UUID], Decimal] = {
            (r.creditor_id, r.debtor_id): (r.amount or Decimal("0")) for r in debt_rows
        }

        # Precompute quantiles for width keys (limits only).
        q33: float | None = None
        q66: float | None = None
        if include_width_keys:
            limits: list[float] = []
            for r in tl_rows:
                try:
                    limits.append(float(r.limit or 0))
                except Exception:
                    continue
            limits = sorted([x for x in limits if x == x])
            q33 = viz_rules.quantile(limits, 0.33) if limits else None
            q66 = viz_rules.quantile(limits, 0.66) if limits else None

        patches: list[dict[str, Any]] = []
        for r in tl_rows:
            src_pid = uuid_to_pid.get(r.from_participant_id)
            dst_pid = uuid_to_pid.get(r.to_participant_id)
            if not src_pid or not dst_pid:
                continue
            if pid_pairs is not None and (src_pid, dst_pid) not in pid_pairs:
                continue

            try:
                limit_amt = Decimal(str(r.limit or 0))
            except Exception:
                limit_amt = Decimal("0")

            used_amt = debt_by_pair.get((r.from_participant_id, r.to_participant_id), Decimal("0"))
            try:
                used_amt = Decimal(str(used_amt or 0))
            except Exception:
                used_amt = Decimal("0")

            avail_amt = limit_amt - used_amt
            if avail_amt < 0:
                avail_amt = Decimal("0")

            try:
                limit_num = float(limit_amt)
            except Exception:
                limit_num = None
            try:
                used_num = float(used_amt)
            except Exception:
                used_num = None

            status_key: str | None
            if isinstance(r.status, str):
                status_key = r.status.strip().lower() or None
            else:
                status_key = None

            p: dict[str, Any] = {
                "source": str(src_pid),
                "target": str(dst_pid),
                "trust_limit": _to_money_str(limit_amt),
                "used": _to_money_str(used_amt),
                "available": _to_money_str(avail_amt),
                "viz_alpha_key": viz_rules.link_alpha_key(status_key, used=used_num, limit=limit_num),
            }
            if include_width_keys:
                p["viz_width_key"] = viz_rules.link_width_key(limit_num, q33=q33, q66=q66)

            patches.append(p)

        return patches

    def _broadcast_topology_edge_patch(
        self,
        *,
        run_id: str,
        run: RunRecord,
        equivalent: str,
        edge_patch: list[dict[str, Any]],
        reason: str,
    ) -> None:
        """Emit topology.changed with an edge_patch payload (no full refresh needed)."""

        try:
            eq_upper = str(equivalent or "").strip().upper()
            if not eq_upper or not edge_patch:
                return

            payload = TopologyChangedPayload(edge_patch=edge_patch)
            evt = SimulatorTopologyChangedEvent(
                event_id=self._sse.next_event_id(run),
                ts=self._utc_now(),
                type="topology.changed",
                equivalent=eq_upper,
                payload=payload,
                reason=reason,
            ).model_dump(mode="json", by_alias=True)

            self._sse.broadcast(run_id, evt)
        except Exception:
            self._logger.warning(
                "simulator.real.topology_edge_patch_broadcast_error eq=%s reason=%s",
                str(equivalent),
                str(reason),
                exc_info=True,
            )

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

    # -----------------------------------------------------------------
    # Trust Drift: init / growth / decay
    # -----------------------------------------------------------------

    def _init_trust_drift(self, run: RunRecord, scenario: dict[str, Any]) -> None:
        """Initialize trust drift config and edge clearing history from scenario.

        Called once per run, when ``_trust_drift_config`` is still ``None``.
        """
        run._trust_drift_config = TrustDriftConfig.from_scenario(scenario)

        if run._edge_clearing_history:
            return  # already populated (e.g. by inject adding edges)

        trustlines = scenario.get("trustlines") or []
        for tl in trustlines:
            eq = str(tl.get("equivalent") or "").strip().upper()
            creditor_pid = str(tl.get("from") or "").strip()
            debtor_pid = str(tl.get("to") or "").strip()
            if not eq or not creditor_pid or not debtor_pid:
                continue

            try:
                limit = Decimal(str(tl.get("limit", 0))).quantize(
                    Decimal("0.01"), rounding=ROUND_DOWN
                )
            except Exception:
                continue
            if limit <= 0:
                continue

            key = f"{creditor_pid}:{debtor_pid}:{eq}"
            run._edge_clearing_history[key] = EdgeClearingHistory(
                original_limit=limit
            )

        if run._trust_drift_config.enabled:
            self._logger.info(
                "simulator.real.trust_drift.init run_id=%s edges=%d "
                "growth_rate=%s decay_rate=%s max_growth=%s "
                "min_limit_ratio=%s overload_threshold=%s",
                run.run_id,
                len(run._edge_clearing_history),
                run._trust_drift_config.growth_rate,
                run._trust_drift_config.decay_rate,
                run._trust_drift_config.max_growth,
                run._trust_drift_config.min_limit_ratio,
                run._trust_drift_config.overload_threshold,
            )

    async def _apply_trust_growth(
        self,
        run: RunRecord,
        clearing_session,
        touched_edges: set[tuple[str, str]],
        eq_code: str,
        tick_index: int,
        cleared_amount_per_edge: dict[tuple[str, str], float],
    ) -> int:
        """Apply trust growth to edges that participated in clearing.

        Uses the *clearing_session* (isolated per-equivalent session used by
        ``tick_real_mode_clearing``).  Commits internally on success.

        Returns count of updated edges.
        """
        cfg = run._trust_drift_config
        if not cfg or not cfg.enabled:
            return 0

        if not touched_edges:
            return 0

        pid_to_uuid: dict[str, uuid.UUID] = {
            pid: uid for uid, pid in (run._real_participants or [])
        }

        eq_upper = eq_code.strip().upper()
        try:
            eq_id = (
                await clearing_session.execute(
                    select(Equivalent.id).where(Equivalent.code == eq_upper)
                )
            ).scalar_one_or_none()
        except Exception:
            return 0
        if not eq_id:
            return 0

        updated = 0
        for creditor_pid, debtor_pid in touched_edges:
            key = f"{creditor_pid}:{debtor_pid}:{eq_upper}"
            hist = run._edge_clearing_history.get(key)
            if not hist:
                continue

            try:
                original_limit = Decimal(str(hist.original_limit)).quantize(
                    Decimal("0.01"), rounding=ROUND_DOWN
                )
            except Exception:
                continue

            # Update history
            hist.clearing_count += 1
            hist.last_clearing_tick = tick_index
            try:
                hist.cleared_volume += Decimal(
                    str(cleared_amount_per_edge.get((creditor_pid, debtor_pid), 0.0))
                ).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
            except Exception:
                pass

            creditor_uuid = pid_to_uuid.get(creditor_pid)
            debtor_uuid = pid_to_uuid.get(debtor_pid)
            if not creditor_uuid or not debtor_uuid:
                continue

            # Get current limit from DB
            tl_limit_row = (
                await clearing_session.execute(
                    select(TrustLine.limit).where(
                        TrustLine.from_participant_id == creditor_uuid,
                        TrustLine.to_participant_id == debtor_uuid,
                        TrustLine.equivalent_id == eq_id,
                    )
                )
            ).scalar_one_or_none()
            if tl_limit_row is None:
                continue

            try:
                current_limit = Decimal(str(tl_limit_row)).quantize(
                    Decimal("0.01"), rounding=ROUND_DOWN
                )
            except Exception:
                continue

            rate_mult = (Decimal("1") + Decimal(str(cfg.growth_rate))).quantize(
                Decimal("0.0000001")
            )
            max_growth = Decimal(str(cfg.max_growth))
            new_limit = min(
                (current_limit * rate_mult),
                (original_limit * max_growth),
            ).quantize(Decimal("0.01"), rounding=ROUND_DOWN)

            if new_limit != current_limit:
                await clearing_session.execute(
                    update(TrustLine)
                    .where(
                        TrustLine.from_participant_id == creditor_uuid,
                        TrustLine.to_participant_id == debtor_uuid,
                        TrustLine.equivalent_id == eq_id,
                    )
                    .values(limit=new_limit)
                )
                # Update scenario trustlines in-memory
                scenario = getattr(run, "_scenario_raw", None) or self._get_scenario_raw(run.scenario_id)
                s_tls = scenario.get("trustlines") or []
                for s_tl in s_tls:
                    if (
                        str(s_tl.get("from") or "").strip() == creditor_pid
                        and str(s_tl.get("to") or "").strip() == debtor_pid
                        and str(s_tl.get("equivalent") or "").strip().upper()
                        == eq_upper
                    ):
                        s_tl["limit"] = float(new_limit)
                        break
                self._logger.info(
                    "simulator.real.trust_drift.growth key=%s old=%s new=%s",
                    key,
                    current_limit,
                    new_limit,
                )
                updated += 1

        if updated:
            # Trust drift changes limits and must invalidate routing cache.
            PaymentRouter._graph_cache.pop(eq_upper, None)
            await clearing_session.commit()
        return updated

    async def _apply_trust_decay(
        self,
        run: RunRecord,
        session,
        tick_index: int,
        debt_snapshot: dict[tuple[str, str, str], Decimal],
        scenario: dict[str, Any],
    ) -> int:
        """Apply trust decay to overloaded edges that didn't get cleared.

        Uses the main tick session.  Does NOT commit — the caller's final
        ``session.commit()`` flushes decay writes together with metrics.

        Returns count of decayed edges.
        """
        cfg = run._trust_drift_config
        if not cfg or not cfg.enabled:
            return 0

        pid_to_uuid: dict[str, uuid.UUID] = {
            pid: uid for uid, pid in (run._real_participants or [])
        }

        eq_id_cache: dict[str, uuid.UUID] = {}
        updated = 0
        touched_eq_codes: set[str] = set()
        trustlines = scenario.get("trustlines") or []

        for tl in trustlines:
            eq_code = str(tl.get("equivalent") or "").strip().upper()
            creditor_pid = str(tl.get("from") or "").strip()
            debtor_pid = str(tl.get("to") or "").strip()
            status = str(tl.get("status") or "active").strip().lower()

            if not eq_code or not creditor_pid or not debtor_pid:
                continue
            if status != "active":
                continue

            key = f"{creditor_pid}:{debtor_pid}:{eq_code}"
            hist = run._edge_clearing_history.get(key)
            if not hist:
                continue

            # Skip if just cleared this tick
            if hist.last_clearing_tick == tick_index:
                continue

            # Get current limit from scenario (in-memory, kept in sync by growth)
            try:
                current_limit = Decimal(str(tl.get("limit", 0))).quantize(
                    Decimal("0.01"), rounding=ROUND_DOWN
                )
            except Exception:
                continue
            if current_limit <= 0:
                continue

            # Debt for this edge: debt_snapshot key is (debtor_pid, creditor_pid, eq_code)
            debt_amount = debt_snapshot.get(
                (debtor_pid, creditor_pid, eq_code), Decimal("0")
            )

            ratio = (debt_amount / current_limit) if current_limit > 0 else Decimal("0")
            if ratio < Decimal(str(cfg.overload_threshold)):
                continue

            # Calculate new limit
            decay_mult = Decimal(str(1 - cfg.decay_rate))
            min_ratio = Decimal(str(cfg.min_limit_ratio))

            try:
                original_limit = Decimal(str(hist.original_limit)).quantize(
                    Decimal("0.01"), rounding=ROUND_DOWN
                )
            except Exception:
                continue
            new_limit = max(
                (current_limit * decay_mult),
                (original_limit * min_ratio),
            ).quantize(Decimal("0.01"), rounding=ROUND_DOWN)

            if new_limit == current_limit:
                continue

            # Resolve UUIDs
            creditor_uuid = pid_to_uuid.get(creditor_pid)
            debtor_uuid = pid_to_uuid.get(debtor_pid)
            if not creditor_uuid or not debtor_uuid:
                continue

            if eq_code not in eq_id_cache:
                eq_row = (
                    await session.execute(
                        select(Equivalent.id).where(Equivalent.code == eq_code)
                    )
                ).scalar_one_or_none()
                if eq_row is None:
                    continue
                eq_id_cache[eq_code] = eq_row

            eq_id = eq_id_cache.get(eq_code)
            if not eq_id:
                continue

            await session.execute(
                update(TrustLine)
                .where(
                    TrustLine.from_participant_id == creditor_uuid,
                    TrustLine.to_participant_id == debtor_uuid,
                    TrustLine.equivalent_id == eq_id,
                )
                .values(limit=new_limit)
            )

            # Update scenario trustlines in-memory
            tl["limit"] = float(new_limit)

            touched_eq_codes.add(eq_code)

            self._logger.info(
                "simulator.real.trust_drift.decay key=%s old=%s new=%s ratio=%.2f",
                key,
                current_limit,
                new_limit,
                float(ratio),
            )
            updated += 1

        if touched_eq_codes:
            for eq in touched_eq_codes:
                PaymentRouter._graph_cache.pop(str(eq).strip().upper(), None)

        return updated

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
        scenario = getattr(run, "_scenario_raw", None) or self._get_scenario_raw(run.scenario_id)

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
                try:
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

                    # Initialize trust drift (once per run)
                    if run._trust_drift_config is None:
                        self._get_trust_drift_engine().init_trust_drift(run, scenario)

                    # Apply due scenario timeline events (note/stress/inject). Best-effort.
                    # IMPORTANT: inject modifies DB state and must happen before payments.
                    await self._apply_due_scenario_events(
                        session, run_id=run_id, run=run, scenario=scenario
                    )

                    # ── Phase 1.4: capacity-aware payment amounts ─────────
                    # Load current debt snapshot *after* events (which may mutate DB)
                    # so that capacity reflects the latest state.  Best-effort:
                    # if the query fails we fall back to static limits.
                    debt_snapshot: dict[tuple[str, str, str], Decimal] = {}
                    try:
                        debt_snapshot = await self._load_debt_snapshot_by_pid(
                            session, participants, equivalents
                        )
                    except Exception:
                        self._logger.debug(
                            "capacity_aware: debt snapshot load failed, "
                            "falling back to static limits"
                        )

                    planned = self._plan_real_payments(
                        run, scenario, debt_snapshot=debt_snapshot
                    )
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
                    per_eq_metric_values: dict[str, dict[str, float]] = {
                        str(eq): {} for eq in equivalents
                    }

                    payments_res = await self._get_real_payments_executor().execute_planned_payments(
                        session=session,
                        run_id=run_id,
                        run=run,
                        planned=planned,
                        equivalents=equivalents,
                        sender_id_by_pid=sender_id_by_pid,
                        max_in_flight=int(run._real_max_in_flight),
                        max_timeouts_per_tick=max_timeouts_per_tick,
                        fail_run=lambda _run_id, code, message: self.fail_run(
                            _run_id, code=code, message=message
                        ),
                    )

                    committed = int(payments_res.committed)
                    rejected = int(payments_res.rejected)
                    errors = int(payments_res.errors)
                    timeouts = int(payments_res.timeouts)
                    stall_ticks = int(payments_res.stall_ticks)

                    per_eq = dict(payments_res.per_eq)
                    per_eq_route = dict(payments_res.per_eq_route)
                    per_eq_edge_stats = dict(payments_res.per_eq_edge_stats)

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
                        clearing_hard_timeout_sec = max(
                            0.1, float(clearing_hard_timeout_sec)
                        )

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

                    # ── Trust Drift: decay overloaded edges ─────────────
                    try:
                        decay_res = await self._get_trust_drift_engine().apply_trust_decay(
                            run=run,
                            session=session,
                            tick_index=int(run.tick_index or 0),
                            debt_snapshot=debt_snapshot,
                            scenario=scenario,
                        )
                        if decay_res.updated_count:
                            await session.commit()
                            # Notify frontend about changed limits via edge_patch (no full refresh).
                            try:
                                for eq in sorted(decay_res.touched_equivalents or set()):
                                    eq_upper = str(eq or "").strip().upper()
                                    if not eq_upper:
                                        continue
                                    only_edges = (decay_res.touched_edges_by_eq or {}).get(eq_upper)
                                    edge_patch = await self._build_edge_patch_for_equivalent(
                                        session=session,
                                        run=run,
                                        equivalent_code=eq_upper,
                                        only_edges=only_edges,
                                        include_width_keys=True,
                                    )
                                    self._broadcast_topology_edge_patch(
                                        run_id=run_id,
                                        run=run,
                                        equivalent=eq_upper,
                                        edge_patch=edge_patch,
                                        reason="trust_drift_decay",
                                    )
                            except Exception:
                                self._logger.warning(
                                    "simulator.real.trust_drift.decay_edge_patch_broadcast_error",
                                    exc_info=True,
                                )
                    except Exception:
                        self._logger.warning(
                            "simulator.real.trust_drift.decay_failed run_id=%s tick=%s",
                            str(run.run_id),
                            int(run.tick_index or 0),
                            exc_info=True,
                        )

                    # Real total debt snapshot (sum of all debts for the equivalent).
                    # Throttled: aggregate SUM can become hot on large Debt tables.
                    total_debt_by_eq: dict[str, float] = {
                        str(eq): 0.0 for eq in equivalents
                    }

                    metrics_every_n = int(self._real_db_metrics_every_n_ticks)
                    should_refresh_total_debt = metrics_every_n <= 1 or (
                        int(run.tick_index) % int(metrics_every_n) == 0
                    )

                    if not should_refresh_total_debt:
                        with self._lock:
                            cached = dict(run._real_total_debt_by_eq or {})
                        for eq in equivalents:
                            total_debt_by_eq[str(eq)] = float(
                                cached.get(str(eq), 0.0) or 0.0
                            )

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
                                        select(
                                            func.coalesce(func.sum(Debt.amount), 0)
                                        ).where(Debt.equivalent_id == eq_id)
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
                                total_debt_by_eq[str(eq)] = float(
                                    cached.get(str(eq), 0.0) or 0.0
                                )

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

                    # --- Network topology metrics (Phase 3) ---
                    # active_participants: count scenario participants with status='active'.
                    # Computed once from in-memory scenario (lightweight, no DB).
                    _scenario_parts = scenario.get("participants") or []
                    _active_participants_count = float(
                        sum(
                            1
                            for _p in _scenario_parts
                            if isinstance(_p, dict)
                            and str(_p.get("status") or "active")
                            .strip()
                            .lower()
                            == "active"
                        )
                    )
                    # active_trustlines per equivalent: count from run._edges_by_equivalent cache.
                    # After inject ops, this cache already reflects frozen/removed edges.
                    with self._lock:
                        _edges_snapshot = dict(run._edges_by_equivalent or {})
                    for eq in equivalents:
                        per_eq_metric_values[str(eq)][
                            "active_participants"
                        ] = _active_participants_count
                        per_eq_metric_values[str(eq)]["active_trustlines"] = float(
                            len(_edges_snapshot.get(str(eq), []))
                        )

                    await self._get_real_tick_persistence().persist_tick_tail(
                        session=session,
                        run=run,
                        equivalents=equivalents,
                        tick_t0=tick_t0,
                        planned_len=len(planned),
                        committed=committed,
                        rejected=rejected,
                        errors=errors,
                        timeouts=timeouts,
                        per_eq=per_eq,
                        per_eq_metric_values=per_eq_metric_values,
                        per_eq_edge_stats=per_eq_edge_stats,
                    )
                except Exception:
                    # CRITICAL: always rollback on tick failure.
                    # Otherwise the underlying pooled connection can be returned
                    # in an invalid transaction state, and subsequent ticks may
                    # fail with "Can't reconnect until invalid transaction is rolled back".
                    try:
                        await session.rollback()
                    except Exception:
                        pass
                    raise
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
        return await self._get_real_clearing_engine().tick_real_mode_clearing(
            session,
            run_id=run_id,
            run=run,
            equivalents=equivalents,
            apply_trust_growth=self._get_trust_drift_engine().apply_trust_growth,
            build_edge_patch_for_equivalent=self._build_edge_patch_for_equivalent,
            broadcast_topology_edge_patch=self._broadcast_topology_edge_patch,
        )

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
            # NOTE: capacity-aware filtering is applied in _plan_real_payments()
            # via the debt_snapshot parameter (Phase 1.4).
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
        self,
        run: RunRecord,
        scenario: dict[str, Any],
        *,
        debt_snapshot: dict[tuple[str, str, str], Decimal] | None = None,
    ) -> list[_RealPaymentAction]:
        return self._get_real_payment_planner().plan_payments(
            run,
            scenario,
            debt_snapshot=debt_snapshot,
        )

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

    async def _load_debt_snapshot_by_pid(
        self,
        session,
        participants: list[tuple[uuid.UUID, str]],
        equivalents: list[str],
    ) -> dict[tuple[str, str, str], Decimal]:
        return await self._get_real_debt_snapshot_loader().load_debt_snapshot_by_pid(
            session=session,
            participants=participants,
            equivalents=equivalents,
        )

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
