from __future__ import annotations

import hashlib
import logging
from typing import Optional

from sqlalchemy import func, select

import app.db.session as db_session
from app.db.models.simulator_storage import SimulatorRunBottleneck, SimulatorRunMetric
from app.core.simulator.models import RunRecord, ScenarioRecord
from app.schemas.simulator import (
    SIMULATOR_API_VERSION,
    BottleneckItem,
    BottleneckReasonCode,
    BottleneckTargetEdge,
    BottlenecksResponse,
    MetricPoint,
    MetricSeries,
    MetricSeriesKey,
    MetricsResponse,
)
from app.utils.exceptions import BadRequestException, NotFoundException
from app.core.simulator.scenario_equivalent import effective_equivalent


class MetricsBottlenecks:
    def __init__(
        self,
        *,
        lock,
        runs: dict[str, RunRecord],
        scenarios: dict[str, ScenarioRecord],
        utc_now,
        db_enabled,
        logger: logging.Logger,
    ) -> None:
        self._lock = lock
        self._runs = runs
        self._scenarios = scenarios
        self._utc_now = utc_now
        self._db_enabled = db_enabled
        self._logger = logger

    def _get_run(self, run_id: str) -> RunRecord:
        with self._lock:
            run = self._runs.get(run_id)
        if run is None:
            raise NotFoundException(f"Run {run_id} not found")
        return run

    def _get_scenario(self, scenario_id: str) -> ScenarioRecord:
        with self._lock:
            rec = self._scenarios.get(scenario_id)
        if rec is None:
            raise NotFoundException(f"Scenario {scenario_id} not found")
        return rec

    async def build_metrics(
        self,
        *,
        run_id: str,
        equivalent: str,
        from_ms: int,
        to_ms: int,
        step_ms: int,
    ) -> MetricsResponse:
        run = self._get_run(run_id)
        _ = self._get_scenario(run.scenario_id)

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
        ]

        # DB-first for real-mode: return persisted points if available.
        if self._db_enabled() and run.mode == "real":
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

                by_key: dict[str, list[tuple[int, float]]] = {str(k): [] for (k, _u) in keys}
                for r in rows:
                    if r.value is None:
                        continue
                    by_key.setdefault(str(r.key), []).append((int(r.t_ms), float(r.value)))

                # Resample persisted tick metrics to (from_ms..to_ms, step_ms) using carry-forward.
                # This guarantees MetricPoint.v is always numeric.
                series: list[MetricSeries] = []
                for key, unit in keys:
                    timeline = by_key.get(str(key), [])
                    idx = 0
                    last_val = 0.0
                    pts: list[MetricPoint] = []
                    for i in range(points_count):
                        t = int(from_ms + i * step_ms)
                        while idx < len(timeline) and int(timeline[idx][0]) <= t:
                            last_val = float(timeline[idx][1])
                            idx += 1
                        pts.append(MetricPoint(t_ms=t, v=float(last_val)))
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
            except Exception:
                # Fall back to synthetic below.
                self._logger.debug(
                    "simulator.metrics.db_query_failed_falling_back run_id=%s equivalent=%s",
                    run_id,
                    equivalent,
                    exc_info=True,
                )

        def v01(x: float) -> float:
            return max(0.0, min(1.0, x))

        def seed_f(*parts: str) -> float:
            h = hashlib.sha256("|".join(parts).encode("utf-8")).digest()
            # [0,1)
            return int.from_bytes(h[:8], "big") / 2**64

        base = seed_f(run_id, equivalent)
        intensity = max(0.0, min(1.0, float(run.intensity_percent) / 100.0))
        edges_n = len(((run._edges_by_equivalent or {}).get(equivalent) or []))

        series: list[MetricSeries] = []

        for key, unit in keys:
            points: list[MetricPoint] = []
            for i in range(points_count):
                t = from_ms + i * step_ms
                # Deterministic pseudo-dynamics; good enough for UI scaffolding.
                if key == "success_rate":
                    # Slightly lower at high intensity.
                    wobble = 0.08 * (0.5 - abs((seed_f(run_id, equivalent, str(t)) - 0.5)))
                    v = v01(0.93 - 0.18 * intensity + wobble)
                    v = v * 100.0
                elif key == "avg_route_length":
                    # Route length grows with graph size a bit.
                    base_len = 2.0 + min(4.0, max(0.0, edges_n / 50.0))
                    jitter = (seed_f(run_id, "arl", equivalent, str(t)) - 0.5) * 0.6
                    v = max(1.0, base_len + jitter)
                elif key == "total_debt":
                    # Monotonic-ish growth with occasional easing.
                    v = max(0.0, (t / 1000.0) * (0.2 + 0.8 * intensity) * (5.0 + 20.0 * base))
                elif key == "clearing_volume":
                    # Peaks around clearing window.
                    if t < 25_000:
                        v = 0.0
                    else:
                        phase = (t - 25_000) % 45_000
                        spike = 1.0 if 0 <= phase < 3_000 else 0.15
                        v = spike * (50.0 + 150.0 * intensity) * (0.8 + 0.4 * base)
                else:  # bottlenecks_score
                    # Higher score when sparse or at high intensity.
                    sparsity = 1.0 if edges_n == 0 else max(0.0, 1.0 - min(1.0, edges_n / 200.0))
                    wobble = (seed_f(run_id, "bns", equivalent, str(t)) - 0.5) * 0.12
                    v = v01(0.20 + 0.55 * sparsity + 0.20 * intensity + wobble) * 100.0

                points.append(MetricPoint(t_ms=t, v=float(v)))
            series.append(MetricSeries(key=key, unit=unit, points=points))

        return MetricsResponse(
            api_version=SIMULATOR_API_VERSION,
            run_id=run_id,
            equivalent=equivalent,
            from_ms=from_ms,
            to_ms=to_ms,
            step_ms=step_ms,
            series=series,
        )

    async def build_bottlenecks(
        self,
        *,
        run_id: str,
        equivalent: str,
        limit: int,
        min_score: Optional[float],
    ) -> BottlenecksResponse:
        run = self._get_run(run_id)
        scenario = getattr(run, "_scenario_raw", None) or self._get_scenario(run.scenario_id).raw

        # DB-first for real-mode: return persisted bottlenecks if available.
        if self._db_enabled() and run.mode == "real":
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
                    if latest is not None:
                        q = select(SimulatorRunBottleneck).where(
                            (SimulatorRunBottleneck.run_id == run_id)
                            & (SimulatorRunBottleneck.equivalent_code == str(equivalent))
                            & (SimulatorRunBottleneck.computed_at == latest)
                        )
                        rows = (await session.execute(q)).scalars().all()

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
            except Exception:
                # Fall back to synthetic below.
                pass

        # Read trustlines so we have access to limits.
        eq_norm = str(equivalent or "").strip().upper()
        tls = [
            tl
            for tl in (scenario.get("trustlines") or [])
            if effective_equivalent(scenario=scenario, payload=(tl or {})) == eq_norm
        ]

        def seed_f(*parts: str) -> float:
            h = hashlib.sha256("|".join(parts).encode("utf-8")).digest()
            return int.from_bytes(h[:8], "big") / 2**64

        # Use current sim time if available.
        t = int(run.sim_time_ms or 0)
        intensity = max(0.0, min(1.0, float(run.intensity_percent) / 100.0))

        items: list[BottleneckItem] = []

        for tl in tls:
            src = str(tl.get("from") or "").strip()
            dst = str(tl.get("to") or "").strip()
            if not src or not dst:
                continue
            limit_raw = tl.get("limit")
            try:
                limit_value = float(limit_raw)
            except Exception:
                limit_value = 0.0

            # Synthetic used fraction: increases with intensity and time.
            wobble = abs(seed_f(run_id, equivalent, src, dst, str(t)) - 0.5) * 2.0
            used_ratio = max(0.0, min(1.0, 0.15 + 0.65 * wobble + 0.15 * intensity))
            available_ratio = 1.0 - used_ratio

            # Score [0..1] where higher means more problematic.
            score = max(0.0, min(1.0, 1.0 - available_ratio))
            if min_score is not None and score < float(min_score):
                continue

            reason: BottleneckReasonCode = "LOW_AVAILABLE" if available_ratio < 0.25 else "HIGH_USED"

            label = None
            suggested_action = None
            if reason == "LOW_AVAILABLE":
                label = "Low available capacity"
                suggested_action = "Increase trust limit or rebalance via clearing"
            else:
                label = "High utilization"
                suggested_action = "Consider clearing or adding alternative routes"

            items.append(
                BottleneckItem(
                    target=BottleneckTargetEdge(kind="edge", **{"from": src}, to=dst),
                    score=float(score),
                    reason_code=reason,
                    label=label,
                    suggested_action=suggested_action,
                )
            )

        items.sort(key=lambda x: x.score, reverse=True)
        items = items[: int(limit)]

        # Keep writing synthetic bottlenecks only for non-real mode (UI scaffolding);
        # real-mode is DB-first and writes from the runner.
        if self._db_enabled() and run.mode != "real":
            try:
                computed_at = self._utc_now()
                async with db_session.AsyncSessionLocal() as session:
                    rows: list[SimulatorRunBottleneck] = []
                    for it in items:
                        target_id = f"{it.target.from_}->{it.target.to}"  # type: ignore[attr-defined]
                        rows.append(
                            SimulatorRunBottleneck(
                                run_id=run_id,
                                equivalent_code=str(equivalent),
                                computed_at=computed_at,
                                target_type="edge",
                                target_id=target_id,
                                score=float(it.score),
                                reason_code=str(it.reason_code),
                                details={
                                    "label": it.label,
                                    "suggested_action": it.suggested_action,
                                },
                            )
                        )
                    session.add_all(rows)
                    await session.commit()
            except Exception:
                pass

        return BottlenecksResponse(
            api_version=SIMULATOR_API_VERSION,
            run_id=run_id,
            equivalent=equivalent,
            items=items,
        )
