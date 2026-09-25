"""Programme 019 stage 5 (`T1907`, `FORK-7`; spec, "Изоляция, писатели и клиринг", item 6): the clearing
holds its stop/hold read `FOR SHARE` from the read through its commit, in every attempt.

WHY. `T1544` promises an observable cutoff: once the deactivating `PATCH` has answered, no money moves in
the equivalent; `T1546` promises the same for an integrity hold. Until stage 5 the clearing read the flag
with a plain `SELECT` (`row_lock=False`) and relied on the equivalent OWNER LOCK: a clearing held it from
its read through its commit, and the PATCH (and the reaction that sets a hold) took the same lock. The
owner lock is exactly what stage 5 may remove, and SERIALIZABLE alone does not give this ORDER OF
COMPLETION - it may serialise "the clearing read `active` -> the PATCH answered 200 -> the clearing
committed" as clearing-before-PATCH, which is serializable and still breaks the cutoff the operator was
told about. A row lock gives it: `FOR SHARE` on the equivalent row makes the PATCH's (or the hold's)
`UPDATE` of that row wait for the clearing's commit.

THE STAND MEASURES THE ROW LOCK ALONE. The money-boundary locks are switched off for the test
(`tests/p019_locks_off.py`), and the stand shows it: the switch counted the clearing's session-owner
acquisition, and no advisory lock is held on this database while the clearing is parked. The clearing is
parked right after its stop/hold read; the writer is started; the probe then asks `pg_locks` /
`pg_stat_activity` whether a backend is waiting on a ROW OR TRANSACTION lock that the CLEARING'S backend
holds (`pg_blocking_pids`) - the probe of the actual blocked backend the spec asks for, not "some advisory
waiter exists".

TWO WRITERS, SEPARATELY (item 6): the deactivating `PATCH` through the admin API, and the hold as the
reaction writes it - one `UPDATE` of the equivalent row (`hold_directly`, the stand helper of step 5c; the
reaction's own ordering against the owner lock is covered in `test_p015_step5c_hold_races_postgres.py`).
`DELETE` of an equivalent is not a cutoff against a clearing: it refuses an equivalent that still has
debts (`_equivalent_usage_counts`), and a clearing runs only over existing debts.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest
from sqlalchemy import select, text

from app.core.clearing.service import ClearingService
from app.db.models.equivalent import Equivalent
from tests.integration.p019_interlock_support import _seed_interlock_case, _use_serializable
from tests.integration.test_p015_p1_money_replay_postgres import factory  # noqa: F401 - fixture
from tests.integration.test_p015_t1544_operator_stop_races_postgres import (  # noqa: F401 - fixture
    _deactivate,
    admin_api,
)
from tests.p019_locks_off import advisory_locks_held, blocked_by, switch_money_boundary_locks_off
from tests.p019_support import require_target
from tests.unit.test_p015_step5c_reaction_and_hold import hold_directly

# MODE B: every commit lands in a clone dropped after the test (`tests/tier_on_a_clone.py`).
from tests.tier_on_a_clone import tier_sessions_on_a_clone  # noqa: E402,F401 - autouse fixture


async def _equivalent_state(factory, equivalent_id):  # noqa: F811
    async with factory() as s:
        return (
            await s.execute(
                select(Equivalent.is_active, Equivalent.integrity_hold_result_id).where(
                    Equivalent.id == equivalent_id
                )
            )
        ).one()


@pytest.mark.asyncio
@pytest.mark.parametrize("writer", ["patch_deactivate", "hold_set"])
async def test_a_stopping_writer_waits_for_a_clearing_that_already_read_the_flag_without_the_owner_lock(
    writer, factory, admin_api, monkeypatch  # noqa: F811
) -> None:
    from tests.conftest import TestingSessionLocal

    client, _gate = admin_api
    switch = switch_money_boundary_locks_off(monkeypatch)
    seed = await _seed_interlock_case()
    clearing_session = TestingSessionLocal()
    observer = TestingSessionLocal()
    completed: list[str] = []
    paused = asyncio.Event()
    release_clearing = asyncio.Event()
    clearing_pid: list[int] = []
    clearing = stopper = None
    try:
        await _use_serializable(clearing_session)
        service = ClearingService(clearing_session)
        original_refuse = service._refuse_if_equivalent_inactive

        async def _park_after_the_stop_read(equivalent_ids):
            await original_refuse(equivalent_ids)
            if not paused.is_set():
                clearing_pid.append(int(await service.session.scalar(text("SELECT pg_backend_pid()"))))
                paused.set()
                await release_clearing.wait()

        monkeypatch.setattr(service, "_refuse_if_equivalent_inactive", _park_after_the_stop_read)

        clearing = asyncio.create_task(service.execute_clearing_with_amount(seed["cycle"]))
        clearing.add_done_callback(lambda _t: completed.append("clearing"))
        await asyncio.wait_for(paused.wait(), timeout=20)
        assert await advisory_locks_held(observer) == 0, "the lock switch is not on: an advisory lock is held"

        if writer == "patch_deactivate":
            stopper = asyncio.create_task(_deactivate(client, seed["equivalent_code"]))
        else:
            stopper = asyncio.create_task(hold_directly(factory, seed["equivalent_id"]))
        stopper.add_done_callback(lambda _t: completed.append(writer))

        waiting: list[tuple[int, str]] = []
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 3.0
        while not stopper.done() and loop.time() < deadline:
            waiting = await blocked_by(observer, clearing_pid[0])
            if waiting:
                break
            await asyncio.sleep(0.02)
        writer_finished_while_parked = stopper.done()

        release_clearing.set()
        amount = await asyncio.wait_for(clearing, timeout=20)
        stopped = await asyncio.wait_for(stopper, timeout=20)
    finally:
        release_clearing.set()
        for task in (clearing, stopper):
            if task is not None and not task.done():
                await asyncio.wait([task], timeout=15)
            if task is not None and not task.done():
                task.cancel()
                await asyncio.wait([task], timeout=5)
        await clearing_session.rollback()
        await clearing_session.close()
        await observer.close()

    # Controls: the switch was on the clearing's path, the clearing was parked after its stop read and
    # then ran to its commit, and the writer itself succeeded.
    assert switch.calls["session_owner"] >= 1, switch.calls
    assert len(clearing_pid) == 1
    assert amount == Decimal("30.00000000"), "premise: the clearing did not run to its commit"
    if writer == "patch_deactivate":
        assert stopped.status_code == 200, stopped.text
    is_active, hold = await _equivalent_state(factory, seed["equivalent_id"])
    assert (is_active is False) if writer == "patch_deactivate" else (hold is not None)

    # THE TARGET is the writer's UPDATE queued on the CLEARING'S transaction while the clearing is
    # parked: a `transactionid`/`tuple` wait whose blocker is the clearing's backend ends only when that
    # transaction ends, so the writer cannot commit before the clearing's money does. (The order in
    # which the two TASKS finish is not that order: the clearing's task still releases its pinned
    # connection after its commit, while the unblocked writer may already be done.)
    require_target(
        not writer_finished_while_parked and bool(waiting),
        f"the {writer} completed while the clearing that had read the flag was still to commit: "
        f"task order {completed}, waiting={waiting}",
    )
    assert {locktype for _pid, locktype in waiting} <= {"transactionid", "tuple"}, (
        f"the {writer} waited, but not on a row/transaction lock of the clearing: {waiting}"
    )
