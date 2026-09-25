"""Programme 019, `T1902`, hypothesis (b): a DEFINITIVE refusal of a staged (simulator) payment is
rolled back by the executor's savepoint, so it is not durable.

THE HYPOTHESIS (spec, "Окончательный отказ..."; read from `app/core/payments/service.py:1043`/`:1057`,
`:1122`/`:1135`, and `app/core/simulator/real_payments_executor.py:421`, `:486`): in staged mode the
service writes `ABORTED` into the caller's transaction and then RAISES; the executor runs every payment
inside `session.begin_nested()` and catches the exception OUTSIDE it, so the savepoint - with the
`ABORTED` row in it - is rolled back before the tick commits.

WHAT EXECUTION SHOWED (2026-09-25), and it is why there are two schedules here, not one.

1. Under the application's isolation, SERIALIZABLE, no definitive BUSINESS refusal reaches the staged
   path after its `NEW` flush. The money phase is one snapshot: routing and the prepare capacity
   re-check read the same rows (`router.py:212`, `engine.py:801`); an operator stop or integrity hold
   committed behind the snapshot meets the commit guard as `40001`, the money phase is replayed, and
   the service's pre-check refuses it BEFORE `NEW` (pinned by
   `test_p015_t1544_operator_stop_races_postgres.py::test_a_tick_that_waited_behind_the_patch_...`);
   the owner lock the tick holds keeps every other money writer out. The one definitive class that IS
   reachable after the staged `NEW` is the terminal TIMEOUT on a real lock wait - and it does NOT reach
   the executor's savepoint as a refusal: the timeout cancels the statement in flight, which
   invalidates the tick's connection; `engine.abort(commit=False)` then fails
   (`event=payment.timeout_abort_failed error_type=PendingRollbackError`), and the tick's money commit
   fails as a whole (`REAL_MODE_TICK_FAILED`). Nothing is durable - but because the whole tick is
   lost, not because of the savepoint. `test_today_a_staged_timeout_...` pins that.

2. The savepoint mechanism itself is reached by a business refusal only where the snapshot is not
   shared: under READ COMMITTED (`DB_POSTGRES_ISOLATION_LEVEL`, a supported setting today; stage 5 is
   to refuse it for money writers). The creditor lowers the trust line between the staged payment's
   routing and its prepare; the prepare re-check refuses (`E002`); the service writes `ABORTED` - read
   back INSIDE the tick's transaction - and raises; the executor's savepoint rolls the row back; the
   tick commits. `test_today_a_staged_capacity_refusal_...` pins that, and the TARGET is built on it.

THE SCHEDULES ARE REAL. (1) An operator's `PATCH /admin/equivalents/{code}` of the DESCRIPTION holds its
row lock through a slow commit (`admin.py:1325` takes the owner lock only for `is_active=false`); the
staged commit guard's `FOR SHARE` (`engine.py:1442`) waits on it until `COMMIT_TIMEOUT_SECONDS` (set
short) ends it. (2) A committed UPDATE of the trust line's limit by another session between routing and
prepare. Neither injects an exception.

TARGET (Q1, FORK-4; spec Verification plan §1): on schedule (2), `ABORTED` after the tick commit,
`tx.failed` published once, and the replay of the same `tx_id` answers the stored refusal. Stage 3
removes the marker; stage 5 moves the stand off READ COMMITTED when it refuses that level. Schedule (1)
has no target here: what a staged timeout that has invalidated the caller's transaction should leave
behind is a stage-3 design question the spec does not settle (reported with `T1902`).
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.core.payments.engine import PaymentEngine
from app.core.payments.service import PaymentService
from app.db.models.transaction import Transaction
from app.db.models.trustline import TrustLine
from tests.integration.p019_stand import (  # noqa: F401 - `api` and `factory` are fixtures
    ADMIN,
    api,
    factory,
    finish,
    tx_row,
    with_session_hook,
)
from tests.integration.test_p015_p1_money_replay_postgres import (
    _OPENING,
    _Sse,
    _debts,
    _forget_the_route_cache,
    _install,
    _run_record,
    _runner,
    _scenario,
    _seed,
)
from tests.p019_support import require_target, target_xfail


@pytest_asyncio.fixture
async def rc_factory(committed_database):
    """The same clone at READ COMMITTED - the one schedule that reaches the savepoint (docstring, 2)."""

    engine = create_async_engine(
        committed_database.url, pool_size=5, max_overflow=0, isolation_level="READ COMMITTED"
    )
    try:
        yield async_sessionmaker(
            bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
        )
    finally:
        await engine.dispose()


class _CommitGate:
    """Holds ONE request at its session commit: its writes flushed (row locks held), nothing committed."""

    def __init__(self) -> None:
        self.reached = asyncio.Event()
        self.release = asyncio.Event()

    def __call__(self, session) -> None:
        original = session.commit

        async def gated_commit():
            self.reached.set()
            await self.release.wait()
            await original()

        session.commit = gated_commit


@dataclass
class _StagedCalls:
    """Pass-through recorder of the staged entry: its arguments and how it ended."""

    calls: list[dict[str, Any]] = field(default_factory=list)

    def install(self, monkeypatch) -> None:
        original = PaymentService.create_payment_internal_staged
        record = self

        async def recording(self_, sender_id, **kwargs):
            entry = {"sender_id": sender_id, **kwargs}
            record.calls.append(entry)
            try:
                staged = await original(self_, sender_id, **kwargs)
            except BaseException as exc:
                entry["raised"] = type(exc).__name__
                raise
            entry["status"] = staged.result.status
            return staged

        monkeypatch.setattr(PaymentService, "create_payment_internal_staged", recording)


async def _row_lock_waiter_exists(factory, *, timeout: float = 10.0) -> bool:  # noqa: F811
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    async with factory() as observer:
        while True:
            # A row-lock wait shows as a non-granted `transactionid` (or `tuple`) lock; those carry
            # no database oid, and this stand's clone is the only database in use.
            waiting = await observer.scalar(
                text(
                    "SELECT EXISTS (SELECT 1 FROM pg_locks WHERE NOT granted "
                    "AND locktype IN ('transactionid', 'tuple'))"
                )
            )
            await observer.rollback()
            if waiting:
                return True
            if loop.time() > deadline:
                return False
            await asyncio.sleep(0.02)


async def _replay_staged(session_factory, world, call: dict[str, Any]) -> tuple[str, Any]:
    """The same `tx_id` through the staged entry again, under a caller savepoint, then committed.

    Returns `(status, error)` of the result, or `("raised:<Exception>", message)`.
    """

    try:
        async with session_factory() as session:
            async with session.begin_nested():
                replay = await PaymentService(session).create_payment_internal_staged(
                    world.sender.id,
                    to_pid=call["to_pid"],
                    equivalent=call["equivalent"],
                    amount=call["amount"],
                    allowed_participant_pids=call.get("allowed_participant_pids"),
                    idempotency_key=call["idempotency_key"],
                )
            await session.commit()
    except Exception as exc:  # noqa: BLE001 - classified by the caller's assertions
        return f"raised:{type(exc).__name__}", getattr(exc, "message", str(exc))
    finally:
        _forget_the_route_cache(world)
    assert replay.result.tx_id == call["idempotency_key"], replay.result
    return str(replay.result.status), replay.result.error


# ── schedule (1): SERIALIZABLE, terminal timeout on a real row-lock wait ──────────────────────


@pytest.mark.asyncio
async def test_today_a_staged_timeout_fails_the_whole_tick_and_leaves_no_row(
    api, factory, monkeypatch  # noqa: F811
) -> None:
    """CHARACTERIZATION (default isolation). Stage 3 decides what replaces it."""

    world = await _seed(factory)
    sse = _Sse()
    run = _run_record(world, f"p019-staged-t-{uuid.uuid4().hex[:8]}")
    runner = _runner(run, _scenario(world), sse)
    _install(monkeypatch, factory)
    staged = _StagedCalls()
    staged.install(monkeypatch)
    monkeypatch.setattr(settings, "COMMIT_TIMEOUT_SECONDS", 0.5)

    gate = _CommitGate()
    patch = tick = None
    try:
        with with_session_hook(gate):
            patch = asyncio.create_task(
                api.patch(
                    f"/api/v1/admin/equivalents/{world.equivalent.code}",
                    json={"description": "p019 slow operator edit", "reason": "p019 t1902"},
                    headers=ADMIN,
                )
            )
        await asyncio.wait_for(gate.reached.wait(), timeout=20)
        tick = asyncio.create_task(asyncio.wait_for(runner.tick_real_mode(run.run_id), 90.0))
        assert await _row_lock_waiter_exists(factory), (
            "premise: the staged payment never waited on the PATCH's row lock"
        )
        await tick
        gate.release.set()
        resp = await asyncio.wait_for(patch, timeout=20)
        assert resp.status_code == 200, resp.text
    finally:
        gate.release.set()
        await finish(tick)
        await finish(patch)
        _forget_the_route_cache(world)

    # The one staged payment timed out while waiting (controls).
    [call] = staged.calls
    assert call.get("raised") == "TimeoutException", call
    assert run.timeouts_total == 1, run.timeouts_total

    # TODAY: not a refusal of one payment - the tick's transaction is lost with it.
    assert run._real_money_committed_ticks_total == 0, "the tick's money phase committed"
    assert run._real_consec_tick_failures == 1
    assert run.last_error["code"] == "REAL_MODE_TICK_FAILED", run.last_error
    assert "invalid transaction" in run.last_error["message"], run.last_error
    failed = [e for e in sse.events if e.get("type") == "tx.failed"]
    assert [(e.get("error") or {}).get("code") for e in failed] == ["PAYMENT_TIMEOUT"], sse.events
    assert await tx_row(factory, str(call["idempotency_key"])) is None
    assert await _debts(factory, world) == {(world.sender.pid, world.receiver.pid): _OPENING}

    # ...and the same tx_id, submitted again, executes afresh: nothing records the refusal.
    status, error = await _replay_staged(factory, world, call)
    assert status == "COMMITTED", (status, error)
    assert await _debts(factory, world) == {
        (world.sender.pid, world.receiver.pid): _OPENING + Decimal(str(call["amount"]))
    }


# ── T1912: the unusable-transaction branch, on the production SERIALIZABLE ─────────────────────


@dataclass
class _TwoEquivalentTick:
    """One tick, two staged payments: seq 0 in the first equivalent, seq 1 in a second one whose row
    an operator's slow description edit holds. Seq 0 commits inside the tick's transaction; seq 1's
    commit guard (`FOR SHARE`) waits on the edit's row lock until `COMMIT_TIMEOUT_SECONDS` ends it."""

    world: Any
    second: Any
    run: Any
    sse: Any
    runner: Any
    calls: list[dict[str, Any]]
    gate: Any


async def _second_equivalent(factory, world):  # noqa: F811
    from app.db.models.equivalent import Equivalent

    async with factory() as s:
        eq = Equivalent(code=f"P19T{uuid.uuid4().hex[:8].upper()}"[:16], precision=2, is_active=True)
        s.add(eq)
        await s.flush()
        s.add(
            TrustLine(
                from_participant_id=world.receiver.id,
                to_participant_id=world.sender.id,
                equivalent_id=eq.id,
                limit=Decimal("1000.00"),
                status="active",
            )
        )
        await s.commit()
    return eq


async def _debt_rows(factory, equivalent_id) -> list[tuple[Any, Any, Decimal]]:  # noqa: F811
    from app.db.models.debt import Debt

    async with factory() as s:
        rows = (
            await s.execute(
                select(Debt.debtor_id, Debt.creditor_id, Debt.amount).where(
                    Debt.equivalent_id == equivalent_id
                )
            )
        ).all()
    return sorted((d, c, Decimal(str(a))) for d, c, a in rows)


async def _two_equivalent_tick(factory, monkeypatch) -> _TwoEquivalentTick:  # noqa: F811
    from app.core.simulator.real_payment_action import _RealPaymentAction

    world = await _seed(factory)
    second = await _second_equivalent(factory, world)
    sse = _Sse()
    run = _run_record(world, f"p019-t1912-{uuid.uuid4().hex[:8]}")
    run._real_equivalents = [world.equivalent.code, second.code]
    scenario = _scenario(world)
    scenario["equivalents"] = [world.equivalent.code, second.code]
    runner = _runner(run, scenario, sse, actions_per_tick_max=2)
    plan = [
        _RealPaymentAction(0, world.equivalent.code, world.sender.pid, world.receiver.pid, "1.00"),
        _RealPaymentAction(1, second.code, world.sender.pid, world.receiver.pid, "1.00"),
    ]
    # The plan is fixed, not generated: the subject is what the money-phase owner does with a timeout
    # AFTER another payment of the same phase already moved money inside the phase's transaction.
    monkeypatch.setattr(runner, "_plan_real_payments", lambda *_a, **_kw: list(plan))
    _install(monkeypatch, factory)
    staged = _StagedCalls()
    staged.install(monkeypatch)
    monkeypatch.setattr(settings, "COMMIT_TIMEOUT_SECONDS", 0.5)
    return _TwoEquivalentTick(world, second, run, sse, runner, staged.calls, _CommitGate())


async def _run_the_tick_behind_a_slow_edit(api, factory, t: _TwoEquivalentTick) -> bool:  # noqa: F811
    """Start the operator's slow edit of the SECOND equivalent, run the tick, then release the edit.
    Returns whether a row-lock waiter was observed while the tick ran (the premise)."""

    patch = tick = None
    try:
        with with_session_hook(t.gate):
            patch = asyncio.create_task(
                api.patch(
                    f"/api/v1/admin/equivalents/{t.second.code}",
                    json={"description": "p019 slow operator edit", "reason": "p019 t1912"},
                    headers=ADMIN,
                )
            )
        await asyncio.wait_for(t.gate.reached.wait(), timeout=20)
        tick = asyncio.create_task(asyncio.wait_for(t.runner.tick_real_mode(t.run.run_id), 90.0))
        queued = await _row_lock_waiter_exists(factory)
        await tick
        t.gate.release.set()
        resp = await asyncio.wait_for(patch, timeout=20)
        assert resp.status_code == 200, resp.text
        return queued
    finally:
        t.gate.release.set()
        await finish(tick)
        await finish(patch)
        _forget_routes(t)


def _forget_routes(t: _TwoEquivalentTick) -> None:
    from app.core.payments.router import PaymentRouter

    PaymentRouter.invalidate_cache(t.world.equivalent.code)
    PaymentRouter.invalidate_cache(t.second.code)


@target_xfail("stage 3 (T1912)", "a staged timeout that leaves the tick unusable fails the tick and records nothing")
@pytest.mark.asyncio
async def test_a_staged_timeout_that_leaves_the_tick_unusable_is_recorded_after_the_phase_rolls_back(
    api, factory, monkeypatch  # noqa: F811
) -> None:
    """TARGET (T1912, production SERIALIZABLE): the owner of the money phase rolls the WHOLE phase back,
    then records the admitted timeout as `ABORTED/E007` in a short transaction of its own; the money
    seq 0 moved inside the phase is discarded with it, and the failure is published once."""

    t = await _two_equivalent_tick(factory, monkeypatch)
    queued = await _run_the_tick_behind_a_slow_edit(api, factory, t)

    # ── controls ──────────────────────────────────────────────────────────────────────────────
    async with factory() as s:
        level = str((await s.execute(text("SHOW transaction_isolation"))).scalar_one())
    assert level == "serializable", level
    assert queued, "premise: the second payment never waited on the edit's row lock"
    first, second = t.calls
    assert first.get("status") == "COMMITTED" and "raised" not in first, first
    assert "raised" in second, second  # the second one timed out on the lock wait
    assert t.run._real_money_committed_ticks_total == 0, "the phase committed"
    assert t.run.last_error["code"] == "REAL_MODE_TICK_FAILED", t.run.last_error

    first_row = await tx_row(factory, str(first["idempotency_key"]))
    second_row = await tx_row(factory, str(second["idempotency_key"]))
    updated = [e for e in t.sse.events if e.get("type") == "tx.updated"]
    failed = [(e.get("error") or {}).get("code") for e in t.sse.events if e.get("type") == "tx.failed"]
    first_debts = await _debt_rows(factory, t.world.equivalent.id)
    second_debts = await _debt_rows(factory, t.second.id)
    replay = await _replay_staged(
        factory, t.world, {**second, "to_pid": t.world.receiver.pid}
    )
    _forget_routes(t)

    require_target(
        first_row is None
        and first_debts == [(t.world.sender.id, t.world.receiver.id, _OPENING)]
        and updated == []
        and second_row is not None
        and second_row[0] == "ABORTED"
        and (second_row[1] or {}).get("code") == "E007"
        and failed == ["PAYMENT_TIMEOUT"]
        and replay == ("ABORTED", replay[1])
        and (replay[1] is not None and replay[1].code == "E007")
        and second_debts == [],
        f"seq 0 row {first_row!r}, first-equivalent debts {first_debts!r}, tx.updated {len(updated)}; "
        f"seq 1 row {second_row!r}, tx.failed {failed!r}, replay {replay!r}, "
        f"second-equivalent debts {second_debts!r}",
    )


# ── schedule (2): READ COMMITTED, the prepare re-check refuses after the staged NEW ───────────


@dataclass
class _RecheckOutcome:
    world: Any
    call: dict[str, Any]
    inside_after_abort: list[str | None]
    row_after_tick: tuple[str, dict | None] | None
    failed_codes: list[str | None]
    replay: tuple[str, Any]
    debts_after_tick: dict
    debts_after_replay: dict


async def _recheck_refusal_in_a_tick(rc_factory, monkeypatch) -> _RecheckOutcome:
    world = await _seed(rc_factory)
    sse = _Sse()
    run = _run_record(world, f"p019-staged-r-{uuid.uuid4().hex[:8]}")
    runner = _runner(run, _scenario(world), sse)
    _install(monkeypatch, rc_factory)
    staged = _StagedCalls()
    staged.install(monkeypatch)

    lowered: list[int] = []
    inside_after_abort: list[str | None] = []
    original_prepare = PaymentEngine.prepare
    original_record = PaymentService._record_refusal_in_transaction

    async def prepare_after_the_creditor_lowers_the_line(self, tx_id, *args, **kwargs):
        if not lowered:
            lowered.append(1)
            # The creditor lowers the line to what is already owed: nothing is left to use.
            async with rc_factory() as other:
                await other.execute(
                    update(TrustLine)
                    .where(
                        TrustLine.from_participant_id == world.receiver.id,
                        TrustLine.to_participant_id == world.sender.id,
                        TrustLine.equivalent_id == world.equivalent.id,
                    )
                    .values(limit=_OPENING)
                )
                await other.commit()
        return await original_prepare(self, tx_id, *args, **kwargs)

    async def record_and_read_back(self, attempt, *args, **kwargs):
        result = await original_record(self, attempt, *args, **kwargs)
        # Read in the TICK's transaction: the row the service wrote before raising. Since stage 3
        # (`T1904`) the refusal is written by `_record_refusal_in_transaction` after the payment
        # operation's savepoint was rolled back - before it, by `engine.abort(commit=False)`.
        inside_after_abort.append(
            await self.session.scalar(
                select(Transaction.state).where(Transaction.tx_id == attempt.tx_id)
            )
        )
        return result

    monkeypatch.setattr(PaymentEngine, "prepare", prepare_after_the_creditor_lowers_the_line)
    monkeypatch.setattr(PaymentService, "_record_refusal_in_transaction", record_and_read_back)
    try:
        await asyncio.wait_for(runner.tick_real_mode(run.run_id), 90.0)
    finally:
        _forget_the_route_cache(world)
    monkeypatch.setattr(PaymentEngine, "prepare", original_prepare)
    monkeypatch.setattr(PaymentService, "_record_refusal_in_transaction", original_record)

    # ── controls ──────────────────────────────────────────────────────────────────────────────
    async with rc_factory() as s:
        level = str((await s.execute(text("SHOW transaction_isolation"))).scalar_one())
    assert level == "read committed", level
    assert lowered == [1], "premise: the line was never lowered between routing and prepare"
    [call] = staged.calls
    # The refusal reached the staged entry: raised today, a structured ABORTED result in the target
    # (spec: "окончательный отказ возвращается структурным результатом"). Either way it was refused.
    assert call.get("raised") == "RoutingException" or call.get("status") == "ABORTED", call
    assert run._real_money_committed_ticks_total == 1, "the tick's money phase did not commit"
    assert run.rejected_total == 1 and run.errors_total == 0, (run.rejected_total, run.errors_total)
    failed_codes = [(e.get("error") or {}).get("code") for e in sse.events if e.get("type") == "tx.failed"]
    assert len(failed_codes) == 1, sse.events
    debts_after_tick = await _debts(rc_factory, world)
    assert debts_after_tick == {(world.sender.pid, world.receiver.pid): _OPENING}, debts_after_tick

    row_after_tick = await tx_row(rc_factory, str(call["idempotency_key"]))
    replay = await _replay_staged(rc_factory, world, call)
    assert replay[0] != "raised:RetryablePaymentConflictException", replay
    return _RecheckOutcome(
        world,
        call,
        inside_after_abort,
        row_after_tick,
        failed_codes,
        replay,
        debts_after_tick,
        await _debts(rc_factory, world),
    )


@pytest.mark.asyncio
async def test_today_a_staged_capacity_refusal_is_written_aborted_then_rolled_back_by_the_savepoint(
    rc_factory, monkeypatch
) -> None:
    """CHARACTERIZATION - hypothesis (b)'s mechanism, on the schedule that reaches it. Stage 3 rewrites it."""

    out = await _recheck_refusal_in_a_tick(rc_factory, monkeypatch)
    assert out.call.get("raised") == "RoutingException", out.call
    # The service DID write ABORTED into the tick's transaction before raising...
    assert out.inside_after_abort == ["ABORTED"], out.inside_after_abort
    # ...and after the tick committed there is no row: the executor's savepoint took it back.
    assert out.row_after_tick is None, out.row_after_tick
    # The replay of the same tx_id is refused afresh - by routing, before any row - not answered.
    assert out.replay[0] == "raised:RoutingException", out.replay
    assert out.debts_after_replay == out.debts_after_tick


@target_xfail("stage 3 (T1905)", "a staged definitive refusal is rolled back by the executor savepoint")
@pytest.mark.asyncio
async def test_a_staged_definitive_refusal_is_durable_and_replays_the_same_refusal(
    rc_factory, monkeypatch
) -> None:
    """TARGET (Q1, FORK-4): ABORTED after the tick commit; the replay answers it and moves nothing."""

    out = await _recheck_refusal_in_a_tick(rc_factory, monkeypatch)
    stored_state = None if out.row_after_tick is None else out.row_after_tick[0]
    require_target(
        stored_state == "ABORTED"
        and out.replay[0] == "ABORTED"
        and out.debts_after_replay == out.debts_after_tick,
        f"after the tick commit the row was {out.row_after_tick!r}; the replay of the same tx_id "
        f"answered {out.replay!r}",
    )
