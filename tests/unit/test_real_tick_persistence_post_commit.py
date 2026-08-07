import logging
import threading
from datetime import datetime, timezone

import pytest

from app.core.simulator.models import RunRecord
from app.core.simulator.real_tick_persistence import RealTickPersistence


class _FailingCommitSession:
    def __init__(self) -> None:
        self.rollbacks = 0

    async def commit(self) -> None:
        raise RuntimeError("outer commit failed")

    async def rollback(self) -> None:
        self.rollbacks += 1


class _SuccessfulCommitSession(_FailingCommitSession):
    def __init__(self) -> None:
        super().__init__()
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1


class _Artifacts:
    def write_real_tick_artifact(self, *args, **kwargs) -> None:
        raise AssertionError("artifact write must be disabled in this test")


@pytest.mark.asyncio
async def test_outer_commit_failure_does_not_apply_payment_effects():
    run = RunRecord(
        run_id="post-commit-failure",
        scenario_id="scenario",
        mode="real",
        state="running",
        started_at=datetime.now(timezone.utc),
    )
    run.tick_index = 1
    run.sim_time_ms = 1000
    session = _FailingCommitSession()
    applied = 0
    rollback_observed = 0

    def _on_commit() -> None:
        nonlocal applied
        applied += 1

    def _on_rollback() -> None:
        nonlocal rollback_observed
        rollback_observed += 1

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

    with pytest.raises(RuntimeError, match="outer commit failed"):
        await persistence.persist_tick_tail(
            session=session,
            run=run,
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
            on_commit=_on_commit,
            on_rollback=_on_rollback,
        )

    assert session.rollbacks == 1
    assert applied == 0
    assert rollback_observed == 1


@pytest.mark.asyncio
async def test_post_commit_callback_failure_does_not_rollback_durable_commit():
    run = RunRecord(
        run_id="post-commit-callback-failure",
        scenario_id="scenario",
        mode="real",
        state="running",
        started_at=datetime.now(timezone.utc),
    )
    run.tick_index = 1
    run.sim_time_ms = 1000
    session = _SuccessfulCommitSession()

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

    await persistence.persist_tick_tail(
        session=session,
        run=run,
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
        on_commit=lambda: (_ for _ in ()).throw(RuntimeError("effect failed")),
    )

    assert session.commits == 1
    assert session.rollbacks == 0
