from __future__ import annotations

import asyncio
import logging
import time
from decimal import Decimal
from typing import Any, Awaitable, Callable

from app.core.simulator.commit_resolution import resolve_commit_under_cancellation
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

    def _apply_payment_effects(self, payments_result: Any | None) -> None:
        apply_effects = getattr(payments_result, "apply_deferred_effects", None)
        if callable(apply_effects):
            try:
                apply_effects()
            except Exception:
                self._logger.warning(
                    "simulator.real.payment_post_commit_callback_failed",
                    exc_info=True,
                )

    def _apply_rollback_observations(self, payments_result: Any | None) -> None:
        apply_effects = getattr(payments_result, "apply_rollback_observations", None)
        if callable(apply_effects):
            try:
                apply_effects()
            except Exception:
                self._logger.warning(
                    "simulator.real.payment_rollback_callback_failed",
                    exc_info=True,
                )

    def _apply_unknown_observations(self, payments_result: Any | None) -> None:
        apply_effects = getattr(
            payments_result,
            "apply_unknown_transaction_observations",
            None,
        )
        if callable(apply_effects):
            try:
                apply_effects()
            except Exception:
                self._logger.warning(
                    "simulator.real.payment_unknown_callback_failed",
                    exc_info=True,
                )

    async def _commit_and_resolve(
        self,
        session: Any,
        payments_result: Any | None,
    ) -> None:
        await resolve_commit_under_cancellation(
            commit=session.commit,
            rollback=session.rollback,
            on_commit=lambda: self._apply_payment_effects(payments_result),
            on_rollback=lambda: self._apply_rollback_observations(payments_result),
            on_unknown=lambda: self._apply_unknown_observations(payments_result),
            logger=self._logger,
        )

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
        run_clearing: Callable[[], Awaitable[dict[str, Decimal]]],
        payments_result: Any | None = None,
    ) -> dict[str, Decimal]:
        # 2026-08-20 / p007_t715: the cleared volume is money and feeds the
        # `clearing_volume` metric series, so it stays Decimal across every
        # branch here - including the early returns, which must not hand the
        # caller a float zero.
        clearing_volume_by_eq: dict[str, Decimal] = {
            str(eq): Decimal("0") for eq in equivalents
        }

        if not clearing_enabled:
            return clearing_volume_by_eq

        # Static cadence - the only clearing policy since programme 021 stage 3 removed the adaptive mode
        # (owner decision 2026-09-21, `specs/021-simulator-as-domain-client/spec.md`, "Решения" item 1).
        if self._clearing_every_n_ticks <= 0:
            return clearing_volume_by_eq

        tick_index = int(run.tick_index)
        if tick_index % int(self._clearing_every_n_ticks) != 0:
            return clearing_volume_by_eq

        return await self._execute_clearing_with_timeout(
            session=session,
            run_id=run_id,
            run=run,
            equivalents=equivalents,
            planned_len=planned_len,
            tick_t0=tick_t0,
            safe_int_env=safe_int_env,
            run_clearing=run_clearing,
            payments_result=payments_result,
        )

    def compute_static_clearing_hard_timeout_sec(
        self,
        *,
        safe_int_env: Callable[[str, int], int],
    ) -> float:
        """Compute the hard timeout used by the static clearing branch.

        Exposed so the orchestrator can apply a bounded "grace" wait for
        a pending background clearing task before starting the next tick.
        """
        clearing_hard_timeout_sec = max(
            2.0,
            float(self._real_clearing_time_budget_ms) / 1000.0 * 4.0,
        )
        env_timeout_cap = float(safe_int_env("SIMULATOR_REAL_CLEARING_HARD_TIMEOUT_SEC", 8))
        if env_timeout_cap > 0:
            clearing_hard_timeout_sec = min(clearing_hard_timeout_sec, env_timeout_cap)
        clearing_hard_timeout_sec = max(0.1, float(clearing_hard_timeout_sec))
        return float(clearing_hard_timeout_sec)

    # ── Shared: execute clearing with task + hard timeout ─────────

    async def _execute_clearing_with_timeout(
        self,
        *,
        session: Any,
        run_id: str,
        run: RunRecord,
        equivalents: list[str],
        planned_len: int,
        tick_t0: float,
        safe_int_env: Callable[[str, int], int],
        run_clearing: Callable[[], Awaitable[dict[str, Decimal]]],
        payments_result: Any | None,
    ) -> dict[str, Decimal]:
        clearing_volume_by_eq: dict[str, Decimal] = {
            str(eq): Decimal("0") for eq in equivalents
        }
        tick_index = int(run.tick_index)

        # End the transaction BEFORE clearing to release the DB write lock.
        #
        # THIS IS NO LONGER THE MONEY COMMIT, corrected 2026-09-12 by programme 015 / P1. It used
        # to be: the payments phase left its transaction open and this was the first commit after
        # it, which made the money's durability point depend on whether clearing was due this
        # tick. The money now commits at its own boundary, before anything in the tail runs
        # (`app/core/simulator/money_replay.py`), and by the time execution reaches this line the
        # payments are already durable. What is committed here is whatever the tail has read or
        # written since, and the call is kept for the reason its first line gives: clearing runs
        # in a session of its own and must not queue behind this one's write lock.
        #
        # The observation callbacks are kept too, and they are no-ops after a successful money
        # commit: the buffer resolves once (`DeferredRealPaymentEffects._resolve`). That is what
        # makes the right edge hard - a failure anywhere in the tail cannot un-publish a committed
        # payment and cannot replay it.
        commit_t0 = time.monotonic()
        await self._commit_and_resolve(session, payments_result)
        commit_ms = (time.monotonic() - commit_t0) * 1000.0
        if commit_ms > 500.0:
            self._logger.warning(
                "simulator.real.tick_commit_slow run_id=%s tick=%s commit_ms=%s total_tick_ms=%s",
                str(run.run_id),
                tick_index,
                int(commit_ms),
                int((time.monotonic() - tick_t0) * 1000.0),
            )

        self._logger.warning(
            "simulator.real.tick_clearing_enter run_id=%s tick=%s eqs=%s planned=%s",
            str(run.run_id),
            tick_index,
            ",".join([str(x) for x in (equivalents or [])]),
            int(planned_len),
        )

        clearing_t0 = time.monotonic()

        clearing_hard_timeout_sec = self.compute_static_clearing_hard_timeout_sec(
            safe_int_env=safe_int_env
        )

        clearing_task: asyncio.Task[dict[str, Decimal]] | None = None
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
                clearing_task,
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
            # Managed timeout: cancel best-effort so we don't leak background clearing.
            try:
                clearing_task.cancel()
            except Exception:
                pass
            try:
                await clearing_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
            with self._lock:
                if run._real_clearing_task is clearing_task:
                    run._real_clearing_task = None
                run.current_phase = None
        except Exception:
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
