from __future__ import annotations

import logging
import time
from decimal import Decimal
from typing import Optional

from sqlalchemy import func, select

import app.db.session as db_session
from app.db.models.simulator_storage import SimulatorRunBottleneck, SimulatorRunMetric
from app.core.simulator.models import RunRecord
from app.schemas.simulator import (
    SIMULATOR_API_VERSION,
    BottleneckItem,
    BottleneckTargetEdge,
    BottlenecksResponse,
    MetricPoint,
    MetricSeries,
    MetricSeriesKey,
    MetricsResponse,
    metric_point_value,
)
from app.utils.error_codes import ErrorCode
from app.utils.exceptions import BadRequestException, GeoException, NotFoundException


# Minimum interval between full tracebacks for the same failure kind. The
# WARNING line is never throttled; only the traceback is.
TRACEBACK_MIN_INTERVAL_S = 60.0

REASON_MESSAGES = {
    "db_read_failed": (
        "Simulator analytics unavailable for this real-mode run: persisted data "
        "could not be read. Synthetic data is never served in real mode."
    ),
    "storage_disabled": (
        "Simulator analytics unavailable for this real-mode run: persistence is "
        "disabled, so there is no measured data. Synthetic data is never served "
        "in real mode."
    ),
}


class MetricsBottlenecks:
    def __init__(
        self,
        *,
        lock,
        runs: dict[str, RunRecord],
        db_enabled,
        logger: logging.Logger,
    ) -> None:
        self._lock = lock
        self._runs = runs
        self._db_enabled = db_enabled
        self._logger = logger
        self._traceback_last_at: dict[str, float] = {}

    def _get_run(self, run_id: str) -> RunRecord:
        with self._lock:
            run = self._runs.get(run_id)
        if run is None:
            raise NotFoundException(f"Run {run_id} not found")
        return run

    def _should_attach_traceback(self, key: str) -> bool:
        """Rate-limit tracebacks so polling clients cannot flood the log.

        The WARNING itself is emitted on every failure; only the (expensive and
        repetitive) traceback is throttled per failure kind.
        """

        now = time.monotonic()
        last = self._traceback_last_at.get(key)
        if last is not None and (now - last) < TRACEBACK_MIN_INTERVAL_S:
            return False
        self._traceback_last_at[key] = now
        return True

    def _real_mode_unavailable(
        self,
        *,
        event: str,
        run_id: str,
        equivalent: str,
        reason: str,
        cause: Optional[BaseException] = None,
        counters: Optional[dict[str, int]] = None,
    ) -> GeoException:
        """Log a real-mode analytics failure and build the explicit error to raise.

        Real mode never degrades into synthetic data: a failure here is a gate,
        not a log line. Only counters and correlation identifiers are logged;
        the exception class stays in the log and never reaches the public body
        (this endpoint is not admin-only).
        """

        error_class = type(cause).__name__ if cause is not None else "none"
        counters_repr = " ".join(
            f"{name}={value}" for name, value in sorted((counters or {}).items())
        )
        attach_traceback = cause is not None and self._should_attach_traceback(
            f"{event}:{reason}"
        )
        self._logger.warning(
            "%s run_id=%s equivalent=%s reason=%s error_class=%s%s",
            event,
            run_id,
            str(equivalent),
            reason,
            error_class,
            f" {counters_repr}" if counters_repr else "",
            exc_info=cause if attach_traceback else None,
        )
        return GeoException(
            REASON_MESSAGES.get(reason, REASON_MESSAGES["db_read_failed"]),
            # E010 (internal error) is the closest existing public code: this is a
            # server-side dependency failure the caller cannot fix by changing the
            # request. A dedicated availability code would mean editing the error
            # taxonomy in app/utils/error_codes.py, which belongs to program 004.
            code=ErrorCode.E010,
            details={
                "run_id": run_id,
                "equivalent": str(equivalent),
                "reason": reason,
            },
            status_code=503,
        )

    async def build_metrics(
        self,
        *,
        run_id: str,
        equivalent: str,
        from_ms: int,
        to_ms: int,
        step_ms: int,
    ) -> MetricsResponse:
        self._get_run(run_id)  # an unknown run is a 404
        # 054-A-16: stored rows carry the upper-cased code (the writers key them by the planned equivalent).
        equivalent = str(equivalent or "").strip().upper()

        if to_ms < from_ms:
            raise BadRequestException("to_ms must be >= from_ms")
        if step_ms <= 0:
            raise BadRequestException("step_ms must be > 0")

        points_count = (to_ms - from_ms) // step_ms + 1
        if points_count > 2000:
            raise BadRequestException("Too many points", details={"max_points": 2000})

        keys: list[tuple[MetricSeriesKey, Optional[str]]] = [
            ("success_rate", "%"),
            ("avg_route_length", "count"),
            ("total_debt", "amount"),
            ("clearing_volume", "amount"),
            ("bottlenecks_score", "%"),
            # Network cardinalities. The writer persists them as plain counts
            # (`storage.write_tick_metrics`, fed by
            # `RealTick.populate_per_eq_metric_values` in `tick.py`: number of active
            # scenario participants and number of edges in the equivalent), so
            # the unit is "count", not "amount".
            ("active_participants", "count"),
            ("active_trustlines", "count"),
        ]

        # Real mode is DB-only: persisted points or an explicit failure (042 S1: there is no other mode).
        if not self._db_enabled():
            raise self._real_mode_unavailable(
                event="simulator.metrics.real_mode_storage_disabled",
                run_id=run_id,
                equivalent=equivalent,
                reason="storage_disabled",
                counters={"points_count": points_count},
            )
        try:
            async with db_session.AsyncSessionLocal() as session:
                rows = (
                    await session.execute(
                        select(SimulatorRunMetric)
                        .where(
                            (SimulatorRunMetric.run_id == run_id)
                            & (SimulatorRunMetric.equivalent_code == str(equivalent))
                            & (SimulatorRunMetric.t_ms >= 0)
                            & (SimulatorRunMetric.t_ms <= int(to_ms))
                            & (SimulatorRunMetric.key.in_([k for (k, _u) in keys]))
                        )
                        .order_by(SimulatorRunMetric.key.asc(), SimulatorRunMetric.t_ms.asc())
                    )
                ).scalars().all()

            # 2026-08-20 / p007_t715: values stay Decimal from the column to
            # the wire. No float ever touches them here.
            # A stored NULL is a gap ("not measured" at that tick), not a row to skip: skipping it carried the
            # previous value over a tick that was never measured (028 F-028-7).
            by_key: dict[str, list[tuple[int, Optional[Decimal]]]] = {str(k): [] for (k, _u) in keys}
            for r in rows:
                by_key.setdefault(str(r.key), []).append((int(r.t_ms), r.value))

            # Resample persisted tick metrics to (from_ms..to_ms, step_ms) using carry-forward.
            # Carry-forward starts at the first real measurement: points before it
            # carry v=None ("not measured"), never an invented 0.0. After the first
            # measurement the last value is held until the next tick.
            series: list[MetricSeries] = []
            for key, unit in keys:
                timeline = by_key.get(str(key), [])
                idx = 0
                last_val: Optional[Decimal] = None
                pts: list[MetricPoint] = []
                for i in range(points_count):
                    t = int(from_ms + i * step_ms)
                    while idx < len(timeline) and int(timeline[idx][0]) <= t:
                        last_val = timeline[idx][1]
                        idx += 1
                    pts.append(
                        MetricPoint(t_ms=t, v=metric_point_value(last_val))
                    )
                series.append(MetricSeries(key=key, unit=unit, points=pts))

            return MetricsResponse(
                api_version=SIMULATOR_API_VERSION,
                run_id=run_id,
                equivalent=equivalent,
                from_ms=from_ms,
                to_ms=to_ms,
                step_ms=step_ms,
                series=series,
            )
        except Exception as exc:
            # Real mode never falls back to synthetic data (see spec F-007-1).
            raise self._real_mode_unavailable(
                event="simulator.metrics.real_mode_db_read_failed",
                run_id=run_id,
                equivalent=equivalent,
                reason="db_read_failed",
                cause=exc,
                counters={"points_count": points_count},
            ) from exc


    async def build_bottlenecks(
        self,
        *,
        run_id: str,
        equivalent: str,
        limit: int,
        min_score: Optional[float],
    ) -> BottlenecksResponse:
        self._get_run(run_id)  # an unknown run is a 404
        # 054-A-16: as in `build_metrics` - the stored code is upper-cased.
        equivalent = str(equivalent or "").strip().upper()

        # Real mode is DB-only: persisted bottlenecks or an explicit failure (042 S1: there is no other mode).
        if not self._db_enabled():
            raise self._real_mode_unavailable(
                event="simulator.bottlenecks.real_mode_storage_disabled",
                run_id=run_id,
                equivalent=equivalent,
                reason="storage_disabled",
            )
        try:
            async with db_session.AsyncSessionLocal() as session:
                latest = (
                    await session.execute(
                        select(func.max(SimulatorRunBottleneck.computed_at)).where(
                            (SimulatorRunBottleneck.run_id == run_id)
                            & (SimulatorRunBottleneck.equivalent_code == str(equivalent))
                        )
                    )
                ).scalar_one_or_none()
                # An empty table is a normal state at the start of a run:
                # it must yield an empty result, not UnboundLocalError.
                rows: list[SimulatorRunBottleneck] = []
                if latest is not None:
                    q = select(SimulatorRunBottleneck).where(
                        (SimulatorRunBottleneck.run_id == run_id)
                        & (SimulatorRunBottleneck.equivalent_code == str(equivalent))
                        & (SimulatorRunBottleneck.computed_at == latest)
                    )
                    rows = list((await session.execute(q)).scalars().all())

            items: list[BottleneckItem] = []
            for r in rows:
                if str(r.target_type) != "edge":
                    continue
                src = ""
                dst = ""
                try:
                    parts = str(r.target_id).split("->", 1)
                    src = parts[0]
                    dst = parts[1]
                except Exception:
                    continue
                if not src or not dst:
                    continue

                score = float(r.score)
                if min_score is not None and score < float(min_score):
                    continue

                details = r.details or {}
                label = details.get("label") if isinstance(details, dict) else None
                suggested_action = details.get("suggested_action") if isinstance(details, dict) else None

                items.append(
                    BottleneckItem(
                        target=BottleneckTargetEdge(kind="edge", **{"from": src}, to=dst),
                        score=score,
                        reason_code=str(r.reason_code),
                        label=label,
                        suggested_action=suggested_action,
                    )
                )

            items.sort(
                key=lambda x: (x.score, getattr(x.target, "from_", ""), getattr(x.target, "to", "")),
                reverse=True,
            )
            items = items[: int(limit)]
            return BottlenecksResponse(
                api_version=SIMULATOR_API_VERSION,
                run_id=run_id,
                equivalent=equivalent,
                items=items,
            )
        except Exception as exc:
            # Real mode never falls back to synthetic data (see spec F-007-1).
            raise self._real_mode_unavailable(
                event="simulator.bottlenecks.real_mode_db_read_failed",
                run_id=run_id,
                equivalent=equivalent,
                reason="db_read_failed",
                cause=exc,
                counters={"limit": int(limit)},
            ) from exc
