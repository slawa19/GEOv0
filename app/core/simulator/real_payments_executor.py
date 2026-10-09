from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field, replace
from typing import Any, Awaitable, Callable, Literal

from sqlalchemy import select

from app.config import settings
from app.core.payments.service import (
    PaymentPostCommitEffects,
    PaymentService,
    PaymentTransactionUnusable,
)
from app.core.simulator.edge_patch_builder import EdgePatchBuilder, line_pairs_of_payment_hops
from app.core.simulator.rejection_codes import map_rejection_code
from app.core.simulator.sse_broadcast import SseBroadcast, SseEventEmitter, schedule_closed_trustlines_publication
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
    # 026 `T2603.2`: trust lines this payment's book operation closed (creditor, debtor PIDs).
    closed_edges: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class _PaymentPatches:
    """The visual patches of one committed payment, read after its commit (034 `F-034-2`)."""

    edge_patch: list[dict[str, Any]] | None = None
    node_patch: list[dict[str, Any]] | None = None
    closed_edges: tuple[tuple[str, str], ...] = ()


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
    # 026 `T2603.2`: the closure publications this buffer's commit scheduled (re-read outside the commit callback).
    closed_publications: list[asyncio.Task] = field(default_factory=list)
    # 034 `F-034-2`: builds the visual patches of the committed observations AFTER the commit, on a session of its
    # own (`RealPaymentsExecutor.build_patches_after_commit`). (open_session, committed items) -> patches by seq.
    patch_builder: Callable[[Callable[[], Any], list[_PaymentObservation]], Awaitable[dict[int, _PaymentPatches]]] | None = None
    _resolution: Literal["commit", "rollback", "unknown", "discarded"] | None = field(
        default=None,
        init=False,
        repr=False,
    )

    async def build_post_commit_patches(self, open_session: Callable[[], Any]) -> None:
        """Give the committed observations their `edge_patch` / `node_patch` / closed lines, read AFTER the commit.

        034 `F-034-2`. Called by the owner of the money phase once its commit is CONFIRMED and before
        `apply_after_commit` publishes (`money_replay._publish_committed`). The patches used to be built inside the
        money transaction, on the money session: a PostgreSQL error in a patch query aborted that transaction, the
        `COMMIT` that followed was answered with a rollback, and the tick still reported the payments as made.
        Now nothing here can touch the money: it runs on `open_session()` and the money is already durable. A
        failure costs the patches - the events are published without them - and is logged; it never raises.
        """

        if self._resolution is not None or self.patch_builder is None:
            return
        committed = [item for item in self.items if item.outcome == "committed"]
        if not committed:
            return
        try:
            patches = await self.patch_builder(open_session, committed)
        except Exception:
            self.logger.warning(
                "simulator.real.payment_patches_failed run_id=%s committed=%d",
                self.run_id,
                len(committed),
                exc_info=True,
            )
            return
        self.items = [
            replace(
                item,
                edge_patch=patches[item.seq].edge_patch,
                node_patch=patches[item.seq].node_patch,
                closed_edges=patches[item.seq].closed_edges,
            )
            if item.outcome == "committed" and item.seq in patches
            else item
            for item in self.items
        ]

    def apply_once(self) -> bool:
        """Resolve this buffer as committed, once.

        PROGRAMME 015 / P1: THIS GUARD PROTECTS ONE BUFFER INSTANCE AND NOTHING MORE. The tick's
        money phase is now replayed on a transient conflict, and every attempt builds a NEW
        `DeferredRealPaymentEffects`, so "publish exactly once per tick" is not something this
        guard can deliver on its own. What delivers it is `discard()` below: a superseded attempt's
        buffer is destroyed before the next attempt is allowed to start, so only the buffer of the
        attempt that actually committed can ever publish. See `app/core/simulator/money_replay.py`.
        """
        return self.apply_after_commit()

    def apply_after_commit(self) -> bool:
        return self._resolve("commit")

    def apply_after_rollback(self) -> bool:
        return self._resolve("rollback")

    def apply_after_unknown_transaction_outcome(self) -> bool:
        return self._resolve("unknown")

    def discard(self) -> bool:
        """Destroy this attempt's observations WITHOUT publishing any of them.

        Programme 015 / P1. Used only when the money phase is about to be replayed: the attempt
        was rolled back, nothing of it is durable, and the replay will re-plan the load from a
        fresh debt snapshot. Publishing here would emit terminal SSE (`tx.updated` / `tx.failed`)
        and move business counters for payments that never happened and are about to be planned
        again under a possibly different amount and `seq`.

        The items are dropped as well as the resolution being marked, so a later caller cannot
        reach them. Every `apply_after_*` on this buffer returns False from here on, which is what
        makes the rule "publish exactly once, after the commit" hold across attempts.
        """
        if self._resolution is not None:
            return False
        self._resolution = "discarded"
        self.items = []
        return True

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
            # Only here, on a confirmed commit: a rollback, an unknown outcome or a discarded attempt never removes.
            task = schedule_closed_trustlines_publication(emitter=self.emitter, lock=self.lock, run_id=self.run_id,
                                                          run=self.run, equivalent=item.equivalent,
                                                          pairs=item.closed_edges)
            if task is not None:
                self.closed_publications.append(task)
            with self.lock:
                self.run.last_event_type = "tx.updated"
                self.run.attempts_total += 1
                self.run.committed_total += 1
            return

        if item.outcome == "rejected":
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
    deferred_effects: DeferredRealPaymentEffects | None = None
    stop_requested: bool = False
    # Programme 015 / P1: the `tx_id` of every payment this call actually staged. These are the
    # ORIGINAL identifiers the replay reads when a commit's outcome is unknown, to establish
    # whether the attempt landed before it is allowed to decide anything.
    staged_tx_ids: frozenset[str] = frozenset()
    # 036 B1 fix-delta: the seqs whose payment ended WITHOUT an established outcome - an exception that is not a definitive
    # business refusal (a timeout, a 5xx, an unexpected error). What the payment did is not known from here: the handler
    # also covers work after the savepoint was released, and a durable row may exist (a replay that timed out while reading
    # it). A scripted event whose payment is in this set is NOT spent: the next tick runs it again under the SAME key, and
    # the payment service answers a repeat with the stored payment if one exists, so the debt moves at most once. A
    # committed payment, a 4xx refusal and a durable ABORTED row returned by the core are terminal and are not in it.
    unresolved_seqs: frozenset[int] = frozenset()


def _classify_refusal(e: BaseException) -> tuple[str | None, str | None, dict[str, Any] | None]:
    """(status, code, err_details) of a refused staged payment - one rule for a raised refusal and for
    a refusal returned as a structured `ABORTED` result (`StagedPaymentResult.refusal`, 019 `T1905`).

    `status` REJECTED with `code` None is a business rejection (a 4xx); `code` PAYMENT_TIMEOUT or
    INTERNAL_ERROR is an error of the run.
    """

    code: str | None = "INTERNAL_ERROR"
    status: str | None = None
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
    return status, code, err_details


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
            patch_builder=lambda open_session, items: self.build_patches_after_commit(
                open_session=open_session, run=run, items=items
            ),
        )

        # 027 stage 2: the caller owns the transaction AND its line locks - the tick's money phase takes its complete
        # set (`PaymentService.lock_staged_lines`) as its first statement, before planning; no lock is taken here.
        sem = asyncio.Semaphore(max(1, int(max_in_flight)))
        action_db_lock = asyncio.Lock()

        # Programme 015 / P1: recorded as each payment is staged, not at the end, because the
        # conflict this exists for propagates out of this function and the prefix that was already
        # staged is exactly what has to be identifiable afterwards.
        staged_tx_ids: set[str] = set()
        unresolved: set[int] = set()

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
            if sender_id is None and isinstance(getattr(action, "idempotency_key", None), str):
                # 036 B1: a scripted payment of a sender the run does not hold is a REFUSAL of the story (the participant
                # may not be introduced yet), not an error of the run: it is reported as `tx.failed` and does not spend
                # the error budget. A planned payment keeps the old classification below.
                return (
                    int(action.seq),
                    str(action.equivalent),
                    str(action.sender_pid),
                    str(action.receiver_pid),
                    str(action.amount),
                    "REJECTED",
                    None,
                    {"reason": "SENDER_NOT_FOUND"},
                    0.0,
                    [],
                    None,
                )
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

            event_key = getattr(action, "idempotency_key", None)
            idem = event_key if isinstance(event_key, str) and event_key else self._sim_idempotency_key(
                run_id=run.run_id,
                tick_ms=run.tick_index,
                sender_pid=str(action.sender_pid),
                receiver_pid=str(action.receiver_pid),
                equivalent=str(action.equivalent),
                amount=str(action.amount),
                seq=int(action.seq),
                epoch=int(getattr(run, "_launch_epoch", 0) or 0),
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
                            tx_id = str(getattr(staged.result, "tx_id", "") or "")
                            # LANDING EVIDENCE is only a row THIS phase wrote (019 stage-3 review,
                            # P2 #2): a fresh payment or a refusal recorded here. A stored result
                            # answered by idempotency or by the identity resolver is a row of an
                            # EARLIER transaction; counting it let a historical row prove that this
                            # phase's unknown commit landed.
                            if tx_id and staged.written_here:
                                staged_tx_ids.add(tx_id)

                    res = staged.result

                    status = str(res.status or "")
                    if staged.refusal is not None:
                        # A definitive refusal recorded ABORTED in this savepoint (019 `T1905`): durable
                        # with the tick, and classified exactly as the same refusal raised used to be.
                        refused_status, refused_code, refused_details = _classify_refusal(staged.refusal)
                        return (
                            int(action.seq),
                            str(action.equivalent),
                            str(action.sender_pid),
                            str(action.receiver_pid),
                            str(getattr(action, "amount", "") or ""),
                            refused_status,
                            refused_code,
                            refused_details,
                            0.0,
                            [],
                            None,
                        )

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
                except PaymentTransactionUnusable as unusable:
                    # The payment left the TICK'S transaction unusable (019 `T1912`). Nothing of this
                    # phase may be counted or published from here: the owner of the money phase rolls
                    # the whole phase back, records the refusal on its own transaction, and then - once
                    # its outcome is established - publishes this ONE observation of it.
                    unusable.publish_refusal = self._refusal_publisher(
                        run_id=run_id, run=run, emitter=emitter, action=action, unusable=unusable
                    )
                    raise
                except RetryablePaymentConflictException:
                    # The outer tick transaction is no longer safe to use: this payment ran inside
                    # the tick's savepoint, and a stale snapshot or serialization failure belongs
                    # to the tick's transaction, not to the savepoint. Do not count a transient
                    # conflict as a terminal payment rejection - propagate it.
                    #
                    # WHO OWNS IT NOW, programme 015 / P1, 2026-09-12. This comment promised a
                    # "tick-level rollback/replay policy" that did not exist, and then described
                    # the loss that followed from its absence. The policy now exists and is
                    # `app/core/simulator/money_replay.py`: this conflict leaves the payments
                    # phase and reaches the money boundary, which discards the whole attempt
                    # WITHOUT publishing any of its observations, opens a fresh session and
                    # transaction, takes a fresh debt snapshot, RE-PLANS the load and runs the
                    # phase again, up to a bounded number of attempts. So propagating no longer
                    # costs the tick - it costs one attempt of it. If the budget is exhausted the
                    # conflict still leaves the tick, but it is recorded as a tick that made no
                    # progress rather than as an error, and it does not spend the error budget.
                    raise
                except Exception as e:
                    status, code, err_details = _classify_refusal(e)
                    if status is None:
                        # Not a definitive refusal (a timeout, a 5xx, an unexpected error): the outcome is NOT established
                        # here - a durable row may exist. Safety is the repeat under the same key, not an absent row.
                        # Terminal outcomes either returned above (committed, a durable ABORTED row) or are a 4xx
                        # business refusal (`status` REJECTED).
                        unresolved.add(int(action.seq))

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
                                # `edge_patch`, `node_patch` and `closed_edges` are read AFTER the commit, on a
                                # session of their own (034 `F-034-2`): `build_post_commit_patches`.
                                edges=[
                                    {"from": a, "to": b} for a, b in edges_pairs
                                ],
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

                    # 034 `F-034-2`: no visual patch is built here. This is the money transaction, under its line
                    # locks; the patches are read after the commit (`build_patches_after_commit`).
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
            deferred_effects=deferred_effects,
            stop_requested=stop_requested,
            staged_tx_ids=frozenset(staged_tx_ids),
            unresolved_seqs=frozenset(unresolved),
        )

    async def build_patches_after_commit(
        self,
        *,
        open_session: Callable[[], Any],
        run: RunRecord,
        items: list[_PaymentObservation],
    ) -> dict[int, _PaymentPatches]:
        """The visual patches of a tick's COMMITTED payments, read on a session of their own (034 `F-034-2`).

        The caller has confirmed the money commit, so every patch describes the rows as committed - the state
        after ALL of the tick's payments - and nothing here shares a transaction with the money. A failure costs
        that payment's patch (its `tx.updated` is published without it) and is logged; the session's read
        transaction is ended by its owner, here, before the next payment's patch is read. Never raises.
        """

        patches: dict[int, _PaymentPatches] = {}
        pid_to_participant_by_eq_and_pids: dict[tuple[str, tuple[str, ...]], dict[str, Participant]] = {}
        quantiles_refreshed_by_eq: set[str] = set()

        async def _end_failed_read(session, *, eq: str, what: str) -> None:
            if self._should_warn_this_tick(run, key=f"{what}:{eq}"):
                self._logger.warning(
                    "simulator.real.%s run_id=%s tick=%s eq=%s",
                    what,
                    str(run.run_id),
                    int(run.tick_index),
                    str(eq),
                    exc_info=True,
                )
            await session.rollback()

        async def _one(session, item: _PaymentObservation) -> _PaymentPatches:
            eq = str(item.equivalent)
            # The observation's edges are the route's HOPS, payer -> payee. The nodes of the patch are their ends;
            # the lines of the patch are `line_pairs_of_payment_hops` of them (034 S1b), not the hops themselves.
            edges_pairs = [(str(e["from"]), str(e["to"])) for e in item.edges]

            helper: VizPatchHelper | None
            with self._lock:
                helper = run._real_viz_by_eq.get(eq)
            if helper is None:
                helper = await VizPatchHelper.create(
                    session,
                    equivalent_code=eq,
                    refresh_every_ticks=int(settings.SIMULATOR_VIZ_QUANTILE_REFRESH_TICKS or 10),
                )
                with self._lock:
                    run._real_viz_by_eq[eq] = helper

            participant_ids: list[uuid.UUID] = []
            if run._real_participants:
                participant_ids = [pid for (pid, _) in run._real_participants]
            if eq not in quantiles_refreshed_by_eq:
                await helper.maybe_refresh_quantiles(
                    session,
                    tick_index=int(run.tick_index),
                    participant_ids=participant_ids,
                )
                quantiles_refreshed_by_eq.add(eq)

            pids = sorted({pid for ab in edges_pairs for pid in ab if pid})
            pids_key = (eq, tuple(pids))
            pid_to_participant = pid_to_participant_by_eq_and_pids.get(pids_key)
            if pid_to_participant is None:
                res = await session.execute(select(Participant).where(Participant.pid.in_(pids)))
                pid_to_participant = {p.pid: p for p in res.scalars().all()}
                # Detached, so a rollback after a failed read below does not expire them: they are plain values
                # for the builders, read once per pass.
                for participant in pid_to_participant.values():
                    session.expunge(participant)
                pid_to_participant_by_eq_and_pids[pids_key] = pid_to_participant

            node_patch: list[dict[str, Any]] | None
            try:
                node_patch = await helper.compute_node_patches(
                    session, pid_to_participant=pid_to_participant, pids=pids
                ) or None
            except Exception:
                # The node patch alone is lost; the edge patch below is still read, on a fresh transaction.
                await _end_failed_read(session, eq=eq, what="node_patch_failed")
                node_patch = None

            closed: set[tuple[str, str]] = set()
            edge_patch = await self._edge_patch_builder.build_edge_patch_for_pairs(
                session=session,
                helper=helper,
                edges_pairs=line_pairs_of_payment_hops(edges_pairs),
                pid_to_participant=pid_to_participant,
                closed=closed,
            ) or None
            return _PaymentPatches(edge_patch=edge_patch, node_patch=node_patch, closed_edges=tuple(sorted(closed)))

        try:
            async with open_session() as session:
                for item in sorted(items, key=lambda observation: observation.seq):
                    try:
                        patches[item.seq] = await _one(session, item)
                    except Exception:
                        await _end_failed_read(session, eq=str(item.equivalent), what="edge_patch_failed")
        except asyncio.CancelledError:
            raise
        except Exception:
            self._logger.warning(
                "simulator.real.payment_patch_session_failed run_id=%s tick=%s",
                str(run.run_id),
                int(run.tick_index),
                exc_info=True,
            )
        return patches

    def _refusal_publisher(
        self,
        *,
        run_id: str,
        run: RunRecord,
        emitter: SseEventEmitter,
        action: Any,
        unusable: PaymentTransactionUnusable,
    ) -> Callable[..., bool]:
        """The one observation of a refusal that left the tick's transaction unusable (`T1912`).

        Built here, where the observation rules live, and published by the money-phase owner - once,
        and only after the refusal's outcome is established. It is the observation a raised refusal of
        the same class has always produced: `tx.failed` with the same code and the same run counters.
        The owner passes `stored` - the public error of a stored refusal it yielded to (030 `T3094` #1) - and the
        observation is then THAT refusal, the outcome every replay answers, not this attempt's own.
        """

        refusal = unusable.refusal
        own: BaseException = refusal.public_error if refusal is not None else unusable.cause

        def publish(stored: BaseException | None = None) -> bool:
            return self._refusal_observation(run_id=run_id, run=run, emitter=emitter, action=action,
                                             cause=stored if stored is not None else own).apply_after_rollback()

        return publish

    def _refusal_observation(self, *, run_id: str, run: RunRecord, emitter: SseEventEmitter, action: Any,
                             cause: BaseException) -> "DeferredRealPaymentEffects":
        _status, code, err_details = _classify_refusal(cause)
        if code is not None:
            outcome: Literal["committed", "rejected", "error"] = "error"
            error_code = str(code)
        else:
            outcome = "rejected"
            try:
                error_code = map_rejection_code(err_details)
            except Exception:
                error_code = "PAYMENT_REJECTED"
        buffer = DeferredRealPaymentEffects(
            lock=self._lock,
            emitter=emitter,
            logger=self._logger,
            utc_now=self._utc_now,
            run_id=str(run_id),
            run=run,
            items=[
                _PaymentObservation(
                    seq=int(action.seq),
                    outcome=outcome,
                    equivalent=str(action.equivalent),
                    sender_pid=str(action.sender_pid),
                    receiver_pid=str(action.receiver_pid),
                    amount=str(getattr(action, "amount", "") or ""),
                    edges=[{"from": str(action.sender_pid), "to": str(action.receiver_pid)}],
                    error_code=error_code,
                    error_details=err_details,
                )
            ],
        )
        return buffer
