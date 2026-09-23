"""How the real tick resolves its money phase, and what it refuses to do afterwards.

REWRITTEN BY PROGRAMME 015 / P1, 2026-09-12, because the contract this module asserts changed.

What it used to assert, and why that is now wrong. The payments phase used to leave its
transaction open, so the first commit after it was whichever tail step happened to run - clearing,
trust drift or the persistence tail. A failure in that tail therefore rolled the money back, and a
transient conflict out of the payments phase ended the tick, incremented `run.errors_total` and
`run._real_consec_tick_failures`, and could stop the run; the next heartbeat started a NEW tick and
the payments were lost.

What it asserts now, which is the P1 contract:

* A transient conflict is REPLAYED - the whole money phase, from a fresh session, transaction and
  debt snapshot - and costs one attempt rather than the tick.
* A transient conflict never spends the error budget, and permanent contention stops the run only
  through its own explicit no-progress criterion. A programmatic failure still stops it exactly as
  before, which is the counter-check for that exclusion.
* The money commits at its own boundary, BEFORE the tail. So the tail's failure path is the hard
  right edge: it cannot un-publish a committed payment and it cannot replay money.

The cancellation scenario is kept as it was in substance - a rollback that is interrupted twice
must still be drained and resolved exactly once - and now runs against the tail's rollback, which
is where that rollback lives after the boundary moved.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from datetime import datetime, timezone

import pytest

import app.core.simulator.real_tick_orchestrator as orchestrator_module
from app.core.simulator.models import RunRecord
from app.core.simulator.real_payments_executor import (
    DeferredRealPaymentEffects,
    _PaymentObservation,
)
from app.core.simulator.real_tick_orchestrator import RealTickOrchestrator
from app.core.simulator.real_tick_payments_coordinator import (
    RealTickPaymentsPhaseResult,
)
from app.utils.exceptions import RetryablePaymentConflictException


class TickFailure(RuntimeError):
    """A programmatic failure in the tick's tail, after the money has committed."""


class RollbackFailure(RuntimeError):
    pass


class _Session:
    def __init__(self) -> None:
        self.rollback_calls = 0
        self.commit_calls = 0

    async def commit(self) -> None:
        self.commit_calls += 1

    async def rollback(self) -> None:
        self.rollback_calls += 1
        raise RollbackFailure("rollback outcome unknown")


class _BlockingRollbackSession(_Session):
    def __init__(self) -> None:
        super().__init__()
        self.rollback_started = asyncio.Event()
        self.release_rollback = asyncio.Event()

    async def rollback(self) -> None:
        self.rollback_started.set()
        await self.release_rollback.wait()
        self.rollback_calls += 1


class _SuccessfulRollbackSession(_Session):
    async def rollback(self) -> None:
        self.rollback_calls += 1


class _SessionContext:
    def __init__(self, session: _Session) -> None:
        self.session = session

    async def __aenter__(self) -> _Session:
        return self.session

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


class _Emitter:
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


class _PaymentsCoordinator:
    """Succeeds immediately. The money phase commits and the tail then fails."""

    def __init__(self, phase: RealTickPaymentsPhaseResult) -> None:
        self.phase = phase
        self.calls = 0

    async def run_payments_phase(self, **_kwargs):
        self.calls += 1
        return self.phase, False


class _RetryableConflictPaymentsCoordinator:
    """Loses to contention on every attempt, or only on the first `conflicts` of them."""

    def __init__(
        self,
        *,
        conflicts: int | None = None,
        phase: RealTickPaymentsPhaseResult | None = None,
    ) -> None:
        self.calls = 0
        self.conflicts = conflicts
        self.phase = phase

    async def run_payments_phase(self, **_kwargs):
        self.calls += 1
        if self.conflicts is None or self.calls <= self.conflicts:
            raise RetryablePaymentConflictException()
        return self.phase, False


class _ClearingCoordinator:
    def __init__(self) -> None:
        self.calls = 0

    async def maybe_run_clearing(self, **_kwargs):
        self.calls += 1
        raise TickFailure("fail after payments")


class _Runner:
    def __init__(
        self,
        *,
        run: RunRecord,
        phase: RealTickPaymentsPhaseResult,
        logger: logging.Logger,
    ) -> None:
        self._run = run
        self._lock = threading.RLock()
        self._logger = logger
        self._real_tick_payments_coordinator = _PaymentsCoordinator(phase)
        self._real_tick_clearing_coordinator = _ClearingCoordinator()
        self._real_payments_executor = object()
        self._utc_now = lambda: datetime.now(timezone.utc)
        self._real_max_timeouts_per_tick_limit = 3
        self._real_max_errors_total_limit = 10
        self._real_max_consec_tick_failures_limit = 0
        # Programme 015 / P1.
        self._real_money_replay_attempts_limit = 3
        self._real_max_consec_money_no_progress_limit = 0
        self.failures: list[tuple[str, str]] = []

    def _get_run(self, _run_id: str) -> RunRecord:
        return self._run

    def _get_scenario_raw(self, _scenario_id: str) -> dict:
        return self._run._scenario_raw or {}

    async def _apply_due_scenario_events(self, *args, **kwargs) -> None:
        return None

    async def _load_debt_snapshot_by_pid(self, *args, **kwargs) -> dict:
        return {}

    def _plan_real_payments(self, *args, **kwargs) -> list:
        return []

    async def fail_run(self, run_id: str, *, code: str, message: str) -> None:
        self.failures.append((code, message))


def _run_with_phase(
    *,
    logger: logging.Logger,
    run_id: str,
) -> tuple[RunRecord, RealTickPaymentsPhaseResult, _Emitter, _PaymentEffect]:
    run = RunRecord(
        run_id=run_id,
        scenario_id="scenario",
        mode="real",
        state="running",
        started_at=datetime.now(timezone.utc),
    )
    run._scenario_raw = {"equivalents": ["UAH"]}
    run._real_seeded = True
    run._real_participants = [
        ("00000000-0000-0000-0000-000000000001", "A"),
        ("00000000-0000-0000-0000-000000000002", "B"),
    ]
    run._real_equivalents = ["UAH"]
    run._trust_drift_config = object()

    emitter = _Emitter()
    committed_effect = _PaymentEffect()
    deferred = DeferredRealPaymentEffects(
        lock=threading.RLock(),
        emitter=emitter,  # type: ignore[arg-type]
        logger=logger,
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
                payment_effects=committed_effect,  # type: ignore[arg-type]
            ),
            _PaymentObservation(
                seq=1,
                outcome="rejected",
                equivalent="UAH",
                sender_pid="A",
                receiver_pid="B",
                amount="2.00",
                edges=[{"from": "A", "to": "B"}],
                error_code="PAYMENT_REJECTED",
                error_details={"message": "rejected"},
            ),
            _PaymentObservation(
                seq=2,
                outcome="error",
                equivalent="UAH",
                sender_pid="A",
                receiver_pid="B",
                amount="3.00",
                edges=[{"from": "A", "to": "B"}],
                error_code="INTERNAL_ERROR",
                error_details={"message": "payment error"},
            ),
        ],
    )
    phase = RealTickPaymentsPhaseResult(
        debt_snapshot={},
        planned=[],
        per_eq_metric_values={},
        committed=1,
        rejected=1,
        errors=1,
        timeouts=0,
        per_eq={},
        per_eq_route={},
        per_eq_edge_stats={},
        stall_ticks=0,
        rejection_codes_by_eq={},
        deferred_effects=deferred,
        staged_tx_ids=frozenset({"tx-1"}),
    )
    return run, phase, emitter, committed_effect


async def _no_owner_locks(self, equivalent_codes) -> None:
    return None


def _bind_session(monkeypatch, session: _Session) -> None:
    monkeypatch.setattr(
        orchestrator_module.db_session,
        "AsyncSessionLocal",
        lambda: _SessionContext(session),
    )
    # The fake session has no database. Until 017 stage 3 (S5) the owner-lock call was skipped
    # here because the fake had no PostgreSQL bind; the call is now unconditional, so it is stubbed
    # explicitly. Owner locks are measured on PostgreSQL elsewhere, not by this module.
    monkeypatch.setattr(
        orchestrator_module.PaymentService,
        "acquire_staged_equivalent_owner_locks",
        _no_owner_locks,
    )


@pytest.mark.asyncio
async def test_a_transient_conflict_is_replayed_and_never_spends_the_error_budget(
    monkeypatch,
) -> None:
    """The defect P1 closes, seen from the tick.

    RED before P1: the conflict was not replayed at all (`calls == 1`), `errors_total` became 1,
    `_real_consec_tick_failures` became 1 and the tick was recorded as `REAL_MODE_TICK_FAILED` -
    so contention alone could end a run, and the tick's payments were simply lost.
    """
    logger = logging.getLogger("test.money_conflict_replayed")
    run, phase, emitter, committed_effect = _run_with_phase(
        logger=logger,
        run_id="money-conflict-replayed",
    )
    runner = _Runner(run=run, phase=phase, logger=logger)
    coordinator = _RetryableConflictPaymentsCoordinator()
    runner._real_tick_payments_coordinator = coordinator
    session = _SuccessfulRollbackSession()
    _bind_session(monkeypatch, session)

    await RealTickOrchestrator(runner).tick_real_mode(run.run_id)  # type: ignore[arg-type]

    # The whole money phase ran again, up to the budget.
    assert coordinator.calls == 3
    assert run._real_money_conflicts_total == 3
    assert run._real_money_replays_total == 2
    assert run._real_money_replay_exhausted_total == 1

    # Nothing was published and nothing committed: every attempt was rolled back.
    assert session.commit_calls == 0
    assert committed_effect.calls == 0
    assert emitter.updated == 0

    # The budget is untouched, and the tick is still diagnosable.
    assert run.errors_total == 0
    assert run._real_consec_tick_failures == 0
    assert len(run._error_timestamps) == 0
    assert run.last_error is not None
    assert run.last_error["code"] == "REAL_MODE_MONEY_CONFLICT_UNRESOLVED"
    assert "RETRYABLE_PAYMENT_CONFLICT" in run.last_error["message"]
    assert run._real_consec_money_no_progress_ticks == 1
    assert runner.failures == []


@pytest.mark.asyncio
async def test_a_replay_that_succeeds_commits_and_publishes_exactly_once(
    monkeypatch,
) -> None:
    """One conflict, then the replay commits: the money lands and is published once."""
    logger = logging.getLogger("test.money_conflict_then_success")
    run, phase, emitter, committed_effect = _run_with_phase(
        logger=logger,
        run_id="money-conflict-then-success",
    )
    runner = _Runner(run=run, phase=phase, logger=logger)
    coordinator = _RetryableConflictPaymentsCoordinator(conflicts=1, phase=phase)
    runner._real_tick_payments_coordinator = coordinator
    session = _SuccessfulRollbackSession()
    _bind_session(monkeypatch, session)

    await RealTickOrchestrator(runner).tick_real_mode(run.run_id)  # type: ignore[arg-type]

    assert coordinator.calls == 2
    assert session.commit_calls == 1
    assert emitter.updated == 1
    assert committed_effect.calls == 1
    assert run.committed_total == 1
    assert run._real_money_committed_ticks_total == 1
    assert run._real_consec_money_no_progress_ticks == 0

    # The tail still failed (clearing raises), and that is an ordinary tick failure.
    assert runner._real_tick_clearing_coordinator.calls == 1
    assert run.last_error["code"] == "REAL_MODE_TICK_FAILED"
    # Two errors, and NEITHER of them is the conflict: one is the phase's own failed payment
    # observation, published by the money commit, and one is the tail failure itself.
    assert run.errors_total == 2


@pytest.mark.asyncio
async def test_a_tail_failure_after_the_money_commit_cannot_unpublish_or_replay_money(
    monkeypatch,
) -> None:
    """The hard right edge: after the money commit, a tail error costs the tail and nothing else.

    RED before P1 in the opposite direction: the payments were still uncommitted at this point, so
    this failure rolled them back and published nothing - the tick's money depended on whether its
    tail happened to succeed.
    """
    logger = logging.getLogger("test.right_edge")
    run, phase, emitter, committed_effect = _run_with_phase(
        logger=logger,
        run_id="right-edge",
    )
    runner = _Runner(run=run, phase=phase, logger=logger)
    session = _SuccessfulRollbackSession()
    _bind_session(monkeypatch, session)

    await RealTickOrchestrator(runner).tick_real_mode(run.run_id)  # type: ignore[arg-type]

    # The money committed at the boundary and was published exactly once, before the tail ran.
    assert session.commit_calls == 1
    assert emitter.updated == 1
    assert committed_effect.calls == 1
    assert run.committed_total == 1

    # The tail failed and rolled ITS transaction back, and that rollback resolved nothing of the
    # money: the buffer had already resolved, so it cannot be re-resolved in any direction.
    assert runner._real_tick_clearing_coordinator.calls == 1
    assert session.rollback_calls == 1
    assert phase.apply_rollback_observations() is False
    assert phase.apply_unknown_transaction_observations() is False
    assert phase.discard_observations() is False

    # The money phase ran once. A tail failure is not a reason to replay money.
    assert runner._real_tick_payments_coordinator.calls == 1
    assert run._real_money_replays_total == 0

    assert run.last_error["code"] == "REAL_MODE_TICK_FAILED"
    # The phase's own failed payment observation (published by the money commit) plus the tail
    # failure. The money that committed is unaffected by either.
    assert run.errors_total == 2
    assert run._real_consec_tick_failures == 1


@pytest.mark.asyncio
async def test_a_programmatic_failure_still_stops_the_run(monkeypatch) -> None:
    """Counter-check for excluding conflicts from the budget: everything else still counts.

    Without this, "transient conflicts leave the error budget" could be satisfied by a tick that
    never stops a run for any reason at all.
    """
    logger = logging.getLogger("test.programmatic_still_stops")
    run, phase, _emitter, _effect = _run_with_phase(
        logger=logger,
        run_id="programmatic-still-stops",
    )
    runner = _Runner(run=run, phase=phase, logger=logger)
    runner._real_max_consec_tick_failures_limit = 1
    session = _SuccessfulRollbackSession()
    _bind_session(monkeypatch, session)

    await RealTickOrchestrator(runner).tick_real_mode(run.run_id)  # type: ignore[arg-type]

    assert run.errors_total == 2  # the phase's failed payment, plus the tail failure
    assert run._real_consec_tick_failures == 1
    assert [code for code, _ in runner.failures] == ["REAL_MODE_TICK_FAILED_REPEATED"]


@pytest.mark.asyncio
async def test_permanent_contention_stops_the_run_by_no_progress_and_not_by_a_sqlstate(
    monkeypatch,
) -> None:
    """The stop criterion is the absence of progress, stated as its own rule.

    A bounded replay promises nothing under permanent contention, so a run must still be able to
    end - but for a reason that is about the run rather than about an error code. Three ticks lose
    every attempt; the run stops on the third because it has committed no money for three ticks in
    a row, and `errors_total` is still zero the whole way.
    """
    logger = logging.getLogger("test.no_progress")
    run, phase, emitter, _effect = _run_with_phase(
        logger=logger,
        run_id="no-progress",
    )
    runner = _Runner(run=run, phase=phase, logger=logger)
    runner._real_tick_payments_coordinator = _RetryableConflictPaymentsCoordinator()
    runner._real_max_consec_money_no_progress_limit = 3
    session = _SuccessfulRollbackSession()
    _bind_session(monkeypatch, session)

    orchestrator = RealTickOrchestrator(runner)
    for tick in (1, 2, 3):
        run.tick_index = tick
        await orchestrator.tick_real_mode(run.run_id)  # type: ignore[arg-type]

    assert run._real_consec_money_no_progress_ticks == 3
    assert run.errors_total == 0
    assert run._real_consec_tick_failures == 0
    assert emitter.updated == 0
    assert [code for code, _ in runner.failures] == ["REAL_MODE_MONEY_NO_PROGRESS"]
    assert "3 consecutive ticks" in runner.failures[0][1]


@pytest.mark.asyncio
async def test_a_committed_money_phase_resets_the_no_progress_counter(
    monkeypatch,
) -> None:
    """Anti-vacuum for the no-progress criterion: progress must actually clear it.

    A counter that only ever rises would stop every long-running simulation eventually, whatever
    it did.
    """
    logger = logging.getLogger("test.no_progress_reset")
    run, phase, _emitter, _effect = _run_with_phase(
        logger=logger,
        run_id="no-progress-reset",
    )
    runner = _Runner(run=run, phase=phase, logger=logger)
    runner._real_max_consec_money_no_progress_limit = 3
    conflicting = _RetryableConflictPaymentsCoordinator()
    runner._real_tick_payments_coordinator = conflicting
    session = _SuccessfulRollbackSession()
    _bind_session(monkeypatch, session)

    orchestrator = RealTickOrchestrator(runner)
    run.tick_index = 1
    await orchestrator.tick_real_mode(run.run_id)  # type: ignore[arg-type]
    assert run._real_consec_money_no_progress_ticks == 1

    # A tick whose money phase commits clears it.
    runner._real_tick_payments_coordinator = _PaymentsCoordinator(phase)
    run.tick_index = 2
    await orchestrator.tick_real_mode(run.run_id)  # type: ignore[arg-type]

    assert run._real_consec_money_no_progress_ticks == 0
    assert runner.failures == []


@pytest.mark.asyncio
async def test_rollback_failure_in_the_tail_resolves_nothing_of_the_committed_money(
    monkeypatch,
    caplog,
) -> None:
    """A tail rollback whose outcome is unknown still cannot touch money that has committed."""
    logger = logging.getLogger("test.tail_rollback_unknown")
    caplog.set_level(logging.DEBUG, logger=logger.name)
    run, phase, emitter, committed_effect = _run_with_phase(
        logger=logger,
        run_id="tail-rollback-unknown",
    )
    runner = _Runner(run=run, phase=phase, logger=logger)
    session = _Session()  # its rollback raises
    _bind_session(monkeypatch, session)

    await RealTickOrchestrator(runner).tick_real_mode(run.run_id)  # type: ignore[arg-type]

    assert session.commit_calls == 1
    assert session.rollback_calls == 1
    assert emitter.updated == 1
    assert committed_effect.calls == 1
    assert run.committed_total == 1

    rollback_logs = [
        record.getMessage()
        for record in caplog.records
        if "simulator.real.rollback_failed" in record.getMessage()
    ]
    assert rollback_logs == [
        "simulator.real.rollback_failed run_id=tail-rollback-unknown tick=0 "
        "original_error=TickFailure rollback_error=RollbackFailure"
    ]


@pytest.mark.asyncio
async def test_double_cancellation_bounds_the_tail_rollback_and_resolves_once(
    monkeypatch,
) -> None:
    """Kept from before the boundary moved: a rollback interrupted twice is still drained.

    It now runs against the TAIL's rollback, which is where the tick's rollback lives once the
    money has its own boundary. The money has already committed and been published, so the
    cancellation can neither un-publish it nor resolve the buffer a second time.
    """
    logger = logging.getLogger("test.tail_rollback_double_cancellation")
    run, phase, emitter, committed_effect = _run_with_phase(
        logger=logger,
        run_id="tail-rollback-double-cancel",
    )
    runner = _Runner(run=run, phase=phase, logger=logger)
    session = _BlockingRollbackSession()
    _bind_session(monkeypatch, session)
    task = asyncio.create_task(
        RealTickOrchestrator(runner).tick_real_mode(run.run_id)  # type: ignore[arg-type]
    )

    await session.rollback_started.wait()
    task.cancel("first cancellation")
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    task.cancel("second cancellation")
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    with pytest.raises(asyncio.CancelledError):
        await task

    assert session.commit_calls == 1
    assert session.rollback_calls == 0
    assert emitter.updated == 1
    assert committed_effect.calls == 1
    assert phase.apply_rollback_observations() is False
    assert phase.apply_unknown_transaction_observations() is False

    session.release_rollback.set()
    await asyncio.sleep(0)
