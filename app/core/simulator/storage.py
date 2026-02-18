from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import delete, select, update as sql_update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

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
                sim_time_ms=(
                    int(run.sim_time_ms) if run.sim_time_ms is not None else None
                ),
                tick_index=int(run.tick_index) if run.tick_index is not None else None,
                seed=int(run.seed) if run.seed is not None else None,
                intensity_percent=(
                    int(run.intensity_percent)
                    if run.intensity_percent is not None
                    else None
                ),
                ops_sec=float(run.ops_sec) if run.ops_sec is not None else None,
                queue_depth=(
                    int(run.queue_depth) if run.queue_depth is not None else None
                ),
                errors_total=(
                    int(run.errors_total) if run.errors_total is not None else None
                ),
                last_event_type=run.last_event_type,
                current_phase=run.current_phase,
                last_error=run.last_error,
                owner_id=run.owner_id if run.owner_id else None,  # empty string → NULL intentionally
                owner_kind=run.owner_kind if run.owner_kind else None,  # empty string → NULL intentionally
            )
            try:
                await session.merge(row)
                await session.commit()
            except Exception:
                await session.rollback()
                raise
    except Exception:
        logger.exception(
            "simulator.storage.upsert_run_failed run_id=%s", getattr(run, "run_id", "")
        )
        return


async def sync_artifacts(run: RunRecord) -> None:
    if not db_enabled():
        return
    base = run.artifacts_dir
    if base is None or not base.exists():
        return

    try:
        sha_max_bytes = int(
            os.getenv("SIMULATOR_ARTIFACT_SHA_MAX_BYTES", "524288") or "524288"
        )

        items: list[dict[str, object]] = []
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
                {
                    "run_id": str(run.run_id),
                    "name": str(p.name),
                    "content_type": artifact_content_type(p.name),
                    "size_bytes": size,
                    "sha256": sha,
                    "storage_url": url,
                }
            )

        async with db.AsyncSessionLocal() as session:
            # Diff-based sync: delete missing + upsert changed/new.
            existing_rows = (
                await session.execute(
                    select(
                        SimulatorRunArtifact.name,
                        SimulatorRunArtifact.content_type,
                        SimulatorRunArtifact.size_bytes,
                        SimulatorRunArtifact.sha256,
                        SimulatorRunArtifact.storage_url,
                    ).where(SimulatorRunArtifact.run_id == run.run_id)
                )
            ).all()

            existing: dict[str, tuple[object, object, object, object]] = {
                str(name): (content_type, size_bytes, sha256, storage_url)
                for (name, content_type, size_bytes, sha256, storage_url) in existing_rows
            }

            names_now: set[str] = set()
            rows_to_upsert: list[dict[str, object]] = []
            for row in items:
                name = str(row.get("name") or "")
                names_now.add(name)
                prev = existing.get(name)
                cur = (
                    row.get("content_type"),
                    row.get("size_bytes"),
                    row.get("sha256"),
                    row.get("storage_url"),
                )
                if prev == cur:
                    continue
                rows_to_upsert.append(row)

            names_to_delete = [n for n in existing.keys() if n not in names_now]
            if names_to_delete:
                await session.execute(
                    delete(SimulatorRunArtifact).where(
                        SimulatorRunArtifact.run_id == run.run_id,
                        SimulatorRunArtifact.name.in_(names_to_delete),
                    )
                )

            if rows_to_upsert:
                bind = None
                try:
                    bind = session.get_bind()
                except Exception:
                    bind = getattr(session, "bind", None)

                dialect_name = None
                try:
                    dialect_name = bind.dialect.name if bind is not None else None
                except Exception:
                    dialect_name = None

                if dialect_name == "sqlite":
                    insert_fn = sqlite_insert
                elif dialect_name in {"postgresql", "postgres"}:
                    insert_fn = pg_insert
                else:
                    raise RuntimeError(
                        f"Unsupported SQL dialect for simulator_run_artifacts upsert: {dialect_name!r}"
                    )

                table = SimulatorRunArtifact.__table__
                stmt = insert_fn(table)
                stmt = stmt.on_conflict_do_update(
                    index_elements=[table.c.run_id, table.c.name],
                    set_={
                        table.c.content_type: stmt.excluded.content_type,
                        table.c.size_bytes: stmt.excluded.size_bytes,
                        table.c.sha256: stmt.excluded.sha256,
                        table.c.storage_url: stmt.excluded.storage_url,
                    },
                )
                await session.execute(stmt, rows_to_upsert)

            await session.commit()
    except Exception:
        logger.exception(
            "simulator.storage.sync_artifacts_failed run_id=%s",
            getattr(run, "run_id", ""),
        )
        return


async def write_tick_metrics(
    *,
    run_id: str,
    t_ms: int,
    per_equivalent: dict[str, dict[str, int]],
    metric_values_by_eq: Optional[dict[str, dict[str, float]]] = None,
    session=None,
    commit: bool = True,
) -> None:
    if not db_enabled():
        return

    try:

        async def _write(s) -> None:
            bind = None
            try:
                bind = s.get_bind()
            except Exception:
                bind = getattr(s, "bind", None)

            dialect_name = None
            try:
                dialect_name = bind.dialect.name if bind is not None else None
            except Exception:
                dialect_name = None

            if dialect_name == "sqlite":
                insert_fn = sqlite_insert
            elif dialect_name in {"postgresql", "postgres"}:
                insert_fn = pg_insert
            else:
                raise RuntimeError(
                    f"Unsupported SQL dialect for simulator_run_metrics upsert: {dialect_name!r}"
                )

            rows: list[dict[str, object]] = []
            for eq, counters in (per_equivalent or {}).items():
                committed = int(counters.get("committed", 0))
                rejected = int(counters.get("rejected", 0))
                errors = int(counters.get("errors", 0))
                timeouts = int(counters.get("timeouts", 0))

                denom = committed + rejected
                success_rate = (committed / denom) * 100.0 if denom > 0 else 0.0
                attempts = committed + rejected + errors
                bottlenecks_score = (
                    ((errors + timeouts) / attempts) * 100.0 if attempts > 0 else 0.0
                )

                mv = (metric_values_by_eq or {}).get(str(eq), {})
                avg_route_length = float(mv.get("avg_route_length", 0.0) or 0.0)
                total_debt = float(mv.get("total_debt", 0.0) or 0.0)
                clearing_volume = float(mv.get("clearing_volume", 0.0) or 0.0)
                active_participants = float(mv.get("active_participants", 0.0) or 0.0)
                active_trustlines = float(mv.get("active_trustlines", 0.0) or 0.0)

                eq_code = str(eq)
                rows.extend(
                    [
                        {
                            "run_id": str(run_id),
                            "equivalent_code": eq_code,
                            "key": "success_rate",
                            "t_ms": int(t_ms),
                            "value": float(success_rate),
                        },
                        {
                            "run_id": str(run_id),
                            "equivalent_code": eq_code,
                            "key": "bottlenecks_score",
                            "t_ms": int(t_ms),
                            "value": float(bottlenecks_score),
                        },
                        {
                            "run_id": str(run_id),
                            "equivalent_code": eq_code,
                            "key": "avg_route_length",
                            "t_ms": int(t_ms),
                            "value": float(avg_route_length),
                        },
                        {
                            "run_id": str(run_id),
                            "equivalent_code": eq_code,
                            "key": "total_debt",
                            "t_ms": int(t_ms),
                            "value": float(total_debt),
                        },
                        {
                            "run_id": str(run_id),
                            "equivalent_code": eq_code,
                            "key": "clearing_volume",
                            "t_ms": int(t_ms),
                            "value": float(clearing_volume),
                        },
                        {
                            "run_id": str(run_id),
                            "equivalent_code": eq_code,
                            "key": "active_participants",
                            "t_ms": int(t_ms),
                            "value": float(active_participants),
                        },
                        {
                            "run_id": str(run_id),
                            "equivalent_code": eq_code,
                            "key": "active_trustlines",
                            "t_ms": int(t_ms),
                            "value": float(active_trustlines),
                        },
                    ]
                )

            if not rows:
                return

            table = SimulatorRunMetric.__table__
            stmt = insert_fn(table)
            stmt = stmt.on_conflict_do_update(
                index_elements=[
                    table.c.run_id,
                    table.c.equivalent_code,
                    table.c.key,
                    table.c.t_ms,
                ],
                set_={table.c.value: stmt.excluded.value},
            )
            await s.execute(stmt, rows)

            if commit:
                await s.commit()
            else:
                await s.flush()

        if session is None:
            async with db.AsyncSessionLocal() as s:
                try:
                    await _write(s)
                except Exception:
                    try:
                        await s.rollback()
                    except Exception:
                        pass
                    raise
        else:
            try:
                await _write(session)
            except Exception:
                try:
                    await session.rollback()
                except Exception:
                    pass
                raise
    except Exception:
        logger.exception(
            "simulator.storage.write_tick_metrics_failed run_id=%s t_ms=%s",
            str(run_id),
            int(t_ms),
        )
        return


async def write_tick_bottlenecks(
    *,
    run_id: str,
    equivalent: str,
    computed_at: datetime,
    edge_stats: dict[tuple[str, str], dict[str, int]],
    session,
    limit: int = 50,
    commit: bool = True,
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
        if commit:
            await session.commit()
        else:
            await session.flush()
    except Exception:
        logger.exception(
            "simulator.storage.write_tick_bottlenecks_failed run_id=%s equivalent=%s",
            str(run_id),
            str(equivalent),
        )
        try:
            await session.rollback()
        except Exception:
            pass
        return


async def reconcile_stale_runs() -> int:
    """Mark runs stuck in non-terminal state as error (server restart recovery).

    Called once at startup (§12 Recovery reconciliation).  Any run still in
    'running', 'paused', or 'stopping' state has no active process after a
    restart, so we transition it to 'error' with last_error={"reason":
    "server_restart"}.

    Returns the number of reconciled runs (0 if DB is disabled or none found).
    This is best-effort: DB errors are logged and swallowed so the server still
    starts up cleanly.
    """
    if not db_enabled():
        return 0

    stale_states = ("running", "paused", "stopping")
    try:
        now = datetime.now(timezone.utc)
        async with db.AsyncSessionLocal() as session:
            result = await session.execute(
                sql_update(SimulatorRun)
                .where(SimulatorRun.state.in_(stale_states))
                .values(
                    state="error",
                    last_error={"reason": "server_restart"},
                    stopped_at=now,
                )
                .execution_options(synchronize_session=False)
            )
            count: int = result.rowcount if result.rowcount is not None else 0
            await session.commit()

        if count:
            logger.warning(
                "simulator.reconcile stale_runs=%d — marked as error (reason: server_restart)",
                count,
            )
        else:
            logger.info("simulator.reconcile no stale runs found at startup")
        return count
    except Exception:
        logger.exception("simulator.reconcile_stale_runs failed (best-effort, server will still start)")
        return 0
