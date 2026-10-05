"""030 S4 (`T3004`, `F-030-12`): a COMMIT that failed is not a rollback because the rollback after it succeeded.

The tick's money phase commits through `resolve_commit_under_cancellation`. Before S4 a COMMIT that raised, followed
by a `ROLLBACK` that succeeded, was resolved as `rolled_back` - the identity resolver of the phase
(`money_replay._attempt_landed`) was never asked - so a commit that LANDED and only lost its acknowledgement was
reported as failed payments (`tx.failed`) while the database kept them `COMMITTED`.

THE FAILURE IS THE ONE THE CLEARING STAND ALREADY USES (`test_clearing_commit_replay_postgres.py`, boundary
`ack_loss`): the real `COMMIT` runs against PostgreSQL and lands, and only its acknowledgement is lost - the error is
raised after it. What is decided is the database's real state, read on a new session; the only constructed thing is
the lost acknowledgement, which is the failure under test. The control is the same boundary losing the connection
BEFORE the `COMMIT` is sent: nothing lands, and the rollback is then a real one.
"""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal

import pytest

import app.core.simulator.money_replay as money_replay
from tests.integration.test_p015_p1_money_replay_postgres import (  # noqa: F401 - fixture
    _OPENING,
    _debts,
    _forget_the_route_cache,
    _install,
    _record_plans,
    _run_record,
    _runner,
    _scenario,
    _seed,
    _Sse,
    _transactions,
    factory,
)
from tests.tier_on_a_clone import tier_sessions_on_a_clone  # noqa: E402,F401 - autouse fixture


def _lose_the_money_commit(monkeypatch, *, landed: bool) -> list[str]:
    """The money phase's COMMIT fails once: after it landed (`ack_loss`), or before it was sent."""
    seen: list[str] = []
    real_resolve = money_replay.resolve_commit_under_cancellation

    async def resolve(*, commit, **kw):
        async def failing_commit():
            if seen:
                return await commit()
            if landed:
                await commit()
                seen.append("committed-then-lost")
                raise RuntimeError("commit acknowledgement lost")
            seen.append("lost-before-commit")
            raise ConnectionError("connection lost before the commit was sent")

        return await real_resolve(commit=failing_commit, **kw)

    monkeypatch.setattr(money_replay, "resolve_commit_under_cancellation", resolve)
    return seen


@pytest.mark.asyncio
@pytest.mark.parametrize("landed", [True, False], ids=["commit_landed", "commit_not_sent"])
async def test_a_failed_commit_is_resolved_by_identity_not_by_the_rollback(factory, monkeypatch, landed) -> None:  # noqa: F811
    world = await _seed(factory)
    try:
        sse = _Sse()
        run = _run_record(world, f"p030-s4-{uuid.uuid4().hex[:8]}")
        runner = _runner(run, _scenario(world), sse)
        _install(monkeypatch, factory)
        plans = _record_plans(monkeypatch, runner)
        seen = _lose_the_money_commit(monkeypatch, landed=landed)

        await asyncio.wait_for(runner.tick_real_mode(run.run_id), timeout=90.0)

        # The mechanism, before the outcome: the boundary failed once, on a planned payment, and the database says
        # what happened to it.
        assert seen == (["committed-then-lost"] if landed else ["lost-before-commit"]), seen
        assert len(plans) == 1 and len(plans[0]) == 1, plans
        transactions = await _transactions(factory, world)
        debts = await _debts(factory, world)
        amount = Decimal(plans[0][0].amount)
        if landed:
            assert list(transactions.values()) == ["COMMITTED"], transactions
            assert debts == {(world.sender.pid, world.receiver.pid): _OPENING + amount}, debts
        else:
            assert transactions == {}, transactions
            assert debts == {(world.sender.pid, world.receiver.pid): _OPENING}, debts
        assert run.errors_total == 1 and run.last_error is not None, (run.errors_total, run.last_error)
        assert run._real_money_replays_total == 0  # neither error is a conflict: nothing is replayed

        # The outcome: what is reported agrees with the database.
        if landed:
            assert (sse.published("tx.updated"), sse.published("tx.failed")) == (1, 0), sse.events
            assert run._real_money_committed_ticks_total == 1
            assert run.committed_total == 1
        else:
            assert sse.published("tx.updated") == 0, sse.events
            assert run._real_money_committed_ticks_total == 0
            assert run.committed_total == 0
    finally:
        _forget_the_route_cache(world)
