from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Callable, Optional

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
        clearing_volume_by_eq: dict[str, Decimal | float],
        per_eq_metric_values: dict[str, dict[str, Optional[Decimal | float]]],
        should_warn: Callable[[str], bool] | None = None,
    ) -> None:
        # Real total debt snapshot (sum of all debts for the equivalent).
        # Throttled: aggregate SUM can become hot on large Debt tables.
        #
        # Only equivalents actually measured in this tick appear here. A missing
        # key means "not measured now" and is persisted as NULL: neither a stale
        # cached value nor 0.0 may be stamped with the current t_ms as if it were
        # a fresh measurement (spec 007, F-007-1).
        total_debt_by_eq: dict[str, Decimal] = {}

        metrics_every_n = int(self._real_db_metrics_every_n_ticks)
        should_refresh_total_debt = metrics_every_n <= 1 or (
            int(run.tick_index) % int(metrics_every_n) == 0
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
                            select(func.coalesce(func.sum(Debt.amount), 0)).where(
                                Debt.equivalent_id == eq_id
                            )
                        )
                    ).scalar_one()
                    # 2026-08-20 / p007_t715: `total_debt` is money (the domain
                    # model calls it "amount in the selected equivalent"), so the
                    # SUM over Numeric(20, 8) debt amounts stays Decimal. The old
                    # `float(total)` narrowed it here, at the source, before any
                    # column type could matter.
                    total_debt_by_eq[str(eq_code)] = (
                        total if isinstance(total, Decimal) else Decimal(str(total))
                    )

                with self._lock:
                    run._real_total_debt_by_eq = dict(total_debt_by_eq)
                    run._real_total_debt_tick = int(run.tick_index)
            except Exception as exc:
                if should_warn is None or should_warn("total_debt_snapshot_failed"):
                    self._logger.warning(
                        "simulator.real.total_debt_snapshot_failed run_id=%s tick=%s error_class=%s equivalents=%d",
                        str(run.run_id),
                        int(run.tick_index),
                        type(exc).__name__,
                        len(equivalents),
                        exc_info=True,
                    )

                # A failed snapshot is not a measurement: persist nothing for
                # total_debt this tick rather than a stale or zero value.
                total_debt_by_eq = {}

        # Avg route length for this tick (successful payments).
        for eq in equivalents:
            r = per_eq_route.get(str(eq), {}) or {}
            n = float(r.get("route_len_n", 0.0) or 0.0)
            s = float(r.get("route_len_sum", 0.0) or 0.0)
            # No successful route in this tick means the average is undefined,
            # not zero; leave it unmeasured.
            if n > 0:
                per_eq_metric_values[str(eq)]["avg_route_length"] = float(s / n)
            if str(eq) in total_debt_by_eq:
                per_eq_metric_values[str(eq)]["total_debt"] = total_debt_by_eq[str(eq)]
            # Clearing volume is money too and arrives as Decimal from
            # `real_clearing_engine`; keep it exact instead of re-narrowing.
            raw_volume = clearing_volume_by_eq.get(str(eq)) or 0
            per_eq_metric_values[str(eq)]["clearing_volume"] = (
                raw_volume if isinstance(raw_volume, Decimal) else Decimal(str(raw_volume))
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
