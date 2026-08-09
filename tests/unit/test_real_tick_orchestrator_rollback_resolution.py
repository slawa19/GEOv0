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


class TickFailure(RuntimeError):
    pass


class RollbackFailure(RuntimeError):
    pass


class _Session:
    def __init__(self) -> None:
        self.rollback_calls = 0

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
    def __init__(self, phase: RealTickPaymentsPhaseResult) -> None:
        self.phase = phase

    async def run_payments_phase(self, **_kwargs):
        return self.phase, False


class _ClearingCoordinator:
    async def maybe_run_clearing(self, **_kwargs):
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

    async def fail_run(self, *args, **kwargs) -> None:
        raise AssertionError("failure threshold is disabled")


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
    )
    return run, phase, emitter, committed_effect


@pytest.mark.asyncio
async def test_rollback_failure_resolves_only_failure_observations_as_unknown(
    monkeypatch,
    caplog,
) -> None:
    logger = logging.getLogger("test.rollback_unknown_outcome")
    caplog.set_level(logging.DEBUG, logger=logger.name)
    run, phase, emitter, committed_effect = _run_with_phase(
        logger=logger,
        run_id="rollback-unknown",
    )
    runner = _Runner(run=run, phase=phase, logger=logger)
    session = _Session()
    monkeypatch.setattr(
        orchestrator_module.db_session,
        "AsyncSessionLocal",
        lambda: _SessionContext(session),
    )

    await RealTickOrchestrator(runner).tick_real_mode(run.run_id)  # type: ignore[arg-type]

    assert session.rollback_calls == 1
    assert committed_effect.calls == 0
    assert emitter.updated == 0
    assert emitter.failed == 2
    assert run.attempts_total == 2
    assert run.committed_total == 0
    assert run.rejected_total == 1
    assert run.errors_total == 2
    assert run.last_error is not None
    assert run.last_error["code"] == "REAL_MODE_TICK_FAILED"
    assert run.last_error["message"] == "fail after payments"
    assert phase.apply_unknown_transaction_observations() is False

    rollback_logs = [
        record.getMessage()
        for record in caplog.records
        if "simulator.real.rollback_failed" in record.getMessage()
    ]
    assert rollback_logs == [
        "simulator.real.rollback_failed run_id=rollback-unknown tick=0 "
        "original_error=TickFailure rollback_error=RollbackFailure"
    ]


@pytest.mark.asyncio
async def test_double_cancellation_bounds_final_rollback_and_resolves_unknown(
    monkeypatch,
) -> None:
    logger = logging.getLogger("test.rollback_double_cancellation")
    run, phase, emitter, committed_effect = _run_with_phase(
        logger=logger,
        run_id="rollback-double-cancel",
    )
    runner = _Runner(run=run, phase=phase, logger=logger)
    session = _BlockingRollbackSession()
    monkeypatch.setattr(
        orchestrator_module.db_session,
        "AsyncSessionLocal",
        lambda: _SessionContext(session),
    )
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

    assert session.rollback_calls == 0
    assert committed_effect.calls == 0
    assert committed_effect.cache_invalidations == 1
    assert emitter.updated == 0
    assert emitter.failed == 2
    assert run.attempts_total == 2
    assert run.committed_total == 0
    assert run.rejected_total == 1
    assert run.errors_total == 1
    assert phase.apply_rollback_observations() is False
    assert phase.apply_unknown_transaction_observations() is False

    session.release_rollback.set()
    await asyncio.sleep(0)
