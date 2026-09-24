from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from decimal import Decimal
from typing import Any, Callable

from sqlalchemy import or_, select
from sqlalchemy.exc import DBAPIError, SQLAlchemyError

from app.core.ledger.book import Book, operation_for
from app.core.payments.engine import PaymentEngine
from app.utils.exceptions import ConflictException
from app.core.simulator.adaptive_clearing_policy import AdaptiveClearingPolicyConfig
from app.core.simulator.artifacts import ArtifactsManager
from app.core.simulator.edge_patch_builder import EdgePatchBuilder
from app.core.simulator.inject_executor import (
    InjectExecutor,
    InjectOwnerLockSetTooNarrow,
    StagedInjectEvent,
    inject_event_equivalent_codes,
    inject_event_freeze_participant_pids,
    invalidate_caches_after_inject as _inject_invalidate_caches_after_inject,
)
from app.core.simulator.models import RunRecord, TrustDriftResult
from app.core.simulator.real_clearing_engine import RealClearingEngine
from app.core.simulator.real_debt_snapshot_loader import RealDebtSnapshotLoader
from app.core.simulator.real_payment_action import _RealPaymentAction
from app.core.simulator.real_payment_planner import RealPaymentPlanner
from app.core.simulator.real_payments_executor import RealPaymentsExecutor
from app.core.simulator.real_scenario_seeder import RealScenarioSeeder
from app.core.simulator.real_tick_clearing_coordinator import RealTickClearingCoordinator
from app.core.simulator.real_tick_metrics import RealTickMetrics
from app.core.simulator.real_tick_orchestrator import RealTickOrchestrator
from app.core.simulator.real_tick_payments_coordinator import RealTickPaymentsCoordinator
from app.core.simulator.real_tick_persistence import RealTickPersistence
from app.core.simulator.real_tick_trust_drift_coordinator import (
    RealTickTrustDriftCoordinator,
)
from app.core.simulator.scenario_equivalent import effective_equivalent
from app.core.simulator.runtime_utils import (
    safe_float_env as _safe_float_env,
    safe_int_env as _safe_int_env,
    safe_optional_decimal_env as _safe_optional_decimal_env,
    safe_str_env as _safe_str_env,
)
from app.core.simulator.sse_broadcast import SseBroadcast, SseEventEmitter
from app.core.simulator.trust_drift_engine import TrustDriftEngine
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine

# 40001 serialization_failure, 40P01 deadlock_detected: PostgreSQL has rolled the transaction back
# and the whole unit of work may run again.
#
# 55P03 lock_not_available: the owner lock was not obtained within its deadline. Nothing of the unit
# of work has been written - the locks open it - so this is a known rollback too. Treating it as an
# ordinary database error would record "inject failed" and mark the event fired, i.e. DROP the
# inject whenever another writer held the equivalent a little too long. The tick orchestrator's own
# lock timeout fails the tick without firing anything; the inject owner now does the same once its
# single retry is spent. Nothing else is retried.
_INJECT_TRANSIENT_SQLSTATES = frozenset({"40001", "40P01", "55P03"})


def _is_transient_inject_db_error(exc: BaseException) -> bool:
    if not isinstance(exc, DBAPIError):
        return False
    orig = getattr(exc, "orig", None)
    # asyncpg's adapted error carries `sqlstate`, psycopg's `pgcode`.
    sqlstate = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    return sqlstate in _INJECT_TRANSIENT_SQLSTATES


class RealRunnerImpl:
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

        # Programme 015 / P1: the bounded replay of the tick's money phase.
        # `..._MONEY_REPLAY_ATTEMPTS` counts ATTEMPTS, not retries, so 1 disables the replay.
        # `..._MAX_CONSEC_MONEY_NO_PROGRESS` is the explicit no-progress criterion that may stop a
        # run under permanent contention - the replacement for stopping a run on a SQLSTATE.
        self._real_money_replay_attempts_limit = _safe_int_env(
            "SIMULATOR_REAL_MONEY_REPLAY_ATTEMPTS", 3
        )
        self._real_max_consec_money_no_progress_limit = _safe_int_env(
            "SIMULATOR_REAL_MAX_CONSEC_MONEY_NO_PROGRESS", 10
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

        # Adaptive clearing policy knobs (§5 of docs/ru/simulator/backend/archive/adaptive-clearing-policy-spec--archived-2026-02-13.md).
        self._clearing_policy = _safe_str_env("SIMULATOR_CLEARING_POLICY", "static")
        if self._clearing_policy not in ("static", "adaptive"):
            self._clearing_policy = "static"
        self._adaptive_clearing_config: AdaptiveClearingPolicyConfig | None = None
        if self._clearing_policy == "adaptive":
            warmup_fallback_cadence = _safe_int_env(
                "SIMULATOR_CLEARING_ADAPTIVE_WARMUP_FALLBACK_CADENCE",
                int(self._clearing_every_n_ticks),
            )
            self._adaptive_clearing_config = AdaptiveClearingPolicyConfig(
                window_ticks=_safe_int_env("SIMULATOR_CLEARING_ADAPTIVE_WINDOW_TICKS", 30),
                no_capacity_high=_safe_float_env("SIMULATOR_CLEARING_ADAPTIVE_NO_CAPACITY_HIGH", 0.60),
                no_capacity_low=_safe_float_env("SIMULATOR_CLEARING_ADAPTIVE_NO_CAPACITY_LOW", 0.30),
                min_interval_ticks=_safe_int_env("SIMULATOR_CLEARING_ADAPTIVE_MIN_INTERVAL_TICKS", 5),
                backoff_max_interval_ticks=_safe_int_env("SIMULATOR_CLEARING_ADAPTIVE_BACKOFF_MAX_INTERVAL_TICKS", 60),
                time_budget_ms_min=_safe_int_env("SIMULATOR_CLEARING_ADAPTIVE_TIME_BUDGET_MS_MIN", 50),
                time_budget_ms_max=_safe_int_env("SIMULATOR_CLEARING_ADAPTIVE_TIME_BUDGET_MS_MAX", 250),
                max_depth_min=_safe_int_env("SIMULATOR_CLEARING_ADAPTIVE_MAX_DEPTH_MIN", 3),
                max_depth_max=_safe_int_env("SIMULATOR_CLEARING_ADAPTIVE_MAX_DEPTH_MAX", 6),
                inflight_threshold=_safe_int_env("SIMULATOR_CLEARING_ADAPTIVE_INFLIGHT_THRESHOLD", 0),
                queue_depth_threshold=_safe_int_env("SIMULATOR_CLEARING_ADAPTIVE_QUEUE_DEPTH_THRESHOLD", 0),
                global_max_depth_ceiling=int(self._clearing_max_depth_limit),
                global_time_budget_ms_ceiling=int(self._real_clearing_time_budget_ms),
                warmup_fallback_cadence=int(warmup_fallback_cadence),
            )

        # Sub-components: eager init (RealRunner is created once on startup).
        self._edge_patch_builder: EdgePatchBuilder = EdgePatchBuilder(logger=self._logger)
        self._real_debt_snapshot_loader: RealDebtSnapshotLoader = RealDebtSnapshotLoader()
        self._sse_emitter: SseEventEmitter = SseEventEmitter(
            sse=self._sse,
            utc_now=self._utc_now,
            logger=self._logger,
        )

        self._real_payment_planner: RealPaymentPlanner = RealPaymentPlanner(
            actions_per_tick_max=int(self._actions_per_tick_max),
            amount_cap_limit=self._real_amount_cap_limit,
            logger=self._logger,
            action_factory=lambda seq, eq, sender_pid, receiver_pid, amount: _RealPaymentAction(
                seq=int(seq),
                equivalent=str(eq),
                sender_pid=str(sender_pid),
                receiver_pid=str(receiver_pid),
                amount=str(amount),
            ),
        )

        self._real_payments_executor: RealPaymentsExecutor = RealPaymentsExecutor(
            lock=self._lock,
            sse=self._sse,
            utc_now=self._utc_now,
            logger=self._logger,
            edge_patch_builder=self._edge_patch_builder,
            should_warn_this_tick=self._should_warn_this_tick,
            sim_idempotency_key=self._sim_idempotency_key,
        )

        self._trust_drift_engine: TrustDriftEngine = TrustDriftEngine(
            sse=self._sse,
            utc_now=self._utc_now,
            logger=self._logger,
            get_scenario_raw=self._get_scenario_raw,
        )

        self._inject_executor: InjectExecutor = InjectExecutor(
            sse=self._sse,
            artifacts=self._artifacts,
            utc_now=self._utc_now,
            logger=self._logger,
        )

        self._real_clearing_engine: RealClearingEngine = RealClearingEngine(
            lock=self._lock,
            sse=self._sse,
            utc_now=self._utc_now,
            logger=self._logger,
            edge_patch_builder=self._edge_patch_builder,
            clearing_max_depth_limit=int(self._clearing_max_depth_limit),
            clearing_max_fx_edges_limit=int(self._clearing_max_fx_edges_limit),
            real_clearing_time_budget_ms=int(self._real_clearing_time_budget_ms),
            should_warn_this_tick=lambda run, key: self._should_warn_this_tick(run, key=key),
        )

        self._real_tick_persistence: RealTickPersistence = RealTickPersistence(
            lock=self._lock,
            artifacts=self._artifacts,
            utc_now=self._utc_now,
            db_enabled=self._db_enabled,
            logger=self._logger,
            real_db_metrics_every_n_ticks=int(self._real_db_metrics_every_n_ticks),
            real_db_bottlenecks_every_n_ticks=int(self._real_db_bottlenecks_every_n_ticks),
            real_last_tick_write_every_ms=int(self._real_last_tick_write_every_ms),
            real_artifacts_sync_every_ms=int(self._real_artifacts_sync_every_ms),
        )

        self._real_tick_metrics: RealTickMetrics = RealTickMetrics(
            lock=self._lock,
            logger=self._logger,
            real_db_metrics_every_n_ticks=int(self._real_db_metrics_every_n_ticks),
        )

        self._real_tick_clearing_coordinator: RealTickClearingCoordinator = (
            RealTickClearingCoordinator(
                lock=self._lock,
                logger=self._logger,
                clearing_every_n_ticks=int(self._clearing_every_n_ticks),
                real_clearing_time_budget_ms=int(self._real_clearing_time_budget_ms),
                clearing_policy=self._clearing_policy,  # type: ignore[arg-type]
                adaptive_config=self._adaptive_clearing_config,
            )
        )
        self._real_tick_trust_drift_coordinator: RealTickTrustDriftCoordinator = (
            RealTickTrustDriftCoordinator(logger=self._logger)
        )
        self._real_tick_payments_coordinator: RealTickPaymentsCoordinator = (
            RealTickPaymentsCoordinator(lock=self._lock, logger=self._logger)
        )
        self._real_scenario_seeder: RealScenarioSeeder = RealScenarioSeeder()

        self._real_tick_orchestrator: RealTickOrchestrator = RealTickOrchestrator(self)

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
        return self._real_payment_planner.compute_stress_multipliers(
            events=events,
            sim_time_ms=sim_time_ms,
        )

    async def _apply_due_scenario_events(
        self, session, *, run_id: str, run: RunRecord, scenario: dict[str, Any]
    ) -> None:
        """Apply the due scenario events, owning every transaction boundary on `session`.

        Programme 015, phase B step 3. The equivalent owner lock is transactional, and the inject
        executor used to commit the transaction the tick orchestrator had locked, which released
        the lock: a second inject event of the same tick wrote `debts` unlocked. From here on the
        due-events phase is the owner of the session's transactions while it runs.

        Contract with the caller:
        - `session` must carry no unflushed ORM changes (`new`, `dirty`, `deleted`); otherwise
          `RuntimeError` is raised before anything happens. An open transaction is ended by the
          first boundary this method draws (committed, like any read it finds open).
        - Every due inject event is ONE unit of work: resolve the owner lock set (the run's
          equivalents, those the event names and those of the active trustlines of every
          participant it freezes) in a short read that is committed; acquire
          those owner locks as the opening of a fresh transaction; stage; mark the event fired;
          commit; then publish (caches, SSE, note) and end publish's read transaction.
        - Staging that reaches an equivalent outside the set rolls back and restarts once with
          the missing equivalents added. A 40001/40P01/55P03 before or at commit rolls back and
          restarts once; a second one propagates, the event stays pending and nothing after it
          runs. Any other database error before commit - including one raised by flushing the
          staged writes, which happens explicitly before the commit - is recorded as "inject
          failed (db error)" and the event is fired. A non-transient failure OF the commit
          statement leaves the outcome unknown: the event stays fired ("inject outcome unknown
          (commit error)") and is not retried - at most once until durable operation keys exist
          (phase B step 4).
        - Cancellation before the commit rolls back and leaves the event pending; cancellation
          during the commit leaves it fired. Both propagate.
        - Returns with no open transaction.
        """

        self._require_no_unflushed_changes(session)

        events = scenario.get("events")
        if isinstance(events, list) and events:
            await self._apply_due_events_in_order(
                session, run_id=run_id, run=run, scenario=scenario, events=events
            )

        await self._end_open_transaction(session)

    @staticmethod
    def _require_no_unflushed_changes(session) -> None:
        if session.new or session.dirty or session.deleted:
            raise RuntimeError(
                "the due-events phase owns its transactions and was handed a session with "
                "unflushed changes; flush and end them before calling it"
            )

    async def _end_open_transaction(self, session) -> None:
        """End whatever transaction is open with a commit; roll back if the commit fails."""

        if not session.in_transaction():
            return
        try:
            await session.commit()
        except Exception:
            self._logger.warning(
                "simulator.real.inject.end_transaction_commit_failed", exc_info=True
            )
            await session.rollback()

    async def _apply_due_events_in_order(
        self,
        session,
        *,
        run_id: str,
        run: RunRecord,
        scenario: dict[str, Any],
        events: list[Any],
    ) -> None:
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
                await self._apply_inject_unit_of_work(
                    session,
                    run_id=run_id,
                    run=run,
                    scenario=scenario,
                    event_index=idx,
                    event_time_ms=t0,
                    event=evt,
                    pid_to_participant_id=pid_to_participant_id,
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
        # No caller in the tree (T1519 lists it as dead code). Until it is deleted it goes through
        # the same owner as the due-events phase, so it cannot be a way around the owner lock
        # (programme 015, phase B step 3).
        self._require_no_unflushed_changes(session)
        await self._apply_inject_unit_of_work(
            session,
            run_id=run_id,
            run=run,
            scenario=scenario,
            event_index=event_index,
            event_time_ms=event_time_ms,
            event=event,
            pid_to_participant_id=pid_to_participant_id,
        )
        await self._end_open_transaction(session)

    async def _resolve_inject_debt_equivalent_ids(
        self,
        session,
        *,
        scenario: dict[str, Any],
        event: dict[str, Any] | None,
    ) -> set[uuid.UUID]:
        """The equivalents this event's `inject_debt` effects NAME - its journal intent.

        Narrower than the owner lock set on purpose (design v2 §2). The lock set also holds the
        run's own equivalents and those of every trustline a `freeze_participant` effect will
        touch; an intent built from it would declare "this operation is about that book" for books
        the event never says a word about, and `debt_operation_equivalents.in_intent` would stop
        meaning anything. Scope stays the lock set, which is the authoritative statement of what
        the operation is ALLOWED to touch; intent is what it SAID it would.
        """

        codes = {
            code
            for eff in ((event or {}).get("effects") or [])
            if isinstance(eff, dict)
            and str(eff.get("op") or "").strip() == "inject_debt"
            for code in (effective_equivalent(scenario=scenario, payload=eff),)
            if code
        }
        if not codes:
            return set()
        return set(
            (
                await session.execute(
                    select(Equivalent.id).where(Equivalent.code.in_(sorted(codes)))
                )
            )
            .scalars()
            .all()
        )

    async def _resolve_inject_owner_lock_ids(
        self,
        session,
        *,
        run: RunRecord,
        scenario: dict[str, Any],
        event: dict[str, Any] | None,
    ) -> set[uuid.UUID]:
        """The run's equivalents and the event's own, as ids, in one read that is then committed.

        "The event's own" includes the equivalents of the active trustlines incident to every
        participant the event freezes: the event does not name them, and staging discovers them
        one at a time, so leaving them to the bounded expansion made a multi-participant freeze
        impossible to complete (external review of step 3).

        The read is ended before the owner locks are taken, so the locks open the unit of work's
        transaction instead of joining a snapshot taken without them.
        """

        codes = {
            str(code).strip().upper()
            for code in (run._real_equivalents or [])
            if str(code).strip()
        }
        codes |= inject_event_equivalent_codes(scenario=scenario, event=event)
        lock_ids: set[uuid.UUID] = set()
        if codes:
            lock_ids = set(
                (
                    await session.execute(
                        select(Equivalent.id).where(Equivalent.code.in_(sorted(codes)))
                    )
                )
                .scalars()
                .all()
            )
        freeze_pids = inject_event_freeze_participant_pids(event=event)
        if freeze_pids:
            lock_ids |= set(
                (
                    await session.execute(
                        select(TrustLine.equivalent_id)
                        .join(
                            Participant,
                            or_(
                                TrustLine.from_participant_id == Participant.id,
                                TrustLine.to_participant_id == Participant.id,
                            ),
                        )
                        .where(
                            Participant.pid.in_(sorted(freeze_pids)),
                            TrustLine.status == "active",
                        )
                    )
                )
                .scalars()
                .all()
            )
        if session.in_transaction():
            await session.commit()
        return lock_ids

    async def _apply_inject_unit_of_work(
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
        """One inject event as one locked unit of work. See `_apply_due_scenario_events`."""

        executor = self._inject_executor
        fired = run._real_fired_scenario_event_indexes

        if not self._real_enable_inject:
            executor.enqueue_inject_note(
                run_id,
                run=run,
                event_index=event_index,
                event_time_ms=event_time_ms,
                description="inject skipped (SIMULATOR_REAL_ENABLE_INJECT=0)",
            )
            fired.add(event_index)
            return

        effects = (event or {}).get("effects")
        if not isinstance(effects, list) or not effects:
            fired.add(event_index)
            return

        lock_ids: set[uuid.UUID] | None = None
        lock_set_expansions_left = 1
        transient_retries_left = 1
        staged: StagedInjectEvent | None = None

        while True:
            try:
                if lock_ids is None:
                    lock_ids = await self._resolve_inject_owner_lock_ids(
                        session, run=run, scenario=scenario, event=event
                    )
                # The owner locks open the transaction the event is staged and committed in. A
                # fresh engine per attempt: its advisory-lock deadline is per unit of work.
                await PaymentEngine(session).acquire_staged_equivalent_owner_locks(lock_ids)
                intent_equivalent_ids = await self._resolve_inject_debt_equivalent_ids(
                    session, scenario=scenario, event=event
                )
                # T1544: no money in an equivalent the operator has deactivated (protocol §11.5.1
                # blocks OPERATIONS in it, and `inject_debt` writes the shared `debts`). The order is
                # owner lock -> `FOR SHARE` -> envelope -> debt write, so the check sits here, before
                # `Book.operation` flushes the envelope, and not inside staging.
                #
                # WHY THE INTENT SET IS ENOUGH. It is built from exactly the `inject_debt` effects,
                # through the same `effective_equivalent` the writer uses, and matched on stored
                # (upper-case) codes; an effect whose code is not stored is skipped by the writer, so
                # every debt this event can write is denominated in an equivalent named here. An
                # event with no `inject_debt` effect has an empty intent and writes no debt, and the
                # call is then a no-op.
                #
                # `FOR SHARE` for the same reason as the payment commit: this transaction's snapshot
                # was taken before it waited on the owner lock. A 40001 from it is transient and
                # retried below on a fresh snapshot; the refusal is a non-retryable
                # `ConflictException`, which the handler below rolls back and re-raises, with no
                # envelope ever opened.
                await PaymentEngine(session).refuse_inactive_equivalents(
                    intent_equivalent_ids, row_lock=True
                )
                # THE OPERATION ENVELOPE (programme 015, phase B step 4). Staging and its flush
                # are one declared operation: `stage_inject_event` is what writes the debts, and
                # the flush below is what sends them.
                #
                # PER ATTEMPT, NOT PER EVENT. This region sits inside the `while True:` retry loop
                # above, and every handler below rolls the session back before it continues - so
                # each attempt opens its own envelope and each rollback takes that attempt's
                # envelope with it. Opening once outside the loop would leave a record of an
                # attempt the database no longer holds, and the second attempt would be refused for
                # nesting inside the first.
                #
                # THE IDENTITY is the event, not the attempt: `run_id:event_index` is the same
                # string on a retry, which is exactly what makes a genuinely duplicated apply
                # collide on `UNIQUE(kind, identity)` rather than quietly write a second envelope.
                # A retry after a rollback does not collide, because the rolled-back envelope is
                # not there.
                #
                # ONE OPERATION FOR THE WHOLE EVENT (018 stage A). The book's envelope wraps staging:
                # the debt effects go through the posting in source order, interleaved with the
                # participant, trust-line and freeze effects the executor still owns, because they
                # see each other (`tests/integration/test_p018_mixed_inject_event_is_one_operation_postgres.py`).
                async with Book.operation(
                    session,
                    operation_for(
                        "INJECT",
                        f"{run_id}:{event_index}",
                        {
                            "run_id": run_id,
                            "event_index": event_index,
                            "event_time_ms": event_time_ms,
                            "effects": effects,
                        },
                        scope_equivalent_ids=set(lock_ids or ()),
                        intent_equivalent_ids=intent_equivalent_ids,
                    ),
                ):
                    staged = await executor.stage_inject_event(
                        session,
                        scenario=scenario,
                        event=event,
                        pid_to_participant_id=pid_to_participant_id,
                        locked_equivalent_ids=frozenset(lock_ids),
                    )
                    # Flush HERE, not inside `commit()`. A flush error (a constraint, a 40001 or a
                    # SQLite busy on the write itself) leaves nothing COMMITTED - the transaction may
                    # well still be open, which is why every handler below rolls back before it
                    # retries or returns. Left to the commit, the same error would be
                    # indistinguishable from a failure of the commit statement, whose outcome is
                    # genuinely unknown, and the inject would be recorded as "outcome unknown"
                    # instead of "failed".
                    await session.flush()
            except asyncio.CancelledError:
                fired.discard(event_index)
                try:
                    await session.rollback()
                except Exception:
                    self._logger.warning(
                        "simulator.real.inject.rollback_after_cancel_failed event_index=%s",
                        event_index,
                        exc_info=True,
                    )
                raise
            except InjectOwnerLockSetTooNarrow as exc:
                fired.discard(event_index)
                await session.rollback()
                if lock_set_expansions_left <= 0:
                    # A second expansion means the incident set moved between two attempts.
                    # Leave the event pending rather than stage it under a set already proven
                    # stale twice.
                    raise
                lock_set_expansions_left -= 1
                lock_ids = set(lock_ids or ()) | set(exc.missing_equivalent_ids)
                self._logger.info(
                    "simulator.real.inject.owner_lock_set_expanded event_index=%s added=%d",
                    event_index,
                    len(exc.missing_equivalent_ids),
                )
                continue
            except Exception as exc:
                fired.discard(event_index)
                await session.rollback()
                if _is_transient_inject_db_error(exc):
                    if transient_retries_left <= 0:
                        raise
                    transient_retries_left -= 1
                    # No sleep: the retry opens with the owner locks, so it waits behind any
                    # lock-holding writer it conflicted with instead of racing it again.
                    self._logger.warning(
                        "simulator.real.inject.transient_retry event_index=%s stage=staging",
                        event_index,
                    )
                    continue
                refusal_reason = (
                    (exc.details or {}).get("reason")
                    if isinstance(exc, ConflictException)
                    else None
                )
                if refusal_reason in PaymentEngine.MONEY_STOP_REASONS:
                    # T1544: the operator's stop refuses THIS inject; it is not an error of the run.
                    # The same rule the payments phase already applies to a refused payment (a 4xx
                    # becomes REJECTED and the tick continues): consumed with a visible note, no
                    # debt and no envelope (the refusal came before `Book.operation`), and no retry
                    # - re-raising here left the event pending, so every later tick failed on it
                    # until the consecutive-failure limit stopped a run that is also serving other
                    # equivalents. Any other `ConflictException` keeps the path below.
                    # Step 5c: the integrity hold is classified identically, with its own reason in
                    # the log marker and the note.
                    self._logger.warning(
                        "simulator.real.inject.refused_%s event_index=%s",
                        refusal_reason,
                        event_index,
                    )
                    executor.enqueue_inject_note(
                        run_id,
                        run=run,
                        event_index=event_index,
                        event_time_ms=event_time_ms,
                        description=(
                            "inject refused (equivalent inactive)"
                            if refusal_reason == PaymentEngine.EQUIVALENT_INACTIVE_REASON
                            else "inject refused (equivalent integrity hold)"
                        ),
                    )
                    fired.add(event_index)
                    return
                if isinstance(exc, SQLAlchemyError):
                    self._logger.warning(
                        "simulator.real.inject.db_error event_index=%s",
                        event_index,
                        exc_info=True,
                    )
                    executor.enqueue_inject_note(
                        run_id,
                        run=run,
                        event_index=event_index,
                        event_time_ms=event_time_ms,
                        description="inject failed (db error)",
                    )
                    fired.add(event_index)
                    return
                raise

            # AT MOST ONCE until durable operation keys exist (programme 015, phase B step 4).
            # Marked immediately before the commit: if the commit's outcome cannot be known, the
            # event must not be applied a second time on the next tick.
            fired.add(event_index)
            try:
                await session.commit()
            except asyncio.CancelledError:
                # The commit may have landed. The mark stays.
                raise
            except Exception as exc:
                await session.rollback()
                if _is_transient_inject_db_error(exc):
                    # PostgreSQL reports 40001/40P01 for a transaction it rolled back: nothing of
                    # this attempt landed, so it may run again.
                    fired.discard(event_index)
                    if transient_retries_left <= 0:
                        raise
                    transient_retries_left -= 1
                    self._logger.warning(
                        "simulator.real.inject.transient_retry event_index=%s stage=commit",
                        event_index,
                    )
                    continue
                self._logger.warning(
                    "simulator.real.inject.commit_outcome_unknown event_index=%s",
                    event_index,
                    exc_info=True,
                )
                executor.enqueue_inject_note(
                    run_id,
                    run=run,
                    event_index=event_index,
                    event_time_ms=event_time_ms,
                    description="inject outcome unknown (commit error)",
                )
                return
            break

        # Committed. Only now do the ids staging learned exist for the next event.
        pid_to_participant_id.update(staged.pid_additions)
        try:
            await executor.publish_committed_inject(
                session,
                run_id=run_id,
                run=run,
                scenario=scenario,
                event_index=event_index,
                event_time_ms=event_time_ms,
                staged=staged,
                build_edge_patch_for_equivalent=self._build_edge_patch_for_equivalent,
                broadcast_topology_edge_patch=self._broadcast_topology_edge_patch,
            )
        except Exception:
            # Post-delivery: the inject is committed and fired. A failed publication is logged,
            # never undone, retried or re-staged (AGENTS.md section 12).
            self._logger.warning(
                "simulator.real.inject.publish_failed event_index=%s",
                event_index,
                exc_info=True,
            )
        await self._end_open_transaction(session)

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
        _inject_invalidate_caches_after_inject(
            logger=self._logger,
            run=run,
            scenario=scenario,
            affected_equivalents=affected_equivalents,
            new_participants=new_participants,
            new_participants_scenario=new_participants_scenario,
            new_trustlines_scenario=new_trustlines_scenario,
            frozen_pids=frozen_pids,
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
        return await self._edge_patch_builder.build_edge_patch_for_equivalent(
            session=session,
            run=run,
            equivalent_code=equivalent_code,
            only_edges=only_edges,
            include_width_keys=include_width_keys,
        )

    def _broadcast_topology_edge_patch(
        self,
        *,
        run_id: str,
        run: RunRecord,
        equivalent: str,
        edge_patch: list[dict[str, Any]],
        reason: str,
    ) -> None:
        self._sse_emitter.emit_topology_edge_patch(
            run_id=run_id,
            run=run,
            equivalent=equivalent,
            edge_patch=edge_patch,
            reason=reason,
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

    def _init_trust_drift(self, run: RunRecord, scenario: dict[str, Any]) -> None:
        self._trust_drift_engine.init_trust_drift(run, scenario)

    async def _apply_trust_growth(
        self,
        run: RunRecord,
        clearing_session,
        touched_edges: set[tuple[str, str]],
        eq_code: str,
        tick_index: int,
        cleared_amount_per_edge: dict[tuple[str, str], float],
    ) -> TrustDriftResult:
        return await self._trust_drift_engine.apply_trust_growth(
            run,
            clearing_session,
            touched_edges,
            eq_code,
            tick_index,
            cleared_amount_per_edge,
        )

    async def _apply_trust_decay(
        self,
        run: RunRecord,
        session,
        tick_index: int,
        debt_snapshot: dict[tuple[str, str, str], Decimal],
        scenario: dict[str, Any],
    ) -> TrustDriftResult:
        return await self._trust_drift_engine.apply_trust_decay(
            run,
            session,
            tick_index,
            debt_snapshot,
            scenario,
        )

    async def flush_pending_storage(self, run_id: str) -> None:
        await self._real_tick_orchestrator.flush_pending_storage(run_id)

    async def tick_real_mode(self, run_id: str) -> None:
        await self._real_tick_orchestrator.tick_real_mode(run_id)

    async def fail_run(self, run_id: str, *, code: str, message: str) -> None:
        await self._real_tick_orchestrator.fail_run(run_id, code=code, message=message)

    async def tick_real_mode_clearing(
        self,
        session,  # NOTE: Unused now; clearing uses its own isolated session
        run_id: str,
        run: RunRecord,
        equivalents: list[str],
        *,
        async_session_local: Any | None = None,
        clearing_service_cls: Any | None = None,
        time_budget_ms_override: int | None = None,
        max_depth_override: int | None = None,
    ) -> dict[str, float]:
        return await self._real_clearing_engine.tick_real_mode_clearing(
            session,
            run_id=run_id,
            run=run,
            equivalents=equivalents,
            apply_trust_growth=self._trust_drift_engine.apply_trust_growth,
            build_edge_patch_for_equivalent=self._build_edge_patch_for_equivalent,
            broadcast_topology_edge_patch=self._broadcast_topology_edge_patch,
            async_session_local=async_session_local,
            clearing_service_cls=clearing_service_cls,
            time_budget_ms_override=time_budget_ms_override,
            max_depth_override=max_depth_override,
        )

    def _plan_real_payments(
        self,
        run: RunRecord,
        scenario: dict[str, Any],
        *,
        debt_snapshot: dict[tuple[str, str, str], Decimal] | None = None,
    ) -> list[_RealPaymentAction]:
        return self._real_payment_planner.plan_payments(
            run,
            scenario,
            debt_snapshot=debt_snapshot,
        )

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
        return await self._real_scenario_seeder.load_real_participants(
            session=session,
            scenario=scenario,
        )

    async def _load_debt_snapshot_by_pid(
        self,
        session,
        participants: list[tuple[uuid.UUID, str]],
        equivalents: list[str],
    ) -> dict[tuple[str, str, str], Decimal]:
        return await self._real_debt_snapshot_loader.load_debt_snapshot_by_pid(
            session=session,
            participants=participants,
            equivalents=equivalents,
        )

    async def _seed_scenario_into_db(self, session, scenario: dict[str, Any]) -> None:
        await self._real_scenario_seeder.seed_scenario_into_db(
            session=session,
            scenario=scenario,
        )
