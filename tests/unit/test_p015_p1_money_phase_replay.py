"""Programme 015 / P1: the money phase's bounded replay, as a mechanism.

WHAT IS UNDER TEST HERE, and what is not. This module drives
`run_money_phase_with_bounded_replay` directly, with a fake session and a fake money attempt, so
that each decision of the policy can be forced on purpose: a conflict before the commit, a conflict
at the commit, a commit whose outcome is unknown, a budget that runs out, a programmatic failure
that must not be replayed. Real database conflicts are the subject of the two stands that run
against real backends - `tests/integration/test_p015_p1_money_replay_sqlite.py` and
`tests/integration/test_p015_p1_money_replay_postgres.py`; what those cannot do is reach every
branch deliberately, which is why both exist.

The observation buffer is the REAL `DeferredRealPaymentEffects` with real `_PaymentObservation`
items, because "publish exactly once, after the commit" is a statement about that object and a fake
would only restate the test's own belief about it.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from datetime import datetime, timezone

import pytest

from app.core.simulator.models import RunRecord
from app.core.simulator.money_replay import (
    MoneyCommitOutcomeUnknown,
    run_money_phase_with_bounded_replay,
)
from app.core.simulator.real_payments_executor import (
    DeferredRealPaymentEffects,
    _PaymentObservation,
)
from app.core.simulator.real_tick_payments_coordinator import RealTickPaymentsPhaseResult
from app.utils.exceptions import RetryablePaymentConflictException

_LOGGER = logging.getLogger("tests.p015.p1.money_replay")


class _Emitter:
    """Counts terminal SSE publications. `tx.updated` is the one that means money moved."""

    def __init__(self) -> None:
        self.updated = 0
        self.failed = 0

    def emit_tx_updated(self, **_kwargs) -> None:
        self.updated += 1

    def emit_tx_failed(self, **_kwargs) -> None:
        self.failed += 1

    def emit_audit_drift(self, **_kwargs) -> None:
        return None


class _PaymentEffect:
    def __init__(self) -> None:
        self.calls = 0
        self.cache_invalidations = 0

    def invalidate_routing_cache_once(self) -> bool:
        self.cache_invalidations += 1
        return True

    def apply_once(self) -> bool:
        self.calls += 1
        return True


class _Session:
    """A session whose commit and rollback outcomes the test chooses."""

    def __init__(
        self,
        *,
        commit_error: BaseException | None = None,
        rollback_error: BaseException | None = None,
        landed_tx_ids: set[str] | None = None,
    ) -> None:
        self.commit_error = commit_error
        self.rollback_error = rollback_error
        self.landed_tx_ids = landed_tx_ids or set()
        self.commits = 0
        self.rollbacks = 0
        self.closed = 0

    async def commit(self) -> None:
        self.commits += 1
        if self.commit_error is not None:
            raise self.commit_error

    async def rollback(self) -> None:
        self.rollbacks += 1
        if self.rollback_error is not None:
            raise self.rollback_error

    async def execute(self, _statement):
        # Only the replay's identifier resolution reads through a session here.
        landed = list(self.landed_tx_ids)

        class _Result:
            def scalars(self_inner):
                class _Scalars:
                    def all(self_scalars):
                        return landed

                return _Scalars()

        return _Result()

    async def __aenter__(self) -> "_Session":
        return self

    async def __aexit__(self, *_exc) -> None:
        self.closed += 1


class _ExplodingResolutionSession(_Session):
    """A session whose identifier read fails, so an unknown outcome STAYS unknown."""

    async def execute(self, _statement):
        raise RuntimeError("the resolution read failed too")


def _run() -> RunRecord:
    run = RunRecord(
        run_id="p1-replay",
        scenario_id="p1",
        mode="real",
        state="running",
        started_at=datetime.now(timezone.utc),
    )
    run.tick_index = 4
    run.seed = 7
    return run


def _phase(
    *,
    run: RunRecord,
    emitter: _Emitter,
    effect: _PaymentEffect,
    tx_id: str,
    committed: int = 1,
) -> RealTickPaymentsPhaseResult:
    """One committed payment and one rejected one - the mix a real tick produces."""
    deferred = DeferredRealPaymentEffects(
        lock=threading.RLock(),
        emitter=emitter,  # type: ignore[arg-type]
        logger=_LOGGER,
        utc_now=lambda: datetime.now(timezone.utc),
        run_id=run.run_id,
        run=run,
        items=[
            _PaymentObservation(
                seq=0,
                outcome="committed",
                equivalent="UAH",
                sender_pid="A",
                receiver_pid="B",
                amount="1.00",
                edges=[{"from": "A", "to": "B"}],
                payment_effects=effect,  # type: ignore[arg-type]
            ),
            _PaymentObservation(
                seq=1,
                outcome="rejected",
                equivalent="UAH",
                sender_pid="A",
                receiver_pid="C",
                amount="2.00",
                edges=[{"from": "A", "to": "C"}],
                error_code="PAYMENT_REJECTED",
                error_details={"message": "rejected"},
            ),
        ],
    )
    return RealTickPaymentsPhaseResult(
        debt_snapshot={},
        planned=[],
        per_eq_metric_values={},
        committed=committed,
        rejected=1,
        errors=0,
        timeouts=0,
        per_eq={},
        per_eq_route={},
        per_eq_edge_stats={},
        stall_ticks=0,
        rejection_codes_by_eq={},
        deferred_effects=deferred,
        staged_tx_ids=frozenset({tx_id}),
    )


async def _drive(
    *,
    run: RunRecord,
    sessions: list[_Session],
    attempt_errors: list[BaseException | None],
    phases: list[RealTickPaymentsPhaseResult],
    max_attempts: int = 3,
):
    """Run the policy over a scripted series of attempts."""
    opened: list[_Session] = []
    calls = {"n": 0}

    def _open_session():
        session = sessions[min(len(opened), len(sessions) - 1)]
        opened.append(session)
        return session

    async def _attempt(_session):
        index = calls["n"]
        calls["n"] += 1
        error = attempt_errors[min(index, len(attempt_errors) - 1)]
        if error is not None:
            raise error
        return phases[min(index, len(phases) - 1)], False

    outcome = await run_money_phase_with_bounded_replay(
        run_id=run.run_id,
        run=run,
        lock=threading.RLock(),
        logger=_LOGGER,
        max_attempts=max_attempts,
        open_session=_open_session,
        run_money_attempt=_attempt,
    )
    return outcome, calls["n"], opened


@pytest.mark.asyncio
async def test_a_conflict_before_the_commit_replays_and_publishes_exactly_once() -> None:
    """The defect, closed: a transient conflict costs one attempt, not the tick.

    RED before P1: nothing replayed at all - the conflict left the payments phase, the tick was
    logged as failed, `errors_total` was incremented and the next heartbeat started a NEW tick, so
    this tick's payments were simply lost.
    """
    run = _run()
    emitter = _Emitter()
    effect = _PaymentEffect()
    good = _phase(run=run, emitter=emitter, effect=effect, tx_id="tx-second")
    first_session = _Session()
    second_session = _Session()

    outcome, attempts, opened = await _drive(
        run=run,
        sessions=[first_session, second_session],
        attempt_errors=[RetryablePaymentConflictException(), None],
        phases=[good],
    )

    assert attempts == 2, "the money phase was not replayed"
    assert outcome.should_stop is False
    assert outcome.phase is good
    assert outcome.conflicts == 1

    # Publication happened exactly once, and only after the commit that actually landed.
    assert second_session.commits == 1
    assert emitter.updated == 1
    assert emitter.failed == 1
    assert effect.calls == 1
    assert run.committed_total == 1
    assert run.rejected_total == 1
    assert run.attempts_total == 2  # one committed + one rejected observation, published once

    # The discarded attempt's session was closed, so its transaction was rolled back.
    assert first_session.closed == 1
    assert opened[0] is first_session and opened[1] is second_session

    # A transient conflict is not an error.
    assert run.errors_total == 0
    assert run._real_money_conflicts_total == 1
    assert run._real_money_replays_total == 1
    assert run._real_money_replay_exhausted_total == 0
    assert run._real_money_committed_ticks_total == 1
    assert run._real_money_committed_payments_total == 1
    assert run._real_consec_money_no_progress_ticks == 0

    # The caller still owns the committed attempt's session.
    assert second_session.closed == 0
    await outcome.stack.aclose()
    assert second_session.closed == 1


@pytest.mark.asyncio
async def test_a_discarded_attempt_publishes_nothing_and_moves_no_business_counter() -> None:
    """The explicit fix for `apply_once` being a per-buffer guard.

    Each attempt builds its own `DeferredRealPaymentEffects`, so the one-shot guard on a single
    buffer cannot deliver "exactly once per tick". The superseded buffer is destroyed instead, and
    this asserts that destruction: no terminal SSE, no counter, and nothing reachable afterwards.
    """
    run = _run()
    emitter = _Emitter()
    discarded_effect = _PaymentEffect()
    kept_effect = _PaymentEffect()
    discarded = _phase(run=run, emitter=emitter, effect=discarded_effect, tx_id="tx-1")
    kept = _phase(run=run, emitter=emitter, effect=kept_effect, tx_id="tx-2")

    conflict_session = _Session(commit_error=RetryablePaymentConflictException())
    good_session = _Session()

    outcome, attempts, _opened = await _drive(
        run=run,
        sessions=[conflict_session, good_session],
        attempt_errors=[None, None],
        phases=[discarded, kept],
    )

    assert attempts == 2
    assert outcome.phase is kept

    # The discarded attempt: destroyed, silent, and inert from here on.
    assert discarded.deferred_effects._resolution == "discarded"
    assert discarded.deferred_effects.items == []
    assert discarded_effect.calls == 0
    assert discarded_effect.cache_invalidations == 0
    assert discarded.apply_deferred_effects() is False
    assert discarded.apply_rollback_observations() is False
    assert discarded.apply_unknown_transaction_observations() is False
    assert discarded.discard_observations() is False

    # Exactly one publication of each terminal event, from the attempt that committed.
    assert (emitter.updated, emitter.failed) == (1, 1)
    assert kept_effect.calls == 1
    assert run.committed_total == 1
    assert run.rejected_total == 1
    assert run.errors_total == 0

    await outcome.stack.aclose()


@pytest.mark.asyncio
async def test_a_discarded_attempt_leaves_no_counter_or_cache_behind() -> None:
    """State a superseded attempt touched is put back, because it never happened.

    The executor advances the capacity-stall counter and CLEARS the tick-failure counter on every
    call, and it caches a `VizPatchHelper` per equivalent built on the attempt's own session. Left
    alone, a discarded attempt would log a stall the run did not have, forgive failures it did
    have, and keep a cache seeded from a rolled-back snapshot.
    """
    run = _run()
    run._real_consec_all_rejected_ticks = 4
    run._real_consec_tick_failures = 2
    run._real_viz_by_eq = {"UAH": object()}
    kept_helper = run._real_viz_by_eq["UAH"]

    emitter = _Emitter()
    kept = _phase(run=run, emitter=emitter, effect=_PaymentEffect(), tx_id="tx-2")

    async def _attempt_that_dirties_the_run(_session):
        # Exactly what the real executor does to the record on every call.
        run._real_consec_all_rejected_ticks += 1
        run._real_consec_tick_failures = 0
        run._real_viz_by_eq["EUR"] = object()
        if _attempt_that_dirties_the_run.calls == 0:
            _attempt_that_dirties_the_run.calls += 1
            raise RetryablePaymentConflictException()
        _attempt_that_dirties_the_run.calls += 1
        return kept, False

    _attempt_that_dirties_the_run.calls = 0

    outcome = await run_money_phase_with_bounded_replay(
        run_id=run.run_id,
        run=run,
        lock=threading.RLock(),
        logger=_LOGGER,
        max_attempts=3,
        open_session=_Session,
        run_money_attempt=_attempt_that_dirties_the_run,
    )

    assert outcome.phase is kept
    # The SECOND attempt's effect on these is kept; only the discarded one was undone.
    assert run._real_consec_all_rejected_ticks == 5
    assert run._real_consec_tick_failures == 0
    # The helper the discarded attempt created is gone; the pre-existing one is untouched.
    assert run._real_viz_by_eq["UAH"] is kept_helper
    assert "EUR" in run._real_viz_by_eq  # created by the attempt that actually committed

    await outcome.stack.aclose()


@pytest.mark.asyncio
async def test_a_programmatic_failure_is_never_replayed() -> None:
    """Anti-vacuum for the replay itself: only a conflict earns another attempt.

    A run whose money phase is broken must fail on its first attempt, loudly. Replaying it would
    repeat the broken work and then report the run as merely contended.
    """
    run = _run()
    emitter = _Emitter()
    effect = _PaymentEffect()
    phase = _phase(run=run, emitter=emitter, effect=effect, tx_id="tx-1")
    session = _Session()

    boom = RuntimeError("the money phase is broken")
    with pytest.raises(RuntimeError, match="the money phase is broken"):
        await _drive(
            run=run,
            sessions=[session],
            attempt_errors=[boom],
            phases=[phase],
        )

    assert session.closed == 1
    assert run._real_money_conflicts_total == 0
    assert run._real_money_replays_total == 0
    assert run._real_money_replay_exhausted_total == 0
    assert emitter.updated == 0


@pytest.mark.asyncio
async def test_the_budget_is_finite_and_its_exhaustion_is_not_an_error() -> None:
    """Permanent contention ends the tick without pretending the tick was broken.

    The final attempt is not superseded by anything, so its observations resolve the way a failed
    tick's always have: the failures it really had are published, the money is not.
    """
    run = _run()
    emitter = _Emitter()
    effect = _PaymentEffect()
    phase = _phase(run=run, emitter=emitter, effect=effect, tx_id="tx-1")
    sessions = [_Session(), _Session(), _Session()]

    with pytest.raises(RetryablePaymentConflictException):
        await _drive(
            run=run,
            sessions=sessions,
            attempt_errors=[RetryablePaymentConflictException()],
            phases=[phase],
            max_attempts=3,
        )

    assert [s.closed for s in sessions] == [1, 1, 1]
    assert run._real_money_conflicts_total == 3
    assert run._real_money_replays_total == 2  # two replays, three attempts
    assert run._real_money_replay_exhausted_total == 1
    assert run._real_money_committed_ticks_total == 0
    assert run.errors_total == 0
    assert emitter.updated == 0


@pytest.mark.asyncio
async def test_one_attempt_disables_the_replay_entirely() -> None:
    """The budget is counted in ATTEMPTS, so a value of 1 is an off switch with no special case."""
    run = _run()
    emitter = _Emitter()
    phase = _phase(run=run, emitter=emitter, effect=_PaymentEffect(), tx_id="tx-1")

    with pytest.raises(RetryablePaymentConflictException):
        await _drive(
            run=run,
            sessions=[_Session()],
            attempt_errors=[RetryablePaymentConflictException()],
            phases=[phase],
            max_attempts=1,
        )

    assert run._real_money_replays_total == 0
    assert run._real_money_replay_exhausted_total == 1


@pytest.mark.asyncio
async def test_a_conflict_at_the_commit_replays_only_because_the_rollback_succeeded() -> None:
    """A commit that failed and then rolled back cleanly is known not to have landed.

    This is the branch that makes a commit-time conflict safe to repeat, and it rests on the
    rollback, not on the error: `sqlite_busy_error_name` documents that a SQLite busy raised by
    `commit()` can leave the transaction OPEN with its own rows visible inside it.
    """
    run = _run()
    emitter = _Emitter()
    kept = _phase(run=run, emitter=emitter, effect=_PaymentEffect(), tx_id="tx-2")
    conflict_session = _Session(commit_error=RetryablePaymentConflictException())
    good_session = _Session()

    outcome, attempts, _opened = await _drive(
        run=run,
        sessions=[conflict_session, good_session],
        attempt_errors=[None, None],
        phases=[
            _phase(run=run, emitter=emitter, effect=_PaymentEffect(), tx_id="tx-1"),
            kept,
        ],
    )

    assert attempts == 2
    assert conflict_session.commits == 1
    assert conflict_session.rollbacks == 1, "the rollback is the retry's precondition"
    assert outcome.phase is kept
    assert emitter.updated == 1
    await outcome.stack.aclose()


@pytest.mark.asyncio
async def test_an_unknown_outcome_that_landed_publishes_once_and_never_replays() -> None:
    """Resolution by the ORIGINAL identifiers, when the commit itself could not say.

    The commit failed and so did the rollback, so nothing about the transaction is known from the
    session. Its own `tx_id` is then found stored, which settles it: the money is durable. It is
    published exactly once and the phase is NOT repeated - repeating it would pay twice.
    """
    run = _run()
    emitter = _Emitter()
    effect = _PaymentEffect()
    phase = _phase(run=run, emitter=emitter, effect=effect, tx_id="tx-landed")
    session = _Session(
        commit_error=RuntimeError("commit outcome unknown"),
        rollback_error=RuntimeError("rollback failed too"),
        landed_tx_ids={"tx-landed"},
    )

    with pytest.raises(RuntimeError, match="commit outcome unknown"):
        await _drive(
            run=run,
            sessions=[session],
            attempt_errors=[None],
            phases=[phase],
            max_attempts=3,
        )

    # Published exactly once, because the money really is there.
    assert emitter.updated == 1
    assert effect.calls == 1
    assert run.committed_total == 1
    assert run._real_money_committed_ticks_total == 1
    # Never repeated, and not counted as contention.
    assert run._real_money_replays_total == 0
    assert run._real_money_conflicts_total == 0
    assert phase.apply_deferred_effects() is False


@pytest.mark.asyncio
async def test_an_unresolvable_unknown_outcome_forbids_the_replay_and_is_not_a_conflict() -> None:
    """The outcome stays unknown, so a blind replay is refused even though the error is transient.

    The attempt failed with a genuine transient conflict, which on its own would earn a replay.
    It does not get one, because the question a replay depends on - did this attempt land? - could
    not be answered. `MoneyCommitOutcomeUnknown` is deliberately not a conflict, so the caller
    spends the error budget on it and can stop the run.
    """
    run = _run()
    emitter = _Emitter()
    effect = _PaymentEffect()
    phase = _phase(run=run, emitter=emitter, effect=effect, tx_id="tx-1")
    session = _ExplodingResolutionSession(
        commit_error=RetryablePaymentConflictException(),
        rollback_error=RuntimeError("rollback failed too"),
    )

    with pytest.raises(MoneyCommitOutcomeUnknown):
        await _drive(
            run=run,
            sessions=[session],
            attempt_errors=[None],
            phases=[phase],
            max_attempts=3,
        )

    assert run._real_money_replays_total == 0
    assert emitter.updated == 0, "money of an unknown outcome must not be published"
    assert effect.calls == 0
    # The committed observation's routing cache is still invalidated: the routes it implies may or
    # may not be real, and that is precisely when they must not be trusted.
    assert effect.cache_invalidations == 1
    assert phase.deferred_effects._resolution == "unknown"


@pytest.mark.asyncio
async def test_a_control_stop_is_returned_and_never_replayed() -> None:
    """`should_stop` is an operator decision. Replaying it would re-run load asked to end."""
    run = _run()
    emitter = _Emitter()
    phase = _phase(run=run, emitter=emitter, effect=_PaymentEffect(), tx_id="tx-1")
    session = _Session()
    calls = {"n": 0}

    async def _attempt(_session):
        calls["n"] += 1
        return phase, True

    outcome = await run_money_phase_with_bounded_replay(
        run_id=run.run_id,
        run=run,
        lock=threading.RLock(),
        logger=_LOGGER,
        max_attempts=3,
        open_session=lambda: session,
        run_money_attempt=_attempt,
    )

    assert calls["n"] == 1
    assert outcome.should_stop is True
    assert session.commits == 0, "a stopping run must not have its money committed by the replay"
    await outcome.stack.aclose()


@pytest.mark.asyncio
async def test_cancellation_is_not_a_conflict_and_resolves_the_attempt_as_unknown() -> None:
    """Cancellation is a control signal: no replay, and the outcome is honestly unknown."""
    run = _run()
    emitter = _Emitter()
    effect = _PaymentEffect()
    phase = _phase(run=run, emitter=emitter, effect=effect, tx_id="tx-1")
    session = _Session()

    async def _attempt(_session):
        raise asyncio.CancelledError()

    async def _go():
        return await run_money_phase_with_bounded_replay(
            run_id=run.run_id,
            run=run,
            lock=threading.RLock(),
            logger=_LOGGER,
            max_attempts=3,
            open_session=lambda: session,
            run_money_attempt=_attempt,
        )

    with pytest.raises(asyncio.CancelledError):
        await _go()

    assert session.closed == 1
    assert run._real_money_conflicts_total == 0
    assert run._real_money_replays_total == 0
    assert emitter.updated == 0
