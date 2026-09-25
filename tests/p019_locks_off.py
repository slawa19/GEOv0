"""The test-only switch of programme 019 stage 5 (`T1907`, `T1908`, `T1909`): the equivalent lock as a no-op.

NOT a test module, and NEVER app code. Stage 5 decided the coordination on MEASUREMENT with the lock absent
while the rest of the code is the code that ships, so the switch replaces exactly the lock primitives of
`app/core/money_boundary.py` - and nothing else - for the duration of one test, through `monkeypatch`.
Since `T1909` there is ONE equivalent lock in two modes, and the switch covers both:

* `_acquire_shared_equivalent_locks_in_order` - the payment's shared lock (and the one the staged entry
  reaches);
* `acquire_shared_equivalent_locks` - the tick's money phase and the inject;
* `acquire_exclusive_equivalent_session_lock` / `release_exclusive_equivalent_session_lock` - the
  clearing's exclusive session lock on its pinned connection (the release answers `True`, "released", so
  the clearing's cleanup does not invalidate a connection that never held anything).

(Until `T1909` it also replaced the transaction and pair locks; they no longer exist.) What stays: the
stop/hold guard, the row locks (`FOR SHARE`/`FOR UPDATE`), SERIALIZABLE, every retry owner. The clearing
still pins its connection; only the lock on it is gone. Its use since `T1909` is the POSITIVE CONTROL of
the starvation probe: the stand must be able to see starvation with the lock off.

AGAINST A SILENT SWITCH. `LocksOff.calls` counts each replaced primitive, so a caller can assert that the
switch was actually on the path it measures (a primitive nobody called proves nothing about removing it),
and `advisory_locks_held` reads `pg_locks` for THIS database, so a stand can show that no advisory lock is
held while its schedule is parked.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from sqlalchemy import text


@dataclass
class LocksOff:
    calls: Counter = field(default_factory=Counter)

    @property
    def total(self) -> int:
        return sum(self.calls.values())


def switch_money_boundary_locks_off(monkeypatch) -> LocksOff:
    """Replace every advisory-lock primitive of `MoneyBoundary` with a counted no-op, for one test."""

    from app.core.money_boundary import MoneyBoundary

    switch = LocksOff()

    async def shared_in_order(self, equivalent_ids):
        switch.calls["shared"] += 1

    async def shared_staged(self, equivalent_ids):
        switch.calls["shared_staged"] += 1

    async def exclusive_session(self, equivalent_id):
        switch.calls["exclusive_session"] += 1

    async def release_exclusive_session(self, equivalent_id):
        switch.calls["exclusive_session_release"] += 1
        return True

    monkeypatch.setattr(MoneyBoundary, "_acquire_shared_equivalent_locks_in_order", shared_in_order)
    monkeypatch.setattr(MoneyBoundary, "acquire_shared_equivalent_locks", shared_staged)
    monkeypatch.setattr(MoneyBoundary, "acquire_exclusive_equivalent_session_lock", exclusive_session)
    monkeypatch.setattr(MoneyBoundary, "release_exclusive_equivalent_session_lock", release_exclusive_session)
    return switch


async def advisory_locks_held(session) -> int:
    """Advisory locks granted on THIS database right now (`pg_locks` is the whole server)."""

    count = await session.scalar(
        text(
            "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND granted AND database = "
            "(SELECT oid FROM pg_database WHERE datname = current_database())"
        )
    )
    await session.rollback()
    return int(count or 0)


async def blocked_by(session, blocker_pid: int) -> list[tuple[int, str]]:
    """(pid, locktype) of every backend of this database waiting on a lock that `blocker_pid` holds.

    Names the ACTUAL lock the waiter is queued on (`transactionid`, `tuple`, `advisory`, ...), read from
    `pg_locks`, with the dependency from `pg_blocking_pids` - the probe the spec asks for in place of
    "some advisory waiter exists" (item 6).
    """

    rows = (
        await session.execute(
            text(
                "SELECT a.pid, l.locktype FROM pg_stat_activity a "
                "JOIN pg_locks l ON l.pid = a.pid AND NOT l.granted "
                "WHERE a.datname = current_database() AND a.wait_event_type = 'Lock' "
                "AND :blocker = ANY(pg_blocking_pids(a.pid))"
            ),
            {"blocker": blocker_pid},
        )
    ).all()
    await session.rollback()
    return [(int(pid), str(locktype)) for pid, locktype in rows]
