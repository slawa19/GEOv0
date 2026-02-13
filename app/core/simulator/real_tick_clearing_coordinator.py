from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable, Literal

from app.core.simulator.adaptive_clearing_policy import (
    AdaptiveClearingPolicy,
    AdaptiveClearingPolicyConfig,
    AdaptiveClearingState,
    TickSignals,
)
from app.core.simulator.models import RunRecord


class RealTickClearingCoordinator:
    def __init__(
        self,
        *,
        lock,
        logger: logging.Logger,
        clearing_every_n_ticks: int,
        real_clearing_time_budget_ms: int,
        clearing_policy: Literal["static", "adaptive"] = "static",
        adaptive_config: AdaptiveClearingPolicyConfig | None = None,
    ) -> None:
        self._lock = lock
        self._logger = logger
        self._clearing_every_n_ticks = int(clearing_every_n_ticks)
        self._real_clearing_time_budget_ms = int(real_clearing_time_budget_ms)
        self._clearing_policy = clearing_policy

        # Adaptive policy state (None when static)
        self._adaptive_state: AdaptiveClearingState | None = None
        self._adaptive_policy: AdaptiveClearingPolicy | None = None
        self._adaptive_config: AdaptiveClearingPolicyConfig | None = None
        if clearing_policy == "adaptive":
            cfg = adaptive_config or AdaptiveClearingPolicyConfig()
            self._adaptive_config = cfg
            self._adaptive_state = AdaptiveClearingState(cfg)
            self._adaptive_policy = AdaptiveClearingPolicy(cfg)

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
        run_clearing_for_eq: Callable[..., Awaitable[dict[str, float]]] | None = None,
        payments_result: Any | None = None,
    ) -> dict[str, float]:
        clearing_volume_by_eq: dict[str, float] = {str(eq): 0.0 for eq in equivalents}

        if not clearing_enabled:
            return clearing_volume_by_eq

        if self._clearing_policy == "adaptive":
            return await self._maybe_run_adaptive(
                session=session,
                run_id=run_id,
                run=run,
                equivalents=equivalents,
                planned_len=planned_len,
                tick_t0=tick_t0,
                safe_int_env=safe_int_env,
                run_clearing_for_eq=run_clearing_for_eq,
                payments_result=payments_result,
            )

        # ── Static mode (original logic) ────────────────────────
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
        )

    # ── Adaptive mode ────────────────────────────────────────────

    async def _maybe_run_adaptive(
        self,
        *,
        session: Any,
        run_id: str,
        run: RunRecord,
        equivalents: list[str],
        planned_len: int,
        tick_t0: float,
        safe_int_env: Callable[[str, int], int],
        run_clearing_for_eq: Callable[..., Awaitable[dict[str, float]]] | None,
        payments_result: Any | None,
    ) -> dict[str, float]:
        clearing_volume_by_eq: dict[str, float] = {str(eq): 0.0 for eq in equivalents}
        state = self._adaptive_state
        policy = self._adaptive_policy
        cfg = self._adaptive_config

        if state is None or policy is None or cfg is None:
            return clearing_volume_by_eq

        def _clamp_int(v: int, lo: int, hi: int) -> int:
            return max(lo, min(hi, v))

        def _resolve_effective_int(
            raw: int | None,
            *,
            cfg_min: int,
            cfg_max: int,
            hard_ceiling: int,
        ) -> int:
            """Resolve None/invalid inputs and clamp deterministically.

            Spec rule:
            - None -> MIN
            - clamp(raw, MIN, min(MAX, CEILING))

            Additional safety:
            - enforce >= 1 so we never propagate 0/negative budgets.
            """

            lo = max(1, int(cfg_min))
            hi = min(int(cfg_max), int(hard_ceiling))
            if hi < lo:
                hi = lo

            if raw is None:
                raw_i = lo
            else:
                try:
                    raw_i = int(raw)
                except Exception:
                    raw_i = lo

            return _clamp_int(raw_i, lo, hi)

        tick_index = int(run.tick_index)

        # Feed tick signals every tick so the rolling window advances with time
        # even when there is no payments_result (zero signals).
        per_eq = getattr(payments_result, "per_eq", None) or {} if payments_result is not None else {}
        rejection_codes_by_eq = (
            getattr(payments_result, "rejection_codes_by_eq", None) or {}
            if payments_result is not None
            else {}
        )
        for eq in equivalents:
            eq_stats = per_eq.get(eq, {})
            attempted = (
                int(eq_stats.get("committed", 0))
                + int(eq_stats.get("rejected", 0))
                + int(eq_stats.get("errors", 0))
                + int(eq_stats.get("timeouts", 0))
            )
            rejected_no_capacity = int(
                rejection_codes_by_eq.get(eq, {}).get("ROUTING_NO_CAPACITY", 0)
            )
            state.record_tick_signals(
                eq,
                TickSignals(
                    attempted_payments=attempted,
                    rejected_no_capacity=rejected_no_capacity,
                ),
            )

        # Guardrail: global in_flight / queue_depth check
        if cfg.inflight_threshold > 0:
            in_flight = int(getattr(run, "_real_in_flight", 0) or 0)
            if in_flight > cfg.inflight_threshold:
                self._logger.warning(
                    "simulator.real.adaptive_clearing_guardrail_inflight reason=CLEARING_SKIPPED_GUARDRAIL run_id=%s tick=%s in_flight=%s threshold=%s",
                    str(run.run_id), tick_index, in_flight, cfg.inflight_threshold,
                )
                return clearing_volume_by_eq
        if cfg.queue_depth_threshold > 0:
            queue_depth = int(getattr(run, "queue_depth", 0) or 0)
            if queue_depth > cfg.queue_depth_threshold:
                self._logger.warning(
                    "simulator.real.adaptive_clearing_guardrail_queue_depth reason=CLEARING_SKIPPED_GUARDRAIL run_id=%s tick=%s queue_depth=%s threshold=%s",
                    str(run.run_id), tick_index, queue_depth, cfg.queue_depth_threshold,
                )
                return clearing_volume_by_eq

        # Evaluate policy per-eq
        eqs_to_clear: list[tuple[str, int | None, int | None]] = []
        for eq in equivalents:
            decision = policy.evaluate(eq, state, tick_index)
            no_cap_rate = state.get_no_capacity_rate(eq)
            per_eq_state = state.get_per_eq_state(eq)
            cooldown_remaining = max(0, (per_eq_state.last_clearing_tick + cfg.min_interval_ticks) - tick_index) if per_eq_state.last_clearing_tick >= 0 else 0

            if decision.reason == "SKIP_BACKOFF" and per_eq_state.last_clearing_tick >= 0:
                next_allowed_tick = per_eq_state.last_clearing_tick + int(
                    max(0, per_eq_state.backoff_interval)
                )
                backoff_remaining = max(0, int(next_allowed_tick) - int(tick_index))
                self._logger.debug(
                    "simulator.real.adaptive_clearing_backoff_skip run_id=%s tick=%s eq=%s backoff_remaining=%s next_allowed_tick=%s",
                    str(run.run_id),
                    int(tick_index),
                    str(eq),
                    int(backoff_remaining),
                    int(next_allowed_tick),
                )

            self._logger.debug(
                "simulator.real.adaptive_clearing_decision run_id=%s tick=%s eq=%s should_run=%s reason=%s no_capacity_rate=%.3f cooldown_remaining=%s budget_ms=%s depth=%s",
                str(run.run_id), tick_index, eq,
                decision.should_run, decision.reason, no_cap_rate,
                cooldown_remaining,
                decision.time_budget_ms, decision.max_depth,
            )

            if decision.should_run:
                eqs_to_clear.append((eq, decision.time_budget_ms, decision.max_depth))

        if not eqs_to_clear:
            return clearing_volume_by_eq

        if run_clearing_for_eq is None:
            self._logger.warning(
                "simulator.real.adaptive_clearing_no_per_eq_runner run_id=%s tick=%s",
                str(run.run_id), tick_index,
            )
            return clearing_volume_by_eq

        # Commit payments BEFORE clearing (same as static mode)
        try:
            commit_t0 = time.monotonic()
            await session.commit()
            commit_ms = (time.monotonic() - commit_t0) * 1000.0
            if commit_ms > 500.0:
                self._logger.warning(
                    "simulator.real.tick_commit_slow run_id=%s tick=%s commit_ms=%s",
                    str(run.run_id), tick_index, int(commit_ms),
                )
        except Exception:
            await session.rollback()
            raise

        self._logger.warning(
            "simulator.real.adaptive_clearing_enter run_id=%s tick=%s eqs=%s",
            str(run.run_id), tick_index,
            ",".join(eq for eq, _, _ in eqs_to_clear),
        )

        # Global cap for the *tick* clearing loop to prevent N * per-eq hard-timeout.
        # 0 = disabled.
        tick_budget_ms = int(safe_int_env("SIMULATOR_CLEARING_ADAPTIVE_TICK_BUDGET_MS", 0) or 0)
        max_eq_per_tick = int(safe_int_env("SIMULATOR_CLEARING_ADAPTIVE_MAX_EQ_PER_TICK", 0) or 0)
        tick_clearing_t0 = time.monotonic()

        # Execute clearing per-eq (with hard timeout to prevent hangs)
        # Hard timeout = max(2s, budget_ms * 4 / 1000), capped at 8s
        for i, (eq, budget_ms, max_depth) in enumerate(eqs_to_clear):
            if max_eq_per_tick > 0 and i >= max_eq_per_tick:
                self._logger.warning(
                    "simulator.real.adaptive_clearing_tick_cap_max_eq reason=CLEARING_SKIPPED_MAX_EQ_PER_TICK run_id=%s tick=%s max_eq=%s requested=%s",
                    str(run.run_id), tick_index, max_eq_per_tick, len(eqs_to_clear),
                )
                break

            if tick_budget_ms > 0:
                elapsed_ms = (time.monotonic() - tick_clearing_t0) * 1000.0
                if elapsed_ms >= float(tick_budget_ms):
                    self._logger.warning(
                        "simulator.real.adaptive_clearing_tick_cap_budget_exhausted reason=CLEARING_SKIPPED_TICK_BUDGET run_id=%s tick=%s budget_ms=%s elapsed_ms=%s",
                        str(run.run_id), tick_index, tick_budget_ms, int(elapsed_ms),
                    )
                    break

            eq_t0 = time.monotonic()
            effective_budget_ms = _resolve_effective_int(
                budget_ms,
                cfg_min=int(cfg.time_budget_ms_min),
                cfg_max=int(cfg.time_budget_ms_max),
                hard_ceiling=min(
                    int(cfg.global_time_budget_ms_ceiling),
                    int(self._real_clearing_time_budget_ms),
                ),
            )
            effective_max_depth = _resolve_effective_int(
                max_depth,
                cfg_min=int(cfg.max_depth_min),
                cfg_max=int(cfg.max_depth_max),
                hard_ceiling=int(cfg.global_max_depth_ceiling),
            )

            hard_timeout_sec = max(2.0, float(effective_budget_ms) / 1000.0 * 4.0)
            hard_timeout_sec = min(hard_timeout_sec, 8.0)
            try:
                result = await asyncio.wait_for(
                    run_clearing_for_eq(
                        eq,
                        time_budget_ms_override=effective_budget_ms,
                        max_depth_override=effective_max_depth,
                    ),
                    timeout=hard_timeout_sec,
                )
                volume = float(result.get(eq, 0.0)) if isinstance(result, dict) else 0.0
                clearing_volume_by_eq[eq] = volume

                cost_ms = (time.monotonic() - eq_t0) * 1000.0
                state.update_clearing_result(eq, volume=volume, cost_ms=cost_ms, tick=tick_index)

                self._logger.warning(
                    "simulator.real.adaptive_clearing_eq_done run_id=%s tick=%s eq=%s volume=%.2f cost_ms=%s",
                    str(run.run_id), tick_index, eq, volume, int(cost_ms),
                )
            except asyncio.TimeoutError:
                cost_ms = (time.monotonic() - eq_t0) * 1000.0
                self._logger.warning(
                    "simulator.real.adaptive_clearing_eq_hard_timeout run_id=%s tick=%s eq=%s timeout_sec=%s elapsed_ms=%s",
                    str(run.run_id), tick_index, eq, hard_timeout_sec, int(cost_ms),
                )
                # Record as zero-yield so backoff kicks in
                state.update_clearing_result(eq, volume=0.0, cost_ms=cost_ms, tick=tick_index)
            except Exception:
                cost_ms = (time.monotonic() - eq_t0) * 1000.0
                self._logger.warning(
                    "simulator.real.adaptive_clearing_eq_failed run_id=%s tick=%s eq=%s",
                    str(run.run_id), tick_index, eq,
                    exc_info=True,
                )
                # Treat any failure as zero-yield so cooldown/backoff prevent
                # hammering clearing every tick under high pressure.
                state.update_clearing_result(eq, volume=0.0, cost_ms=cost_ms, tick=tick_index)

        return clearing_volume_by_eq

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
        run_clearing: Callable[[], Awaitable[dict[str, float]]],
    ) -> dict[str, float]:
        clearing_volume_by_eq: dict[str, float] = {str(eq): 0.0 for eq in equivalents}
        tick_index = int(run.tick_index)

        # Commit payments BEFORE clearing to release the DB write lock.
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
            try:
                clearing_task.cancel()
            except Exception:
                pass
            with self._lock:
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
