import asyncio
import logging
import threading
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.core.simulator.models import RunRecord
from app.core.simulator.real_tick_clearing_coordinator import (
    RealTickClearingCoordinator,
)
from app.core.simulator.real_tick_persistence import RealTickPersistence
from app.core.simulator.real_tick_trust_drift_coordinator import (
    RealTickTrustDriftCoordinator,
)


class _ControlledSession:
    def __init__(
        self,
        *,
        commit_error: Exception | None = None,
        rollback_error: BaseException | None = None,
        block_rollback: bool = False,
    ) -> None:
        self.commit_started = asyncio.Event()
        self.release_commit = asyncio.Event()
        self.rollback_started = asyncio.Event()
        self.release_rollback = asyncio.Event()
        if not block_rollback:
            self.release_rollback.set()
        self.commit_error = commit_error
        self.rollback_error = rollback_error
        self.commits = 0
        self.rollbacks = 0

    async def commit(self) -> None:
        self.commit_started.set()
        await self.release_commit.wait()
        if self.commit_error is not None:
            raise self.commit_error
        self.commits += 1

    async def rollback(self) -> None:
        self.rollback_started.set()
        await self.release_rollback.wait()
        self.rollbacks += 1
        if self.rollback_error is not None:
            raise self.rollback_error


class _Resolution:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0
        self.unknowns = 0

    def apply_deferred_effects(self) -> bool:
        self.commits += 1
        return True

    def apply_rollback_observations(self) -> bool:
        self.rollbacks += 1
        return True

    def apply_unknown_transaction_observations(self) -> bool:
        self.unknowns += 1
        return True


class _Artifacts:
    def write_real_tick_artifact(self, *args, **kwargs) -> None:
        raise AssertionError("artifact write must be disabled")


class _DecayEngine:
    def __init__(self) -> None:
        self.committed_effects = 0

    async def apply_trust_decay(self, **_kwargs):
        return SimpleNamespace(
            updated_count=1,
            touched_equivalents={"UAH"},
            touched_edges_by_eq={"UAH": set()},
        )

    def apply_committed_effects(self, **_kwargs) -> None:
        self.committed_effects += 1


def _run() -> RunRecord:
    run = RunRecord(
        run_id="commit-cancellation",
        scenario_id="scenario",
        mode="real",
        state="running",
        started_at=datetime.now(timezone.utc),
    )
    run.tick_index = 1
    run.sim_time_ms = 1000
    return run


def _public_commit_path(
    boundary: str,
    session: _ControlledSession,
    resolution: _Resolution,
):
    if boundary == "clearing":
        coordinator = RealTickClearingCoordinator(
            lock=threading.RLock(),
            logger=logging.getLogger(__name__),
            clearing_every_n_ticks=1,
            real_clearing_time_budget_ms=250,
        )
        return coordinator.maybe_run_clearing(
            session=session,
            run_id="commit-cancellation",
            run=_run(),
            equivalents=["UAH"],
            planned_len=1,
            tick_t0=0.0,
            clearing_enabled=True,
            safe_int_env=lambda _key, default: default,
            run_clearing=_unexpected_clearing,
            payments_result=resolution,
        )
    if boundary == "persistence":
        persistence = RealTickPersistence(
            lock=threading.RLock(),
            artifacts=_Artifacts(),
            utc_now=lambda: datetime.now(timezone.utc),
            db_enabled=lambda: False,
            logger=logging.getLogger(__name__),
            real_db_metrics_every_n_ticks=100,
            real_db_bottlenecks_every_n_ticks=100,
            real_last_tick_write_every_ms=0,
            real_artifacts_sync_every_ms=0,
        )
        return persistence.persist_tick_tail(
            session=session,
            run=_run(),
            equivalents=["UAH"],
            tick_t0=0.0,
            planned_len=1,
            committed=1,
            rejected=0,
            errors=0,
            timeouts=0,
            per_eq={"UAH": {"committed": 1}},
            per_eq_metric_values={"UAH": {}},
            per_eq_edge_stats={"UAH": {}},
            on_commit=resolution.apply_deferred_effects,
            on_rollback=resolution.apply_rollback_observations,
            on_unknown=resolution.apply_unknown_transaction_observations,
        )
    if boundary == "trust":
        coordinator = RealTickTrustDriftCoordinator(logger=logging.getLogger(__name__))
        return coordinator.apply_trust_decay_and_broadcast(
            session=session,
            run_id="commit-cancellation",
            run=_run(),
            tick_index=1,
            debt_snapshot={},
            scenario={},
            trust_drift_engine=_DecayEngine(),  # type: ignore[arg-type]
            build_edge_patch_for_equivalent=_unexpected_edge_patch,
            broadcast_topology_edge_patch=lambda **_kwargs: None,
            on_commit=resolution.apply_deferred_effects,
            on_rollback=resolution.apply_rollback_observations,
            on_unknown=resolution.apply_unknown_transaction_observations,
        )
    raise AssertionError(f"unknown boundary: {boundary}")


async def _unexpected_clearing():
    raise AssertionError("clearing must not run after cancellation")


async def _cancel_during_commit(task, session: _ControlledSession) -> None:
    await session.commit_started.wait()
    task.cancel()
    session.release_commit.set()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.parametrize("boundary", ["clearing", "persistence", "trust"])
@pytest.mark.asyncio
async def test_repeated_cancellation_bounds_wait_and_marks_commit_unknown(boundary: str):
    session = _ControlledSession()
    resolution = _Resolution()
    task = asyncio.create_task(_public_commit_path(boundary, session, resolution))

    await session.commit_started.wait()
    task.cancel("first cancellation")
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    task.cancel("second cancellation")
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    with pytest.raises(asyncio.CancelledError) as cancelled:
        await task
    assert cancelled.value.args == ("first cancellation",)

    assert session.commits == 0
    # The cancelled commit is drained before the owner session can close, then
    # the same session is rolled back to a terminal cleanup outcome.
    assert session.rollbacks == 1
    assert resolution.commits == 0
    assert resolution.rollbacks == 0
    assert resolution.unknowns == 1

    session.release_commit.set()
    await asyncio.wait_for(session.commit_started.wait(), timeout=1.0)
    await asyncio.sleep(0)


@pytest.mark.parametrize("boundary", ["clearing", "persistence", "trust"])
@pytest.mark.asyncio
async def test_repeated_cancellation_bounds_rollback_wait_and_marks_unknown(boundary: str):
    session = _ControlledSession(
        commit_error=RuntimeError("commit failed"),
        block_rollback=True,
    )
    resolution = _Resolution()
    task = asyncio.create_task(_public_commit_path(boundary, session, resolution))

    await session.commit_started.wait()
    task.cancel("cancel during commit")
    await asyncio.sleep(0)
    session.release_commit.set()
    await session.rollback_started.wait()

    task.cancel("cancel during rollback")
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    with pytest.raises(asyncio.CancelledError) as cancelled:
        await task
    assert cancelled.value.args == ("cancel during commit",)

    assert session.commits == 0
    assert session.rollbacks == 0
    assert resolution.commits == 0
    assert resolution.rollbacks == 0
    assert resolution.unknowns == 1

    session.release_rollback.set()
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_clearing_cancellation_waits_for_commit_and_resolves_commit():
    session = _ControlledSession()
    resolution = _Resolution()
    clearing_called = False

    async def _run_clearing():
        nonlocal clearing_called
        clearing_called = True
        return {"UAH": 0.0}

    coordinator = RealTickClearingCoordinator(
        lock=threading.RLock(),
        logger=logging.getLogger(__name__),
        clearing_every_n_ticks=1,
        real_clearing_time_budget_ms=250,
    )
    task = asyncio.create_task(
        coordinator.maybe_run_clearing(
            session=session,
            run_id="commit-cancellation",
            run=_run(),
            equivalents=["UAH"],
            planned_len=1,
            tick_t0=0.0,
            clearing_enabled=True,
            safe_int_env=lambda _key, default: default,
            run_clearing=_run_clearing,
            payments_result=resolution,
        )
    )

    await _cancel_during_commit(task, session)

    assert session.commits == 1
    assert session.rollbacks == 0
    assert resolution.commits == 1
    assert resolution.rollbacks == 0
    assert resolution.unknowns == 0
    assert clearing_called is False


@pytest.mark.asyncio
async def test_persistence_cancellation_waits_for_commit_and_resolves_commit():
    session = _ControlledSession()
    resolution = _Resolution()
    persistence = RealTickPersistence(
        lock=threading.RLock(),
        artifacts=_Artifacts(),
        utc_now=lambda: datetime.now(timezone.utc),
        db_enabled=lambda: False,
        logger=logging.getLogger(__name__),
        real_db_metrics_every_n_ticks=100,
        real_db_bottlenecks_every_n_ticks=100,
        real_last_tick_write_every_ms=0,
        real_artifacts_sync_every_ms=0,
    )
    task = asyncio.create_task(
        persistence.persist_tick_tail(
            session=session,
            run=_run(),
            equivalents=["UAH"],
            tick_t0=0.0,
            planned_len=1,
            committed=1,
            rejected=0,
            errors=0,
            timeouts=0,
            per_eq={"UAH": {"committed": 1}},
            per_eq_metric_values={"UAH": {}},
            per_eq_edge_stats={"UAH": {}},
            on_commit=resolution.apply_deferred_effects,
            on_rollback=resolution.apply_rollback_observations,
            on_unknown=resolution.apply_unknown_transaction_observations,
        )
    )

    await _cancel_during_commit(task, session)

    assert session.commits == 1
    assert session.rollbacks == 0
    assert resolution.commits == 1
    assert resolution.rollbacks == 0
    assert resolution.unknowns == 0


@pytest.mark.asyncio
async def test_trust_drift_cancellation_waits_for_commit_and_resolves_commit():
    session = _ControlledSession()
    resolution = _Resolution()
    decay_engine = _DecayEngine()
    coordinator = RealTickTrustDriftCoordinator(logger=logging.getLogger(__name__))
    task = asyncio.create_task(
        coordinator.apply_trust_decay_and_broadcast(
            session=session,
            run_id="commit-cancellation",
            run=_run(),
            tick_index=1,
            debt_snapshot={},
            scenario={},
            trust_drift_engine=decay_engine,  # type: ignore[arg-type]
            build_edge_patch_for_equivalent=_unexpected_edge_patch,
            broadcast_topology_edge_patch=lambda **_kwargs: None,
            on_commit=resolution.apply_deferred_effects,
            on_rollback=resolution.apply_rollback_observations,
            on_unknown=resolution.apply_unknown_transaction_observations,
        )
    )

    await _cancel_during_commit(task, session)

    assert session.commits == 1
    assert session.rollbacks == 0
    assert resolution.commits == 1
    assert resolution.rollbacks == 0
    assert resolution.unknowns == 0
    assert decay_engine.committed_effects == 1


@pytest.mark.asyncio
async def test_trust_drift_commit_failure_does_not_apply_staged_effects():
    session = _ControlledSession(commit_error=RuntimeError("trust commit failed"))
    session.release_commit.set()
    resolution = _Resolution()
    decay_engine = _DecayEngine()
    coordinator = RealTickTrustDriftCoordinator(logger=logging.getLogger(__name__))

    with pytest.raises(RuntimeError, match="trust commit failed"):
        await coordinator.apply_trust_decay_and_broadcast(
            session=session,
            run_id="commit-failure",
            run=_run(),
            tick_index=1,
            debt_snapshot={},
            scenario={},
            trust_drift_engine=decay_engine,  # type: ignore[arg-type]
            build_edge_patch_for_equivalent=_unexpected_edge_patch,
            broadcast_topology_edge_patch=lambda **_kwargs: None,
            on_commit=resolution.apply_deferred_effects,
            on_rollback=resolution.apply_rollback_observations,
            on_unknown=resolution.apply_unknown_transaction_observations,
        )

    assert session.rollbacks == 1
    assert resolution.commits == 0
    assert resolution.rollbacks == 1
    assert resolution.unknowns == 0
    assert decay_engine.committed_effects == 0


async def _unexpected_edge_patch(**_kwargs):
    raise AssertionError("edge patch must not run after cancellation")


@pytest.mark.parametrize("boundary", ["clearing", "persistence", "trust"])
@pytest.mark.parametrize(
    "rollback_outcome",
    [
        "success",
        "failure",
        "cancelled",
    ],
)
@pytest.mark.asyncio
async def test_commit_failure_resolves_terminal_rollback_outcome_and_propagates_original(
    boundary: str,
    rollback_outcome: str,
    caplog,
):
    rollback_error: BaseException | None = None
    if rollback_outcome == "failure":
        rollback_error = RuntimeError("rollback failed")
    elif rollback_outcome == "cancelled":
        rollback_error = asyncio.CancelledError("rollback cancelled")

    session = _ControlledSession(
        commit_error=RuntimeError("commit failed"),
        rollback_error=rollback_error,
    )
    session.release_commit.set()
    resolution = _Resolution()
    caplog.set_level(logging.WARNING, logger=__name__)

    with pytest.raises(RuntimeError, match="commit failed"):
        await _public_commit_path(boundary, session, resolution)

    assert session.commits == 0
    assert session.rollbacks == 1
    assert resolution.commits == 0
    assert resolution.rollbacks == (1 if rollback_error is None else 0)
    assert resolution.unknowns == (0 if rollback_error is None else 1)

    rollback_failure_logs = [
        record.getMessage()
        for record in caplog.records
        if "simulator.real.rollback_after_commit_failure_failed" in record.getMessage()
    ]
    if rollback_error is not None:
        assert rollback_failure_logs == [
            "simulator.real.rollback_after_commit_failure_failed "
            f"commit_error=RuntimeError rollback_error={type(rollback_error).__name__}"
        ]
    else:
        assert rollback_failure_logs == []


@pytest.mark.parametrize("boundary", ["clearing", "persistence", "trust"])
@pytest.mark.parametrize(
    "rollback_outcome",
    [
        "success",
        "failure",
        "cancelled",
    ],
)
@pytest.mark.asyncio
async def test_cancelled_commit_resolves_unknown_for_every_cleanup_outcome(
    boundary: str,
    rollback_outcome: str,
    caplog,
):
    rollback_error: BaseException | None = None
    if rollback_outcome == "failure":
        rollback_error = RuntimeError("rollback failed")
    elif rollback_outcome == "cancelled":
        rollback_error = asyncio.CancelledError("rollback cancelled")

    session = _ControlledSession(
        commit_error=asyncio.CancelledError("commit response lost"),
        rollback_error=rollback_error,
    )
    session.release_commit.set()
    resolution = _Resolution()
    caplog.set_level(logging.WARNING, logger=__name__)

    with pytest.raises(asyncio.CancelledError, match="commit response lost"):
        await _public_commit_path(boundary, session, resolution)

    assert session.commits == 0
    assert session.rollbacks == 1
    assert resolution.commits == 0
    assert resolution.rollbacks == 0
    assert resolution.unknowns == 1

    ambiguity_logs = [
        record.getMessage()
        for record in caplog.records
        if "simulator.real.commit_outcome_unknown" in record.getMessage()
    ]
    assert ambiguity_logs == [
        "simulator.real.commit_outcome_unknown reason=commit_cancelled "
        f"cleanup_outcome={'rollback_succeeded' if rollback_error is None else 'rollback_failed'} "
        "commit_error=CancelledError "
        f"rollback_error={type(rollback_error).__name__ if rollback_error is not None else 'None'}"
    ]

    cleanup_failure_logs = [
        record.getMessage()
        for record in caplog.records
        if "simulator.real.rollback_after_commit_failure_failed" in record.getMessage()
    ]
    if rollback_error is None:
        assert cleanup_failure_logs == []
    else:
        assert cleanup_failure_logs == [
            "simulator.real.rollback_after_commit_failure_failed "
            "commit_error=CancelledError "
            f"rollback_error={type(rollback_error).__name__}"
        ]
