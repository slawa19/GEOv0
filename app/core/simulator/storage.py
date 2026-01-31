from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Optional

from sqlalchemy import delete

import app.db.session as db
from app.config import settings
from app.core.simulator.helpers import artifact_content_type, artifact_sha256
from app.core.simulator.models import RunRecord
from app.db.models.simulator_storage import (
    SimulatorRun,
    SimulatorRunArtifact,
    SimulatorRunBottleneck,
    SimulatorRunMetric,
)

logger = logging.getLogger(__name__)


def db_enabled() -> bool:
    return bool(getattr(settings, "SIMULATOR_DB_ENABLED", False))


def _validate_run_for_storage(run: RunRecord) -> None:
    if not str(getattr(run, "run_id", "") or "").strip():
        raise ValueError("run_id is required")
    if not str(getattr(run, "scenario_id", "") or "").strip():
        raise ValueError("scenario_id is required")
    if not str(getattr(run, "mode", "") or "").strip():
        raise ValueError("mode is required")
    if not str(getattr(run, "state", "") or "").strip():
        raise ValueError("state is required")


async def upsert_run(run: RunRecord) -> None:
    if not db_enabled():
        return
    try:
        _validate_run_for_storage(run)
        async with db.AsyncSessionLocal() as session:
            row = SimulatorRun(
                run_id=run.run_id,
                scenario_id=run.scenario_id,
                mode=str(run.mode),
                state=str(run.state),
                started_at=run.started_at,
                stopped_at=run.stopped_at,
                sim_time_ms=int(run.sim_time_ms) if run.sim_time_ms is not None else None,
                tick_index=int(run.tick_index) if run.tick_index is not None else None,
                seed=int(run.seed) if run.seed is not None else None,
                intensity_percent=int(run.intensity_percent) if run.intensity_percent is not None else None,
                ops_sec=float(run.ops_sec) if run.ops_sec is not None else None,
                queue_depth=int(run.queue_depth) if run.queue_depth is not None else None,
                errors_total=int(run.errors_total) if run.errors_total is not None else None,
                last_event_type=run.last_event_type,
                current_phase=run.current_phase,
                last_error=run.last_error,
            )
            await session.merge(row)
            await session.commit()
    except Exception:
        logger.exception("simulator.storage.upsert_run_failed run_id=%s", getattr(run, "run_id", ""))
        return


async def sync_artifacts(run: RunRecord) -> None:
    if not db_enabled():
        return
    base = run.artifacts_dir
    if base is None or not base.exists():
        return

    try:
        sha_max_bytes = int(os.getenv("SIMULATOR_ARTIFACT_SHA_MAX_BYTES", "524288") or "524288")

        items: list[SimulatorRunArtifact] = []
        for p in sorted(base.iterdir()):
            if not p.is_file():
                continue
            url = f"/api/v1/simulator/runs/{run.run_id}/artifacts/{p.name}"
            sha = None
            size = None
            try:
                size = int(p.stat().st_size)
                if size <= sha_max_bytes:
                    sha = artifact_sha256(p)
            except Exception:
                logger.exception(
                    "simulator.storage.artifact_hash_failed run_id=%s name=%s",
                    getattr(run, "run_id", ""),
                    getattr(p, "name", ""),
                )
            items.append(
                SimulatorRunArtifact(
                    run_id=run.run_id,
                    name=p.name,
                    content_type=artifact_content_type(p.name),
                    size_bytes=size,
                    sha256=sha,
                    storage_url=url,
                )
            )

        async with db.AsyncSessionLocal() as session:
            await session.execute(delete(SimulatorRunArtifact).where(SimulatorRunArtifact.run_id == run.run_id))
            session.add_all(items)
            await session.commit()
    except Exception:
        logger.exception("simulator.storage.sync_artifacts_failed run_id=%s", getattr(run, "run_id", ""))
        return


async def write_tick_metrics(
    *,
    run_id: str,
    t_ms: int,
    per_equivalent: dict[str, dict[str, int]],
    metric_values_by_eq: Optional[dict[str, dict[str, float]]] = None,
    session=None,
) -> None:
    if not db_enabled():
        return

    try:
        async def _write(s) -> None:
            for eq, counters in per_equivalent.items():
                committed = int(counters.get("committed", 0))
                rejected = int(counters.get("rejected", 0))
                errors = int(counters.get("errors", 0))
                timeouts = int(counters.get("timeouts", 0))

                denom = committed + rejected
                success_rate = (committed / denom) * 100.0 if denom > 0 else 0.0
                attempts = committed + rejected + errors
                bottlenecks_score = ((errors + timeouts) / attempts) * 100.0 if attempts > 0 else 0.0

                mv = (metric_values_by_eq or {}).get(str(eq), {})
                avg_route_length = float(mv.get("avg_route_length", 0.0) or 0.0)
                total_debt = float(mv.get("total_debt", 0.0) or 0.0)
                clearing_volume = float(mv.get("clearing_volume", 0.0) or 0.0)

                await s.merge(
                    SimulatorRunMetric(
                        run_id=run_id,
                        equivalent_code=str(eq),
                        key="success_rate",
                        t_ms=int(t_ms),
                        value=float(success_rate),
                    )
                )
                await s.merge(
                    SimulatorRunMetric(
                        run_id=run_id,
                        equivalent_code=str(eq),
                        key="bottlenecks_score",
                        t_ms=int(t_ms),
                        value=float(bottlenecks_score),
                    )
                )
                await s.merge(
                    SimulatorRunMetric(
                        run_id=run_id,
                        equivalent_code=str(eq),
                        key="avg_route_length",
                        t_ms=int(t_ms),
                        value=float(avg_route_length),
                    )
                )
                await s.merge(
                    SimulatorRunMetric(
                        run_id=run_id,
                        equivalent_code=str(eq),
                        key="total_debt",
                        t_ms=int(t_ms),
                        value=float(total_debt),
                    )
                )
                await s.merge(
                    SimulatorRunMetric(
                        run_id=run_id,
                        equivalent_code=str(eq),
                        key="clearing_volume",
                        t_ms=int(t_ms),
                        value=float(clearing_volume),
                    )
                )
            await s.commit()

        if session is None:
            async with db.AsyncSessionLocal() as s:
                await _write(s)
        else:
            await _write(session)
    except Exception:
        return


async def write_tick_bottlenecks(
    *,
    run_id: str,
    equivalent: str,
    computed_at: datetime,
    edge_stats: dict[tuple[str, str], dict[str, int]],
    session,
    limit: int = 50,
) -> None:
    if not db_enabled():
        return

    try:
        items: list[SimulatorRunBottleneck] = []
        for (src, dst), st in edge_stats.items():
            attempts = int(st.get("attempts", 0))
            if attempts <= 0:
                continue
            errors = int(st.get("errors", 0))
            timeouts = int(st.get("timeouts", 0))
            rejected = int(st.get("rejected", 0))
            committed = int(st.get("committed", 0))

            bad = errors + timeouts + rejected
            score = max(0.0, min(1.0, float(bad) / float(attempts)))
            if score <= 0:
                continue

            if timeouts > 0 and float(timeouts) / float(attempts) >= 0.2:
                reason: str = "TOO_MANY_TIMEOUTS"
                label = "Too many timeouts"
                suggested = "Reduce load or increase routing timeouts"
            elif rejected > 0 or errors > 0:
                reason = "FREQUENT_ABORTS"
                label = "Frequent failures"
                suggested = "Increase trust limits, add alternative routes, or clear"
            else:
                reason = "HIGH_USED"
                label = "High utilization"
                suggested = "Consider clearing or adding alternative routes"

            items.append(
                SimulatorRunBottleneck(
                    run_id=run_id,
                    equivalent_code=str(equivalent),
                    computed_at=computed_at,
                    target_type="edge",
                    target_id=f"{src}->{dst}",
                    score=float(score),
                    reason_code=str(reason),
                    details={
                        "attempts": attempts,
                        "committed": committed,
                        "rejected": rejected,
                        "errors": errors,
                        "timeouts": timeouts,
                        "label": label,
                        "suggested_action": suggested,
                    },
                )
            )

        items.sort(key=lambda r: (float(r.score), str(r.target_id)), reverse=True)
        items = items[: int(limit)]
        if not items:
            return

        session.add_all(items)
        await session.commit()
    except Exception:
        try:
            await session.rollback()
        except Exception:
            pass
        return
