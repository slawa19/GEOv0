"""T1544 on PostgreSQL: the operator's stop has an observable cutoff.

After `PATCH /admin/equivalents/{code}` with `is_active=false` returns, no money may commit in that
equivalent. Only two outcomes are allowed for anything racing it: the money commits BEFORE the PATCH
returns, or the PATCH commits first and the money is refused.

ONE MECHANISM SINCE 019 STAGE 5 (`T1909`): THE ROW. Every money writer - payment, tick, inject and, since
`T1907`, the clearing in every attempt - reads the equivalent row `FOR SHARE` and holds it through its
commit (`MoneyBoundary.refuse_inactive_equivalents(row_lock=True)`); the PATCH takes no advisory lock and
simply `UPDATE`s the row. So:

* a writer that has already read the row waits for nothing and the PATCH's `UPDATE` queues on the
  writer's transaction (`pg_locks` `transactionid`/`tuple`, `pg_blocking_pids` = the writer): the money
  commits before the PATCH can answer;
* a writer that reaches the row while the PATCH holds it uncommitted queues on the PATCH's transaction,
  then meets 40001 (its snapshot predates the PATCH's commit) and its owner's retry refuses it;
* a writer whose snapshot predates the PATCH but that has not yet read the row does not hold anything the
  PATCH needs - the equivalent advisory lock it holds shared is not the PATCH's business any more - so the
  PATCH answers first, and the writer's `FOR SHARE` then meets 40001 and its retry refuses it.

Until `T1909` the PATCH also held the equivalent owner advisory lock through its commit, and every race
here asserted "some backend waits on an advisory lock". Those probes are replaced by the probe of the
ACTUAL wait (spec, "Изоляция, писатели и клиринг", item 6): which backend waits, on which lock type, and
behind whom - and, as the mechanism of the one lock order, which advisory lock the waiting WRITER already
holds (shared for payment/tick/inject, exclusive for the clearing) while it waits on the row.

WHY `FOR SHARE` AND NOT A PLAIN READ. The application runs SERIALIZABLE, and a transaction takes its
snapshot at its first statement, before any lock wait. Probed 2026-09-13 on raw connections: after a wait,
a plain read of `is_active` still returns the value from before the PATCH committed, and so does
`FOR KEY SHARE`; only `FOR SHARE` fails with 40001, which the owner's retry turns into a fresh snapshot
that sees the stop.

THE STAND. Its own SERIALIZABLE engine (`factory`, from the P1 stand - the shared test engine runs READ
COMMITTED, where none of these races can be seen). The PATCH goes through the real route; a barrier on
its session's commit holds it with `is_active=false` flushed (the row lock held) and nothing committed,
and records its backend pid. Every control asserts its own premise - that the waiter really waited on a
row/transaction lock of the named backend, that the retry really was a 40001 - so none of them can pass
by never having raced.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import func, select, text, update

from app.api.deps import get_db
from app.config import settings
from app.core.clearing.service import ClearingService
from app.core.money_boundary import MoneyBoundary
from app.core.payments.service import PaymentService
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.transaction import Transaction
from app.main import app
from app.utils.exceptions import ConflictException, RetryablePaymentConflictException
from tests.integration.p019_interlock_support import (
    _no_advisory_lock_is_held,
    _seed_interlock_case,
    _use_serializable,
)
from tests.integration.test_p015_p1_money_replay_postgres import (  # noqa: F401 - `factory` is a fixture
    _OPENING,
    _Sse,
    _forget_the_route_cache,
    _debts,
    _install,
    _record_plans,
    _run_record,
    _runner,
    _scenario,
    _seed,
    _transactions,
    factory,
)

# MODE B (017 stage 2c, T1702): every commit of this module lands in a clone dropped after the test,
# not in the tier database it shares with mode-A tests - see `tests/tier_on_a_clone.py`.
from tests.tier_on_a_clone import tier_sessions_on_a_clone  # noqa: E402,F401 - autouse fixture

ADMIN = {"X-Admin-Token": settings.ADMIN_TOKEN}


class _PatchGate:
    """Holds ONE admin request at its session commit: its row writes flushed (their row locks held),
    nothing committed. `pid` is that request's backend, recorded at the gate (019 stage 5, `T1909`: the
    probe of the actual wait names the blocker)."""

    def __init__(self) -> None:
        self.armed = False
        self.reached = asyncio.Event()
        self.release = asyncio.Event()
        self.pid: int | None = None


@pytest_asyncio.fixture
async def admin_api(factory):
    gate = _PatchGate()

    async def override_get_db():
        async with factory() as session:
            if gate.armed:
                gate.armed = False
                original_commit = session.commit

                async def gated_commit():
                    gate.pid = int(await session.scalar(text("SELECT pg_backend_pid()")))
                    gate.reached.set()
                    await gate.release.wait()
                    await original_commit()

                session.commit = gated_commit
            yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        async with AsyncClient(app=app, base_url="http://test") as client:
            yield client, gate
    finally:
        gate.release.set()
        app.dependency_overrides.clear()


async def _deactivate(client, code: str):
    return await client.patch(
        f"/api/v1/admin/equivalents/{code}",
        json={"is_active": False, "reason": "t1544 operator stop"},
        headers=ADMIN,
    )


async def _advisory_waiter_exists(timeout: float = 5.0) -> bool:
    """A backend of THIS database is waiting on an advisory lock. Observed on its own connection.

    No race of this module uses it since 019 stage 5 (`T1909`): the admin paths take no advisory lock,
    so no waiter behind them is advisory. Kept for its importer outside this module
    (`test_p015_step5b_criterion_b_postgres.py`), where a clearing may still queue on the equivalent lock.
    """
    from tests.conftest import TestingSessionLocal

    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    async with TestingSessionLocal() as observer:
        while True:
            waiting = await observer.scalar(
                text(
                    "SELECT EXISTS (SELECT 1 FROM pg_locks WHERE locktype = 'advisory' "
                    "AND NOT granted AND database = "
                    "(SELECT oid FROM pg_database WHERE datname = current_database()))"
                )
            )
            await observer.rollback()
            if waiting:
                return True
            if loop.time() > deadline:
                return False
            await asyncio.sleep(0.02)


_ROW_WAITS = frozenset({"transactionid", "tuple"})

_ADVISORY_MODES_SQL = text(
    "SELECT mode FROM pg_locks WHERE locktype = 'advisory' AND granted AND pid = :pid "
    "AND database = (SELECT oid FROM pg_database WHERE datname = current_database()) "
    "AND classid = :namespace AND objid = :objid AND objsubid = 2 ORDER BY mode"
)


async def _waiters_behind(blocker_pid: int, *, timeout: float = 5.0) -> list[tuple[int, str]]:
    """(pid, locktype) of every backend queued on a lock `blocker_pid` holds, polled until one appears.

    `pg_locks` (the ungranted lock) joined with `pg_blocking_pids` (the dependency), on an observer
    connection of its own (`tests/p019_locks_off.blocked_by`). Empty after `timeout` means: nobody waited.
    """
    from tests.conftest import TestingSessionLocal
    from tests.p019_locks_off import blocked_by

    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    async with TestingSessionLocal() as observer:
        while True:
            waiting = await blocked_by(observer, blocker_pid)
            if waiting or loop.time() > deadline:
                return waiting
            await asyncio.sleep(0.02)


def _assert_row_wait(waiting: list[tuple[int, str]], *, what: str, behind: str) -> int:
    """Exactly one backend waits, on a ROW or TRANSACTION lock of `behind`; returns its pid."""

    assert waiting, f"{what} did not queue behind {behind}: it can finish while {behind} is still open"
    pids = {pid for pid, _locktype in waiting}
    assert len(pids) == 1, f"premise: more than one backend queued behind {behind}: {waiting}"
    assert {locktype for _pid, locktype in waiting} <= _ROW_WAITS, (
        f"{what} waits behind {behind}, but not on a row/transaction lock: {waiting}"
    )
    return next(iter(pids))


async def _advisory_modes(pid: int, equivalent_id) -> list[str]:
    """The modes of the equivalent advisory lock `pid` holds right now: `ShareLock` for the shared holders
    (payment, tick, inject), `ExclusiveLock` for the clearing, `[]` for everyone else (admin, reaction)."""
    from app.core.money_boundary import _EQUIVALENT_OWNER_LOCK_NAMESPACE
    from tests.conftest import TestingSessionLocal

    params = {
        "pid": pid,
        "namespace": _EQUIVALENT_OWNER_LOCK_NAMESPACE,
        "objid": MoneyBoundary._equivalent_owner_lock_key(equivalent_id) & 0xFFFFFFFF,
    }
    async with TestingSessionLocal() as observer:
        modes = (await observer.execute(_ADVISORY_MODES_SQL, params)).scalars().all()
        await observer.rollback()
    return [str(mode) for mode in modes]


def _retries_on_40001(caplog, event: str) -> list[str]:
    """The retry records of `event` (`payment.attempt_retry`, `clearing.attempt_retry`) caused by 40001.

    The payment logs `pgcode=40001`, the clearing the list of codes it classified (`pgcode=['40001']`).
    """
    return [
        r.getMessage()
        for r in caplog.records
        if f"event={event} " in r.getMessage()
        and ("pgcode=40001" in r.getMessage() or "pgcode=['40001']" in r.getMessage())
    ]


async def _clearing_transactions(factory, seed) -> int:  # noqa: F811 - `factory` is a fixture name
    async with factory() as verify:
        return int(
            await verify.scalar(
                select(func.count(Transaction.id)).where(
                    Transaction.type == "CLEARING",
                    Transaction.initiator_id.in_(seed["participant_ids"]),
                )
            )
        )


def _assert_stop_refusal(exc: BaseException, code: str) -> None:
    assert isinstance(exc, ConflictException), repr(exc)
    assert not isinstance(exc, RetryablePaymentConflictException), (
        "the operator stop was raised as a retryable conflict"
    )
    assert exc.code == "E008"
    assert exc.details.get("reason") == MoneyBoundary.EQUIVALENT_INACTIVE_REASON, exc.details
    assert exc.details.get("equivalents") == [code], exc.details
    assert "retryable" not in exc.details, exc.details


async def _is_active(factory, equivalent_id) -> bool:
    async with factory() as s:
        return bool(
            await s.scalar(select(Equivalent.is_active).where(Equivalent.id == equivalent_id))
        )


# ── payment <-> PATCH: FOR SHARE at commit ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_stop_arriving_between_the_binding_and_the_money_phase_answers_first_and_the_payment_is_refused(
    factory, admin_api, monkeypatch, caplog
) -> None:
    """The payment has bound (holds the equivalent lock SHARED) but not yet read the stop; the PATCH arrives.

    HISTORY OF THIS SCHEDULE. Until 019 stage 3 it was `test_a_payment_prepared_before_the_stop_and_waiting_
    behind_it_is_refused`: a durable `PREPARED` released the owner lock between the phases, the PATCH took
    it, and the commit was refused through the `FOR SHARE` serialization failure. Stages 3-4 held the owner
    lock EXCLUSIVELY from the binding phase to the commit, and the PATCH took it too, so the PATCH waited
    and the payment committed first (`..._waits_for_the_payment`). Since stage 5 (`T1909`) the payment
    holds the equivalent lock SHARED and the PATCH takes none: the payment holds nothing the PATCH needs
    until its `FOR SHARE` read of the row, so the PATCH ANSWERS FIRST and the payment is REFUSED - the
    other of the two outcomes T1544 allows, reached the way the manifest (`t1901-manifest.md` 5.5, rows
    :192-:245) planned it: the payment's snapshot predates the PATCH, its `FOR SHARE` meets 40001, `pay()`
    retries on a fresh snapshot, and the stop refuses the admitted payment (stored `ABORTED`).

    RED if the money phase read the stop without `FOR SHARE` (its stale snapshot says active and it commits
    after the PATCH answered), or if the PATCH waited on the payment's shared lock (the PATCH would not
    answer while the payment is parked).
    """
    client, _gate = admin_api
    world = await _seed(factory)
    code = world.equivalent.code
    monkeypatch.setattr(settings, "COMMIT_TIMEOUT_SECONDS", 30)
    monkeypatch.setattr(settings, "PAYMENT_TOTAL_TIMEOUT_SECONDS", 60)

    prepared = asyncio.Event()
    release_commit = asyncio.Event()
    payment_pid: list[int] = []
    original_commit = PaymentService._apply_payment

    # The barrier stands at the entry of the payment's money phase: the binding phase has taken the
    # equivalent lock (shared) and read the capacities; the stop has not been read yet. The first attempt
    # waits here; the retry passes through.
    async def _commit_after_barrier(self, declaration, **kwargs):
        if not prepared.is_set():
            payment_pid.append(int(await self.session.scalar(text("SELECT pg_backend_pid()"))))
            prepared.set()
            await release_commit.wait()
        return await original_commit(self, declaration, **kwargs)

    monkeypatch.setattr(PaymentService, "_apply_payment", _commit_after_barrier)

    async def _pay(tx_id: str):
        async with factory() as session:
            return await PaymentService(session).create_payment_internal(
                world.sender.id,
                to_pid=world.receiver.pid,
                equivalent=code,
                amount="10.00",
                idempotency_key=tx_id,
            )

    tx_id = str(uuid.uuid4())
    completed: list[str] = []
    payment = patch = None
    try:
        with caplog.at_level(logging.WARNING):
            payment = asyncio.create_task(_pay(tx_id))
            payment.add_done_callback(lambda _t: completed.append("payment"))
            await asyncio.wait_for(prepared.wait(), timeout=20)
            # One transaction: nothing of the payment is visible to anyone else yet.
            assert await _transactions(factory, world) == {}, "premise: the payment is durable before its commit"
            assert await _advisory_modes(payment_pid[0], world.equivalent.id) == ["ShareLock"], (
                "premise: the parked payment does not hold the equivalent lock shared"
            )

            patch = asyncio.create_task(_deactivate(client, code))
            patch.add_done_callback(lambda _t: completed.append("patch"))
            resp = await asyncio.wait_for(patch, timeout=20)
            assert resp.status_code == 200, resp.text
            assert not payment.done(), "premise: the payment was not parked while the PATCH ran"
            assert completed == ["patch"], completed

            release_commit.set()
            with pytest.raises(ConflictException) as refused_first:
                await asyncio.wait_for(payment, timeout=30)

        _assert_stop_refusal(refused_first.value, code)
        retries = _retries_on_40001(caplog, "payment.attempt_retry")
        assert len(retries) == 1, (
            f"premise: the refusal did not come through the payment's FOR SHARE 40001 and one retry: {retries}"
        )
        assert await _debts(factory, world) == {(world.sender.pid, world.receiver.pid): _OPENING}
        assert await _transactions(factory, world) == {tx_id: "ABORTED"}
        assert await _is_active(factory, world.equivalent.id) is False

        # After the PATCH returned, the stop is in force.
        monkeypatch.setattr(PaymentService, "_apply_payment", original_commit)
        with pytest.raises(ConflictException) as refused:
            await _pay(str(uuid.uuid4()))
        _assert_stop_refusal(refused.value, code)
    finally:
        release_commit.set()
        for task in (payment, patch):
            if task is not None and not task.done():
                await asyncio.wait([task], timeout=15)
            if task is not None and not task.done():
                task.cancel()
                await asyncio.wait([task], timeout=5)
        _forget_the_route_cache(world)


# ── clearing <-> PATCH: the clearing's `FOR SHARE` against the PATCH's UPDATE ──────────────────


@pytest.mark.asyncio
async def test_a_deactivating_patch_waits_for_a_clearing_that_already_read_the_flag(
    factory, admin_api, monkeypatch
) -> None:
    """Clearing holds its exclusive equivalent lock, has read `True` `FOR SHARE` and locked its cycle rows,
    and pauses before mutating (at its auto-clearing policy check, the first step after the cycle's
    `FOR UPDATE`).

    The PATCH must stay pending until the clearing commits: its `UPDATE` queues on the CLEARING'S
    transaction (measured in `pg_locks`, blocker named by `pg_blocking_pids`), holding no advisory lock
    itself. THE ORDER `["clearing", "patch"]` is the order of COMMIT against ANSWER, read at the answer:
    when the PATCH response arrives, the clearing's transaction is already visible. (Until `T1909` it was
    read from task completion; the clearing now lets the PATCH go at its commit and still releases its
    pinned connection afterwards, so task order no longer means commit order.)

    RED if the clearing read the flag without `FOR SHARE` or released the row before its commit: the PATCH
    then returns while the clearing is paused, and the clearing commits money after the operator was
    told `200`.
    """
    from tests.conftest import TestingSessionLocal

    client, _gate = admin_api
    seed = await _seed_interlock_case()
    clearing_session = TestingSessionLocal()
    paused = asyncio.Event()
    release_clearing = asyncio.Event()
    clearing_pid: list[int] = []
    clearing = patch = None
    try:
        await _use_serializable(clearing_session)
        service = ClearingService(clearing_session)
        original_policy = service._cycle_respects_auto_clearing

        # Execution only: this stand calls `execute_clearing_with_amount` directly, so detection (the
        # other caller of the policy check) never runs here.
        async def _pause_before_mutation(debts):
            respects = await original_policy(debts)
            if not paused.is_set():
                clearing_pid.append(int(await service.session.scalar(text("SELECT pg_backend_pid()"))))
                paused.set()
                await release_clearing.wait()
            return respects

        monkeypatch.setattr(service, "_cycle_respects_auto_clearing", _pause_before_mutation)

        async def _patch_then_look():
            resp = await _deactivate(client, seed["equivalent_code"])
            return resp, await _clearing_transactions(factory, seed)

        clearing = asyncio.create_task(service.execute_clearing_with_amount(seed["cycle"]))
        await asyncio.wait_for(paused.wait(), timeout=20)
        assert await _advisory_modes(clearing_pid[0], seed["equivalent_id"]) == ["ExclusiveLock"], (
            "premise: the parked clearing does not hold its exclusive equivalent lock"
        )

        patch = asyncio.create_task(_patch_then_look())
        patch_pid = _assert_row_wait(
            await _waiters_behind(clearing_pid[0]), what="the PATCH", behind="the clearing"
        )
        assert await _advisory_modes(patch_pid, seed["equivalent_id"]) == [], (
            "the PATCH holds the equivalent advisory lock: since T1909 the row is its whole protocol"
        )
        assert not patch.done()

        release_clearing.set()
        amount = await asyncio.wait_for(clearing, timeout=20)
        resp, clearings_at_answer = await asyncio.wait_for(patch, timeout=20)

        assert amount == Decimal("30.00000000"), "premise: the clearing did not run to its commit"
        assert resp.status_code == 200, resp.text
        order = ["clearing", "patch"] if clearings_at_answer == 1 else ["patch", "clearing"]
        assert order == ["clearing", "patch"], (
            f"the PATCH answered before the clearing's commit was visible ({clearings_at_answer} CLEARING)"
        )
        assert await _is_active(factory, seed["equivalent_id"]) is False
    finally:
        release_clearing.set()
        for task in (clearing, patch):
            # A released task is let to FINISH first. Cancelling a PATCH that was just let out of its
            # commit barrier leaves its connection idle in transaction, holding the equivalent row,
            # and the cleanup below then waits on it forever (seen under a mutation, 2026-09-14).
            if task is not None and not task.done():
                await asyncio.wait([task], timeout=15)
            if task is not None and not task.done():
                task.cancel()
                await asyncio.wait([task], timeout=5)
        await clearing_session.rollback()
        await clearing_session.close()


@pytest.mark.asyncio
async def test_a_clearing_that_waited_behind_the_patch_refuses_in_its_fresh_snapshot(
    factory, admin_api, caplog
) -> None:
    """The PATCH holds the row with `False` flushed; the clearing queues on it; the PATCH commits.

    The clearing takes its exclusive equivalent lock (nobody holds it), rolls back to a fresh snapshot and
    reads the flag `FOR SHARE` - which queues on the PATCH's transaction (measured: `pg_locks`
    `transactionid`/`tuple`, blocker = the PATCH's backend, the waiter holding the exclusive equivalent
    lock - the one order, equivalent lock -> row). The PATCH commits; the clearing's `FOR SHARE` meets
    40001 (its snapshot predates the commit), its retry owner (`_run_attempts`) runs a fresh attempt, and
    that attempt reads the stop and refuses.

    RED if the clearing read the flag without `FOR SHARE` (it reads `True` from its stale snapshot and
    clears the cycle after the PATCH returned), or if a conflict were not retried on a fresh snapshot.
    """
    from tests.conftest import TestingSessionLocal

    client, gate = admin_api
    seed = await _seed_interlock_case()
    clearing_session = TestingSessionLocal()
    clearing = patch = None
    try:
        await _use_serializable(clearing_session)
        with caplog.at_level(logging.WARNING):
            gate.armed = True
            patch = asyncio.create_task(_deactivate(client, seed["equivalent_code"]))
            await asyncio.wait_for(gate.reached.wait(), timeout=20)

            clearing = asyncio.create_task(
                ClearingService(clearing_session).execute_clearing_with_amount(seed["cycle"])
            )
            clearing_pid = _assert_row_wait(
                await _waiters_behind(gate.pid), what="the clearing", behind="the PATCH"
            )
            assert await _advisory_modes(clearing_pid, seed["equivalent_id"]) == ["ExclusiveLock"], (
                "the backend queued on the PATCH's row does not hold the clearing's exclusive lock"
            )
            assert await _advisory_modes(gate.pid, seed["equivalent_id"]) == [], (
                "the PATCH holds the equivalent advisory lock: since T1909 the row is its whole protocol"
            )
            assert not clearing.done()

            gate.release.set()
            resp = await asyncio.wait_for(patch, timeout=20)
            assert resp.status_code == 200, resp.text

            with pytest.raises(ConflictException) as refused:
                await asyncio.wait_for(clearing, timeout=20)
        _assert_stop_refusal(refused.value, seed["equivalent_code"])
        assert _retries_on_40001(caplog, "clearing.attempt_retry"), (
            "premise: the refusal did not come through the clearing's FOR SHARE 40001 and a fresh attempt"
        )

        async with factory() as verify:
            debts = {
                d.id: (d.amount, d.version)
                for d in (
                    await verify.scalars(
                        select(Debt).where(Debt.equivalent_id == seed["equivalent_id"])
                    )
                ).all()
            }
            clearing_transactions = (
                await verify.scalar(
                    select(func.count(Transaction.id)).where(
                        Transaction.type == "CLEARING",
                        Transaction.initiator_id.in_(seed["participant_ids"]),
                    )
                )
            )
        assert debts == {
            seed["debt_ids"][0]: (Decimal("100.00000000"), 1),
            seed["debt_ids"][1]: (Decimal("30.00000000"), 1),
            seed["debt_ids"][2]: (Decimal("40.00000000"), 1),
        }, debts
        assert clearing_transactions == 0
        assert not clearing_session.in_transaction()
        # Not vacuous: the clearing held its exclusive session lock while it waited (asserted above), so
        # this is the release of that lock by its cleanup - and the cleanup did not invalidate the connection.
        await _no_advisory_lock_is_held(caplog)
    finally:
        gate.release.set()
        for task in (clearing, patch):
            # A released task is let to FINISH first. Cancelling a PATCH that was just let out of its
            # commit barrier leaves its connection idle in transaction, holding the equivalent row,
            # and the cleanup below then waits on it forever (seen under a mutation, 2026-09-14).
            if task is not None and not task.done():
                await asyncio.wait([task], timeout=15)
            if task is not None and not task.done():
                task.cancel()
                await asyncio.wait([task], timeout=5)
        await clearing_session.rollback()
        await clearing_session.close()


# ── staged simulator payments: the same commit guard, through the bounded money replay ────────


def _record_staged_outcomes(monkeypatch) -> list[str]:
    outcomes: list[str] = []
    original = PaymentService.create_payment_internal_staged

    async def _recording(self, *args, **kwargs):
        try:
            result = await original(self, *args, **kwargs)
        except ConflictException as exc:
            reason = (exc.details or {}).get("reason")
            outcomes.append(f"{type(exc).__name__}:{reason}" if reason else type(exc).__name__)
            raise
        outcomes.append(f"result:{result.result.status}")
        return result

    monkeypatch.setattr(PaymentService, "create_payment_internal_staged", _recording)
    return outcomes


@pytest.mark.asyncio
async def test_a_tick_that_waited_behind_the_patch_discards_its_attempt_and_the_replay_refuses(
    factory, admin_api, monkeypatch, caplog
) -> None:
    """The PATCH-first stale-snapshot race through the real staged payment path.

    The tick's money attempt, holding the equivalent lock shared, queues with its `FOR SHARE` on the
    PATCH's row (measured: `pg_locks` `transactionid`/`tuple` behind the PATCH's backend) with a
    snapshot from before the PATCH commits. Its staged commit meets `40001`; the attempt is discarded
    without publishing; the fresh replay sees the stop and refuses. RED if the commit `FOR SHARE` is
    removed: the first attempt then commits money after the PATCH returned.
    """
    client, gate = admin_api
    world = await _seed(factory)
    patch = tick = None
    try:
        sse = _Sse()
        run = _run_record(world, f"t1544-pg-{uuid.uuid4().hex[:8]}")
        runner = _runner(run, _scenario(world), sse)
        _install(monkeypatch, factory)
        plans = _record_plans(monkeypatch, runner)
        outcomes = _record_staged_outcomes(monkeypatch)

        with caplog.at_level(logging.WARNING):
            gate.armed = True
            patch = asyncio.create_task(_deactivate(client, world.equivalent.code))
            await asyncio.wait_for(gate.reached.wait(), timeout=20)

            tick = asyncio.create_task(
                asyncio.wait_for(runner.tick_real_mode(run.run_id), timeout=90.0)
            )
            tick_pid = _assert_row_wait(
                await _waiters_behind(gate.pid, timeout=30.0),
                what="the tick's money attempt",
                behind="the PATCH",
            )
            assert await _advisory_modes(tick_pid, world.equivalent.id) == ["ShareLock"], (
                "the tick's money attempt queued on the row without holding the equivalent lock shared"
            )
            assert not tick.done()

            gate.release.set()
            resp = await asyncio.wait_for(patch, timeout=20)
            assert resp.status_code == 200, resp.text
            await tick

        replays = [
            r.getMessage()
            for r in caplog.records
            if "simulator.real.money_phase_replay " in r.getMessage()
        ]
        assert len(replays) == 1, replays
        assert "conflict=RETRYABLE_PAYMENT_CONFLICT" in replays[0], replays
        assert len(plans) == 2 and len(plans[0]) >= 1, f"premise: no staged payment to race: {plans}"
        assert outcomes == [
            "RetryablePaymentConflictException",
            f"ConflictException:{MoneyBoundary.EQUIVALENT_INACTIVE_REASON}",
        ], outcomes

        assert await _debts(factory, world) == {(world.sender.pid, world.receiver.pid): _OPENING}
        assert await _transactions(factory, world) == {}
        # Nothing of the discarded attempt is published or counted; the replay's refusal is a rejection
        # of load, not an error of the run.
        assert sse.published("tx.updated") == 0
        assert run.committed_total == 0
        assert run.errors_total == 0
        assert run._real_money_replays_total == 1
        assert run._real_money_committed_ticks_total == 1
    finally:
        gate.release.set()
        for task in (patch, tick):
            # A released task is let to FINISH first. Cancelling a PATCH that was just let out of its
            # commit barrier leaves its connection idle in transaction, holding the equivalent row,
            # and the cleanup below then waits on it forever (seen under a mutation, 2026-09-14).
            if task is not None and not task.done():
                await asyncio.wait([task], timeout=15)
            if task is not None and not task.done():
                task.cancel()
                await asyncio.wait([task], timeout=5)
        _forget_the_route_cache(world)


@pytest.mark.asyncio
async def test_a_commit_guard_conflict_on_every_attempt_exhausts_the_budget_without_money(
    factory, monkeypatch, caplog
) -> None:
    """Boundedness: every attempt's staged commit meets `40001` on the `FOR SHARE` row lock.

    After each attempt's debt snapshot, a competitor updates the equivalent row (a column other than
    `is_active`, so the stop never becomes true). The replay must stop after exactly the configured
    number of attempts and record a tick without progress. RED if another attempt is allowed.
    """
    world = await _seed(factory)
    try:
        sse = _Sse()
        run = _run_record(world, f"t1544-pg-bound-{uuid.uuid4().hex[:8]}")
        runner = _runner(run, _scenario(world), sse)
        _install(monkeypatch, factory)
        _record_plans(monkeypatch, runner)
        outcomes = _record_staged_outcomes(monkeypatch)
        attempts_allowed = int(runner._real_money_replay_attempts_limit)
        assert attempts_allowed >= 2, f"premise: the replay is disabled ({attempts_allowed})"

        touches: list[int] = []
        original_snapshot = runner._load_debt_snapshot_by_pid

        async def _snapshot_then_touch_the_equivalent(session, participants, equivalents):
            snapshot = await original_snapshot(session, participants, equivalents)
            async with factory() as other:
                await other.execute(
                    update(Equivalent)
                    .where(Equivalent.id == world.equivalent.id)
                    .values(description=f"t1544-touch-{len(touches)}")
                )
                await other.commit()
            touches.append(1)
            return snapshot

        monkeypatch.setattr(
            runner, "_load_debt_snapshot_by_pid", _snapshot_then_touch_the_equivalent
        )

        with caplog.at_level(logging.WARNING):
            await asyncio.wait_for(runner.tick_real_mode(run.run_id), timeout=90.0)

        assert len(touches) == attempts_allowed, (touches, attempts_allowed)
        assert outcomes == ["RetryablePaymentConflictException"] * attempts_allowed, outcomes
        exhausted = [
            r.getMessage()
            for r in caplog.records
            if "simulator.real.money_phase_replay_exhausted" in r.getMessage()
        ]
        assert len(exhausted) == 1, exhausted
        assert await _debts(factory, world) == {(world.sender.pid, world.receiver.pid): _OPENING}
        assert await _transactions(factory, world) == {}
        assert sse.published("tx.updated") == 0
        assert run.errors_total == 0
        assert run.last_error["code"] == "REAL_MODE_MONEY_CONFLICT_UNRESOLVED"
        assert run._real_money_replay_exhausted_total == 1
        assert run._real_consec_money_no_progress_ticks == 1
        assert await _is_active(factory, world.equivalent.id) is True
    finally:
        _forget_the_route_cache(world)


# ── placement: the payment guard below the TTL branch - DROPPED by 019 stage 4 ──────────────────────────
#
# `test_an_expired_payment_in_a_deactivated_equivalent_is_aborted_as_expired` seeded a durable `PREPARED`
# payment with an expired `PrepareLock` in a deactivated equivalent and called `PaymentEngine.commit`,
# asserting that the engine's TTL branch ("expired before commit") ran above the T1544 guard, and that the
# expired payment was ABORTED with the debts unchanged (manifest `t1901-manifest.md` 5.5, rows :590-634,
# :636-643, :644-645). All three contracts are removed with the engine, the reservation TTL and
# `app/core/recovery.py`: CHECK `030` refuses the seed itself, and a payment's stop guard is the only
# refusal between its admission and its commit. The stop refusal it guarded stays asserted by every race
# here (`_assert_stop_refusal`) and by `tests/integration/test_p015_t1544_operator_stop_refuses_money.py`.


@pytest.mark.asyncio
async def test_a_patch_arriving_while_a_payment_holds_the_stop_check_waits_for_that_payment(
    factory, admin_api, monkeypatch
) -> None:
    """Payment first, PATCH second: the payment has passed its commit check and holds its locks.

    The deactivating PATCH must wait - measured in `pg_locks`, not slept: its `UPDATE` queues on the
    PAYMENT'S transaction (the payment's `FOR SHARE`), and it holds no advisory lock - and return only
    after the payment has committed; the next payment is then refused. Either outcome of the cutoff is
    allowed, and this is the "money commits before the PATCH returns" one.
    """
    client, _gate = admin_api
    world = await _seed(factory)
    code = world.equivalent.code
    monkeypatch.setattr(settings, "COMMIT_TIMEOUT_SECONDS", 30)
    monkeypatch.setattr(settings, "PAYMENT_TOTAL_TIMEOUT_SECONDS", 60)

    checked = asyncio.Event()
    release_payment = asyncio.Event()
    payment_pid: list[int] = []
    original_check = MoneyBoundary.refuse_inactive_equivalents

    async def _check_then_hold(self, equivalent_ids, *, row_lock):
        await original_check(self, equivalent_ids, row_lock=row_lock)
        if row_lock and not checked.is_set():
            payment_pid.append(int(await self.session.scalar(text("SELECT pg_backend_pid()"))))
            checked.set()
            await release_payment.wait()

    monkeypatch.setattr(MoneyBoundary, "refuse_inactive_equivalents", _check_then_hold)

    async def _pay(tx_id: str):
        async with factory() as session:
            return await PaymentService(session).create_payment_internal(
                world.sender.id,
                to_pid=world.receiver.pid,
                equivalent=code,
                amount="10.00",
                idempotency_key=tx_id,
            )

    completed: list[str] = []
    first_tx = str(uuid.uuid4())
    payment = patch = None
    try:
        payment = asyncio.create_task(_pay(first_tx))
        payment.add_done_callback(lambda _t: completed.append("payment"))
        await asyncio.wait_for(checked.wait(), timeout=20)

        assert await _advisory_modes(payment_pid[0], world.equivalent.id) == ["ShareLock"], (
            "premise: the parked payment does not hold the equivalent lock shared"
        )
        patch = asyncio.create_task(_deactivate(client, code))
        patch.add_done_callback(lambda _t: completed.append("patch"))
        patch_pid = _assert_row_wait(
            await _waiters_behind(payment_pid[0]),
            what="the PATCH",
            behind="the payment that had already passed its stop check",
        )
        assert await _advisory_modes(patch_pid, world.equivalent.id) == [], (
            "the PATCH holds the equivalent advisory lock: since T1909 the row is its whole protocol"
        )
        assert not patch.done()

        release_payment.set()
        result = await asyncio.wait_for(payment, timeout=30)
        resp = await asyncio.wait_for(patch, timeout=30)

        assert result.status == "COMMITTED", result
        assert resp.status_code == 200, resp.text
        assert completed == ["payment", "patch"], completed
        assert await _debts(factory, world) == {
            (world.sender.pid, world.receiver.pid): _OPENING + Decimal("10.00")
        }

        with pytest.raises(ConflictException) as refused:
            await _pay(str(uuid.uuid4()))
        _assert_stop_refusal(refused.value, code)
        assert await _debts(factory, world) == {
            (world.sender.pid, world.receiver.pid): _OPENING + Decimal("10.00")
        }
    finally:
        release_payment.set()
        for task in (payment, patch):
            if task is not None and not task.done():
                await asyncio.wait([task], timeout=15)
            if task is not None and not task.done():
                task.cancel()
                await asyncio.wait([task], timeout=5)
        _forget_the_route_cache(world)


# ── the real-mode inject writer: PATCH first ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_inject_that_waited_behind_the_patch_is_refused_and_writes_nothing(
    factory, admin_api, monkeypatch, caplog
) -> None:
    """The inject's owner transaction, holding the equivalent lock shared, queues with its `FOR SHARE`
    check on the PATCH's row (measured) with a snapshot from before the PATCH commits.

    Its `FOR SHARE` check then meets 40001, the inject owner retries on a fresh snapshot, and the
    retry refuses it: consumed as a rejection, no debt, no envelope. RED if the check at the inject owner
    reads without `FOR SHARE`: the stale snapshot still says active and the debt is written after the
    PATCH returned.
    """
    client, gate = admin_api
    world = await _seed(factory)
    run_id = f"t1544-inject-{uuid.uuid4().hex[:8]}"
    task = patch = None
    try:
        sse = _Sse()
        run = _run_record(world, run_id)
        scenario = dict(_scenario(world))
        scenario["events"] = [
            {
                "type": "inject",
                "time": 0,
                "effects": [
                    {
                        "op": "inject_debt",
                        "from": world.receiver.pid,
                        "to": world.sender.pid,
                        "equivalent": world.equivalent.code,
                        "amount": "5.00",
                    }
                ],
            }
        ]
        runner = _runner(run, scenario, sse)
        runner._real_enable_inject = True
        _install(monkeypatch, factory)

        async def _inject():
            async with factory() as session:
                await runner._apply_due_scenario_events(
                    session, run_id=run_id, run=run, scenario=scenario
                )

        with caplog.at_level(logging.WARNING):
            gate.armed = True
            patch = asyncio.create_task(_deactivate(client, world.equivalent.code))
            await asyncio.wait_for(gate.reached.wait(), timeout=20)

            task = asyncio.create_task(_inject())
            inject_pid = _assert_row_wait(
                await _waiters_behind(gate.pid, timeout=30.0), what="the inject", behind="the PATCH"
            )
            assert await _advisory_modes(inject_pid, world.equivalent.id) == ["ShareLock"], (
                "the inject queued on the row without holding the equivalent lock shared"
            )
            assert not task.done()

            gate.release.set()
            resp = await asyncio.wait_for(patch, timeout=20)
            assert resp.status_code == 200, resp.text

            # A rejection of the inject, consumed by its owner - not an exception for the tick.
            await asyncio.wait_for(task, timeout=30)

        refusals = [
            r.getMessage()
            for r in caplog.records
            if "simulator.real.inject.refused_equivalent_inactive" in r.getMessage()
        ]
        assert len(refusals) == 1, (
            f"premise: the inject was not refused by the operator stop exactly once: {refusals}"
        )
        retries = [
            r.getMessage()
            for r in caplog.records
            if "simulator.real.inject.transient_retry" in r.getMessage()
        ]
        assert retries, (
            "premise: the inject did not meet the FOR SHARE serialization failure before refusing"
        )
        assert await _debts(factory, world) == {(world.sender.pid, world.receiver.pid): _OPENING}
        async with factory() as fresh:
            envelopes = await fresh.scalar(
                text("SELECT count(*) FROM debt_operations WHERE identity = :identity"),
                {"identity": f"{run_id}:0"},
            )
        assert envelopes == 0, envelopes
        assert 0 in run._real_fired_scenario_event_indexes, (
            "the refused inject was left pending; a rejection is consumed, not retried every tick"
        )
    finally:
        gate.release.set()
        for pending in (patch, task):
            if pending is not None and not pending.done():
                await asyncio.wait([pending], timeout=15)
            if pending is not None and not pending.done():
                pending.cancel()
                await asyncio.wait([pending], timeout=5)
        _forget_the_route_cache(world)
