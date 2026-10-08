"""034 S1 (`F-034-1`, `F-034-2`): the two rules of the fix that need no database.

1. THE KEY. The first launch of a run (epoch 0) keeps the idempotency key it always had, byte for byte - the stored
   payments of every run that was never restarted stay answerable - and each restart gives the same planned payment
   another key. The old formula is spelled out below on purpose: it is the contract being kept.
2. THE PUBLICATION. Once the money commit is confirmed, it is counted and its payments are published whatever happens
   to the reading of their visual patches: a failure of it, or a cancellation of the caller while it runs - which
   cancels the reading (it is optional), never the publication or the counters.

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
    """A committed phase of one payment whose patch reading is under the test's control."""

    committed = 1
    staged_tx_ids = frozenset({"tx-1"})

    def __init__(self, *, fail: bool = False) -> None:
        self.started, self.release = asyncio.Event(), asyncio.Event()
        self.fail, self.built, self.read_cancelled, self.published = fail, False, False, 0

    async def build_post_commit_patches(self, _open_session) -> None:
        self.started.set()
        if self.fail:
            raise RuntimeError("p034: the patches could not be read")
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.read_cancelled = True
            raise
        self.built = True

    def apply_deferred_effects(self) -> bool:
        self.published += 1
        return True

    def _already_resolved(self) -> bool:
        assert self.published, "a committed phase was resolved as something else before it was published"
        return False

    apply_rollback_observations = apply_unknown_transaction_observations = discard_observations = _already_resolved


class _Session:
    """A money session whose COMMIT succeeds."""

    async def __aenter__(self) -> "_Session":
        return self

    async def __aexit__(self, *_exc) -> None:
        return None

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


def _money_phase(phase: _Phase) -> tuple[RunRecord, "asyncio.Task"]:
    """The real owner of the money phase (`run_money_phase_with_bounded_replay`) over a session that commits."""

    run = RunRecord(run_id="run-1", scenario_id="s", mode="real", state="running")
    run._real_consec_money_no_progress_ticks = 2  # a confirmed commit is progress: it must clear this

    async def attempt(_session):
        return phase, False

    task = asyncio.create_task(money_replay.run_money_phase_with_bounded_replay(
        run_id=run.run_id, run=run, lock=threading.RLock(), logger=logging.getLogger("tests.p034.publish"),
        max_attempts=1, open_session=_Session, run_money_attempt=attempt))
    return run, task


def _money_counters(run: RunRecord) -> tuple[int, int, int, int]:
    return (run._real_money_committed_ticks_total, run._real_money_committed_payments_total,
            run._real_money_attempts_total, run._real_consec_money_no_progress_ticks)


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


async def _cancel_while_the_patches_are_read(phase: _Phase, task: "asyncio.Task") -> bool:
    """Cancel `task` once it is reading the patches; say whether it ended WITHOUT the reading being released."""

    await phase.started.wait()
    task.cancel()  # the tick is being stopped; the commit is already confirmed
    done, _pending = await asyncio.wait({task}, timeout=5.0)
    ended = task in done
    phase.release.set()  # only so that a tree which still drains the reading does not hang this test
    await asyncio.gather(task, return_exceptions=True)
    return ended


@pytest.mark.asyncio
async def test_a_caller_cancelled_while_the_patches_are_read_publishes_without_them() -> None:
    """REWRITTEN 2026-10-08 (§15 review of `62cce627`, finding C; was `..._still_publishes`, which held that the
    reading runs to its end through the cancellation). That contract is gone: only the money's commit and rollback
    are drained; an optional visual read is waited for CANCELLABLY, so a run can be stopped while it is stuck."""

    phase = _Phase()
    task = asyncio.create_task(
        money_replay._publish_committed(
            phase, open_session=lambda: None, logger=logging.getLogger("tests.p034.publish"), run_id="run-1"
        )
    )
    ended = await _cancel_while_the_patches_are_read(phase, task)
    assert (ended, task.cancelled(), phase.read_cancelled, phase.built, phase.published) == (True, True, True, False, 1), (
        f"ended without the reading being released: {ended}; the cancellation left: {task.cancelled()}; the reading "
        f"was cancelled: {phase.read_cancelled}, completed: {phase.built}; published: {phase.published}"
    )


@pytest.mark.asyncio
async def test_a_confirmed_commit_is_counted_before_the_patches_are_waited_for() -> None:
    """Finding B: a cancellation during the patch reading left the payment committed and published while the
    counters of the committed money phase stayed at zero and "no money progress" was not cleared."""

    phase = _Phase()
    run, task = _money_phase(phase)
    await _cancel_while_the_patches_are_read(phase, task)
    assert task.cancelled() and phase.published == 1, (task, phase.published)
    assert _money_counters(run) == (1, 1, 1, 0), (
        f"committed money ticks, committed money payments, money attempts, ticks without money progress = "
        f"{_money_counters(run)} after a commit that was confirmed and published; expected (1, 1, 1, 0)"
    )


@pytest.mark.asyncio
async def test_a_confirmed_commit_is_counted_once_on_the_ordinary_path() -> None:
    phase = _Phase()
    phase.release.set()
    run, task = _money_phase(phase)
    outcome = await task
    await outcome.stack.aclose()
    assert (phase.built, phase.published, _money_counters(run)) == (True, 1, (1, 1, 1, 0))
