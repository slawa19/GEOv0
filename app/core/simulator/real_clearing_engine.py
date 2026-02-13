from __future__ import annotations

import asyncio
import logging
import secrets
import time
import uuid
import hashlib
from decimal import Decimal, ROUND_DOWN
from typing import Any, Awaitable, Callable

from sqlalchemy import select

import app.db.session as db_session
from app.config import settings
from app.core.clearing.service import ClearingService
from app.core.simulator.edge_patch_builder import EdgePatchBuilder
from app.core.simulator.models import RunRecord
from app.core.simulator.sse_broadcast import SseBroadcast, SseEventEmitter
from app.core.simulator.viz_patch_helper import VizPatchHelper
from app.db.models.participant import Participant


class RealClearingEngine:
    def __init__(
        self,
        *,
        lock,
        sse: SseBroadcast,
        utc_now,
        logger: logging.Logger,
        edge_patch_builder: EdgePatchBuilder,
        clearing_max_depth_limit: int,
        clearing_max_fx_edges_limit: int,
        real_clearing_time_budget_ms: int,
        should_warn_this_tick: Callable[[RunRecord, str], bool] | None = None,
    ) -> None:
        self._lock = lock
        self._sse = sse
        self._utc_now = utc_now
        self._logger = logger
        self._edge_patch_builder = edge_patch_builder
        self._clearing_max_depth_limit = int(clearing_max_depth_limit)
        self._clearing_max_fx_edges_limit = int(clearing_max_fx_edges_limit)
        self._real_clearing_time_budget_ms = int(real_clearing_time_budget_ms)

        self._should_warn_this_tick_cb = should_warn_this_tick

    def _should_warn_this_tick(self, run: RunRecord, key: str) -> bool:
        if self._should_warn_this_tick_cb is None:
            return True
        try:
            return bool(self._should_warn_this_tick_cb(run, key))
        except Exception:
            return True

    async def tick_real_mode_clearing(
        self,
        session,  # NOTE: unused; clearing uses its own isolated session
        run_id: str,
        run: RunRecord,
        equivalents: list[str],
        *,
        apply_trust_growth: Callable[
            ..., Awaitable[int]
        ],
        build_edge_patch_for_equivalent: Callable[
            ..., Awaitable[list[dict[str, Any]]]
        ],
        broadcast_topology_edge_patch: Callable[..., None],
        async_session_local: Any | None = None,
        clearing_service_cls: Any | None = None,
        time_budget_ms_override: int | None = None,
        max_depth_override: int | None = None,
    ) -> dict[str, float]:
        """Execute clearing for all equivalents using an isolated session.

        IMPORTANT: This method uses its own session to avoid poisoning the parent
        tick_real_mode session with commit/rollback side effects. PostgreSQL marks
        a transaction as "aborted" after any error, and subsequent queries fail
        with InFailedSQLTransactionError.

        Optional overrides (for adaptive clearing policy):
        - time_budget_ms_override: if set, used instead of self._real_clearing_time_budget_ms
          (clamped to the constructor ceiling as guardrail).
        - max_depth_override: if set, used instead of self._clearing_max_depth_limit
          (clamped to the constructor ceiling as guardrail).
        """

        max_depth = min(
            int(max_depth_override)
            if max_depth_override is not None
            else int(self._clearing_max_depth_limit),
            int(self._clearing_max_depth_limit),
        )
        effective_time_budget_ms = min(
            int(time_budget_ms_override)
            if time_budget_ms_override is not None
            else int(self._real_clearing_time_budget_ms),
            int(self._real_clearing_time_budget_ms),
        )

        # Safety: never allow non-positive budgets/depth.
        # If time_budget_ms <= 0, the loop would skip the budget check entirely.
        max_depth = max(1, int(max_depth))
        effective_time_budget_ms = max(1, int(effective_time_budget_ms))
        max_fx_edges = int(self._clearing_max_fx_edges_limit)
        cleared_amount_by_eq: dict[str, float] = {str(eq): 0.0 for eq in equivalents}

        emitter = SseEventEmitter(sse=self._sse, utc_now=self._utc_now, logger=self._logger)

        session_local = async_session_local or db_session.AsyncSessionLocal
        service_cls = clearing_service_cls or ClearingService

        for eq in equivalents:
            try:
                async with session_local() as clearing_session:
                    service = service_cls(clearing_session)

                    eq_t0 = time.monotonic()
                    self._logger.warning(
                        "simulator.real.clearing_eq_enter run_id=%s tick=%s eq=%s",
                        str(run.run_id),
                        int(run.tick_index),
                        str(eq),
                    )

                    self._logger.warning(
                        "simulator.real.clearing_find_cycles_start run_id=%s tick=%s eq=%s max_depth=%s",
                        str(run.run_id),
                        int(run.tick_index),
                        str(eq),
                        int(max_depth),
                    )
                    _fc_t0 = time.monotonic()
                    cycles = await service.find_cycles(eq, max_depth=max_depth)
                    _fc_ms = int((time.monotonic() - _fc_t0) * 1000.0)
                    if _fc_ms > 500:
                        self._logger.warning(
                            "simulator.real.clearing_find_cycles_slow run_id=%s tick=%s eq=%s elapsed_ms=%s",
                            str(run.run_id),
                            int(run.tick_index),
                            str(eq),
                            int(_fc_ms),
                        )
                    self._logger.warning(
                        "simulator.real.clearing_find_cycles_done run_id=%s tick=%s eq=%s cycles_n=%s elapsed_ms=%s",
                        str(run.run_id),
                        int(run.tick_index),
                        str(eq),
                        int(len(cycles or [])),
                        int(_fc_ms),
                    )
                    if not cycles:
                        continue

                    def _prioritize_cycle_for_tick(cycles_in: list[Any]) -> list[Any]:
                        if len(cycles_in) <= 1:
                            return cycles_in
                        seed = f"{run.run_id}:{run.tick_index}:{eq}".encode("utf-8")
                        digest = hashlib.sha256(seed).digest()
                        idx = int.from_bytes(digest[:4], byteorder="big", signed=False) % len(
                            cycles_in
                        )
                        if idx <= 0:
                            return cycles_in
                        # Put the chosen cycle first (so visualization and execution are aligned).
                        chosen = cycles_in[idx]
                        return [chosen, *cycles_in[:idx], *cycles_in[idx + 1 :]]

                    # IMPORTANT: keep visualization aligned with what we attempt to clear first.
                    cycles = _prioritize_cycle_for_tick(list(cycles))

                    plan_id = f"plan_{secrets.token_hex(6)}"

                    with self._lock:
                        run.current_phase = "clearing"

                    cleared_cycles = 0
                    cleared_amount_dec = Decimal("0")
                    touched_nodes: set[str] = set()
                    touched_edges: set[tuple[str, str]] = set()
                    cleared_amount_per_edge: dict[tuple[str, str], float] = {}
                    clearing_started = time.monotonic()
                    progress_last_log = 0.0

                    while True:
                        now = time.monotonic()
                        if progress_last_log <= 0.0:
                            progress_last_log = now
                        elif (now - progress_last_log) >= 5.0:
                            self._logger.warning(
                                "simulator.real.clearing_progress run_id=%s tick=%s eq=%s elapsed_ms=%s cleared_cycles=%s",
                                str(run.run_id),
                                int(run.tick_index),
                                str(eq),
                                int((now - clearing_started) * 1000.0),
                                int(cleared_cycles),
                            )
                            progress_last_log = now

                        if cleared_cycles and (cleared_cycles % 5 == 0):
                            await asyncio.sleep(0)

                        budget_ms = int(effective_time_budget_ms)
                        elapsed_ms = (time.monotonic() - clearing_started) * 1000.0
                        if elapsed_ms >= float(budget_ms):
                            if self._should_warn_this_tick(
                                run, f"clearing_time_budget_exceeded:{eq}"
                            ):
                                self._logger.warning(
                                    "simulator.real.clearing_time_budget_exceeded run_id=%s tick=%s eq=%s budget_ms=%s elapsed_ms=%s",
                                    str(run.run_id),
                                    int(run.tick_index),
                                    str(eq),
                                    int(budget_ms),
                                    int(elapsed_ms),
                                )
                            break

                        self._logger.debug(
                            "simulator.real.clearing_find_cycles_loop_start run_id=%s tick=%s eq=%s cleared_cycles=%s",
                            str(run.run_id),
                            int(run.tick_index),
                            str(eq),
                            int(cleared_cycles),
                        )
                        _loop_fc_t0 = time.monotonic()
                        cycles = await service.find_cycles(eq, max_depth=max_depth)
                        _loop_fc_ms = int((time.monotonic() - _loop_fc_t0) * 1000.0)
                        if _loop_fc_ms > 500:
                            self._logger.warning(
                                "simulator.real.clearing_find_cycles_loop_slow run_id=%s tick=%s eq=%s elapsed_ms=%s",
                                str(run.run_id),
                                int(run.tick_index),
                                str(eq),
                                int(_loop_fc_ms),
                            )
                        if not cycles:
                            break

                        # Keep execution order aligned with visualization policy.
                        cycles = _prioritize_cycle_for_tick(list(cycles))

                        executed = False
                        for cycle in cycles:
                            try:
                                amts: list[Decimal] = []
                                for edge in cycle:
                                    if isinstance(edge, dict):
                                        amts.append(Decimal(str(edge.get("amount"))))
                                    else:
                                        amts.append(Decimal(str(getattr(edge, "amount"))))
                                clear_amount = min(amts) if amts else Decimal("0")
                            except Exception:
                                if self._should_warn_this_tick(
                                    run, f"clearing_clear_amount_parse_failed:{eq}"
                                ):
                                    self._logger.debug(
                                        "simulator.real.clearing_clear_amount_parse_failed run_id=%s tick=%s eq=%s",
                                        str(run.run_id),
                                        int(run.tick_index),
                                        str(eq),
                                        exc_info=True,
                                    )
                                clear_amount = Decimal("0")

                            self._logger.warning(
                                "simulator.real.clearing_execute_start run_id=%s tick=%s eq=%s clear_amount=%s cycle_len=%s",
                                str(run.run_id),
                                int(run.tick_index),
                                str(eq),
                                str(clear_amount),
                                int(len(cycle or [])),
                            )
                            _exec_t0 = time.monotonic()
                            success = await service.execute_clearing(cycle)
                            _exec_ms = int((time.monotonic() - _exec_t0) * 1000.0)
                            self._logger.warning(
                                "simulator.real.clearing_execute_done run_id=%s tick=%s eq=%s success=%s elapsed_ms=%s",
                                str(run.run_id),
                                int(run.tick_index),
                                str(eq),
                                bool(success),
                                int(_exec_ms),
                            )
                            if _exec_ms > 500:
                                self._logger.warning(
                                    "simulator.real.clearing_execute_slow run_id=%s tick=%s eq=%s elapsed_ms=%s",
                                    str(run.run_id),
                                    int(run.tick_index),
                                    str(eq),
                                    int(_exec_ms),
                                )

                            if success:
                                cleared_cycles += 1
                                if clear_amount > 0:
                                    cleared_amount_dec += clear_amount

                                try:
                                    for edge in cycle:
                                        if not isinstance(edge, dict):
                                            continue
                                        debtor_pid = str(edge.get("debtor") or "").strip()
                                        creditor_pid = str(edge.get("creditor") or "").strip()
                                        if debtor_pid:
                                            touched_nodes.add(debtor_pid)
                                        if creditor_pid:
                                            touched_nodes.add(creditor_pid)
                                        if creditor_pid and debtor_pid:
                                            touched_edges.add((creditor_pid, debtor_pid))
                                            edge_key = (creditor_pid, debtor_pid)
                                            cleared_amount_per_edge[edge_key] = (
                                                cleared_amount_per_edge.get(edge_key, 0.0)
                                                + float(clear_amount)
                                            )
                                except Exception:
                                    if self._should_warn_this_tick(
                                        run, f"clearing_touched_parse_failed:{eq}"
                                    ):
                                        self._logger.debug(
                                            "simulator.real.clearing_touched_parse_failed run_id=%s tick=%s eq=%s",
                                            str(run.run_id),
                                            int(run.tick_index),
                                            str(eq),
                                            exc_info=True,
                                        )
                                executed = True
                                break

                        if not executed:
                            break
                        if cleared_cycles > 100:
                            break

                    cleared_amount_by_eq[str(eq)] = float(cleared_amount_dec)

                    if touched_edges:
                        try:
                            growth_res = await apply_trust_growth(
                                run=run,
                                clearing_session=clearing_session,
                                touched_edges=touched_edges,
                                eq_code=str(eq),
                                tick_index=int(run.tick_index or 0),
                                cleared_amount_per_edge=cleared_amount_per_edge,
                            )
                            if int(getattr(growth_res, "updated_count", 0) or 0) > 0:
                                try:
                                    edge_patch = await build_edge_patch_for_equivalent(
                                        session=clearing_session,
                                        run=run,
                                        equivalent_code=str(eq),
                                        only_edges=None,
                                        include_width_keys=True,
                                    )
                                    broadcast_topology_edge_patch(
                                        run_id=run_id,
                                        run=run,
                                        equivalent=str(eq),
                                        edge_patch=edge_patch,
                                        reason="trust_drift_growth",
                                    )
                                except Exception:
                                    self._logger.warning(
                                        "simulator.real.trust_drift.growth_edge_patch_failed",
                                        exc_info=True,
                                    )
                        except Exception:
                            self._logger.warning(
                                "simulator.real.trust_drift.growth_failed run_id=%s tick=%s eq=%s",
                                str(run.run_id),
                                int(run.tick_index or 0),
                                str(eq),
                                exc_info=True,
                            )

                    node_patch_list: list[dict[str, Any]] | None = None
                    edge_patch_list: list[dict[str, Any]] | None = None

                    cleared_amount_str: str | None = None
                    if cleared_amount_dec > 0:
                        try:
                            cleared_amount_str = format(
                                cleared_amount_dec.quantize(
                                    Decimal("0.01"), rounding=ROUND_DOWN
                                ),
                                "f",
                            )
                        except Exception:
                            cleared_amount_str = str(cleared_amount_dec)

                    self._logger.warning(
                        "simulator.real.clearing_patch_start run_id=%s tick=%s eq=%s touched_nodes=%s touched_edges=%s cleared_cycles=%s",
                        str(run.run_id),
                        int(run.tick_index),
                        str(eq),
                        int(len(touched_nodes)),
                        int(len(touched_edges)),
                        int(cleared_cycles),
                    )
                    _patch_t0 = time.monotonic()
                    try:
                        helper: VizPatchHelper | None
                        with self._lock:
                            helper = run._real_viz_by_eq.get(str(eq))

                        if helper is None:
                            helper = await VizPatchHelper.create(
                                clearing_session,
                                equivalent_code=str(eq),
                                refresh_every_ticks=int(
                                    getattr(
                                        settings,
                                        "SIMULATOR_VIZ_QUANTILE_REFRESH_TICKS",
                                        10,
                                    )
                                    or 10
                                ),
                            )
                            with self._lock:
                                run._real_viz_by_eq[str(eq)] = helper

                        if cleared_amount_dec > 0:
                            try:
                                precision = int(getattr(helper, "precision", 2) or 2)
                                money_quant = Decimal(1) / (Decimal(10) ** precision)
                                cleared_amount_str = format(
                                    cleared_amount_dec.quantize(
                                        money_quant, rounding=ROUND_DOWN
                                    ),
                                    "f",
                                )
                            except Exception:
                                pass

                        participant_ids: list[uuid.UUID] = []
                        if run._real_participants:
                            participant_ids = [pid for (pid, _) in run._real_participants]
                        await helper.maybe_refresh_quantiles(
                            clearing_session,
                            tick_index=int(run.tick_index),
                            participant_ids=participant_ids,
                        )

                        pids = sorted({str(x).strip() for x in touched_nodes if str(x).strip()})
                        if pids:
                            res = await clearing_session.execute(
                                select(Participant).where(Participant.pid.in_(pids))
                            )
                            pid_to_participant = {p.pid: p for p in res.scalars().all()}
                            node_patch_list = await helper.compute_node_patches(
                                clearing_session,
                                pid_to_participant=pid_to_participant,
                                pids=pids,
                            )
                            if node_patch_list == []:
                                node_patch_list = None

                            pairs = sorted(touched_edges)
                            edge_patch_list = await self._edge_patch_builder.build_edge_patch_for_pairs(
                                session=clearing_session,
                                helper=helper,
                                edges_pairs=pairs,
                                pid_to_participant=pid_to_participant,
                            )
                            if edge_patch_list == []:
                                edge_patch_list = None
                    except Exception:
                        if self._should_warn_this_tick(
                            run, f"clearing_done_patch_failed:{eq}"
                        ):
                            self._logger.debug(
                                "simulator.real.clearing_done_patch_failed run_id=%s tick=%s eq=%s",
                                str(run.run_id),
                                int(run.tick_index),
                                str(eq),
                                exc_info=True,
                            )
                        node_patch_list = None
                        edge_patch_list = None

                    _patch_ms = int((time.monotonic() - _patch_t0) * 1000.0)
                    if _patch_ms > 500:
                        self._logger.warning(
                            "simulator.real.clearing_patch_slow run_id=%s tick=%s eq=%s elapsed_ms=%s",
                            str(run.run_id),
                            int(run.tick_index),
                            str(eq),
                            int(_patch_ms),
                        )
                    self._logger.warning(
                        "simulator.real.clearing_patch_done run_id=%s tick=%s eq=%s elapsed_ms=%s",
                        str(run.run_id),
                        int(run.tick_index),
                        str(eq),
                        int(_patch_ms),
                    )

                    with self._lock:
                        run.last_event_type = "clearing.done"
                        run.current_phase = None

                    # Provide authoritative edges for visualization: what was actually touched by clearing.
                    done_cycle_edges: list[dict[str, str]] | None = None
                    if touched_edges:
                        pairs = sorted(touched_edges)
                        if max_fx_edges > 0 and len(pairs) > max_fx_edges:
                            pairs = pairs[:max_fx_edges]
                        done_cycle_edges = [{"from": a, "to": b} for (a, b) in pairs]
                    emitter.emit_clearing_done(
                        run_id=run_id,
                        run=run,
                        equivalent=eq,
                        plan_id=plan_id,
                        cleared_cycles=cleared_cycles,
                        cleared_amount=cleared_amount_str,
                        cycle_edges=done_cycle_edges,
                        node_patch=node_patch_list,
                        edge_patch=edge_patch_list,
                    )

                    self._logger.warning(
                        "simulator.real.clearing_eq_done run_id=%s tick=%s eq=%s elapsed_ms=%s cleared_cycles=%s",
                        str(run.run_id),
                        int(run.tick_index),
                        str(eq),
                        int((time.monotonic() - eq_t0) * 1000.0),
                        int(cleared_cycles),
                    )
            except Exception as e:
                if self._should_warn_this_tick(run, f"clearing_failed:{eq}"):
                    self._logger.warning(
                        "simulator.real.clearing_failed run_id=%s tick=%s eq=%s",
                        str(run.run_id),
                        int(run.tick_index),
                        str(eq),
                        exc_info=True,
                    )
                with self._lock:
                    run.errors_total += 1
                    run._error_timestamps.append(time.time())
                    cutoff = time.time() - 60.0
                    while run._error_timestamps and run._error_timestamps[0] < cutoff:
                        run._error_timestamps.popleft()
                    run.last_error = {
                        "code": "CLEARING_ERROR",
                        "message": str(e),
                        "at": self._utc_now().isoformat(),
                    }
                continue

        return cleared_amount_by_eq
