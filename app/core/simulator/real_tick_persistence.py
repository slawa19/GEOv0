from __future__ import annotations

import logging
import time
from typing import Any, Callable

import app.db.session as db_session
import app.core.simulator.storage as simulator_storage
from app.core.simulator.artifacts import ArtifactsManager
from app.core.simulator.models import RunRecord


class RealTickPersistence:
    def __init__(
        self,
        *,
        lock,
        artifacts: ArtifactsManager,
        utc_now,
        db_enabled: Callable[[], bool],
        logger: logging.Logger,
        real_db_metrics_every_n_ticks: int,
        real_db_bottlenecks_every_n_ticks: int,
        real_last_tick_write_every_ms: int,
        real_artifacts_sync_every_ms: int,
    ) -> None:
        self._lock = lock
        self._artifacts = artifacts
        self._utc_now = utc_now
        self._db_enabled = db_enabled
        self._logger = logger

        self._real_db_metrics_every_n_ticks = int(real_db_metrics_every_n_ticks)
        self._real_db_bottlenecks_every_n_ticks = int(real_db_bottlenecks_every_n_ticks)
        self._real_last_tick_write_every_ms = int(real_last_tick_write_every_ms)
        self._real_artifacts_sync_every_ms = int(real_artifacts_sync_every_ms)

    async def persist_tick_tail(
        self,
        *,
        session: Any,
        run: RunRecord,
        equivalents: list[str],
        tick_t0: float,
        planned_len: int,
        committed: int,
        rejected: int,
        errors: int,
        timeouts: int,
        per_eq: dict[str, Any],
        per_eq_metric_values: dict[str, dict[str, float]],
        per_eq_edge_stats: dict[str, Any],
    ) -> None:
        computed_at = self._utc_now()
        with self._lock:
            run._real_last_tick_storage_payload = {
                "run_id": str(run.run_id),
                "tick_index": int(run.tick_index),
                "t_ms": int(run.sim_time_ms),
                "per_equivalent": per_eq,
                "metric_values_by_eq": per_eq_metric_values,
                "bottlenecks": {
                    "computed_at": computed_at,
                    "equivalents": list(equivalents),
                    "edge_stats_by_eq": per_eq_edge_stats,
                },
            }

        metrics_every_n = int(self._real_db_metrics_every_n_ticks)
        bottlenecks_every_n = int(self._real_db_bottlenecks_every_n_ticks)

        should_write_metrics = metrics_every_n <= 1 or (
            int(run.tick_index) % int(metrics_every_n) == 0
        )
        should_write_bottlenecks = bottlenecks_every_n <= 1 or (
            int(run.tick_index) % int(bottlenecks_every_n) == 0
        )

        if should_write_metrics:
            await simulator_storage.write_tick_metrics(
                run_id=run.run_id,
                t_ms=int(run.sim_time_ms),
                per_equivalent=per_eq,
                metric_values_by_eq=per_eq_metric_values,
                session=session,
                commit=False,
            )

        if should_write_bottlenecks and self._db_enabled():
            for eq in equivalents:
                await simulator_storage.write_tick_bottlenecks(
                    run_id=run.run_id,
                    equivalent=str(eq),
                    computed_at=computed_at,
                    edge_stats=per_eq_edge_stats.get(str(eq), {}),
                    session=session,
                    limit=50,
                    commit=False,
                )

        if should_write_metrics or should_write_bottlenecks:
            with self._lock:
                run._real_last_tick_storage_flushed_tick = int(run.tick_index)

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

        now_ms = int(time.time() * 1000)
        tick_write_every_ms = int(self._real_last_tick_write_every_ms)
        artifacts_sync_every_ms = int(self._real_artifacts_sync_every_ms)

        if tick_write_every_ms > 0 and (
            now_ms - int(run._artifact_last_tick_written_at_ms or 0)
        ) >= tick_write_every_ms:
            self._artifacts.write_real_tick_artifact(
                run,
                {
                    "tick_index": run.tick_index,
                    "sim_time_ms": run.sim_time_ms,
                    "budget": int(planned_len),
                    "committed": int(committed),
                    "rejected": int(rejected),
                    "errors": int(errors),
                    "timeouts": int(timeouts),
                },
            )
            run._artifact_last_tick_written_at_ms = now_ms

        if artifacts_sync_every_ms > 0 and (
            now_ms - int(run._artifact_last_sync_at_ms or 0)
        ) >= artifacts_sync_every_ms:
            await simulator_storage.sync_artifacts(run)
            run._artifact_last_sync_at_ms = now_ms

    async def flush_pending_storage(self, *, run_id: str, run: RunRecord) -> None:
        """Best-effort flush of the last computed tick metrics/bottlenecks.

        Used on stop/error to avoid losing the last batch when DB writes are throttled.
        """

        if not self._db_enabled():
            return

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
                    if self._db_enabled() and isinstance(payload.get("bottlenecks"), dict):
                        computed_at = (
                            payload.get("bottlenecks", {}).get("computed_at")
                            or self._utc_now()
                        )
                        edge_stats_by_eq = (
                            payload.get("bottlenecks", {}).get("edge_stats_by_eq") or {}
                        )
                        equivalents = payload.get("bottlenecks", {}).get("equivalents") or []
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
