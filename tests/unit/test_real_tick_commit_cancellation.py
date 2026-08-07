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
    def __init__(self, *, commit_error: Exception | None = None) -> None:
        self.commit_started = asyncio.Event()
        self.release_commit = asyncio.Event()
        self.commit_error = commit_error
        self.commits = 0
        self.rollbacks = 0

    async def commit(self) -> None:
        self.commit_started.set()
        await self.release_commit.wait()
        if self.commit_error is not None:
            raise self.commit_error
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


class _Resolution:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

    def apply_deferred_effects(self) -> bool:
        self.commits += 1
        return True

    def apply_rollback_observations(self) -> bool:
        self.rollbacks += 1
        return True


class _Artifacts:
    def write_real_tick_artifact(self, *args, **kwargs) -> None:
        raise AssertionError("artifact write must be disabled")


class _DecayEngine:
    async def apply_trust_decay(self, **_kwargs):
        return SimpleNamespace(
            updated_count=1,
            touched_equivalents={"UAH"},
            touched_edges_by_eq={"UAH": set()},
        )


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


async def _cancel_during_commit(task, session: _ControlledSession) -> None:
    await session.commit_started.wait()
    task.cancel()
    session.release_commit.set()
    with pytest.raises(asyncio.CancelledError):
        await task


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
        )
    )

    await _cancel_during_commit(task, session)

    assert session.commits == 1
    assert session.rollbacks == 0
    assert resolution.commits == 1
    assert resolution.rollbacks == 0


@pytest.mark.asyncio
async def test_trust_drift_cancellation_waits_for_commit_and_resolves_commit():
    session = _ControlledSession()
    resolution = _Resolution()
    coordinator = RealTickTrustDriftCoordinator(logger=logging.getLogger(__name__))
    task = asyncio.create_task(
        coordinator.apply_trust_decay_and_broadcast(
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
        )
    )

    await _cancel_during_commit(task, session)

    assert session.commits == 1
    assert session.rollbacks == 0
    assert resolution.commits == 1
    assert resolution.rollbacks == 0


async def _unexpected_edge_patch(**_kwargs):
    raise AssertionError("edge patch must not run after cancellation")


@pytest.mark.asyncio
async def test_trust_drift_commit_failure_rolls_back_resolves_and_propagates():
    session = _ControlledSession(commit_error=RuntimeError("commit failed"))
    session.release_commit.set()
    resolution = _Resolution()
    coordinator = RealTickTrustDriftCoordinator(logger=logging.getLogger(__name__))

    with pytest.raises(RuntimeError, match="commit failed"):
        await coordinator.apply_trust_decay_and_broadcast(
            session=session,
            run_id="commit-failure",
            run=_run(),
            tick_index=1,
            debt_snapshot={},
            scenario={},
            trust_drift_engine=_DecayEngine(),  # type: ignore[arg-type]
            build_edge_patch_for_equivalent=_unexpected_edge_patch,
            broadcast_topology_edge_patch=lambda **_kwargs: None,
            on_commit=resolution.apply_deferred_effects,
            on_rollback=resolution.apply_rollback_observations,
        )

    assert session.commits == 0
    assert session.rollbacks == 1
    assert resolution.commits == 0
    assert resolution.rollbacks == 1
