from __future__ import annotations

import asyncio
import logging
import secrets
import time
import uuid
from decimal import Decimal
from typing import Any, Awaitable, Callable

from sqlalchemy import select

import app.db.session as db_session
from app.config import settings
from app.core.clearing.runner import ClearingPassCancelled, ClearingPassError, run_clearing_pass
from app.core.simulator.edge_patch_builder import EdgePatchBuilder
from app.core.simulator.net_balance_utils import to_money_str
from app.core.simulator.run_perimeter import run_perimeter_pids
from app.core.simulator.models import RunRecord
from app.core.simulator.sse_broadcast import SseBroadcast, SseEventEmitter
from app.core.simulator.viz_patch_helper import VizPatchHelper
from app.db.models.participant import Participant
from app.core.money_boundary import MoneyBoundary
from app.utils.exceptions import ConflictException, GeoException


class RealClearingEngine:
    def __init__(
        self,
        *,
        lock,
        sse: SseBroadcast,
        utc_now,
        logger: logging.Logger,
        edge_patch_builder: EdgePatchBuilder,
        clearing_max_fx_edges_limit: int,
        real_clearing_time_budget_ms: int,
        should_warn_this_tick: Callable[[RunRecord, str], bool] | None = None,
        clearing_max_depth_limit: int | None = None,
    ) -> None:
        # `clearing_max_depth_limit` is INACTIVE since programme 023 slice (d): execution has no depth. Accepted and
        # ignored until 021 `T2109` removes the driver.
        self._lock = lock
        self._sse = sse
        self._utc_now = utc_now
        self._logger = logger
        self._edge_patch_builder = edge_patch_builder
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

    def _cleared_amount_str(self, run: RunRecord, eq: Any, amount: Decimal) -> str:
        """The single rendering of `clearing.done.cleared_amount`.

        012 / `T1207`.  This field used to be produced three times in this file with two
        different scales: `Decimal("0.01")` hard-coded before the viz helper exists, then
        re-quantised by `Equivalent.precision` once it does, and hard-coded to `0.01` again on
        the `CancelledError` path -- which never reached the second one.  So the same cleared
        amount was reported at scale 2 or at scale `precision` depending only on whether the
        clearing had been cancelled, and a `precision`-4 equivalent lost two digits precisely
        when something had gone wrong.  All three sites now call this.

        Precision comes from the `VizPatchHelper` cached on the run, which is where this
        module already keeps it; no DB round trip is added to the tick.  Before the first
        helper for an equivalent exists the fallback is 2 -- the same default the rest of the
        codebase uses for a missing `Equivalent.precision` -- and, crucially, it is now the
        same fallback on both paths instead of a different one on each.
        """

        precision = 2
        try:
            with self._lock:
                helper = (run._real_viz_by_eq or {}).get(str(eq))
            if helper is not None:
                precision = int(getattr(helper, "precision", 2) or 2)
        except Exception:
            precision = 2
        return to_money_str(amount, precision)

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
        clearing_pass: Callable[..., Awaitable[Any]] | None = None,
    ) -> dict[str, Decimal]:
        """Execute clearing for all equivalents using an isolated session.

        IMPORTANT: This method uses its own session to avoid poisoning the parent
        tick_real_mode session with commit/rollback side effects. PostgreSQL marks
        a transaction as "aborted" after any error, and subsequent queries fail
        with InFailedSQLTransactionError.

        Programme 023 slice (d): one pass of the common clearing runner per equivalent, in the run's perimeter.

        - time_budget_ms_override: if set, used instead of self._real_clearing_time_budget_ms
          (clamped to the constructor ceiling as guardrail); it is the runner's caller deadline.
        - max_depth_override, clearing_service_cls: INACTIVE since 023 (d) - execution has no depth and the
          runner owns its service; kept only until 021 `T2109` removes the driver's signature.
        - clearing_pass: the runner entry (`run_clearing_pass`); a seam for tests.
        """

        # Programme 023, slice (d): the tick clears through the common runner (decisions 7, 10; R3, R4). No
        # execution depth: `max_depth_override` and the constructor's `clearing_max_depth_limit` are inactive, kept
        # only until their owners remove them (the driver and `real_runner.py` - 021 `T2109`; the coordinator and
        # its port - 021 stage 4). The budget is the runner's caller deadline, checked before every cycle start.
        effective_time_budget_ms = min(
            int(time_budget_ms_override)
            if time_budget_ms_override is not None
            else int(self._real_clearing_time_budget_ms),
            int(self._real_clearing_time_budget_ms),
        )

        # Safety: never allow a non-positive budget.
        effective_time_budget_ms = max(1, int(effective_time_budget_ms))
        max_fx_edges = int(self._clearing_max_fx_edges_limit)
        # 2026-08-20 / p007_t715: cleared volume is money and stays Decimal all
        # the way out of this engine. `float(cleared_amount_dec)` used to narrow
        # it here, at the source, so no downstream column type could restore it.
        cleared_amount_by_eq: dict[str, Decimal] = {
            str(eq): Decimal("0") for eq in equivalents
        }

        emitter = SseEventEmitter(sse=self._sse, utc_now=self._utc_now, logger=self._logger)

        session_local = async_session_local or db_session.AsyncSessionLocal
        run_pass = clearing_pass or run_clearing_pass
        # Participant UUID -> PID of the run, read from the run's own list (no DB lookup, no await): the runner's
        # progress is by UUID, debtor -> creditor; the tick's accounting and SSE are by PID, creditor -> debtor.
        with self._lock:
            pid_by_id = {participant_id: str(pid) for (participant_id, pid) in (run._real_participants or [])}

        for eq in equivalents:
            plan_id = f"plan_{secrets.token_hex(6)}"
            cleared_cycles = 0
            cleared_amount_dec = Decimal("0")
            touched_nodes: set[str] = set()
            touched_edges: set[tuple[str, str]] = set()
            cleared_amount_per_edge: dict[tuple[str, str], float] = {}
            partial_done_emitted = False

            def _account(occurrence) -> None:
                """Decision 10: called by the runner right after a commit, with no await in between."""

                nonlocal cleared_cycles, cleared_amount_dec
                cleared_cycles += 1
                cleared_amount_dec += occurrence.amount
                for edge in occurrence.edges:
                    debtor_pid = pid_by_id.get(edge.debtor_id)
                    creditor_pid = pid_by_id.get(edge.creditor_id)
                    if debtor_pid:
                        touched_nodes.add(debtor_pid)
                    if creditor_pid:
                        touched_nodes.add(creditor_pid)
                    if creditor_pid and debtor_pid:
                        # Trust-line direction (creditor, debtor): the snapshot links and edge patches use it.
                        edge_key = (creditor_pid, debtor_pid)
                        touched_edges.add(edge_key)
                        cleared_amount_per_edge[edge_key] = cleared_amount_per_edge.get(edge_key, 0.0) + float(
                            occurrence.amount
                        )

            try:
                eq_t0 = time.monotonic()
                self._logger.warning(
                    "simulator.real.clearing_eq_enter run_id=%s tick=%s eq=%s",
                    str(run.run_id),
                    int(run.tick_index),
                    str(eq),
                )
                with self._lock:
                    run.current_phase = "clearing"

                execution_error: Exception | None = None
                deadline = asyncio.get_running_loop().time() + effective_time_budget_ms / 1000.0
                try:
                    result = await run_pass(
                        session_local,
                        str(eq),
                        allowed_participant_pids=run_perimeter_pids(run),
                        on_committed=_account,
                        deadline=deadline,
                    )
                except ClearingPassCancelled as cancelled:
                    # Reported, then preserved: a cancellation during planning does NOT stop the planner worker,
                    # and the result says so (`planner_abandoned`); its late plan starts nothing.
                    self._logger.warning(
                        "simulator.real.clearing_pass_cancelled run_id=%s tick=%s eq=%s committed=%s "
                        "planner_abandoned=%s",
                        str(run.run_id),
                        int(run.tick_index),
                        str(eq),
                        len(cancelled.result.committed),
                        bool(cancelled.result.planner_abandoned),
                    )
                    raise
                except ClearingPassError as failed:
                    if cleared_cycles <= 0:
                        raise failed.cause
                    # Progress is durable: publish it below, then let the cause take its classification.
                    execution_error = failed.cause
                else:
                    self._logger.warning(
                        "simulator.real.clearing_pass_done run_id=%s tick=%s eq=%s status=%s reason=%s "
                        "committed=%s remaining_cycles=%s elapsed_ms=%s",
                        str(run.run_id),
                        int(run.tick_index),
                        str(eq),
                        result.status,
                        None if result.reason is None else result.reason.value,
                        len(result.committed),
                        result.remaining_cycles,
                        int((time.monotonic() - eq_t0) * 1000.0),
                    )

                if cleared_cycles <= 0:
                    with self._lock:
                        run.current_phase = None
                    continue

                cleared_amount_by_eq[str(eq)] = cleared_amount_dec

                async with session_local() as clearing_session:
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
                        # The `except: str(cleared_amount_dec)` fallback that used to sit here
                        # was the exponential-money escape hatch (`1E-8` on the wire);
                        # `to_money_str` is total and cannot raise, so there is nothing to
                        # fall back to.
                        cleared_amount_str = self._cleared_amount_str(
                            run, eq, cleared_amount_dec
                        )

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
                            # Recomputed now that the helper (and therefore the equivalent's
                            # precision) is certainly cached on the run.  Same function as the
                            # two other sites, so this can only add digits, never change form.
                            cleared_amount_str = self._cleared_amount_str(
                                run, eq, cleared_amount_dec
                            )

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

                        # `touched_edges` is tracked as (creditor_pid, debtor_pid) because that's the
                        # trustline direction used by snapshot links (source->target) and edge patches.
                        # However, some call-sites / older data may treat edges as (debtor->creditor).
                        # To keep UI highlighting stable, prefer edges that exist in the scenario-topology
                        # cache for this run/equivalent, and fall back gracefully if the cache is missing.
                        with self._lock:
                            topo_edges = set(
                                ((run._edges_by_equivalent or {}).get(str(eq)) or [])
                            )

                        out_edges: list[dict[str, str]] = []
                        limit_n = max(1, int(max_fx_edges))
                        if topo_edges:
                            for a, b in pairs:
                                if (a, b) in topo_edges:
                                    out_edges.append({"from": a, "to": b})
                                elif (b, a) in topo_edges:
                                    out_edges.append({"from": b, "to": a})
                                if len(out_edges) >= limit_n:
                                    break
                        else:
                            for a, b in pairs[:limit_n]:
                                out_edges.append({"from": a, "to": b})

                        if out_edges:
                            done_cycle_edges = out_edges
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
                    partial_done_emitted = True

                    self._logger.warning(
                        "simulator.real.clearing_eq_done run_id=%s tick=%s eq=%s elapsed_ms=%s cleared_cycles=%s",
                        str(run.run_id),
                        int(run.tick_index),
                        str(eq),
                        int((time.monotonic() - eq_t0) * 1000.0),
                        int(cleared_cycles),
                    )
                    if execution_error is not None:
                        raise execution_error
            except asyncio.CancelledError:
                if cleared_cycles > 0 and not partial_done_emitted:
                    cleared_amount_by_eq[str(eq)] = cleared_amount_dec
                    with self._lock:
                        run.last_event_type = "clearing.done"
                        run.current_phase = None
                        topology_edges = set(
                            ((run._edges_by_equivalent or {}).get(str(eq)) or [])
                        )

                    fallback_edges: list[dict[str, str]] = []
                    for from_pid, to_pid in sorted(touched_edges):
                        if topology_edges and (from_pid, to_pid) not in topology_edges:
                            if (to_pid, from_pid) in topology_edges:
                                from_pid, to_pid = to_pid, from_pid
                            else:
                                continue
                        fallback_edges.append({"from": from_pid, "to": to_pid})
                        if len(fallback_edges) >= max(1, max_fx_edges):
                            break

                    try:
                        emitter.emit_clearing_done(
                            run_id=run_id,
                            run=run,
                            equivalent=eq,
                            plan_id=plan_id or f"plan_{secrets.token_hex(6)}",
                            cleared_cycles=cleared_cycles,
                            # Was a hard-coded `Decimal("0.01")` while the happy path above
                            # used `Equivalent.precision`: one field, two scales, chosen by
                            # whether the clearing was cancelled.
                            cleared_amount=self._cleared_amount_str(
                                run, eq, cleared_amount_dec
                            ),
                            cycle_edges=fallback_edges or None,
                            node_patch=None,
                            edge_patch=None,
                        )
                    except Exception:
                        self._logger.warning(
                            "simulator.real.clearing_cancel_partial_emit_failed "
                            "run_id=%s tick=%s eq=%s",
                            str(run.run_id),
                            int(run.tick_index),
                            str(eq),
                            exc_info=True,
                        )
                raise
            except Exception as exc:
                refusal_reason = (
                    (exc.details or {}).get("reason")
                    if isinstance(exc, ConflictException)
                    else None
                )
                if refusal_reason in MoneyBoundary.MONEY_STOP_REASONS:
                    # T1544: the operator's stop refuses clearing in THIS equivalent; it is not a
                    # failure of the run. It arrives here unwrapped - `ClearingService` re-raises the
                    # refusal itself, and the loop above re-raises it (at once, or after a partial
                    # `clearing.done`). Same rule as a refused payment: skip the equivalent without
                    # touching `errors_total`, `_error_timestamps` or `last_error`, which would
                    # otherwise be spent on every clearing tick until the run-level error limit.
                    # Step 5c: an integrity hold is skipped identically, its reason in the marker.
                    self._logger.info(
                        "simulator.real.clearing_refused_%s run_id=%s tick=%s eq=%s exc=%s",
                        refusal_reason,
                        str(run.run_id),
                        int(run.tick_index),
                        str(eq),
                        type(exc).__name__,
                    )
                    # The phase was set to "clearing" above and the normal resets were skipped.
                    with self._lock:
                        run.current_phase = None
                    continue
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
                        "message": GeoException().message,
                        "at": self._utc_now().isoformat(),
                    }
                    run.current_phase = None
                continue

        return cleared_amount_by_eq
