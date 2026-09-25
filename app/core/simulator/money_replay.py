"""Programme 015, `P1`: the bounded replay of the tick's money phase.

WHAT WAS WRONG. A staged simulator payment that hit a transient database conflict raised
`RetryablePaymentConflictException` out of the payments executor, under a comment saying that the
"tick-level rollback/replay policy" owned it. No such policy existed. The conflict left the
payments phase, the tick orchestrator rolled its session back, logged
`simulator.real.tick_failed`, incremented `run.errors_total` and `run._real_consec_tick_failures`
and could end the run with `REAL_MODE_TICK_FAILED_REPEATED`; the next heartbeat then incremented
`tick_index` and started a NEW tick, so the failed tick's payments were simply lost. Verified
2026-09-12 to predate T1525 (`21753fd` and `4ca910c` are its ancestors): a long-standing
resilience defect of the money path on BOTH backends, not a SQLite-only regression.

WHAT THIS MODULE IS. The owner of that repeat. Its unit is the WHOLE money phase and never one
action: every action runs inside `session.begin_nested()` on the tick's session
(`real_payments_executor.py`), so a rollback removes them all and repeating only the last one
would lose the earlier ones.

THE BOUNDARY. It opens after the due injects have completed and BEFORE the first read and the
owner-lock acquisition, and it closes with an explicit commit. Inside it: a fresh session and
transaction, the owner locks, the debt snapshot, planning, the staged payments, the commit.
Clearing, trust drift and the persistence tail run only after it has succeeded - and after it has
succeeded the payments are durable, so a failure in that tail must never replay money.

WHAT IS PRESERVED ACROSS ATTEMPTS. Tick identity, simulation time, seed, load settings and the
scenario events already marked fired: all of them live on the `RunRecord`, and no attempt touches
them. RECREATED on every attempt: the session and its transaction, the locks, the debt snapshot,
the routes, the capacity checks, the staged writes and the observation buffer.

THE PLAN IS NEVER REUSED. The planner caps amounts by already-used debt
(`real_payment_planner.py:614`, `:633`, `:655`), so a reused plan would size payments against a
picture the concurrent commit has already refuted. The tick's generator is seeded from `seed` and
`tick_index` (`real_payment_planner.py:363`), so a replan is reproducible - and its result may
legitimately differ, because this is regeneration of load that was never accepted. Nothing about
an ACCEPTED payment is regenerated: its `payload` and `tx_id` are immutable.

PUBLICATION HAPPENS EXACTLY ONCE, AFTER THE COMMIT. A discarded attempt publishes no terminal SSE
and increments no business counter: its buffer is destroyed by `discard()`. This is why
`DeferredRealPaymentEffects.apply_once` is not sufficient on its own - that guard protects ONE
buffer instance, and every attempt builds a new one.

AN UNKNOWN COMMIT OUTCOME FORBIDS A BLIND REPLAY. When the commit's own outcome cannot be read,
this module resolves it by the attempt's own identifiers - the `tx_id`s of its staged payments,
read on a NEW session - before deciding anything. Only an outcome established as "nothing landed"
may be replayed; an outcome that stays unknown raises `MoneyCommitOutcomeUnknown`, which is
deliberately NOT a transient conflict, so the orchestrator counts it and can stop the run.

A TRANSACTION A PAYMENT LEFT UNUSABLE IS NEVER REPLAYED OR COMMITTED (019 `T1912`). A staged payment
whose failure left the phase's transaction unusable - a timeout that cancelled a statement in flight -
raises `PaymentTransactionUnusable` instead of a refusal. This owner then rolls the WHOLE phase back,
establishes that rollback, and only then records the admitted refusal (`ABORTED`, a timeout `E007`) on a
short transaction of its own, yielding to a concurrent row of the same `tx_id` and never overwriting
`COMMITTED`; the refusal is published once, and the phase's other staged payments and observations are
discarded with the rollback. The tick fails under control (`_settle_unusable_phase`).

A BOUNDED REPEAT PROMISES NOTHING UNDER PERMANENT CONTENTION. When the budget runs out the
conflict leaves this module unchanged, and the orchestrator records a tick that made no progress
instead of a programmatic error. The stop criterion is that absence of progress, never a SQLSTATE.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Literal

from sqlalchemy import select
from sqlalchemy.exc import DBAPIError

from app.core.ledger.book import DebtVersionConflict
from app.core.payments.service import (
    PaymentTransactionUnusable,
    _drain_call,
    record_definitive_refusal,
)
from app.core.simulator.commit_resolution import resolve_commit_under_cancellation
from app.core.simulator.models import RunRecord
from app.db.models.transaction import Transaction
from app.utils.exceptions import RetryablePaymentConflictException

#: PostgreSQL reports these for a transaction IT has already rolled back, which is what makes the
#: attempt known not to have landed. 40001 serialization_failure, 40P01 deadlock_detected - the
#: same pair `PaymentEngine._is_retryable_db_error` and the inject loop already use. Kept as one
#: frozenset so the three retry sites cannot drift apart silently.
_TRANSIENT_SQLSTATES = frozenset({"40001", "40P01"})


class MoneyCommitOutcomeUnknown(RuntimeError):
    """The money commit's outcome could not be established, by the commit or by its identifiers.

    Deliberately not a transient conflict. `money_conflict_name` returns None for it, so the
    orchestrator treats it as a programmatic failure: it spends the error budget and can stop the
    run. An unresolved outcome is an integrity question, not a contention one, and must not be
    excused as contention (AGENTS.md §9: a broken invariant is a gate, not a log line).
    """


def money_conflict_name(exc: BaseException | None) -> str | None:
    """The transient-conflict name THIS failure carries, or None if it is another failure.

    The predicate the whole replay rests on, so it names its inputs rather than matching text:

    * `RetryablePaymentConflictException` is the typed conflict the payment service raises once it
      has classified a 40001/40P01 (`app/core/payments/service.py::_classify_payment_db_error`). It is
      what a staged payment propagates.
    * A raw `DBAPIError` is classified here because the money boundary contains statements the
      payment service never sees - the debt snapshot read and the owner-lock acquisition - and a
      SERIALIZABLE waiter can take a genuine 40001 on either.
    * `DebtVersionConflict` is the book's optimistic-version conflict on a payment flow (019 stage 3,
      `FORK-1`). The book no longer retries it from the same snapshot; the owner of the transaction
      does, and this is that owner. Only that subclass: a bare `StaleDataError` from anywhere else is
      not a conflict this replay can cure and stays None.

    ANTI-VACUUM (AGENTS.md §9). This function excludes, so it owes a counter-check: everything
    that is not one of those two shapes must come back None, including an `IntegrityError` from
    the same driver and a plain `RuntimeError`. That is asserted on errors produced by the real
    driver in `tests/unit/test_p015_p1_money_conflict_predicate.py`; a predicate that quietly went
    permissive would turn a terminal failure into an endless replay.
    """

    if exc is None:
        return None
    if isinstance(exc, RetryablePaymentConflictException):
        return "RETRYABLE_PAYMENT_CONFLICT"
    if isinstance(exc, DebtVersionConflict):
        return "DEBT_VERSION_CONFLICT"
    if isinstance(exc, DBAPIError):
        orig = getattr(exc, "orig", None)
        sqlstate = (
            getattr(orig, "sqlstate", None)
            or getattr(orig, "pgcode", None)
            or getattr(orig, "code", None)
        )
        if sqlstate in _TRANSIENT_SQLSTATES:
            return str(sqlstate)
    return None


@dataclass(frozen=True)
class MoneyPhaseOutcome:
    """What the boundary produced, and the session the tail is allowed to use.

    `stack` owns the committed attempt's session. The caller closes it when the tick ends; every
    discarded attempt's stack is closed by this module before the next attempt begins.
    """

    stack: AsyncExitStack | None
    session: Any | None
    phase: Any | None
    should_stop: bool
    attempts: int
    conflicts: int


@dataclass
class _CommitResolution:
    state: Literal["committed", "rolled_back", "unknown"]
    error: BaseException | None


@dataclass(frozen=True)
class _AttemptRunState:
    """The run state a DISCARDED attempt must not be allowed to leave behind.

    An attempt that is superseded never happened, so it may not move a business counter and may
    not seed a cache from a snapshot that was rolled back.

    * `_real_consec_all_rejected_ticks` is the capacity-stall counter the executor advances on
      every call (`real_payments_executor.py:826`); a discarded attempt would inflate it and, at
      five, make the run log a stall it did not have.
    * `_real_consec_tick_failures` is the error-budget counter the executor CLEARS on every call
      (`:824`); a discarded attempt would forgive failures the run really had.
    * `_real_viz_by_eq` caches a `VizPatchHelper` per equivalent, and one created during a
      discarded attempt was built from that attempt's session. Helpers created during the attempt
      are dropped.

    KNOWN LIMIT, named rather than hidden: a helper that already existed keeps any quantile
    refresh the discarded attempt performed. Those quantiles were read from a snapshot that
    included the competitor's committed rows and only excluded the attempt's own uncommitted
    payments, they drive node/edge width and colour and nothing monetary, and they are refreshed
    every `SIMULATOR_VIZ_QUANTILE_REFRESH_TICKS`. Deep-copying helpers to close that was judged
    not worth its cost; if it ever matters, this is where it is.
    """

    viz_keys: frozenset[str]
    consec_all_rejected_ticks: int
    consec_tick_failures: int

    @classmethod
    def capture(cls, run: RunRecord, lock: Any) -> "_AttemptRunState":
        with lock:
            return cls(
                viz_keys=frozenset(run._real_viz_by_eq or {}),
                consec_all_rejected_ticks=int(run._real_consec_all_rejected_ticks or 0),
                consec_tick_failures=int(run._real_consec_tick_failures or 0),
            )

    def restore(self, run: RunRecord, lock: Any) -> None:
        with lock:
            for key in [k for k in (run._real_viz_by_eq or {}) if k not in self.viz_keys]:
                run._real_viz_by_eq.pop(key, None)
            run._real_consec_all_rejected_ticks = int(self.consec_all_rejected_ticks)
            run._real_consec_tick_failures = int(self.consec_tick_failures)


async def _close_quietly(stack: AsyncExitStack, logger: logging.Logger, *, run_id: str) -> None:
    """Close a discarded attempt's session. A failure here must not mask the original error."""
    try:
        await stack.aclose()
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.warning(
            "simulator.real.money_attempt_close_failed run_id=%s",
            str(run_id),
            exc_info=True,
        )


async def _commit_money(
    *,
    session: Any,
    phase: Any,
    logger: logging.Logger,
) -> _CommitResolution:
    """Commit the money and report WHICH outcome the commit actually reached.

    Only the committed branch publishes. The rolled-back branch deliberately does NOT call
    `apply_rollback_observations`: whether a non-committed attempt's observations are published or
    destroyed is the replay's decision, and resolving the buffer here would take it away - a
    superseded attempt would publish `tx.failed` for payments the replay is about to re-plan.
    """

    state: Literal["committed", "rolled_back", "unknown"] = "unknown"

    def _on_commit() -> None:
        nonlocal state
        state = "committed"
        if phase is not None:
            phase.apply_deferred_effects()

    def _on_rollback() -> None:
        nonlocal state
        state = "rolled_back"

    def _on_unknown() -> None:
        nonlocal state
        state = "unknown"

    error: BaseException | None = None
    try:
        await resolve_commit_under_cancellation(
            commit=session.commit,
            rollback=session.rollback,
            on_commit=_on_commit,
            on_rollback=_on_rollback,
            on_unknown=_on_unknown,
            logger=logger,
        )
    except asyncio.CancelledError:
        raise
    except BaseException as exc:  # noqa: BLE001 - classified by the caller, never swallowed
        error = exc
    return _CommitResolution(state=state, error=error)


async def _attempt_landed(
    *,
    open_session: Callable[[], Any],
    tx_ids: Any,
    logger: logging.Logger,
    run_id: str,
) -> bool | None:
    """Did this attempt's transaction land? Read the ORIGINAL identifiers on a NEW session.

    True: at least one of the attempt's own `tx_id`s is stored, so the transaction committed.
    False: none is stored, so nothing of it is durable and it may be replayed.
    None: the question could not be answered, and a blind replay is therefore forbidden.

    A new session is required: the attempt's own session is exactly the one whose state is in
    doubt. The whole attempt is ONE database transaction, so a single stored identifier settles it
    for all of them; identifiers from an earlier attempt cannot be mistaken for these, because an
    earlier attempt that committed would have ended the loop, and one that did not commit left no
    rows behind.
    """

    ids = sorted({str(tx_id) for tx_id in (tx_ids or ()) if str(tx_id)})
    if not ids:
        # Nothing was staged, so this transaction carried no money and there is nothing that could
        # have landed. Establishing that is not the same as failing to establish anything.
        return False

    stack = AsyncExitStack()
    try:
        session = await stack.enter_async_context(open_session())
        rows = (
            await session.execute(select(Transaction.tx_id).where(Transaction.tx_id.in_(ids)))
        ).scalars().all()
    except asyncio.CancelledError:
        await _close_quietly(stack, logger, run_id=run_id)
        raise
    except Exception:
        logger.warning(
            "simulator.real.money_commit_outcome_unresolved run_id=%s tx_ids=%d",
            str(run_id),
            len(ids),
            exc_info=True,
        )
        await _close_quietly(stack, logger, run_id=run_id)
        return None
    await _close_quietly(stack, logger, run_id=run_id)
    return bool(rows)


async def _roll_back_the_phase(session: Any, logger: logging.Logger, *, run_id: str) -> bool:
    """End the phase's transaction and say whether its rollback is POSITIVELY established.

    The owner has sent no `COMMIT` on this transaction - the unusable branch is entered from inside
    the attempt, before `_commit_money` - so once the transaction has ended nothing of it can land.
    It has ended when `rollback()` returns (a live connection is sent `ROLLBACK`; one the payment
    service already invalidated is discarded, and the server aborts its transaction with it), or, when
    the rollback itself fails, when the connection is invalidated - closed, so the server aborts the
    transaction and no `COMMIT` can ever reach it. Anything else is NOT established: the transaction
    may still be open on the server, and the refusal must not be recorded beside it.
    """

    if session is None:
        return False
    try:
        await session.rollback()
        return True
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.warning(
            "simulator.real.unusable_phase_rollback_failed run_id=%s", str(run_id), exc_info=True
        )
    try:
        await session.invalidate()
        return True
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.error(
            "simulator.real.unusable_phase_rollback_unconfirmed run_id=%s", str(run_id), exc_info=True
        )
        return False


async def _settle_unusable_phase(
    error: PaymentTransactionUnusable,
    *,
    session: Any,
    open_session: Callable[[], Any],
    logger: logging.Logger,
    run_id: str,
) -> None:
    """The money-phase owner's half of the unusable-transaction branch (019 `T1912`).

    1. Roll the WHOLE phase back and establish it (`_roll_back_the_phase`). Every staged payment of
       the phase goes with it - the ones that succeeded before the failure too - and their observations
       are never published: the buffer that held them was never returned to this owner.
    2. Only then record the admitted refusal, `ABORTED` with its error (for a timeout `E007`), in a
       short transaction of its own (`record_definitive_refusal`): it yields to a concurrent row of the
       same `tx_id`, resolves its identity, and never overwrites `COMMITTED`.
    3. Publish the refusal's one observation (`tx.failed`) once its outcome is established - recorded,
       or an `ABORTED` of the same request already standing. A `COMMITTED` winner publishes nothing
       here: that payment did not fail.

    An unestablished rollback, a failed recording or a refusal-less error records and publishes
    nothing; the caller raises the error either way, and the tick fails under control.
    """

    established = await _roll_back_the_phase(session, logger, run_id=run_id)
    refusal = error.refusal
    if not established:
        logger.error(
            "simulator.real.staged_refusal_not_recorded run_id=%s tx_id=%s reason=rollback_unconfirmed",
            str(run_id),
            getattr(refusal, "tx_id", None),
        )
        return
    if refusal is None:
        return
    stored, failure = await _drain_call(lambda: record_definitive_refusal(open_session, refusal))
    if failure is not None:
        logger.error(
            "simulator.real.staged_refusal_record_failed run_id=%s tx_id=%s error_type=%s",
            str(run_id),
            refusal.tx_id,
            type(failure).__name__,
        )
        if isinstance(failure, asyncio.CancelledError):
            raise failure
        return
    if stored is not None and stored.status == "COMMITTED":
        logger.warning(
            "simulator.real.staged_refusal_yielded_to_committed run_id=%s tx_id=%s",
            str(run_id),
            refusal.tx_id,
        )
        return
    logger.warning(
        "simulator.real.staged_refusal_recorded_after_rollback run_id=%s tx_id=%s code=%s",
        str(run_id),
        refusal.tx_id,
        refusal.error.get("code"),
    )
    if error.publish_refusal is not None:
        try:
            error.publish_refusal()
        except Exception:
            logger.warning(
                "simulator.real.staged_refusal_publish_failed run_id=%s tx_id=%s",
                str(run_id),
                refusal.tx_id,
                exc_info=True,
            )


def _resolve_non_replayed(phase: Any, state: str) -> None:
    """Resolve an attempt that will NOT be replayed, the way a failed tick always has.

    A final attempt is not superseded by anything, so its observations keep the behaviour that
    predates this module: a known rollback publishes the failures it really had, and an unknown
    outcome resolves as unknown (which invalidates routing caches for the committed items without
    publishing them).
    """
    if phase is None:
        return
    if state == "rolled_back":
        phase.apply_rollback_observations()
    else:
        phase.apply_unknown_transaction_observations()


async def run_money_phase_with_bounded_replay(
    *,
    run_id: str,
    run: RunRecord,
    lock: Any,
    logger: logging.Logger,
    max_attempts: int,
    open_session: Callable[[], Any],
    run_money_attempt: Callable[[Any], Awaitable[tuple[Any, bool]]],
) -> MoneyPhaseOutcome:
    """Run the tick's money phase, repeating the WHOLE of it on a transient conflict.

    `run_money_attempt` receives a fresh session and must perform exactly the inside of the
    boundary: the owner locks, the debt snapshot, planning and the staged payments. The commit is
    performed here, because the decision that depends on its outcome is made here.
    """

    attempts_allowed = max(1, int(max_attempts))
    attempt = 0
    conflicts = 0

    while True:
        attempt += 1
        is_last_attempt = attempt >= attempts_allowed
        before = _AttemptRunState.capture(run, lock)

        stack = AsyncExitStack()
        session: Any = None
        phase: Any = None
        error: BaseException | None = None
        landed: bool | None = False
        state = "rolled_back"

        try:
            session = await stack.enter_async_context(open_session())
            phase, should_stop = await run_money_attempt(session)

            if should_stop:
                # A control stop, not a conflict: `run_payments_phase` has already rolled back and
                # resolved its own observations. Replaying a run that is stopping would be a
                # second execution of load the operator asked to end.
                return MoneyPhaseOutcome(
                    stack=stack,
                    session=session,
                    phase=phase,
                    should_stop=True,
                    attempts=attempt,
                    conflicts=conflicts,
                )

            resolution = await _commit_money(session=session, phase=phase, logger=logger)
            state = resolution.state
            error = resolution.error

            if state == "committed":
                with lock:
                    run._real_money_committed_ticks_total += 1
                    run._real_money_committed_payments_total += int(
                        getattr(phase, "committed", 0) or 0
                    )
                    run._real_money_attempts_total += attempt
                    run._real_consec_money_no_progress_ticks = 0
                return MoneyPhaseOutcome(
                    stack=stack,
                    session=session,
                    phase=phase,
                    should_stop=False,
                    attempts=attempt,
                    conflicts=conflicts,
                )

            if state == "unknown":
                landed = await _attempt_landed(
                    open_session=open_session,
                    tx_ids=getattr(phase, "staged_tx_ids", ()),
                    logger=logger,
                    run_id=run_id,
                )
                if landed is True:
                    # The money IS durable. Publish exactly once and let the error out: the tick is
                    # recorded as failed and its tail is skipped, but nothing may replay it.
                    if phase is not None:
                        phase.apply_deferred_effects()
                    with lock:
                        run._real_money_committed_ticks_total += 1
                        run._real_money_committed_payments_total += int(
                            getattr(phase, "committed", 0) or 0
                        )
                        run._real_money_attempts_total += attempt
                        run._real_consec_money_no_progress_ticks = 0
                    logger.warning(
                        "simulator.real.money_commit_landed_after_unknown run_id=%s tick=%s",
                        str(run_id),
                        int(run.tick_index or 0),
                    )
                    await _close_quietly(stack, logger, run_id=run_id)
                    raise (
                        error
                        if error is not None
                        else MoneyCommitOutcomeUnknown(
                            "the money commit landed but reported no outcome"
                        )
                    )
            else:
                landed = False

        except asyncio.CancelledError:
            # Cancellation is a control signal and never a conflict. The buffer is resolved as
            # unknown because a cancelled attempt's transaction outcome genuinely is.
            _resolve_non_replayed(phase, "unknown")
            await _close_quietly(stack, logger, run_id=run_id)
            raise
        except BaseException as exc:  # noqa: BLE001 - classified below, always re-raised
            if error is None:
                # The attempt failed BEFORE any commit was attempted: the owner locks, the debt
                # snapshot, the planner or a staged write. No commit means nothing durable.
                error = exc
                state = "rolled_back"
                landed = False
            elif exc is not error:
                raise

        # ── One attempt has failed. Decide whether another one is allowed. ──────────────
        conflict = money_conflict_name(error)
        if conflict is not None:
            conflicts += 1
            with lock:
                run._real_money_conflicts_total += 1

        if isinstance(error, PaymentTransactionUnusable):
            # A staged payment left THIS transaction unusable (019 `T1912`). It is not a conflict and
            # is never replayed: the phase is rolled back whole, and the admitted refusal is recorded
            # on a transaction of its own only once that rollback is established.
            await _settle_unusable_phase(
                error, session=session, open_session=open_session, logger=logger, run_id=run_id
            )

        if landed is None:
            # An unknown outcome that its own identifiers could not settle. No replay, ever.
            _resolve_non_replayed(phase, "unknown")
            await _close_quietly(stack, logger, run_id=run_id)
            with lock:
                run._real_money_attempts_total += attempt
            raise MoneyCommitOutcomeUnknown(
                f"the money commit outcome of tick {int(run.tick_index or 0)} could not be "
                f"resolved by its own identifiers"
            ) from error

        if conflict is not None and not is_last_attempt:
            # Superseded: destroy this attempt's buffer without publishing anything, put back the
            # run state it touched, and close its session so the transaction is rolled back.
            if phase is not None:
                phase.discard_observations()
            before.restore(run, lock)
            await _close_quietly(stack, logger, run_id=run_id)
            with lock:
                run._real_money_replays_total += 1
            logger.warning(
                "simulator.real.money_phase_replay run_id=%s tick=%s attempt=%s/%s conflict=%s",
                str(run_id),
                int(run.tick_index or 0),
                attempt,
                attempts_allowed,
                conflict,
            )
            continue

        _resolve_non_replayed(phase, state)
        await _close_quietly(stack, logger, run_id=run_id)
        with lock:
            run._real_money_attempts_total += attempt
            if conflict is not None:
                run._real_money_replay_exhausted_total += 1
        if conflict is not None:
            logger.warning(
                "simulator.real.money_phase_replay_exhausted run_id=%s tick=%s attempts=%s "
                "conflict=%s",
                str(run_id),
                int(run.tick_index or 0),
                attempt,
                conflict,
            )
        assert error is not None  # noqa: S101 - the loop only reaches here with a failure
        raise error
