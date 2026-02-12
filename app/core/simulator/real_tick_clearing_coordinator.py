from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable

from app.core.simulator.models import RunRecord


class RealTickClearingCoordinator:
    def __init__(
        self,
        *,
        lock,
        logger: logging.Logger,
        clearing_every_n_ticks: int,
        real_clearing_time_budget_ms: int,
    ) -> None:
        self._lock = lock
        self._logger = logger
        self._clearing_every_n_ticks = int(clearing_every_n_ticks)
        self._real_clearing_time_budget_ms = int(real_clearing_time_budget_ms)

    async def maybe_run_clearing(
        self,
        *,
        session: Any,
        run_id: str,
        run: RunRecord,
        equivalents: list[str],
        planned_len: int,
        tick_t0: float,
        clearing_enabled: bool,
        safe_int_env: Callable[[str, int], int],
        run_clearing: Callable[[], Awaitable[dict[str, float]]],
    ) -> dict[str, float]:
        clearing_volume_by_eq: dict[str, float] = {str(eq): 0.0 for eq in equivalents}

        if self._clearing_every_n_ticks <= 0:
            return clearing_volume_by_eq
        if not clearing_enabled:
            return clearing_volume_by_eq

        tick_index = int(run.tick_index)
        if tick_index % int(self._clearing_every_n_ticks) != 0:
            return clearing_volume_by_eq

        # Commit payments BEFORE clearing to release the DB write lock.
        # Clearing uses an isolated session (separate connection) that needs
        # write access. On SQLite (single-writer) the clearing session would
        # deadlock if the parent session still holds an uncommitted transaction.
        try:
            commit_t0 = time.monotonic()
            await session.commit()
            commit_ms = (time.monotonic() - commit_t0) * 1000.0
            if commit_ms > 500.0:
                self._logger.warning(
                    "simulator.real.tick_commit_slow run_id=%s tick=%s commit_ms=%s total_tick_ms=%s",
                    str(run.run_id),
                    tick_index,
                    int(commit_ms),
                    int((time.monotonic() - tick_t0) * 1000.0),
                )
        except Exception:
            await session.rollback()
            raise

        self._logger.warning(
            "simulator.real.tick_clearing_enter run_id=%s tick=%s eqs=%s planned=%s",
            str(run.run_id),
            tick_index,
            ",".join([str(x) for x in (equivalents or [])]),
            int(planned_len),
        )

        clearing_t0 = time.monotonic()

        # Hard timeout: clearing must not block the heartbeat loop indefinitely.
        # Note: asyncio.wait_for() relies on cancellation being delivered. Some
        # DB awaits (or driver edge cases) may delay cancellation, so we run
        # clearing in a separate task and time out without waiting for teardown.
        clearing_hard_timeout_sec = max(
            2.0,
            float(self._real_clearing_time_budget_ms) / 1000.0 * 4.0,
        )
        env_timeout_cap = float(safe_int_env("SIMULATOR_REAL_CLEARING_HARD_TIMEOUT_SEC", 8))
        if env_timeout_cap > 0:
            clearing_hard_timeout_sec = min(clearing_hard_timeout_sec, env_timeout_cap)
        clearing_hard_timeout_sec = max(0.1, float(clearing_hard_timeout_sec))

        clearing_task: asyncio.Task[dict[str, float]] | None = None
        with self._lock:
            existing = run._real_clearing_task
            if existing is not None and existing.done():
                run._real_clearing_task = None
                existing = None
            clearing_task = existing

        if clearing_task is None:
            clearing_task = asyncio.create_task(run_clearing())
            with self._lock:
                run._real_clearing_task = clearing_task
        else:
            self._logger.warning(
                "simulator.real.tick_clearing_already_running run_id=%s tick=%s",
                str(run.run_id),
                tick_index,
            )

        try:
            clearing_volume_by_eq = await asyncio.wait_for(
                asyncio.shield(clearing_task),
                timeout=clearing_hard_timeout_sec,
            )
            with self._lock:
                if run._real_clearing_task is clearing_task:
                    run._real_clearing_task = None
        except asyncio.TimeoutError:
            self._logger.warning(
                "simulator.real.tick_clearing_hard_timeout run_id=%s tick=%s timeout_sec=%s",
                str(run.run_id),
                tick_index,
                clearing_hard_timeout_sec,
            )
            # Clearing timed out — proceed with the rest of the tick.
            # Best-effort: request cancellation, but do not await it here.
            # We keep the task reference to avoid overlapping clearing runs.
            try:
                clearing_task.cancel()
            except Exception:
                pass
            with self._lock:
                run.current_phase = None
        except Exception:
            # Clearing is best-effort; do not fail the whole tick.
            with self._lock:
                if run._real_clearing_task is clearing_task:
                    run._real_clearing_task = None
            self._logger.warning(
                "simulator.real.tick_clearing_failed run_id=%s tick=%s",
                str(run.run_id),
                tick_index,
                exc_info=True,
            )

        self._logger.warning(
            "simulator.real.tick_clearing_done run_id=%s tick=%s elapsed_ms=%s",
            str(run.run_id),
            tick_index,
            int((time.monotonic() - clearing_t0) * 1000.0),
        )

        return clearing_volume_by_eq
