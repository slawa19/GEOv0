"""The one expected-failure shape of programme 019 (spec, Verification plan §1). NOT a test module.

WHY A DEDICATED EXCEPTION. A target test of 019 first asserts, with ordinary assertions, that its
mechanism was reached and that the operation completed: the barrier was hit, the observer's snapshot
is fresh, the conflict really was the SQLSTATE it claims, the payment ended. Only AFTER all of that
does it compare the observed outcome with the target, and a mismatch there - and only there - is
raised as `TargetMismatch`. The marker is `xfail(raises=TargetMismatch, strict=True)`:

* a broken stand raises `AssertionError` (or anything else), which `raises=` does NOT accept, so the
  test goes red instead of being filed as an expected failure. `raises=AssertionError` would accept a
  broken barrier as the expected mismatch, which is exactly the false green the spec forbids;
* a tree that already behaves as the target passes the comparison, and `strict=True` turns that
  XPASS into a failure - the stage that fixes the behaviour must take the marker off, it cannot be
  forgotten.

`TargetMismatch` deliberately does not inherit from `AssertionError`, so no plain `assert` anywhere can
ever be mistaken for it.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest


class TargetMismatch(Exception):
    """The observed outcome differs from the 019 target. Raised only after every control passed."""


def target_xfail(stage: str, what: str):
    """The 019 marker: an expected `TargetMismatch`, strict, naming the stage that removes it."""

    return pytest.mark.xfail(
        raises=TargetMismatch,
        strict=True,
        reason=f"019 target, fixed by {stage}: {what}",
    )


def require_target(condition: bool, message: str) -> None:
    """The final comparison of a target test. Call it LAST, after every control assertion."""

    if not condition:
        raise TargetMismatch(message)


def allow_below_serializable_for_a_diagnostic(monkeypatch) -> list[str]:
    """A NAMED DIAGNOSTIC CONTROL below the supported isolation (019 stage 5, `T1907`, item 7).

    Since `T1907` every money writer refuses a transaction that does not run SERIALIZABLE
    (`MoneyBoundary.require_serializable`). A few stands run a writer at READ COMMITTED ON PURPOSE - to
    reach a path that exists only there (a `StaleDataError` instead of 40001, a re-check refusal after the
    row insert) or to show that a verifier DOES detect the disagreement such a level allows (the positive
    control of criterion (b)). They switch the check off for their own test only, through this function,
    so every such stand is findable by one name and none of them is evidence that the application runs
    below SERIALIZABLE; the production refusal is `test_p019_money_writers_refuse_non_serializable_postgres.py`.
    Returns the writers whose check was skipped, so a stand can show it was on its path.
    """

    from app.core.money_boundary import MoneyBoundary

    skipped: list[str] = []

    async def skip(session, *, writer: str) -> None:
        skipped.append(writer)

    monkeypatch.setattr(MoneyBoundary, "require_read_committed", staticmethod(skip))  # 027 stage 2: the guard is RC
    return skipped


async def deadlock_after_the_wait(session, holding, second_lock) -> None:
    """027 stage 2: the competitor of a real `40P01`. It already holds a row; it waits until a backend queues on it,
    then asks for `second_lock` (rows the waiter holds): the waiter waited first, so ITS deadlock check aborts it.

    NOT A GUARANTEE OF THE VICTIM (031 `T3102`, review `T3096` finding 3). PostgreSQL 16 checks for a deadlock once
    per lock wait, `deadlock_timeout` after it began, in the waiting backend, and aborts the backend whose check
    finds the cycle. The waiter's single check can run BEFORE this competitor closes the cycle (a slow poll), pass,
    and leave the competitor's own later check to find it - the competitor is then the victim. A stand that needs
    the error in a named backend uses `queue_behind_the_victim` instead."""

    import asyncio

    from sqlalchemy import text

    me = await session.scalar(text("SELECT pg_backend_pid()"))
    holding.set()
    while not await session.scalar(text("SELECT count(*) FROM pg_stat_activity a WHERE CAST(:me AS int) = "
                                        "ANY(pg_blocking_pids(a.pid))"), {"me": me}):
        await asyncio.sleep(0.02)
    await session.execute(second_lock)


#: The competitor's deadlock check: far beyond any stand's deadline, so it never runs while the stand is alive.
_COMPETITOR_DEADLOCK_TIMEOUT = "10min"
#: The victim's deadlock check: short, and it runs only after the victim's own wait has closed the cycle.
_VICTIM_DEADLOCK_TIMEOUT = "200ms"


@dataclass
class QueuedCompetitor:
    """A competitor confirmed WAITING on the victim's row; the victim's next conflicting lock closes the cycle."""

    victim_pid: int
    competitor_pid: int
    waiting: asyncio.Task


async def queue_behind_the_victim(victim, competitor, *, hold, wait_on_victim, deadline_s: float = 15.0) -> QueuedCompetitor:
    """031 `T3102`: a real `40P01` whose victim is `victim` - by construction, not by timing.

    THE ORDER. The competitor takes `hold` (the row the victim will ask for next), then asks for `wait_on_victim`
    (rows the victim already holds) and is CONFIRMED waiting on the victim's backend (`pg_blocking_pids`) before
    this returns. The caller then lets the victim ask for the `hold` row: the victim's wait is the one that closes
    the cycle. Its deadlock check (`_VICTIM_DEADLOCK_TIMEOUT` after its wait began) therefore always finds the
    cycle closed, and the competitor's check (`_COMPETITOR_DEADLOCK_TIMEOUT`, set for its transaction only) never
    runs before it. Both are `SET LOCAL`: the GUC is superuser-only (`PGC_SUSET`), as the test role is here and in
    CI (`postgres:16` with `POSTGRES_USER=geo`); a role that may not set it fails here loudly with `42501`.

    No sleep stands in for the order: the poll below only waits for the server to REPORT the wait, and returns as
    soon as it does. The competitor's statement is `waiting`; the caller awaits it after the victim's failure has
    ended the victim's transaction, and commits or rolls the competitor back itself.
    """

    import asyncio

    from sqlalchemy import text

    victim_pid = int(await victim.scalar(text("SELECT pg_backend_pid()")))
    competitor_pid = int(await competitor.scalar(text("SELECT pg_backend_pid()")))
    await competitor.execute(text(f"SET LOCAL deadlock_timeout = '{_COMPETITOR_DEADLOCK_TIMEOUT}'"))
    await victim.execute(text(f"SET LOCAL deadlock_timeout = '{_VICTIM_DEADLOCK_TIMEOUT}'"))
    await competitor.execute(hold)
    waiting = asyncio.create_task(competitor.execute(wait_on_victim))
    loop = asyncio.get_running_loop()
    until = loop.time() + deadline_s
    while loop.time() < until and not waiting.done():
        if await victim.scalar(text("SELECT CAST(:v AS int) = ANY(pg_blocking_pids(:c))"),
                               {"v": victim_pid, "c": competitor_pid}):
            return QueuedCompetitor(victim_pid, competitor_pid, waiting)
        await asyncio.sleep(0.005)
    if waiting.done():
        waiting.result()  # its own error, if it failed
        raise AssertionError("the competitor got the victim's rows without waiting: no cycle can be formed")
    waiting.cancel()
    raise AssertionError(f"the competitor (pid {competitor_pid}) never queued on the victim (pid {victim_pid})")


def deadlock_detail(exc: BaseException) -> str:
    """The server's DETAIL of a deadlock error ("Process A waits for ...; blocked by process B."), from anywhere in
    the chain of `orig` / `__cause__` / `__context__`; empty when no such detail is carried."""

    seen: set[int] = set()
    todo: list[object] = [exc]
    while todo:
        current = todo.pop()
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        detail = getattr(current, "detail", None)
        if isinstance(detail, str) and "blocked by process" in detail:
            return detail
        todo.extend(getattr(current, name, None) for name in ("orig", "__cause__", "__context__"))
    return ""


def assert_victim_of(exc: BaseException, queued: QueuedCompetitor) -> None:
    """The deadlock was detected IN the victim's backend, against the competitor: PostgreSQL names the detecting
    process first ("Process <victim> waits ... blocked by process <competitor>")."""

    detail = deadlock_detail(exc)
    assert detail.startswith(f"Process {queued.victim_pid} waits"), (queued, detail)
    assert f"blocked by process {queued.competitor_pid}." in detail, (queued, detail)


async def wait_until_blocked(observer, *, holder_pid: int, waiter_pid: int) -> bool:
    import asyncio

    from sqlalchemy import text

    for _ in range(250):
        blocked = await observer.scalar(text("SELECT CAST(:h AS int) = ANY(pg_blocking_pids(:w))"), {"h": holder_pid, "w": waiter_pid})
        await observer.rollback()
        if blocked:
            return True
        await asyncio.sleep(0.02)
    return False
