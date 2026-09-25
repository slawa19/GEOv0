"""The test-only switch of programme 019 stage 5 (`T1907`, `T1908`): the money-boundary locks as no-ops.

NOT a test module, and NEVER app code. Stage 5 may remove the advisory coordination only on evidence
that the system keeps its invariants without it (`specs/019-payment-one-transaction/spec.md`, "Изоляция,
писатели и клиринг", items 1-7). That evidence has to be MEASURED with the locks absent while the rest of
the code is the code that will ship, so the switch replaces exactly the lock primitives of
`app/core/money_boundary.py` - and nothing else - for the duration of one test, through `monkeypatch`:

* `_acquire_equivalent_owner_locks` - the payment's owner lock (and the one every staged entry reaches);
* `acquire_staged_equivalent_owner_locks` - the tick, the inject, admin and reconciliation;
* `acquire_session_equivalent_owner_lock` / `release_session_equivalent_owner_lock` - the clearing
  interlock on its pinned connection (the release answers `True`, "released", so the clearing's cleanup
  does not invalidate a connection that never held anything);
* `_acquire_tx_advisory_lock` and `_acquire_segment_advisory_lock_keys` - the payment's transaction and
  pair locks.

What stays: the stop/hold guard, the row locks (`FOR SHARE`/`FOR UPDATE`), SERIALIZABLE, every retry
owner. The clearing still pins its connection; only the session lock on it is gone.

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

    async def owner(self, equivalent_ids):
        switch.calls["owner"] += 1

    async def staged(self, equivalent_ids):
        switch.calls["staged_owner"] += 1

    async def session_owner(self, equivalent_id):
        switch.calls["session_owner"] += 1

    async def release_session_owner(self, equivalent_id):
        switch.calls["session_owner_release"] += 1
        return True

    async def tx(self, tx_id):
        switch.calls["tx"] += 1

    async def pairs(self, keys):
        switch.calls["pair"] += 1

    monkeypatch.setattr(MoneyBoundary, "_acquire_equivalent_owner_locks", owner)
    monkeypatch.setattr(MoneyBoundary, "acquire_staged_equivalent_owner_locks", staged)
    monkeypatch.setattr(MoneyBoundary, "acquire_session_equivalent_owner_lock", session_owner)
    monkeypatch.setattr(MoneyBoundary, "release_session_equivalent_owner_lock", release_session_owner)
    monkeypatch.setattr(MoneyBoundary, "_acquire_tx_advisory_lock", tx)
    monkeypatch.setattr(MoneyBoundary, "_acquire_segment_advisory_lock_keys", pairs)
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
