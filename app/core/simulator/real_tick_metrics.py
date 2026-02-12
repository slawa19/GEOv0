from __future__ import annotations

import logging
from typing import Any, Callable

from sqlalchemy import func, select

from app.core.simulator.models import RunRecord
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent


class RealTickMetrics:
    def __init__(
        self,
        *,
        lock,
        logger: logging.Logger,
        real_db_metrics_every_n_ticks: int,
    ) -> None:
        self._lock = lock
        self._logger = logger
        self._real_db_metrics_every_n_ticks = int(real_db_metrics_every_n_ticks)

    async def populate_per_eq_metric_values(
        self,
        *,
        session: Any,
        run: RunRecord,
        scenario: dict[str, Any],
        equivalents: list[str],
        per_eq_route: dict[str, Any],
        clearing_volume_by_eq: dict[str, float],
        per_eq_metric_values: dict[str, dict[str, float]],
        should_warn: Callable[[str], bool] | None = None,
    ) -> None:
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
                if should_warn is None or should_warn("total_debt_snapshot_failed"):
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
            r = per_eq_route.get(str(eq), {}) or {}
            n = float(r.get("route_len_n", 0.0) or 0.0)
            s = float(r.get("route_len_sum", 0.0) or 0.0)
            per_eq_metric_values[str(eq)]["avg_route_length"] = float(s / n) if n > 0 else 0.0
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
                and str(_p.get("status") or "active").strip().lower() == "active"
            )
        )

        # active_trustlines per equivalent: count from run._edges_by_equivalent cache.
        # After inject ops, this cache already reflects frozen/removed edges.
        with self._lock:
            _edges_snapshot = dict(run._edges_by_equivalent or {})

        for eq in equivalents:
            per_eq_metric_values[str(eq)]["active_participants"] = _active_participants_count
            per_eq_metric_values[str(eq)]["active_trustlines"] = float(
                len(_edges_snapshot.get(str(eq), []))
            )
