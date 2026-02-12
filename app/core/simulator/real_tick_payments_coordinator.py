from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Awaitable, Callable

from app.core.simulator.models import RunRecord
from app.core.simulator.real_payments_executor import RealPaymentsExecutor, RealPaymentsResult


@dataclass(frozen=True)
class RealTickPaymentsPhaseResult:
    debt_snapshot: dict[tuple[str, str, str], Decimal]
    planned: list[Any]
    per_eq_metric_values: dict[str, dict[str, float]]

    committed: int
    rejected: int
    errors: int
    timeouts: int

    per_eq: dict[str, Any]
    per_eq_route: dict[str, Any]
    per_eq_edge_stats: dict[str, Any]

    stall_ticks: int


class RealTickPaymentsCoordinator:
    def __init__(self, *, lock, logger: logging.Logger) -> None:
        self._lock = lock
        self._logger = logger

    async def run_payments_phase(
        self,
        *,
        session: Any,
        run_id: str,
        run: RunRecord,
        scenario: dict[str, Any],
        participants: list[tuple[uuid.UUID, str]],
        equivalents: list[str],
        load_debt_snapshot_by_pid: Callable[
            [Any, list[tuple[uuid.UUID, str]], list[str]],
            Awaitable[dict[tuple[str, str, str], Decimal]],
        ],
        plan_payments: Callable[
            [RunRecord, dict[str, Any]],
            list[Any],
        ]
        | Callable[
            [RunRecord, dict[str, Any], dict[tuple[str, str, str], Decimal] | None],
            list[Any],
        ],
        payments_executor: RealPaymentsExecutor,
        max_in_flight: int,
        max_timeouts_per_tick: int,
        max_errors_total: int,
        fail_run: Callable[[str, str, str], Awaitable[None]],
    ) -> tuple[RealTickPaymentsPhaseResult, bool]:
        # ── Phase 1.4: capacity-aware payment amounts ─────────
        # Load current debt snapshot *after* events (which may mutate DB)
        # so that capacity reflects the latest state.  Best-effort:
        # if the query fails we fall back to static limits.
        debt_snapshot: dict[tuple[str, str, str], Decimal] = {}
        try:
            debt_snapshot = await load_debt_snapshot_by_pid(session, participants, equivalents)
        except Exception:
            self._logger.debug(
                "capacity_aware: debt snapshot load failed, falling back to static limits"
            )

        planned = plan_payments(run, scenario, debt_snapshot)  # type: ignore[misc]
        with self._lock:
            run.ops_sec = float(len(planned))
            run.queue_depth = len(planned)
            run._real_in_flight = 0
            run.current_phase = "payments" if planned else None

        sender_id_by_pid = {pid: participant_id for (participant_id, pid) in participants}

        per_eq_metric_values: dict[str, dict[str, float]] = {str(eq): {} for eq in equivalents}

        payments_res: RealPaymentsResult = await payments_executor.execute_planned_payments(
            session=session,
            run_id=run_id,
            run=run,
            planned=planned,
            equivalents=equivalents,
            sender_id_by_pid=sender_id_by_pid,
            max_in_flight=int(max_in_flight),
            max_timeouts_per_tick=int(max_timeouts_per_tick),
            fail_run=lambda _run_id, code, message: fail_run(
                _run_id, code, message
            ),
        )

        committed = int(payments_res.committed)
        rejected = int(payments_res.rejected)
        errors = int(payments_res.errors)
        timeouts = int(payments_res.timeouts)
        stall_ticks = int(payments_res.stall_ticks)

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

        if int(max_errors_total) > 0 and run.errors_total >= int(max_errors_total):
            await fail_run(
                run_id,
                "REAL_MODE_TOO_MANY_ERRORS",
                f"Too many total errors: {run.errors_total}",
            )
            should_stop = True
        else:
            should_stop = False

        res = RealTickPaymentsPhaseResult(
            debt_snapshot=debt_snapshot,
            planned=planned,
            per_eq_metric_values=per_eq_metric_values,
            committed=committed,
            rejected=rejected,
            errors=errors,
            timeouts=timeouts,
            per_eq=dict(payments_res.per_eq),
            per_eq_route=dict(payments_res.per_eq_route),
            per_eq_edge_stats=dict(payments_res.per_eq_edge_stats),
            stall_ticks=stall_ticks,
        )

        return res, should_stop
