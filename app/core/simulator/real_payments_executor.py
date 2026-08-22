from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from sqlalchemy import select

from app.config import settings
from app.core.payments.service import PaymentPostCommitEffects, PaymentService
from app.core.simulator.edge_patch_builder import EdgePatchBuilder
from app.core.simulator.rejection_codes import map_rejection_code
from app.core.simulator.sse_broadcast import SseBroadcast, SseEventEmitter
from app.core.simulator.viz_patch_helper import VizPatchHelper
from app.core.simulator.models import RunRecord
from app.core.simulator.run_perimeter import run_perimeter_pids
from app.db.models.participant import Participant
from app.utils.exceptions import (
    GeoException,
    RetryablePaymentConflictException,
    TimeoutException,
)


@dataclass(frozen=True)
class _PaymentObservation:
    seq: int
    outcome: Literal["committed", "rejected", "error"]
    equivalent: str
    sender_pid: str
    receiver_pid: str
    amount: str
    edges: list[dict[str, str]]
    payment_effects: PaymentPostCommitEffects | None = None
    edge_patch: list[dict[str, Any]] | None = None
    node_patch: list[dict[str, Any]] | None = None
    error_code: str | None = None
    error_details: dict[str, Any] | None = None


@dataclass
class DeferredRealPaymentEffects:
    """Ordered observations resolved after the enclosing transaction outcome."""

    lock: Any
    emitter: SseEventEmitter
    logger: logging.Logger
    utc_now: Callable[[], Any]
    run_id: str
    run: RunRecord
    items: list[_PaymentObservation] = field(default_factory=list)
    _resolution: Literal["commit", "rollback", "unknown"] | None = field(
        default=None,
        init=False,
        repr=False,
    )

    def apply_once(self) -> bool:
        return self.apply_after_commit()

    def apply_after_commit(self) -> bool:
        return self._resolve("commit")

    def apply_after_rollback(self) -> bool:
        return self._resolve("rollback")

    def apply_after_unknown_transaction_outcome(self) -> bool:
        return self._resolve("unknown")

    def _resolve(
        self,
        resolution: Literal["commit", "rollback", "unknown"],
    ) -> bool:
        if self._resolution is not None:
            return False
        self._resolution = resolution

        for item in sorted(self.items, key=lambda observation: observation.seq):
            if resolution != "commit" and item.outcome == "committed":
                if resolution == "unknown" and item.payment_effects is not None:
                    try:
                        item.payment_effects.invalidate_routing_cache_once()
                    except Exception:
                        self.logger.warning(
                            "simulator.real.payment_unknown_cache_invalidation_failed "
                            "run_id=%s seq=%s",
                            self.run_id,
                            item.seq,
                            exc_info=True,
                        )
                continue
            try:
                self._apply_observation(item)
            except Exception:
                # One malformed/broken observation must not suppress later seq items.
                self.logger.warning(
                    "simulator.real.payment_observation_failed run_id=%s seq=%s outcome=%s",
                    self.run_id,
                    item.seq,
                    item.outcome,
                    exc_info=True,
                )
        return True

    def _apply_observation(self, item: _PaymentObservation) -> None:
        if item.outcome == "committed":
            if item.payment_effects is not None:
                try:
                    item.payment_effects.apply_once()
                except Exception:
                    self.logger.warning(
                        "simulator.real.payment_post_commit_effect_failed run_id=%s seq=%s",
                        self.run_id,
                        item.seq,
                        exc_info=True,
                    )

            try:
                self.emitter.emit_tx_updated(
                    run_id=self.run_id,
                    run=self.run,
                    equivalent=item.equivalent,
                    from_pid=item.sender_pid,
                    to_pid=item.receiver_pid,
                    amount=item.amount,
                    amount_flyout=True,
                    ttl_ms=1200,
                    edges=[dict(edge) for edge in item.edges],
                    node_badges=None,
                    edge_patch=item.edge_patch,
                    node_patch=item.node_patch,
                )
            except Exception:
                self.logger.warning(
                    "simulator.real.post_commit_tx_updated_failed run_id=%s tx_from=%s tx_to=%s",
                    self.run_id,
                    item.sender_pid,
                    item.receiver_pid,
                    exc_info=True,
                )
            with self.lock:
                self.run.last_event_type = "tx.updated"
                self.run.attempts_total += 1
                self.run.committed_total += 1
            return

        if item.outcome == "rejected":
            details = (item.error_details or {}).get("details")
            if (
                isinstance(details, dict)
                and str(details.get("invariant") or "") == "PAYMENT_DELTA_DRIFT"
            ):
                try:
                    self.emitter.emit_audit_drift(
                        run_id=self.run_id,
                        run=self.run,
                        equivalent=item.equivalent,
                        tick_index=int(self.run.tick_index or 0),
                        severity="critical",
                        total_drift=str(details.get("total_drift") or "0"),
                        drifts=list(details.get("drifts") or []),
                        source="delta_check",
                    )
                except Exception:
                    self.logger.warning(
                        "simulator.real.audit_drift_observation_failed run_id=%s seq=%s",
                        self.run_id,
                        item.seq,
                        exc_info=True,
                    )

            with self.lock:
                self.run.last_event_type = "tx.failed"
                self.run.attempts_total += 1
                self.run.rejected_total += 1
        else:
            with self.lock:
                self.run.attempts_total += 1
                self.run.errors_total += 1
                if item.error_code == "PAYMENT_TIMEOUT":
                    self.run.timeouts_total += 1
                self.run._error_timestamps.append(time.time())
                cutoff = time.time() - 60.0
                while (
                    self.run._error_timestamps
                    and self.run._error_timestamps[0] < cutoff
                ):
                    self.run._error_timestamps.popleft()
                self.run.last_error = {
                    "code": str(item.error_code or "INTERNAL_ERROR"),
                    "message": str(
                        (item.error_details or {}).get("message")
                        or item.error_code
                        or "INTERNAL_ERROR"
                    ),
                    "at": self.utc_now().isoformat(),
                }
                self.run.last_event_type = "tx.failed"

        error_code = str(item.error_code or "PAYMENT_REJECTED")
        try:
            self.emitter.emit_tx_failed(
                run_id=self.run_id,
                run=self.run,
                equivalent=item.equivalent,
                from_pid=item.sender_pid,
                to_pid=item.receiver_pid,
                error_code=error_code,
                error_message=str(
                    (item.error_details or {}).get("message") or error_code
                ),
                error_details=item.error_details,
            )
        except Exception:
            self.logger.warning(
                "simulator.real.tx_failed_observation_failed run_id=%s seq=%s",
                self.run_id,
                item.seq,
                exc_info=True,
            )


@dataclass(frozen=True)
class RealPaymentsResult:
    committed: int
    rejected: int
    errors: int
    timeouts: int
    stall_ticks: int
    per_eq: dict[str, dict[str, int]]
    per_eq_route: dict[str, dict[str, float]]
    per_eq_edge_stats: dict[str, dict[tuple[str, str], dict[str, int]]]
    # Per-eq rejection codes breakdown.
    # Contract: always a dict (never None) for downstream consumers (adaptive policy).
    rejection_codes_by_eq: dict[str, dict[str, int]] = field(default_factory=dict)
    deferred_effects: DeferredRealPaymentEffects | None = None
    stop_requested: bool = False


class RealPaymentsExecutor:
    def __init__(
        self,
        *,
        lock,
        sse: SseBroadcast,
        utc_now,
        logger: logging.Logger,
        edge_patch_builder: EdgePatchBuilder,
        should_warn_this_tick: Callable[[RunRecord, str], bool],
        sim_idempotency_key: Callable[..., str],
    ) -> None:
        self._lock = lock
        self._sse = sse
        self._utc_now = utc_now
        self._logger = logger
        self._edge_patch_builder = edge_patch_builder
        self._should_warn_this_tick = should_warn_this_tick
        self._sim_idempotency_key = sim_idempotency_key

    async def execute_planned_payments(
        self,
        *,
        session,
        run_id: str,
        run: RunRecord,
        planned: list[Any],
        equivalents: list[str],
        sender_id_by_pid: dict[str, uuid.UUID],
        max_in_flight: int,
        max_timeouts_per_tick: int,
        fail_run: Callable[[str, str, str], Any],
    ) -> RealPaymentsResult:
        committed = 0
        rejected = 0
        errors = 0
        timeouts = 0

        emitter = SseEventEmitter(sse=self._sse, utc_now=self._utc_now, logger=self._logger)
        deferred_effects = DeferredRealPaymentEffects(
            lock=self._lock,
            emitter=emitter,
            logger=self._logger,
            utc_now=self._utc_now,
            run_id=str(run_id),
            run=run,
        )

        planned_equivalents = sorted(
            {
                str(action.equivalent).strip().upper()
                for action in (planned or [])
                if str(action.equivalent).strip()
            }
        )
        if planned_equivalents:
            await PaymentService(session).acquire_staged_equivalent_owner_locks(
                planned_equivalents
            )

        sem = asyncio.Semaphore(max(1, int(max_in_flight)))
        action_db_lock = asyncio.Lock()

        per_eq: dict[str, dict[str, int]] = {
            str(eq): {"committed": 0, "rejected": 0, "errors": 0, "timeouts": 0}
            for eq in equivalents
        }
        per_eq_route: dict[str, dict[str, float]] = {
            str(eq): {"route_len_sum": 0.0, "route_len_n": 0.0} for eq in equivalents
        }
        per_eq_edge_stats: dict[str, dict[tuple[str, str], dict[str, int]]] = {
            str(eq): {} for eq in equivalents
        }
        rejection_codes_by_eq: dict[str, dict[str, int]] = {
            str(eq): {} for eq in equivalents
        }

        def _rejection_code_inc(eq: str, code: str) -> None:
            m = rejection_codes_by_eq.setdefault(str(eq), {})
            m[code] = int(m.get(code, 0)) + 1

        def _edge_inc(eq: str, src: str, dst: str, key: str, n: int = 1) -> None:
            m = per_eq_edge_stats.setdefault(str(eq), {})
            st = m.setdefault(
                (str(src), str(dst)),
                {
                    "attempts": 0,
                    "committed": 0,
                    "rejected": 0,
                    "errors": 0,
                    "timeouts": 0,
                },
            )
            st[key] = int(st.get(key, 0)) + int(n)

        async def _do_one(action: Any) -> tuple[
            int,
            str,
            str,
            str,
            str | None,
            str | None,
            dict[str, Any] | None,
            float,
            list[tuple[str, str]],
            PaymentPostCommitEffects | None,
        ]:
            """Execute one action under a SAVEPOINT and return its staged result."""

            sender_id = sender_id_by_pid.get(str(action.sender_pid))
            if sender_id is None:
                return (
                    int(action.seq),
                    str(action.equivalent),
                    str(action.sender_pid),
                    str(action.receiver_pid),
                    None,  # amount
                    None,  # status
                    "SENDER_NOT_FOUND",
                    {"reason": "SENDER_NOT_FOUND"},
                    0.0,
                    [],
                    None,
                )

            idem = self._sim_idempotency_key(
                run_id=run.run_id,
                tick_ms=run.tick_index,
                sender_pid=str(action.sender_pid),
                receiver_pid=str(action.receiver_pid),
                equivalent=str(action.equivalent),
                amount=str(action.amount),
                seq=int(action.seq),
            )

            async with sem:
                with self._lock:
                    run._real_in_flight += 1

                try:
                    async with action_db_lock:
                        async with session.begin_nested():
                            service = PaymentService(session)
                            staged = await service.create_payment_internal_staged(
                                sender_id,
                                to_pid=str(action.receiver_pid),
                                equivalent=str(action.equivalent),
                                amount=str(action.amount),
                                allowed_participant_pids=run_perimeter_pids(run),
                                idempotency_key=idem,
                            )

                    res = staged.result

                    status = str(res.status or "")

                    routes = res.routes or []

                    route_edges: list[tuple[str, str]] = []
                    for r in routes:
                        path = r.path
                        if len(path) < 2:
                            continue
                        route_edges = [(str(a), str(b)) for a, b in zip(path, path[1:])]
                        if route_edges:
                            break

                    avg_route_len = 0.0
                    lens = [float(len(r.path) - 1) for r in routes if len(r.path) >= 2]
                    if lens:
                        avg_route_len = float(sum(lens) / len(lens))

                    return (
                        int(action.seq),
                        str(action.equivalent),
                        str(action.sender_pid),
                        str(action.receiver_pid),
                        str(action.amount),
                        status,
                        None,
                        None,
                        float(avg_route_len),
                        route_edges,
                        staged.post_commit_effects,
                    )
                except RetryablePaymentConflictException:
                    # The outer tick transaction is no longer safe to use. Do not
                    # count a transient conflict as a terminal payment rejection;
                    # propagate so the tick-level rollback/replay policy owns it.
                    raise
                except Exception as e:
                    code = "INTERNAL_ERROR"
                    status = None
                    err_details: dict[str, Any] | None = {
                        "exc": type(e).__name__,
                        "message": str(e),
                    }

                    if isinstance(e, TimeoutException):
                        code = "PAYMENT_TIMEOUT"
                        err_details = {
                            "exc": type(e).__name__,
                            "geo_code": getattr(e, "code", None),
                            "message": getattr(e, "message", str(e)),
                            "details": getattr(e, "details", None),
                        }
                    elif isinstance(e, GeoException):
                        geo_status = int(getattr(e, "status_code", 500) or 500)
                        if 400 <= geo_status < 500:
                            status = "REJECTED"
                            code = None
                        err_details = {
                            "exc": type(e).__name__,
                            "geo_code": getattr(e, "code", None),
                            "message": getattr(e, "message", str(e)),
                            "details": getattr(e, "details", None),
                            "status_code": geo_status,
                        }

                    return (
                        int(action.seq),
                        str(action.equivalent),
                        str(action.sender_pid),
                        str(action.receiver_pid),
                        str(getattr(action, "amount", "") or ""),
                        status,
                        code,
                        err_details,
                        0.0,
                        [],
                        None,
                    )
                finally:
                    with self._lock:
                        run._real_in_flight = max(0, run._real_in_flight - 1)

        tasks = [asyncio.create_task(_do_one(a)) for a in (planned or [])]

        next_seq = 0
        ready: dict[
            int,
            tuple[
                str,
                str,
                str,
                str,
                str | None,
                str | None,
                dict[str, Any] | None,
                float,
                list[tuple[str, str]],
                list[dict[str, Any]] | None,
                list[dict[str, Any]] | None,
                PaymentPostCommitEffects | None,
            ],
        ] = {}

        def _inc(eq: str, key: str, n: int = 1) -> None:
            d = per_eq.setdefault(
                str(eq), {"committed": 0, "rejected": 0, "errors": 0, "timeouts": 0}
            )
            d[key] = int(d.get(key, 0)) + int(n)

        def _route_add(eq: str, route_len: float) -> None:
            d = per_eq_route.setdefault(str(eq), {"route_len_sum": 0.0, "route_len_n": 0.0})
            d["route_len_sum"] = float(d.get("route_len_sum", 0.0)) + float(route_len)
            d["route_len_n"] = float(d.get("route_len_n", 0.0)) + 1.0

        def _record_if_ready() -> None:
            nonlocal next_seq, committed, rejected, errors, timeouts
            while True:
                item = ready.get(next_seq)
                if item is None:
                    return
                del ready[next_seq]

                (
                    eq,
                    sender_pid,
                    receiver_pid,
                    amount,
                    status,
                    err_code,
                    err_details,
                    avg_route_len,
                    route_edges,
                    edge_patch,
                    node_patch,
                    payment_effects,
                ) = item

                edges_pairs = route_edges or [(sender_pid, receiver_pid)]

                if err_code is not None:
                    errors += 1
                    _inc(eq, "errors")
                    for a, b in edges_pairs:
                        _edge_inc(eq, a, b, "attempts")
                        _edge_inc(eq, a, b, "errors")
                    if err_code == "PAYMENT_TIMEOUT":
                        timeouts += 1
                        _inc(eq, "timeouts")
                        for a, b in edges_pairs:
                            _edge_inc(eq, a, b, "timeouts")
                    if err_code == "PAYMENT_REJECTED":
                        _inc(eq, "rejected")
                        for a, b in edges_pairs:
                            _edge_inc(eq, a, b, "rejected")

                    deferred_effects.items.append(
                        _PaymentObservation(
                            seq=next_seq,
                            outcome="error",
                            equivalent=eq,
                            sender_pid=sender_pid,
                            receiver_pid=receiver_pid,
                            amount=amount,
                            edges=[{"from": a, "to": b} for a, b in edges_pairs],
                            error_code=str(err_code),
                            error_details=err_details,
                        )
                    )
                else:
                    for a, b in edges_pairs:
                        _edge_inc(eq, a, b, "attempts")

                    if status == "COMMITTED":
                        committed += 1
                        _inc(eq, "committed")
                        for a, b in edges_pairs:
                            _edge_inc(eq, a, b, "committed")
                        if float(avg_route_len) > 0:
                            _route_add(eq, float(avg_route_len))

                        deferred_effects.items.append(
                            _PaymentObservation(
                                seq=next_seq,
                                outcome="committed",
                                payment_effects=payment_effects,
                                equivalent=eq,
                                sender_pid=sender_pid,
                                receiver_pid=receiver_pid,
                                amount=amount,
                                edges=[
                                    {"from": a, "to": b} for a, b in edges_pairs
                                ],
                                edge_patch=edge_patch,
                                node_patch=node_patch,
                            )
                        )
                    else:
                        rejected += 1
                        _inc(eq, "rejected")
                        for a, b in edges_pairs:
                            _edge_inc(eq, a, b, "rejected")

                        try:
                            rejection_code = map_rejection_code(err_details)
                        except Exception:
                            if self._should_warn_this_tick(
                                run, key="map_rejection_code_failed"
                            ):
                                self._logger.debug(
                                    "simulator.real.map_rejection_code_failed run_id=%s tick=%s",
                                    str(run.run_id),
                                    int(run.tick_index),
                                    exc_info=True,
                                )
                            rejection_code = "PAYMENT_REJECTED"

                        _rejection_code_inc(eq, rejection_code)
                        deferred_effects.items.append(
                            _PaymentObservation(
                                seq=next_seq,
                                outcome="rejected",
                                equivalent=eq,
                                sender_pid=sender_pid,
                                receiver_pid=receiver_pid,
                                amount=amount,
                                edges=[{"from": a, "to": b} for a, b in edges_pairs],
                                error_code=str(rejection_code),
                                error_details=err_details,
                            )
                        )

                with self._lock:
                    run.queue_depth = max(0, run.queue_depth - 1)

                next_seq += 1

        stop_requested = False
        timeout_stop_triggered = False

        try:
            if tasks:
                patch_session = session

                per_tick_pid_to_participant_by_eq_and_pids: dict[
                    tuple[str, tuple[str, ...]],
                    dict[str, Participant],
                ] = {}
                per_tick_quantiles_refreshed_by_eq: set[str] = set()

                emitted_since_yield = 0

                for t in asyncio.as_completed(tasks):
                    (
                        seq,
                        eq,
                        sender_pid,
                        receiver_pid,
                        amount,
                        status,
                        err_code,
                        err_details,
                        avg_route_len,
                        route_edges,
                        payment_effects,
                    ) = await t

                    if run.state != "running":
                        stop_requested = True

                    edge_patch_list: list[dict[str, Any]] | None = None
                    node_patch_list: list[dict[str, Any]] | None = None

                    async with action_db_lock:
                        try:
                            if status == "COMMITTED" and not stop_requested:
                                edges_pairs = route_edges or [(sender_pid, receiver_pid)]

                                helper: VizPatchHelper | None
                                with self._lock:
                                    helper = run._real_viz_by_eq.get(str(eq))

                                if helper is None:
                                    helper = await VizPatchHelper.create(
                                        patch_session,
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

                                participant_ids: list[uuid.UUID] = []
                                if run._real_participants:
                                    participant_ids = [pid for (pid, _) in run._real_participants]
                                if str(eq) not in per_tick_quantiles_refreshed_by_eq:
                                    await helper.maybe_refresh_quantiles(
                                        patch_session,
                                        tick_index=int(run.tick_index),
                                        participant_ids=participant_ids,
                                    )
                                    per_tick_quantiles_refreshed_by_eq.add(str(eq))

                                pids = sorted({pid for ab in edges_pairs for pid in ab if pid})
                                pids_key = (str(eq), tuple(pids))

                                pid_to_participant = per_tick_pid_to_participant_by_eq_and_pids.get(pids_key)
                                if pid_to_participant is None:
                                    res = await patch_session.execute(
                                        select(Participant).where(Participant.pid.in_(pids))
                                    )
                                    pid_to_participant = {p.pid: p for p in res.scalars().all()}
                                    per_tick_pid_to_participant_by_eq_and_pids[pids_key] = pid_to_participant

                                try:
                                    # Do NOT cache node patches within a tick: net balances can
                                    # change multiple times per tick, and caching would make UI
                                    # updates appear delayed/stale.
                                    node_patch_list = await helper.compute_node_patches(
                                        patch_session,
                                        pid_to_participant=pid_to_participant,
                                        pids=pids,
                                    )
                                    if node_patch_list == []:
                                        node_patch_list = None
                                except Exception:
                                    if self._should_warn_this_tick(run, key=f"node_patch_failed:{eq}"):
                                        self._logger.warning(
                                            "simulator.real.node_patch_failed run_id=%s tick=%s eq=%s",
                                            str(run.run_id),
                                            int(run.tick_index),
                                            str(eq),
                                            exc_info=True,
                                        )
                                    node_patch_list = None

                                edge_patch_list = await self._edge_patch_builder.build_edge_patch_for_pairs(
                                    session=patch_session,
                                    helper=helper,
                                    edges_pairs=edges_pairs,
                                    pid_to_participant=pid_to_participant,
                                )
                        except Exception:
                            if self._should_warn_this_tick(run, key=f"edge_patch_failed:{eq}"):
                                self._logger.warning(
                                    "simulator.real.edge_patch_failed run_id=%s tick=%s eq=%s",
                                    str(run.run_id),
                                    int(run.tick_index),
                                    str(eq),
                                    exc_info=True,
                                )
                            edge_patch_list = None
                            node_patch_list = None

                    if edge_patch_list == []:
                        edge_patch_list = None

                    ready[int(seq)] = (
                        str(eq),
                        str(sender_pid),
                        str(receiver_pid),
                        str(amount),
                        str(status) if status is not None else None,
                        str(err_code) if err_code is not None else None,
                        err_details,
                        float(avg_route_len),
                        route_edges,
                        edge_patch_list,
                        node_patch_list,
                        payment_effects,
                    )
                    _record_if_ready()

                    emitted_since_yield += 1
                    if emitted_since_yield % 5 == 0:
                        await asyncio.sleep(0)

                    if (
                        max_timeouts_per_tick > 0
                        and timeouts >= max_timeouts_per_tick
                        and not timeout_stop_triggered
                    ):
                        timeout_stop_triggered = True
                        try:
                            await fail_run(
                                run_id,
                                "REAL_MODE_TOO_MANY_TIMEOUTS",
                                f"Too many payment timeouts in one tick: {timeouts}",
                            )
                        except Exception:
                            self._logger.warning(
                                "simulator.real.fail_run_after_timeout_failed run_id=%s",
                                str(run_id),
                                exc_info=True,
                            )
                        stop_requested = True
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

        _record_if_ready()
        if run.state != "running":
            stop_requested = True

        with self._lock:
            run._real_in_flight = 0
            run.queue_depth = 0
            run.current_phase = None
            run._real_consec_tick_failures = 0

            if len(planned) > 0 and committed == 0 and errors == 0:
                run._real_consec_all_rejected_ticks += 1
            else:
                run._real_consec_all_rejected_ticks = 0

            stall_ticks = run._real_consec_all_rejected_ticks

        return RealPaymentsResult(
            committed=int(committed),
            rejected=int(rejected),
            errors=int(errors),
            timeouts=int(timeouts),
            stall_ticks=int(stall_ticks),
            per_eq=per_eq,
            per_eq_route=per_eq_route,
            per_eq_edge_stats=per_eq_edge_stats,
            rejection_codes_by_eq=rejection_codes_by_eq,
            deferred_effects=deferred_effects,
            stop_requested=stop_requested,
        )
