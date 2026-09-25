"""017 `T1707`: the default tier executes the equivalent owner lock, and the database shows it.

WHY THIS EXISTS. Until programme 017 the default tier ran on SQLite, where
`PaymentEngine._acquire_equivalent_owner_locks` returned early behind `if not self._is_postgres()`
without executing anything: the lock that binds payments, clearing and the equivalent PATCH together
was never taken on the tier every pull request ran. That early return left with SQLite (stage 3,
slice S5), and the tier moved to PostgreSQL (stage 2c). This test is the measurement that the tier
now SEES the lock (since 019 stage 5, `T1909`, the equivalent lock a payment takes is SHARED, and
the granted mode is read too): it is a test of the measurer (AGENTS.md §15), not of the concurrency the lock
provides - that is what the concurrent schedules in `tests/integration/*_postgres.py` measure, and
they stay beside it.

WHAT IT READS. `pg_locks` for this session's own backend, i.e. the database's own record that an
advisory lock is HELD - not the SQL text the engine sent, which would prove a statement was built
and not that PostgreSQL granted anything. A two-argument `pg_advisory_xact_lock(int4, int4)` shows
up as `locktype = 'advisory'`, `classid` = first argument, `objid` = second, `objsubid = 2`, both
reported as `oid`, i.e. the unsigned 32-bit reading of the signed key.

WHAT IT DOES NOT SHOW: that the lock is taken on every money path (that is per path, in the
integration modules), or that two sessions actually serialise on it (the concurrency schedules).
A non-empty set is required; an empty set takes no lock on any engine, which the second test pins
so that "no lock" can never pass here for the wrong reason.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from app.core.money_boundary import _EQUIVALENT_OWNER_LOCK_NAMESPACE, MoneyBoundary

_UNSIGNED = 0xFFFFFFFF


async def _held_owner_locks(session) -> set[tuple[int, int]]:
    rows = (
        await session.execute(
            text(
                "SELECT classid::bigint AS classid, objid::bigint AS objid, mode FROM pg_locks "
                "WHERE pid = pg_backend_pid() AND locktype = 'advisory' AND objsubid = 2 AND granted"
            )
        )
    ).all()
    return {(int(row.classid), int(row.objid), str(row.mode)) for row in rows}


@pytest.mark.asyncio
async def test_the_owner_lock_is_executed_and_held_on_the_default_tier(db_session) -> None:
    """A non-empty equivalent set takes one granted advisory lock per equivalent, in pg_locks.

    MUTATION that must redden this: restore an early `return` at the top of
    `_acquire_shared_equivalent_locks_in_order` (the shape of the pre-017 `if not self._is_postgres()`
    guard), and the set below stays empty; take the lock exclusively (`pg_advisory_xact_lock`), and the
    mode is `ExclusiveLock` instead of `ShareLock`.
    """

    assert db_session.get_bind().dialect.name == "postgresql", (
        "this measures the default tier's own database; the tier is PostgreSQL since 017 stage 2c"
    )
    equivalent_ids = [uuid.uuid4(), uuid.uuid4()]
    expected = {
        (
            _EQUIVALENT_OWNER_LOCK_NAMESPACE & _UNSIGNED,
            MoneyBoundary._equivalent_owner_lock_key(equivalent_id) & _UNSIGNED,
            "ShareLock",
        )
        for equivalent_id in equivalent_ids
    }
    before = await _held_owner_locks(db_session)
    assert not (expected & before), f"the owner locks were already held before the call: {before}"

    await MoneyBoundary(db_session)._acquire_shared_equivalent_locks_in_order(equivalent_ids)

    held = await _held_owner_locks(db_session)
    assert expected <= held, (
        f"the default tier did not take the equivalent lock SHARED: expected {sorted(expected)} "
        f"among the granted advisory locks of this backend, found {sorted(held)}"
    )


@pytest.mark.asyncio
async def test_an_empty_equivalent_set_takes_no_lock(db_session) -> None:
    """The counter-check: an empty set takes no lock on any engine, so it cannot stand in for one."""

    before = await _held_owner_locks(db_session)
    await MoneyBoundary(db_session)._acquire_shared_equivalent_locks_in_order([])
    assert await _held_owner_locks(db_session) == before
