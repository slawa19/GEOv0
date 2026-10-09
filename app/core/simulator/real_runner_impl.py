from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from decimal import Decimal
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.exc import DBAPIError, SQLAlchemyError

from app.core.money_boundary import MoneyBoundary
from app.core.payments.service import is_debt_pair_collision
from app.core.simulator.artifacts import ArtifactsManager
from app.core.simulator.edge_patch_builder import EdgePatchBuilder
from app.core.simulator.inject_executor import (
    InjectExecutor,
    InjectOwnerLockSetTooNarrow,
    StagedInjectEvent,
    inject_event_equivalent_codes,
    inject_event_freeze_participant_pids,
    inject_event_participant_pids,
)
from app.core.simulator.models import RunRecord, TrustDriftResult
from app.core.simulator.real_debt_snapshot_loader import RealDebtSnapshotLoader
from app.core.simulator.real_payment_action import _RealPaymentAction
from app.core.simulator.real_payment_planner import RealPaymentPlanner
from app.core.simulator.real_payments_executor import RealPaymentsExecutor
from app.core.simulator.real_scenario_seeder import RealScenarioSeeder
from app.core.simulator.scenario_equivalent import effective_equivalent
from app.config import settings
from app.core.simulator.sse_broadcast import SseBroadcast, SseEventEmitter
from app.core.simulator.tick import RealTick
from app.core.simulator.trust_drift_engine import TrustDriftEngine
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.sqlstate import ROLLED_BACK_SQLSTATES, sqlstate

# 40001 serialization_failure, 40P01 deadlock_detected: PostgreSQL has rolled the transaction back
# and the whole unit of work may run again.
#
# 55P03 lock_not_available: the owner lock was not obtained within its deadline. Nothing of the unit
# of work has been written - the locks open it - so this is a known rollback too. Treating it as an
# ordinary database error would record "inject failed" and mark the event fired, i.e. DROP the
# inject whenever another writer held the equivalent a little too long. The tick orchestrator's own
# lock timeout fails the tick without firing anything; the inject owner now does the same once its
# single retry is spent. Nothing else is retried.
_INJECT_TRANSIENT_SQLSTATES = ROLLED_BACK_SQLSTATES | {"55P03"}


def _is_transient_inject_db_error(exc: BaseException) -> bool:
    if not isinstance(exc, DBAPIError):
        return False
    # The driver error the wrapper carries, one level, `sqlstate`/`pgcode` only (never `.code`).
    code = sqlstate(getattr(exc, "orig", None), walk=False, bare_code=False)
    # 019 stage 5 (`T1909`, precondition 1): a concurrent writer inserted the same new debt row - a
    # `23505` on exactly `uq_debts_debtor_creditor_equivalent`, transient like 40001; no other 23505.
    return code in _INJECT_TRANSIENT_SQLSTATES or is_debt_pair_collision(exc)


def scripted_event_idempotency_key(run_id: str, epoch: int, event_index: int) -> str:
    """The idempotency key (and `tx_id`) of a scenario's scripted `payment` event: run id, LAUNCH epoch, event index.

    036 B1. NOT the tick's key (`RealRunnerImpl._sim_idempotency_key` carries the tick number): a scripted event is
    not tied to the tick it happens to run at. If a tick fails before the event is marked spent and the event runs
    again one tick later, the key is the same and the payment service answers with the stored payment - the debt
    moves once. After a `restart` the epoch differs, so the same event is a NEW operation that moves money again; the
    first launch's key is never repeated (the class of F-034-1: a stored payment must not be reported as paid)."""

    material = f"scripted|{run_id}|{int(epoch)}|{int(event_index)}"
    return "sim:" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


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

        # Limits from Settings (024 `T2414.1`); None there keeps the runtime default passed in.
        self._real_max_consec_tick_failures_limit = self._real_max_consec_tick_failures_default if settings.SIMULATOR_REAL_MAX_CONSEC_TICK_FAILURES is None else settings.SIMULATOR_REAL_MAX_CONSEC_TICK_FAILURES
        self._real_max_timeouts_per_tick_limit = self._real_max_timeouts_per_tick_default if settings.SIMULATOR_REAL_MAX_TIMEOUTS_PER_TICK is None else settings.SIMULATOR_REAL_MAX_TIMEOUTS_PER_TICK
        self._real_max_errors_total_limit = self._real_max_errors_total_default if settings.SIMULATOR_REAL_MAX_ERRORS_TOTAL is None else settings.SIMULATOR_REAL_MAX_ERRORS_TOTAL
        # `SIMULATOR_CLEARING_MAX_DEPTH` is no longer read (programme 023 slice (d), R4): clearing execution has no
        # depth. The runbook records the variable as removed.
        self._clearing_max_fx_edges_limit = settings.SIMULATOR_CLEARING_MAX_EDGES_FOR_FX
        # Amount cap is opt-in. Default must not override scenario amount_model bounds.
        self._real_amount_cap_limit = settings.SIMULATOR_REAL_AMOUNT_CAP
        self._real_enable_inject = settings.SIMULATOR_REAL_ENABLE_INJECT >= 1

        # Programme 015 / P1: the bounded replay of the tick's money phase.
        # `..._MONEY_REPLAY_ATTEMPTS` counts ATTEMPTS, not retries, so 1 disables the replay.
        # `..._MAX_CONSEC_MONEY_NO_PROGRESS` is the explicit no-progress criterion that may stop a
        # run under permanent contention - the replacement for stopping a run on a SQLSTATE.
        self._real_money_replay_attempts_limit = settings.SIMULATOR_REAL_MONEY_REPLAY_ATTEMPTS
        self._real_max_consec_money_no_progress_limit = settings.SIMULATOR_REAL_MAX_CONSEC_MONEY_NO_PROGRESS

        # Throttling knobs from Settings.
        self._real_db_metrics_every_n_ticks = settings.SIMULATOR_REAL_DB_METRICS_EVERY_N_TICKS
        self._real_db_bottlenecks_every_n_ticks = settings.SIMULATOR_REAL_DB_BOTTLENECKS_EVERY_N_TICKS
        self._real_last_tick_write_every_ms = settings.SIMULATOR_REAL_LAST_TICK_WRITE_EVERY_MS
        self._real_artifacts_sync_every_ms = settings.SIMULATOR_REAL_ARTIFACTS_SYNC_EVERY_MS

        # Clearing loop throttling: keep default behavior, but avoid long event-loop stalls.
        # If budget is exceeded, clearing will continue on the next tick.
        self._real_clearing_time_budget_ms = settings.SIMULATOR_REAL_CLEARING_TIME_BUDGET_MS

        # Sub-components: eager init (the runner is created once on startup).
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

        self._real_scenario_seeder: RealScenarioSeeder = RealScenarioSeeder()

        # Programme 021 stage 4: the tick is one module (`tick.py`). It reads this runner's collaborators and limits
        # at call time and captures the static intervals and the clearing budget here, once.
        self._tick: RealTick = RealTick(self)

    def _parse_event_time_ms(self, evt: Any) -> int | None:
        if not isinstance(evt, dict):
            return None
        t = evt.get("time")
        if isinstance(t, int):
            return max(0, int(t))
        # MVP: token-based times are future.
        return None

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
          restarts once (as does a 23505 on exactly `uq_debts_debtor_creditor_equivalent`, 019 `T1909`);
          a second one propagates, the event stays pending and nothing after it
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

            if evt_type in ("payment", "clearing"):
                # 036 B1: executed by the TICK, not here. A payment is a planned payment of the money phase (it needs
                # the owner locks and the phase's commit) and is marked fired only once that commit is durable
                # (`scripted_payments_due`, `TickPaymentsPhase.apply_deferred_effects`); a clearing runs after the
                # payments phase (`RealTick.run_scripted_clearings`). Marking either fired here would spend an event
                # that nothing has executed (F-036-2).
                continue

            # Unknown / unsupported event types are ignored, but we still mark them fired once due.
            run._real_fired_scenario_event_indexes.add(idx)

    async def _resolve_inject_owner_lock_ids(
        self,
        session,
        *,
        run: RunRecord,
        scenario: dict[str, Any],
        event: dict[str, Any] | None,
    ) -> set[uuid.UUID]:
        """The run's equivalents and the event's own, as ids, in one read that is then committed.

        A freeze names no equivalent: since 028 `F-028-29` it writes no trust line (until then the equivalents of
        the frozen participant's lines were read here, external review of 015 step 3).

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

        # 036 B2: the scenario's opt-in (`settings.playback.inject_enabled`) can only narrow the process flag, never raise it.
        # Absent: the flag alone decides, as before. A skipped inject is said aloud - the events artifact and the run's
        # progress - and is spent (a flag does not change during a run).
        playback = ((scenario.get("settings") or {}).get("playback") or {}) if isinstance(scenario, dict) else {}
        scenario_disables = isinstance(playback, dict) and playback.get("inject_enabled") is False
        if not self._real_enable_inject or scenario_disables:
            reason = "inject_disabled_by_process" if not self._real_enable_inject else "inject_disabled_by_scenario"
            # ONE note, written by the progress writer, with the reason in words (not a second, direct one).
            self._write_story_progress(
                run, event_index, event_time_ms, int(run._launch_epoch), {"kind": "inject", "status": "refused", "reason": reason},
                spend=True,
                note=(
                    "inject skipped (SIMULATOR_REAL_ENABLE_INJECT=0)" if not self._real_enable_inject
                    else "inject skipped (settings.playback.inject_enabled=false)"
                ),
            )
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
                # 027 stage 2 (`T2704`): the stop/hold and step reads of a line creation (`TrustLineService`) hold against
                # a concurrent writer only at READ COMMITTED behind the row locks. Read on the unit of work's transaction
                # before its first write; the refusal is neither transient nor a stop, so it propagates and the event
                # stays pending.
                await MoneyBoundary.require_read_committed(session, writer="inject")
                # 028 `F-028-28`: the FIRST locks of the event - the rows of every participant its effects name, in
                # `participants.id` order, `FOR UPDATE` for the targets of its freezes (taken now, never upgraded from
                # `FOR SHARE` mid-event), `FOR SHARE` for the rest. Each effect reads the status under them.
                event_ids = dict((await session.execute(select(Participant.pid, Participant.id).where(
                    Participant.pid.in_(sorted(inject_event_participant_pids(event=event)))))).all())
                await MoneyBoundary(session).lock_participants(
                    event_ids.values(), exclusive=[event_ids[p] for p in inject_event_freeze_participant_pids(
                        event=event) if p in event_ids], timeout_ms=MoneyBoundary.lock_budget_ms())
                # NO DEBT IS WRITTEN HERE (030 S3b, F-030-6): the event is participants, lines and freezes, each through
                # its service with that service's own checks (the line creation refuses a stopped or held equivalent),
                # so there is no operation envelope, no line lock set for debts and no stop check of the event's own.
                staged = await executor.stage_inject_event(
                    session,
                    scenario=scenario,
                    event=event,
                    pid_to_participant_id=pid_to_participant_id,
                    locked_equivalent_ids=frozenset(lock_ids),
                )
                # Flush HERE, not inside `commit()`. A flush error (a constraint, a 40001 or a SQLite busy on the write
                # itself) leaves nothing COMMITTED - the transaction may well still be open, which is why every handler
                # below rolls back before it retries or returns. Left to the commit, the same error would be
                # indistinguishable from a failure of the commit statement, whose outcome is genuinely unknown, and the
                # inject would be recorded as "outcome unknown" instead of "failed".
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
                    # No sleep: the conflict is reported once the other writer has committed (or
                    # rolled back), so the retry's fresh transaction already sees its outcome.
                    self._logger.warning(
                        "simulator.real.inject.transient_retry event_index=%s stage=staging",
                        event_index,
                    )
                    continue
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
                run_id=run_id,
                run=run,
                scenario=scenario,
                event_index=event_index,
                event_time_ms=event_time_ms,
                staged=staged,
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
    ) -> TrustDriftResult:
        return await self._trust_drift_engine.apply_trust_growth(
            run,
            clearing_session,
            touched_edges,
            eq_code,
            tick_index,
        )

    async def _apply_trust_decay(
        self,
        run: RunRecord,
        session,
        tick_index: int,
        scenario: dict[str, Any],
    ) -> TrustDriftResult:
        return await self._trust_drift_engine.apply_trust_decay(
            run,
            session,
            tick_index,
            scenario,
        )

    async def flush_pending_storage(self, run_id: str) -> None:
        await self._tick.flush_pending_storage(run_id)

    async def tick_real_mode(self, run_id: str) -> None:
        run = self._get_run(run_id)
        spent_before = frozenset(run._real_fired_scenario_event_indexes)
        epoch = int(run._launch_epoch)
        # `RealTick.tick` answers the failures of a tick's work itself (a failed tick is counted and the run goes on or
        # stops) and returns, so a pause that belongs to a tick whose money went durable is decided here on the normal
        # return. What still leaves it pauses nothing: cancellation, and an exception raised inside its own failure
        # handlers (e.g. the status publish of `fail_run`) - by then the run is already `error`, not `running`.
        await self._tick.tick(run_id)
        self._pause_after_spent_episodes(run, spent_before, epoch)

    def _pause_after_spent_episodes(self, run: RunRecord, spent_before: frozenset[int], epoch: int) -> None:
        """036 B2: pause the run after the tick that SPENT an episode with `pause_after: true` (after its money phase and
        clearing; the sim time stands until `resume`). "Spent" is the B1 meaning: a clearing that did not complete, a
        payment whose outcome is not established and an event of another launch are not spent and do not pause. A run that
        is stopping, stopped or already paused is left alone."""

        with self._lock:
            if int(run._launch_epoch) != int(epoch) or run.state != "running":
                return
            events = self._scenario_of(run).get("events")
            pausing = sorted(
                index
                for index in run._real_fired_scenario_event_indexes - spent_before
                if isinstance(events, list) and 0 <= index < len(events)
                and isinstance(events[index], dict) and events[index].get("pause_after") is True
            )
            if not pausing:
                return
            run.state = "paused"
        # The status is published by the heartbeat loop right after the tick returns (`_heartbeat_loop`), once: not here.
        self._logger.info(
            "simulator.real.paused_after_episode run_id=%s event_index=%s tick=%s", str(run.run_id), pausing[0], int(run.tick_index)
        )

    async def fail_run(self, run_id: str, *, code: str, message: str) -> None:
        await self._tick.fail_run(run_id, code=code, message=message)

    def _plan_real_payments(
        self,
        run: RunRecord,
        scenario: dict[str, Any],
        *,
        debt_snapshot: dict[tuple[str, str, str], Decimal] | None = None,
        precision_by_eq: dict[str, int] | None = None,
    ) -> list[_RealPaymentAction]:
        return self._real_payment_planner.plan_payments(
            run,
            scenario,
            debt_snapshot=debt_snapshot,
            precision_by_eq=precision_by_eq,
        )

    # ── scripted `payment` / `clearing` events (036 B1) ───────────────────────────────────────────────

    def _refuse_scripted_event(self, run: RunRecord, index: int, event: dict[str, Any], reason: str, **extra: Any) -> None:
        """An event that cannot even be attempted (malformed, unresolved equivalent): said aloud, then spent.

        Never a silent skip: a warning in the log, a note in the events artifact and the run's story progress. A
        refusal the CORE gives (no route, a step finer than the equivalent's, an unknown participant) is not this - it
        is an ordinary `tx.failed` of the payment itself."""

        self._write_story_progress(
            run, int(index), int(event.get("time") or 0), int(run._launch_epoch),
            {"kind": str(event.get("type")), "status": "refused", "reason": reason, **extra}, spend=True,
        )

    def _write_story_progress(
        self,
        run: RunRecord,
        index: int,
        event_time_ms: int,
        epoch: int,
        record: dict[str, Any],
        *,
        spend: bool,
        add_cycles: list[dict[str, Any]] | None = None,
        note: str | None = None,
    ) -> None:
        """THE ONE WRITER of the run's story progress (036 B2): `record` for event `index` of launch `epoch`.

        Discarded if the run has been restarted since `epoch` (the event belongs to a launch that is over). `add_cycles`
        (a clearing) are appended to the cycles this event already committed in this epoch, so an event that needs several
        attempts reports everything it cleared. `spend` marks the event fired. A change of (status, reason) - and only
        that - is logged and written to the events artifact (AGENTS.md section 12: not on every tick), as `note` when the
        caller says it in its own words, else as "scripted <kind> <status>".

        ONE attempt is one call: `attempts` counts the calls that reach here for an event within an epoch, so a caller that
        reports the same attempt twice (the money phase does: see `report_durable` in `tick.py`) would count it twice."""

        with self._lock:
            if int(run._launch_epoch) != int(epoch):
                self._logger.info(
                    "simulator.real.scripted_event_discarded_after_restart run_id=%s event_index=%s planned_epoch=%s epoch=%s",
                    str(run.run_id), int(index), int(epoch), int(run._launch_epoch),
                )
                return
            previous = run._real_story_progress.get(int(index)) or {}
            same_epoch = previous.get("epoch") == int(epoch)
            stored = {**record, "epoch": int(epoch), "attempts": int(previous.get("attempts", 0) if same_epoch else 0) + 1}
            if add_cycles is not None:
                cycles = [*((previous.get("cycles") or []) if same_epoch else []), *add_cycles]
                stored["cycles"], stored["cleared_cycles"] = cycles, len(cycles)
            changed = (previous.get("status"), previous.get("reason")) != (stored["status"], stored.get("reason")) or not same_epoch
            run._real_story_progress[int(index)] = stored
            if spend:
                run._real_fired_scenario_event_indexes.add(int(index))
        if changed:
            log = self._logger.info if stored["status"] == "done" else self._logger.warning
            log(
                "simulator.real.scripted_event_status run_id=%s event_index=%s kind=%s status=%s reason=%s attempts=%s",
                str(run.run_id), int(index), stored.get("kind"), stored["status"], stored.get("reason"), stored["attempts"],
            )
            self._inject_executor.enqueue_inject_note(
                run.run_id, run=run, event_index=index, event_time_ms=int(event_time_ms),
                description=note or f"scripted {stored.get('kind')} {stored['status']}", stats=stored,
            )

    def _scenario_of(self, run: RunRecord) -> dict[str, Any]:
        """The scenario a tick of this run reads: the run's own deep copy, else the registry's (as `RealTick.tick` does)."""

        return getattr(run, "_scenario_raw", None) or self._get_scenario_raw(run.scenario_id) or {}

    def _event_time_ms_of(self, run: RunRecord, index: int) -> int:
        events = self._scenario_of(run).get("events")
        event = events[index] if isinstance(events, list) and 0 <= index < len(events) else None
        time_ms = event.get("time") if isinstance(event, dict) else 0
        return int(time_ms) if isinstance(time_ms, int) and not isinstance(time_ms, bool) else 0

    def scripted_payments_due(
        self, run: RunRecord, scenario: dict[str, Any], *, first_seq: int
    ) -> tuple[list[_RealPaymentAction], dict[int, int], int]:
        """The scripted `payment` events that are due and not spent, as payments of the coming money phase.

        Called once per ATTEMPT of the phase (a replayed attempt asks again: nothing is spent until the commit is
        durable). Each action carries the event's own key, so a repeat never pays twice. Seqs continue the planned
        ones (`first_seq`...): the executor orders its results by a contiguous seq. Returns the actions, the event index
        by seq, and the launch EPOCH they were planned under: whatever is recorded for them afterwards is discarded if the
        run has been restarted meanwhile (`mark_scripted_events_fired`)."""

        events = scenario.get("events")
        actions: list[_RealPaymentAction] = []
        index_by_seq: dict[int, int] = {}
        epoch = int(run._launch_epoch)
        for idx, evt in enumerate(events if isinstance(events, list) else []):
            if not isinstance(evt, dict) or evt.get("type") != "payment" or idx in run._real_fired_scenario_event_indexes:
                continue
            t0 = self._parse_event_time_ms(evt)
            if t0 is None or int(run.sim_time_ms) < int(t0):
                continue
            sender, receiver, amount = evt.get("from"), evt.get("to"), evt.get("amount")
            equivalent = effective_equivalent(scenario, evt)
            if not (isinstance(sender, str) and sender and isinstance(receiver, str) and receiver
                    and isinstance(amount, str) and amount):
                self._refuse_scripted_event(run, idx, evt, "malformed_payment")
                continue
            if not equivalent:
                # No `equivalent` on the event and no declared `baseEquivalent`: the first of `equivalents[]` is NOT a
                # default, and guessing would pay in a currency the author never named.
                self._refuse_scripted_event(run, idx, evt, "unresolved_equivalent")
                continue
            if equivalent not in {str(x).upper() for x in (run._real_equivalents or [])}:
                # The money phase locked the lines of the RUN's equivalents as its first statement (027 stage 2); a payment
                # in any other equivalent would run without those locks. Refused aloud, not attempted.
                self._refuse_scripted_event(run, idx, evt, "equivalent_not_in_the_run", equivalent=equivalent)
                continue
            actions.append(
                _RealPaymentAction(
                    seq=int(first_seq) + len(actions),
                    equivalent=equivalent,
                    sender_pid=sender,
                    receiver_pid=receiver,
                    amount=amount,
                    idempotency_key=scripted_event_idempotency_key(run.run_id, epoch, idx),
                )
            )
            index_by_seq[int(first_seq) + len(actions) - 1] = idx
        return actions, index_by_seq, epoch

    def mark_scripted_events_fired(
        self, run: RunRecord, indexes: frozenset[int], epoch: int, progress: dict[int, dict[str, Any]] | None = None
    ) -> None:
        """The money phase that carried these payments is DURABLE (committed, or landed after an unknown commit).

        Only the events that reached a TERMINAL outcome are passed in (the caller leaves out a payment that failed
        transiently before admission). `epoch` is the launch the phase was planned under: if the run has been restarted
        since, the events are those of a launch that is over and the NEW epoch's events are still due - nothing is
        marked. `progress` is the outcome of each scripted payment of the phase (done, refused, or incomplete for one whose
        outcome is not established); it is written here, once, when the phase is durable."""

        with self._lock:
            if int(run._launch_epoch) != int(epoch):
                self._logger.info(
                    "simulator.real.scripted_events_not_spent_after_restart run_id=%s planned_epoch=%s epoch=%s",
                    str(run.run_id), int(epoch), int(run._launch_epoch),
                )
                return
            run._real_fired_scenario_event_indexes.update(int(i) for i in indexes)
        for index, record in sorted((progress or {}).items()):
            self._write_story_progress(run, int(index), self._event_time_ms_of(run, int(index)), int(epoch), record, spend=False)

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
        epoch: int = 0,
    ) -> str:
        """The idempotency key (and `tx_id`) of one planned payment of one tick of one LAUNCH of a run.

        034 `F-034-1`: `epoch` is `RunRecord._launch_epoch`. A restart keeps `run_id` and starts the tick numbers
        again, so without it the restarted run's first ticks repeated the keys of the first launch and the payment
        service answered them with the stored payments - reported as paid, nothing moved. The first launch
        (epoch 0) keeps the key it always had, byte for byte; within one launch a repeated tick still repeats its
        keys, which is what makes a replayed money phase idempotent.
        """
        material = f"{run_id}|{tick_ms}|{sender_pid}|{receiver_pid}|{equivalent}|{amount}|{seq}"
        if int(epoch) > 0:
            material += f"|epoch={int(epoch)}"
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
