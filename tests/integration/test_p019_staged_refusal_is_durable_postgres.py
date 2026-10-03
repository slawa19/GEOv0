"""Programme 019: a DEFINITIVE refusal of a staged (simulator) payment is durable (`T1905`), and a staged
failure that leaves the tick's transaction unusable is recorded by the owner of the money phase (`T1912`).

WHAT `T1902` FOUND (2026-09-25), before stage 3's refusal contract. In staged mode the service wrote
`ABORTED` into the caller's transaction and then RAISED; the executor runs every payment inside
`session.begin_nested()` (`real_payments_executor.py:421`) and catches the exception OUTSIDE it
(`:486`), so the savepoint - with the `ABORTED` row in it - was rolled back before the tick committed.
Two schedules reach a refusal after the staged insert:

1. SERIALIZABLE (the application's isolation): the terminal TIMEOUT on a real lock wait. The timeout
   cancels the statement in flight, which invalidates the tick's connection; the refusal could not be
   written, and the tick's money commit failed as a whole (`REAL_MODE_TICK_FAILED`, "invalid
   transaction"): nothing durable, the whole tick lost, and the same `tx_id` executed afresh later.
2. READ COMMITTED (a level the application no longer starts at: since 2026-09-25 `app/config.py`
   refuses any `DB_POSTGRES_ISOLATION_LEVEL` but SERIALIZABLE; the test asks for it on its own
   connection as a mechanism probe of the staged refusal path, not as an application configuration):
   the creditor lowers the trust line between the staged payment's routing and its prepare; the
   prepare re-check refuses (`E002`); the row was written, then rolled back by the savepoint.

THE CONTRACT SINCE STAGE 3 (spec, "Путь записи окончательного отказа" and "Ветка непригодной
транзакции"), each pinned by a target that was red before it (`056b27b` for `T1912`):

* schedule (2) - the refusal is RETURNED as a structured `ABORTED` result through the caller's savepoint
  (`StagedPaymentResult.refusal`): durable with the tick, `tx.failed` once, the replay of the same
  `tx_id` answers it (`test_a_staged_definitive_refusal_is_durable_and_replays_the_same_refusal`);
* schedule (1), with a second payment of the same phase that already moved money - `execute()` raises
  `PaymentTransactionUnusable`; the money-phase owner rolls the whole phase back, establishes the
  rollback, records `ABORTED/E007` in a short transaction of its own, discards the phase's other money
  and observations, and publishes the failure once
  (`test_a_staged_timeout_that_leaves_the_tick_unusable_is_recorded_after_the_phase_rolls_back`); no
  established rollback - nothing recorded (`test_the_owner_records_nothing_when_...`); a COMMITTED
  winner of the same `tx_id` - yielded to, never overwritten (`test_the_owner_yields_to_...`).

THE SCHEDULES ARE REAL. An operator's `PATCH /admin/equivalents/{code}` of the DESCRIPTION holds its row
lock through a slow commit (`admin.py` takes the owner lock only for `is_active=false`); the staged
commit guard's `FOR SHARE` waits on it until the payment's commit budget (set short) ends it. A committed
UPDATE of the trust line's limit by another session between routing and prepare. Neither injects an
exception; the `T1912` plan is fixed (two payments, two equivalents) so the timeout meets a phase that
already staged money.
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
from app.core.payments.service import PaymentService, PaymentTransactionUnusable
from app.db.models.transaction import Transaction
from app.db.models.trustline import TrustLine
from tests.p019_support import allow_below_serializable_for_a_diagnostic
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
from tests.p019_support import require_target


@pytest_asyncio.fixture
async def rc_factory(committed_database, monkeypatch):
    """The same clone at READ COMMITTED - the one schedule that reaches the savepoint (docstring, 2)."""

    # 019 stage 5 (`T1907`): READ COMMITTED here is a named diagnostic below the supported level.
    allow_below_serializable_for_a_diagnostic(monkeypatch)

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
        self.committed = asyncio.Event()  # the held commit went through: its row locks are gone

    def __call__(self, session) -> None:
        original = session.commit

        async def gated_commit():
            self.reached.set()
            await self.release.wait()
            await original()
            self.committed.set()

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


async def _row_lock_waiter_exists(factory, pid: int, *, timeout: float = 10.0) -> bool:  # noqa: F811
    """Whether the backend `pid` waits on a row lock. Called only while that wait cannot end on its
    own (the stand holds the lock and the payment's budget is long), so the poll cannot miss it.

    BY PID (2026-09-28). The earlier form asked whether ANY session of the server waited: row-lock
    waits (`transactionid`, `tuple`) carry no database oid, so with two test tiers on one server
    (`-TaskSlug` runs in parallel) another tier's waiter satisfied it, the stop came before seq 1
    had even been called, and `test_stopping_the_run_...` failed (`not enough values to unpack`).
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    async with factory() as observer:
        while True:
            waiting = await observer.scalar(
                text(
                    "SELECT EXISTS (SELECT 1 FROM pg_locks WHERE pid = :pid AND NOT granted "
                    "AND locktype IN ('transactionid', 'tuple'))"
                ),
                {"pid": pid},
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



# ── T1912: the unusable-transaction branch, on the production SERIALIZABLE ─────────────────────


@dataclass
class _TwoEquivalentTick:
    """One tick, two staged payments: seq 0 in the first equivalent, seq 1 in a second one whose row
    an operator's slow description edit holds. Seq 0 commits inside the tick's transaction; seq 1's
    commit guard (`FOR SHARE`) waits on the edit's row lock until its commit budget ends it (`_Subject`)."""

    world: Any
    second: Any
    run: Any
    sse: Any
    runner: Any
    calls: list[dict[str, Any]]
    gate: Any
    subject: "_Subject"


#: The commit budget of the SUBJECT only - seq 1's money phase, the one that waits on the edit's row
#: lock. The wait ends by this budget and nothing else, so it is also how long the lock wait lasts.
#: Every other payment of these tests (seq 0, the winner, the replays) runs on the application's own
#: `COMMIT_TIMEOUT_SECONDS`.
#:
#: WHY IT IS SCOPED (2026-09-28, CI runs `36383388623` attempt 1 and `36390360987`). It used to be set
#: process-wide to 0.5 s, and under the loaded CI runner it ended payments that were not the subject:
#: in the first run seq 0 - uncontended, in the first equivalent - timed out at
#: `check_debt_symmetry` (`service.py:1973`), the tick failed on seq 0 and seq 1 never reached the
#: lock (the premise was red); in the second the winner of `test_the_owner_yields_...` timed out on
#: its own commit guard while the released edit was still committing, raised
#: `PaymentTransactionUnusable` out of the recording hook, and nothing was recorded (`winners == []`).
_SUBJECT_COMMIT_TIMEOUT_S = 2.0


@dataclass
class _Subject:
    """Seq 1's money phase, instrumented: its commit budget, and how its commit guard ended.

    THE PREMISE IS READ, NOT POLLED. The guard is the `FOR SHARE` of the second equivalent's row
    (`MoneyBoundary.refuse_inactive_equivalents`, `service.py:1919`). The recorder
    notes whether the operator's edit held that row uncommitted - its commit reached and not released
    - when the guard statement was issued AND when it ended, and how it ended. A `FOR SHARE` on a
    row another open transaction has updated cannot complete while that transaction stays open, so
    "issued and ended by `CancelledError` with the edit held throughout" means the statement was
    ended by the commit timeout while it waited on the edit's row lock. A `pg_locks` poll during the
    tick (`_row_lock_waiter_exists`) could only see that wait while it lasted; this record cannot
    miss it, and it names THIS payment's statement rather than any waiter in the cluster.
    """

    second_equivalent_id: Any
    gate: Any
    commit_timeout_s: float | None = _SUBJECT_COMMIT_TIMEOUT_S
    boundary: Any = None
    guard: dict[str, Any] = field(default_factory=dict)
    issued: asyncio.Event = field(default_factory=asyncio.Event)  # the guard statement is about to run
    pid: int | None = None  # the backend that runs it

    def install(self, monkeypatch) -> None:
        from app.core.money_boundary import MoneyBoundary

        subject = self
        original_operation = PaymentService._run_payment_operation
        original_guard = MoneyBoundary.refuse_inactive_equivalents

        async def operation(self_, attempt, **kwargs):
            # The FIRST money phase in the second equivalent is the tick's seq 1; later ones (the
            # winner, a replay) are not the subject and keep the budget they were given.
            if subject.boundary is None and kwargs.get("equivalent_id") == subject.second_equivalent_id:
                subject.boundary = self_._boundary
                if subject.commit_timeout_s is not None:
                    kwargs["commit_timeout_s"] = subject.commit_timeout_s
            return await original_operation(self_, attempt, **kwargs)

        async def guard(self_, equivalent_ids):
            if self_ is not subject.boundary or subject.guard:
                return await original_guard(self_, equivalent_ids)
            held = lambda: subject.gate.reached.is_set() and not subject.gate.release.is_set()  # noqa: E731
            raw = await (await self_.session.connection()).get_raw_connection()
            subject.pid = raw.driver_connection.get_server_pid()
            subject.guard["held_at_issue"] = held()
            subject.issued.set()
            try:
                result = await original_guard(self_, equivalent_ids)
            except BaseException as exc:
                subject.guard["ended"] = type(exc).__name__
                raise
            else:
                subject.guard["ended"] = "returned"
                return result
            finally:
                subject.guard["held_at_end"] = held()

        monkeypatch.setattr(PaymentService, "_run_payment_operation", operation)
        monkeypatch.setattr(MoneyBoundary, "refuse_inactive_equivalents", guard)

    def waited_on_the_edit_until_the_timeout(self) -> bool:
        return self.guard == {"held_at_issue": True, "ended": "CancelledError", "held_at_end": True}


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
    gate = _CommitGate()
    subject = _Subject(second.id, gate)
    subject.install(monkeypatch)
    return _TwoEquivalentTick(world, second, run, sse, runner, staged.calls, gate, subject)


async def _run_the_tick_behind_a_slow_edit(api, factory, t: _TwoEquivalentTick) -> bool:  # noqa: F811
    """Start the operator's slow edit of the SECOND equivalent, run the tick, then release the edit.
    Returns whether seq 1's commit guard waited on the edit's row lock until its commit timeout ended
    it (the premise, `_Subject`)."""

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
        await tick
        queued = t.subject.waited_on_the_edit_until_the_timeout()
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
    assert level == "read committed", level
    assert queued, f"premise: the second payment never waited on the edit's row lock {t.subject.guard}"
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
    # Since T1912 the branch that did it is named: the staged call raised the unusable-transaction
    # condition, not an ordinary timeout the executor would have counted and gone on from.
    assert second.get("raised") == "PaymentTransactionUnusable", second


@pytest.mark.asyncio
async def test_the_owner_records_nothing_when_the_phase_rollback_is_not_established(
    api, factory, monkeypatch  # noqa: F811
) -> None:
    """T1912, the other half of "only after a confirmed rollback": the phase session's rollback AND its
    connection invalidation fail, so the rollback is not established - nothing is recorded, nothing is
    published, and the tick fails. (Mutation: terminalize without the established rollback -> red.)"""

    t = await _two_equivalent_tick(factory, monkeypatch)
    original = PaymentService.create_payment_internal_staged
    broken: list[str] = []

    async def the_phase_cannot_be_rolled_back(self_, sender_id, **kwargs):
        try:
            return await original(self_, sender_id, **kwargs)
        except PaymentTransactionUnusable:
            session = self_.session

            def failing(name):
                async def fail(*_a, **_kw):
                    broken.append(name)
                    raise RuntimeError(f"p019 t1912: {name} cannot be established")

                return fail

            session.rollback = failing("rollback")
            session.invalidate = failing("invalidate")
            raise

    monkeypatch.setattr(PaymentService, "create_payment_internal_staged", the_phase_cannot_be_rolled_back)
    queued = await _run_the_tick_behind_a_slow_edit(api, factory, t)

    assert queued, f"premise: the second payment never waited on the edit's row lock {t.subject.guard}"
    # The owner tried to end the phase both ways, and both were refused.
    assert "rollback" in broken and "invalidate" in broken, broken
    assert t.run.last_error["code"] == "REAL_MODE_TICK_FAILED", t.run.last_error
    tx_ids = [str(c["idempotency_key"]) for c in t.calls]
    assert [await tx_row(factory, tx) for tx in tx_ids] == [None, None]
    assert [e for e in t.sse.events if e.get("type") in ("tx.failed", "tx.updated")] == []
    assert await _debt_rows(factory, t.second.id) == []


@pytest.mark.asyncio
async def test_the_owner_yields_to_a_committed_winner_of_the_same_tx_id(
    api, factory, monkeypatch  # noqa: F811
) -> None:
    """T1912: between the phase rollback and the recording, the same request is executed and committed
    by another transaction. The recording yields to it (identity checked), never overwrites COMMITTED,
    and publishes no failure for a payment that did not fail."""

    import app.core.simulator.money_replay as money_replay

    t = await _two_equivalent_tick(factory, monkeypatch)
    original_record = money_replay.record_definitive_refusal
    winners: list[str] = []

    async def a_winner_commits_first(sessions, refusal):
        # The slow edit finishes, and the winner starts only once it has COMMITTED (a barrier, not a
        # race): the winner's transaction then begins after the edit, meets no row lock on its commit
        # guard and no concurrent update of that row, and runs on the application's commit budget
        # (`_Subject` shortens only seq 1's). Before 2026-09-28 the winner started right after the
        # release, raced the edit's commit under a process-wide 0.5 s budget, and on a loaded runner
        # timed out itself (CI run `36390360987`: `winners == []`).
        t.gate.release.set()
        await asyncio.wait_for(t.gate.committed.wait(), timeout=20)
        call = t.calls[1]
        async with factory() as session:
            async with session.begin_nested():
                won = await original_create(
                    PaymentService(session),
                    call["sender_id"],
                    to_pid=call["to_pid"],
                    equivalent=call["equivalent"],
                    amount=call["amount"],
                    allowed_participant_pids=call.get("allowed_participant_pids"),
                    idempotency_key=call["idempotency_key"],
                )
            await session.commit()
        winners.append(won.result.status)
        return await original_record(sessions, refusal)

    original_create = PaymentService.create_payment_internal_staged
    monkeypatch.setattr(money_replay, "record_definitive_refusal", a_winner_commits_first)
    queued = await _run_the_tick_behind_a_slow_edit(api, factory, t)

    assert queued, f"premise: the second payment never waited on the edit's row lock {t.subject.guard}"
    assert winners == ["COMMITTED"], winners
    assert t.calls[1].get("raised") == "PaymentTransactionUnusable", t.calls[1]
    assert await tx_row(factory, str(t.calls[1]["idempotency_key"])) == ("COMMITTED", None)
    assert await _debt_rows(factory, t.second.id) == [
        (t.world.sender.id, t.world.receiver.id, Decimal("1.00"))
    ]
    assert [e for e in t.sse.events if e.get("type") == "tx.failed"] == []


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
    original_prepare = PaymentService._bind_payment  # the binding phase (019 stage 4; the engine's prepare before)
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

    monkeypatch.setattr(PaymentService, "_bind_payment", prepare_after_the_creditor_lowers_the_line)
    monkeypatch.setattr(PaymentService, "_record_refusal_in_transaction", record_and_read_back)
    try:
        await asyncio.wait_for(runner.tick_real_mode(run.run_id), 90.0)
    finally:
        _forget_the_route_cache(world)
    monkeypatch.setattr(PaymentService, "_bind_payment", original_prepare)
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


@pytest.mark.asyncio
async def test_a_replayed_stored_timeout_is_observed_as_a_timeout(api, factory, monkeypatch) -> None:  # noqa: F811
    """Stage-3 review (Codex, `fae2208`, P2 #6), through the executor and SSE: the T1912 tick leaves
    seq 1 stored `ABORTED/E007`; the run restarts at the same tick index (`run_lifecycle.py` keeps its
    identity and rows), the same plan runs again, and seq 1 is answered from its stored row. That
    answer must be observed as what it is - a payment TIMEOUT (`tx.failed` `PAYMENT_TIMEOUT`, a timeout
    and an error of the run) - not as a generic `PAYMENT_REJECTED` rejection. Before the fix the stored
    result came back with no refusal and the executor discarded its error."""

    t = await _two_equivalent_tick(factory, monkeypatch)
    await _run_the_tick_behind_a_slow_edit(api, factory, t)
    second = t.calls[1]
    assert second.get("raised") == "PaymentTransactionUnusable", second
    assert (await tx_row(factory, str(second["idempotency_key"])) or ("",))[0] == "ABORTED"

    # ── the restart: same tick index, same plan, nothing slow this time ──────────────────────────
    t.sse.events.clear()
    before = (t.run.timeouts_total, t.run.errors_total, t.run.rejected_total)
    try:
        await asyncio.wait_for(t.runner.tick_real_mode(t.run.run_id), 90.0)
    finally:
        _forget_routes(t)
    replay_first, replay_second = t.calls[2:4]
    assert replay_first.get("status") == "COMMITTED", replay_first
    assert replay_second.get("status") == "ABORTED", replay_second  # answered from the stored row
    assert replay_second["idempotency_key"] == second["idempotency_key"]
    after = (t.run.timeouts_total, t.run.errors_total, t.run.rejected_total)
    failed = [(e.get("error") or {}).get("code") for e in t.sse.events if e.get("type") == "tx.failed"]
    require_target(
        failed == ["PAYMENT_TIMEOUT"] and (after[0] - before[0], after[2] - before[2]) == (1, 0),
        f"the replayed E007 was observed as tx.failed {failed!r}; (timeouts, errors, rejected) went "
        f"{before} -> {after}",
    )


@pytest.mark.asyncio
async def test_stopping_the_run_while_an_admitted_payment_waits_records_its_cancellation(
    api, factory, monkeypatch  # noqa: F811
) -> None:
    """Stage-3 review (Codex, `fae2208`, P2 #3): the run is stopped - its tick task cancelled, as
    `run_lifecycle` cancels the heartbeat - while an ADMITTED staged payment waits on a real row lock
    (the commit guard's `FOR SHARE` behind the operator's slow edit; no timeout this time). The table's
    row "timeout or cancellation after admission, confirmed rollback -> ABORTED/E007" applies: after the
    phase is rolled back the cancellation is recorded, and the same `tx_id` answers it. Before the fix
    the refusal was written into the tick's transaction - rolled back with it - so nothing remained,
    and the identity could execute later."""

    t = await _two_equivalent_tick(factory, monkeypatch)
    t.subject.commit_timeout_s = None  # seq 1 too waits on the budget below; the stop ends the wait
    monkeypatch.setattr(settings, "COMMIT_TIMEOUT_SECONDS", 60)
    monkeypatch.setattr(settings, "PAYMENT_TOTAL_TIMEOUT_SECONDS", 60)
    patch = tick = None
    try:
        with with_session_hook(t.gate):
            patch = asyncio.create_task(
                api.patch(
                    f"/api/v1/admin/equivalents/{t.second.code}",
                    json={"description": "p019 slow operator edit", "reason": "p019 stop"},
                    headers=ADMIN,
                )
            )
        await asyncio.wait_for(t.gate.reached.wait(), timeout=20)
        tick = asyncio.create_task(t.runner.tick_real_mode(t.run.run_id))
        await asyncio.wait_for(t.subject.issued.wait(), timeout=20)
        queued = await _row_lock_waiter_exists(factory, t.subject.pid)
        tick.cancel()  # the run stops
        await asyncio.wait([tick], timeout=30)
        t.gate.release.set()
        resp = await asyncio.wait_for(patch, timeout=20)
        assert resp.status_code == 200, resp.text
    finally:
        t.gate.release.set()
        await finish(tick)
        await finish(patch)
        _forget_routes(t)

    assert queued, f"premise: the second payment never waited on the edit's row lock {t.subject.guard}"
    assert tick.cancelled() or isinstance(tick.exception(), asyncio.CancelledError), tick
    first, second = t.calls
    assert first.get("status") == "COMMITTED", first
    assert second.get("raised") == "CancelledError", second
    first_row = await tx_row(factory, str(first["idempotency_key"]))
    second_row = await tx_row(factory, str(second["idempotency_key"]))
    replay = await _replay_staged(factory, t.world, second)
    _forget_routes(t)
    require_target(
        first_row is None
        and second_row is not None
        and second_row[0] == "ABORTED"
        and (second_row[1] or {}).get("code") == "E007"
        and replay[0] == "ABORTED"
        and await _debt_rows(factory, t.second.id) == [],
        f"after the stop: seq 0 row {first_row!r}, seq 1 row {second_row!r}, replay {replay!r}",
    )
