"""034 S1 (`F-034-1`, `F-034-2`): the two rules of the fix that need no database.

1. THE KEY. The first launch of a run (epoch 0) keeps the idempotency key it always had, byte for byte - the stored
   payments of every run that was never restarted stay answerable - and each restart gives the same planned payment
   another key. The old formula is spelled out below on purpose: it is the contract being kept.
2. THE PUBLICATION. Once the money commit is confirmed, its payments are published whatever happens to the reading of
   their visual patches: a failure of it, or a cancellation of the caller while it runs.

The restart itself, the stored rows and the SQL are the subject of the PostgreSQL stands
`tests/integration/test_p034_s1_*`.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import threading
from datetime import datetime, timezone

import pytest

import app.core.simulator.money_replay as money_replay
from app.core.simulator.models import RunRecord
from app.core.simulator.real_runner_impl import RealRunnerImpl
from app.core.simulator.run_lifecycle import RunLifecycle


def _runner() -> RealRunnerImpl:
    return RealRunnerImpl(
        lock=threading.RLock(), get_run=lambda _run_id: None, get_scenario_raw=lambda _scenario_id: {}, sse=None,
        artifacts=None, utc_now=lambda: datetime.now(timezone.utc), publish_run_status=lambda _run_id: None,
        db_enabled=lambda: False, actions_per_tick_max=1, clearing_every_n_ticks=25,
        real_max_consec_tick_failures_default=3, real_max_timeouts_per_tick_default=3,
        real_max_errors_total_default=10, logger=logging.getLogger(__name__),
    )


_PAYMENT = dict(run_id="run-1", tick_ms=1, sender_pid="A", receiver_pid="B", equivalent="UAH", amount="25.35", seq=0)


def test_the_first_launch_keeps_its_key_and_every_restart_gets_another() -> None:
    runner = _runner()
    before_034 = "sim:" + hashlib.sha256(b"run-1|1|A|B|UAH|25.35|0").hexdigest()[:32]

    assert runner._sim_idempotency_key(**_PAYMENT) == before_034
    assert runner._sim_idempotency_key(**_PAYMENT, epoch=0) == before_034
    keys = [runner._sim_idempotency_key(**_PAYMENT, epoch=epoch) for epoch in range(4)]
    assert len(set(keys)) == 4, keys
    assert runner._sim_idempotency_key(**_PAYMENT, epoch=2) == keys[2]  # one launch, one key: still idempotent
    # The epoch is its own field: it cannot be mistaken for a longer `seq` of the first launch.
    assert runner._sim_idempotency_key(**{**_PAYMENT, "seq": 1}, epoch=0) != keys[1]


@pytest.mark.asyncio
async def test_each_restart_starts_a_new_launch_epoch(monkeypatch) -> None:
    import app.core.simulator.storage as simulator_storage

    async def _noop(*_a, **_kw):
        return None

    monkeypatch.setattr(simulator_storage, "upsert_run", _noop)
    run = RunRecord(run_id="run-1", scenario_id="s", mode="real", state="stopped")
    run.tick_index, run.sim_time_ms = 7, 7000
    unused = dict.fromkeys(["new_run_id", "get_scenario_raw", "edges_by_equivalent"])
    lifecycle = RunLifecycle(
        lock=threading.RLock(), runs={run.run_id: run}, set_active_run_id=lambda *_: None,
        utc_now=lambda: datetime.now(timezone.utc),
        sse=type("S", (), {"prune_event_buffer_locked": lambda _s, _r: None})(), heartbeat_loop=_noop,
        publish_run_status=lambda _: None, run_to_status=lambda _: None, get_run_status_payload_json=lambda _: {},
        real_max_in_flight_default=1, get_max_active_runs=lambda: 0, get_max_run_records=lambda: 0, logger=None,
        artifacts=type("A", (), {"start_events_writer": lambda _s, _r: None})(), **unused,
    )
    assert run._launch_epoch == 0
    for expected in (1, 2):
        run.state = "stopped"
        await lifecycle.restart(run.run_id)
        assert (run._launch_epoch, run.tick_index, run.run_id) == (expected, 0, "run-1")


class _Phase:
    """A committed phase whose patch reading is under the test's control."""

    def __init__(self, *, fail: bool = False) -> None:
        self.started, self.release = asyncio.Event(), asyncio.Event()
        self.fail, self.built, self.published = fail, False, 0

    async def build_post_commit_patches(self, _open_session) -> None:
        self.started.set()
        if self.fail:
            raise RuntimeError("p034: the patches could not be read")
        await self.release.wait()
        self.built = True

    def apply_deferred_effects(self) -> bool:
        self.published += 1
        return True


@pytest.mark.asyncio
async def test_a_failed_patch_reading_is_logged_and_the_payments_are_still_published(caplog) -> None:
    phase = _Phase(fail=True)
    logger = logging.getLogger("tests.p034.publish")
    with caplog.at_level(logging.WARNING, logger=logger.name):
        await money_replay._publish_committed(phase, open_session=lambda: None, logger=logger, run_id="run-1")
    assert phase.published == 1
    assert [r.getMessage() for r in caplog.records if r.name == logger.name] == [
        "simulator.real.payment_patches_failed run_id=run-1 error_type=RuntimeError"
    ]


@pytest.mark.asyncio
async def test_a_caller_cancelled_while_the_patches_are_read_still_publishes(caplog) -> None:
    phase = _Phase()
    task = asyncio.create_task(
        money_replay._publish_committed(
            phase, open_session=lambda: None, logger=logging.getLogger("tests.p034.publish"), run_id="run-1"
        )
    )
    await phase.started.wait()
    task.cancel()  # the tick is being stopped; the commit is already confirmed
    phase.release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    # The reading ran to its end (its session was not abandoned mid-statement), the payments were published once,
    # and only then did the cancellation leave.
    assert (phase.built, phase.published) == (True, 1)
