"""019 `T1903` -> `T1909`: ONE lock order, raced for real - payment and clearing against three admin paths.

THE CONTRACT SINCE 019 STAGE 5 (`T1909`, decision `KEEP-EQUIVALENT-LOCK` of the fourth consultation;
`app/core/money_boundary.py`, module docstring; spec, "Один порядок локов"). There is ONE equivalent
advisory lock, in two modes: a payment (and a tick's money phase, an inject) holds it SHARED, the clearing
holds it EXCLUSIVE on its pinned connection. A money writer takes it BEFORE it touches a row of the
equivalent - the equivalent row `FOR SHARE` (held through its commit), then the debt rows. The admin paths
take NO advisory lock at all: `PATCH` and the hold clear change the equivalent row, the `DELETE` deletes
it, and that row is their whole protocol with money.

So there is one order - equivalent lock -> row - among the paths that take the lock, and the admin paths
cannot invert it because they hold no advisory lock anyone could wait for. Until `T1909` the admin paths
took the (then exclusive) owner lock before the row, and this module asserted that every waiter queued on
the ADVISORY lock. What each schedule asserts now, in `pg_locks` / `pg_stat_activity` (via
`pg_blocking_pids`), while one side is parked at a barrier AFTER it has read/locked its rows:

* MONEY FIRST (the money holds its equivalent lock - `ShareLock` for the payment, `ExclusiveLock` for the
  clearing, asserted - and the row `FOR SHARE`): `PATCH` and the hold clear queue on a ROW or TRANSACTION
  lock of the money's backend, holding no advisory lock themselves; the money commits before the admin
  answers (read AT the answer from another session, not from task completion). The `DELETE` of an ACTIVE
  equivalent touches no locked row: it answers `409 Deactivate equivalent before delete` while the money is
  still parked, and nobody queues behind the money.
* ADMIN FIRST (the admin holds its row change uncommitted, or - `DELETE` - its usage read, and no advisory
  lock, asserted): money queues on the admin's transaction with its `FOR SHARE`, ALREADY HOLDING its
  equivalent lock (the order equivalent lock -> row, measured on the waiter), meets 40001 when the admin
  commits, and its retry owner finishes with the outcome the admin change implies - refused after a stop,
  run after a lifted hold. Where the money met a COMMITTED change of the row first (the deactivation
  before a `DELETE`; the hold before a clear, for a payment admitted before it), it is refused on its
  retry while the admin is still parked, and nobody queues behind the admin.
* EVERY SCHEDULE: no `40P01` reaches either outcome or the log, and no advisory lock is left behind -
  not vacuous: each schedule first asserted that the money held its lock (the clearing's is session-level
  and released by its cleanup).

THE THREE ADMIN PATHS, raced separately against payment and against clearing, in both orders (12
schedules, the same twelve as `T1903`): `PATCH` deactivation (`admin.py`, UPDATE of the row), the
integrity-hold clear (`FOR UPDATE` of the row -> UPDATE), and the equivalent `DELETE` (usage count ->
delete). A DELETE that could delete would need an unused equivalent, where no money can run; so its
admin-first race pauses it after its usage read and it answers the usage `409`, and its money-first race
meets an active equivalent and answers the `409` that precedes any lock.

THE STAND. The P1 SERIALIZABLE engine on a mode-B clone (`factory`), the real admin routes through
`admin_api` (its gate holds one request at its session commit and records its backend pid), the interlock
world for both money operations (a three-edge cycle for clearing, the A -> B line for a payment). No
sleeps on the clock: waits are observed in `pg_locks`. The clearing is parked at its auto-clearing policy
check (`_cycle_respects_auto_clearing`), the first step after its cycle's `FOR UPDATE` and before any
mutation; this stand calls `execute_clearing_with_amount` directly, so detection - the other caller of
that check - never runs here.

WHAT IT DOES NOT PROVE: that no OTHER interleaving deadlocks; the order for writers not listed here
(tick, inject - their row waits against the PATCH are in `test_p015_t1544_operator_stop_races_postgres.py`;
the reaction's in `test_p015_step5c_hold_races_postgres.py`).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import insert, select, text, update

from app.api.v1 import admin as admin_module
from app.config import settings
from app.core.clearing.service import ClearingService
from app.core.ledger.reconciliation import PASSED
from app.core.money_boundary import MoneyBoundary
from app.core.payments.router import PaymentRouter
from app.core.payments.service import PaymentService
from app.db.models.equivalent import Equivalent
from app.db.models.transaction import Transaction
from app.db.reconciliation_tables import debt_reconciliation_results
from app.schemas.payment import PaymentCreateRequest
from app.utils.exceptions import ConflictException
from tests.integration.p019_interlock_support import (
    _no_advisory_lock_is_held,
    _seed_interlock_case,
)
from tests.integration.test_p015_p1_money_replay_postgres import factory  # noqa: F401 - fixture
from tests.integration.test_p015_t1544_operator_stop_races_postgres import (  # noqa: F401 - fixture
    ADMIN,
    _advisory_modes,
    _assert_row_wait,
    _clearing_transactions,
    _retries_on_40001,
    _waiters_behind,
    admin_api,
)
from tests.unit.test_p015_step5c_reaction_and_hold import hold_directly

# MODE B: every commit of this module lands in a clone dropped after the test.
from tests.tier_on_a_clone import tier_sessions_on_a_clone  # noqa: E402,F401 - autouse fixture

ADMIN_PATHS = ("patch", "hold_clear", "delete")
MONEY = ("payment", "clearing")
_CYCLE_AMOUNT = Decimal("30.00000000")
#: The mode of the equivalent advisory lock each money operation holds (`pg_locks.mode`).
_MONEY_MODE = {"payment": ["ShareLock"], "clearing": ["ExclusiveLock"]}
_RETRY_EVENT = {"payment": "payment.attempt_retry", "clearing": "clearing.attempt_retry"}


@pytest.fixture(autouse=True)
def _barrier_budgets(monkeypatch):
    # The barriers hold locks for a moment; the default lock budget is seconds and is not what these
    # races are about. A payment held before its equivalent lock waits inside its binding phase, which
    # runs under `PREPARE_TIMEOUT_SECONDS`.
    monkeypatch.setattr(settings, "PREPARE_TIMEOUT_SECONDS", 30)
    monkeypatch.setattr(settings, "COMMIT_TIMEOUT_SECONDS", 30)
    monkeypatch.setattr(settings, "PAYMENT_TOTAL_TIMEOUT_SECONDS", 60)


def _no_deadlock(outcomes, caplog) -> None:
    """No `40P01` in any outcome's exception chain and none in the log."""

    for outcome in outcomes:
        seen = outcome
        while isinstance(seen, BaseException):
            text_of = f"{type(seen).__name__}: {seen}"
            assert "40P01" not in text_of and "deadlock detected" not in text_of, text_of
            code = getattr(getattr(seen, "orig", None), "sqlstate", None) or getattr(seen, "sqlstate", None)
            assert code != "40P01", text_of
            seen = seen.__cause__ or seen.__context__
    logged = [r.getMessage() for r in caplog.records if "40P01" in r.getMessage() or "deadlock" in r.getMessage()]
    assert logged == [], logged


async def _finish(*tasks) -> None:
    for task in tasks:
        if task is not None and not task.done():
            await asyncio.wait([task], timeout=15)
        if task is not None and not task.done():
            task.cancel()
            await asyncio.wait([task], timeout=5)


def _admin_call(client, path: str, code: str):
    if path == "patch":
        return client.patch(
            f"/api/v1/admin/equivalents/{code}",
            json={"is_active": False, "reason": "t1903 race"},
            headers=ADMIN,
        )
    if path == "hold_clear":
        return client.post(
            f"/api/v1/admin/equivalents/{code}/integrity-hold/clear",
            json={"reason": "t1903 race"},
            headers=ADMIN,
        )
    return client.request(
        "DELETE", f"/api/v1/admin/equivalents/{code}", json={"reason": "t1903 race"}, headers=ADMIN
    )


async def _set_held_with_a_later_pass(factory, equivalent_id) -> None:  # noqa: F811
    hold_id = await hold_directly(factory, equivalent_id)
    now = datetime.now(timezone.utc)
    async with factory() as session:
        await session.execute(
            update(debt_reconciliation_results)
            .where(debt_reconciliation_results.c.id == hold_id)
            .values(is_latest=False)
        )
        await session.execute(
            insert(debt_reconciliation_results).values(
                id=uuid.uuid4(), equivalent_id=equivalent_id, status=PASSED, fingerprint="p" * 64,
                detail={"stand": "t1903 later PASSED"}, checked_at=now, last_checked_at=now, is_latest=True,
            )
        )
        await session.commit()


async def _equivalent_row(factory, equivalent_id):  # noqa: F811
    async with factory() as session:
        return (
            await session.execute(
                select(Equivalent.is_active, Equivalent.integrity_hold_result_id).where(
                    Equivalent.id == equivalent_id
                )
            )
        ).one_or_none()


async def _state(factory, tx_id: str) -> str | None:  # noqa: F811
    async with factory() as session:
        return await session.scalar(select(Transaction.state).where(Transaction.tx_id == tx_id))


async def _pid(session) -> int:
    return int(await session.scalar(text("SELECT pg_backend_pid()")))


# ── money first: the money operation holds its equivalent lock and its rows; the admin path arrives ─


@pytest.mark.asyncio
@pytest.mark.parametrize("admin_path", ADMIN_PATHS)
@pytest.mark.parametrize("money", MONEY)
async def test_an_admin_path_arriving_while_money_holds_its_rows(
    factory, admin_api, monkeypatch, caplog, money, admin_path  # noqa: F811 - fixtures
) -> None:
    """Money has taken its equivalent lock and read/locked its rows (payment: the commit-time `FOR SHARE`
    on the equivalent; clearing: its `FOR SHARE` stop read and the cycle's `FOR UPDATE`).

    `PATCH` / hold clear: the admin request must queue on the MONEY'S row/transaction lock, holding no
    advisory lock, and answer only after the money committed. `DELETE`: the equivalent is active, so it
    answers its `409` without touching a locked row - while the money is still parked.

    RED if the admin path took the equivalent advisory lock (it would wait on `advisory`, or hold one), if
    the money read its stop without `FOR SHARE` (the admin would not queue and would answer before the
    money commits), or if the two waited on each other (`40P01`).
    """
    client, _gate = admin_api
    seed = await _seed_interlock_case()
    code, equivalent_id = seed["equivalent_code"], seed["equivalent_id"]
    tx_id = str(uuid.uuid4())
    paused, release = asyncio.Event(), asyncio.Event()
    holder_pid: list[int] = []
    completed: list[str] = []
    money_task = admin_task = None

    if money == "payment":
        original_guard = MoneyBoundary.refuse_inactive_equivalents

        async def _guard_then_hold(self, equivalent_ids, *, row_lock):
            await original_guard(self, equivalent_ids, row_lock=row_lock)
            if row_lock and not paused.is_set():
                holder_pid.append(await _pid(self.session))
                paused.set()
                await release.wait()

        monkeypatch.setattr(MoneyBoundary, "refuse_inactive_equivalents", _guard_then_hold)

        async def _money():
            async with factory() as session:
                return await PaymentService(session).create_payment_internal(
                    seed["participant_ids"][0],
                    to_pid=seed["participant_pids"][1],
                    equivalent=code,
                    amount="5.00",
                    idempotency_key=tx_id,
                )

        async def _money_committed():
            return await _state(factory, tx_id) == "COMMITTED"
    else:
        clearing_session = factory()
        service = ClearingService(clearing_session)
        original_policy = service._cycle_respects_auto_clearing

        async def _policy_then_hold(debts):
            respects = await original_policy(debts)
            if not paused.is_set():
                holder_pid.append(await _pid(service.session))
                paused.set()
                await release.wait()
            return respects

        monkeypatch.setattr(service, "_cycle_respects_auto_clearing", _policy_then_hold)

        async def _money():
            try:
                return await service.execute_clearing_with_amount(seed["cycle"])
            finally:
                await clearing_session.close()

        async def _money_committed():
            return await _clearing_transactions(factory, seed) == 1

    async def _admin_then_look():
        response = await _admin_call(client, admin_path, code)
        return response, await _money_committed()

    try:
        with caplog.at_level(logging.INFO):
            money_task = asyncio.create_task(_money())
            money_task.add_done_callback(lambda _t: completed.append("money"))
            await asyncio.wait_for(paused.wait(), timeout=20)
            assert await _advisory_modes(holder_pid[0], equivalent_id) == _MONEY_MODE[money], (
                f"premise: the parked {money} does not hold its equivalent lock ({_MONEY_MODE[money]})"
            )

            admin_task = asyncio.create_task(_admin_then_look())
            admin_task.add_done_callback(lambda _t: completed.append("admin"))
            if admin_path == "delete":
                # The active equivalent is refused before any lock: the DELETE answers while money is parked.
                await asyncio.wait_for(asyncio.shield(admin_task), timeout=20)
                assert not money_task.done(), "premise: the money was not parked while the DELETE answered"
                assert await _waiters_behind(holder_pid[0], timeout=0) == [], (
                    "something queued behind the parked money after the DELETE answered"
                )
            else:
                admin_pid = _assert_row_wait(
                    await _waiters_behind(holder_pid[0]), what=f"the admin {admin_path}", behind=f"the {money}"
                )
                assert await _advisory_modes(admin_pid, equivalent_id) == [], (
                    f"the admin {admin_path} holds the equivalent advisory lock: since T1909 it takes none"
                )
                assert not admin_task.done()

            release.set()
            outcomes = await asyncio.wait_for(
                asyncio.gather(money_task, admin_task, return_exceptions=True), timeout=60
            )

        money_outcome, admin_outcome = outcomes
        _no_deadlock(outcomes, caplog)
        assert not isinstance(admin_outcome, BaseException), repr(admin_outcome)
        response, money_committed_at_answer = admin_outcome
        if money == "payment":
            assert getattr(money_outcome, "status", None) == "COMMITTED", money_outcome
        else:
            assert money_outcome == _CYCLE_AMOUNT, money_outcome
        expected = {"patch": 200, "hold_clear": 409, "delete": 409}[admin_path]
        assert response.status_code == expected, response.text
        if admin_path == "hold_clear":
            assert response.json()["error"]["details"]["reason"] == "no_integrity_hold", response.text
        if admin_path == "delete":
            assert "Deactivate equivalent before delete" in response.text, response.text
            assert completed == ["admin", "money"], completed
            assert money_committed_at_answer is False
        else:
            # THE ORDER: the money's commit is visible when the admin answers (another session, read at the
            # answer); for the payment, task completion says the same.
            assert money_committed_at_answer is True, (
                f"the admin {admin_path} answered before the {money}'s commit was visible"
            )
            if money == "payment":
                assert completed == ["money", "admin"], completed
        await _no_advisory_lock_is_held(caplog)
    finally:
        release.set()
        await _finish(money_task, admin_task)
        PaymentRouter.invalidate_cache(code)


# ── admin first: the admin path holds its row change (or its usage read); money arrives ─────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("admin_path", ADMIN_PATHS)
@pytest.mark.parametrize("money", MONEY)
async def test_money_arriving_while_an_admin_path_holds_its_row(
    factory, admin_api, monkeypatch, caplog, money, admin_path  # noqa: F811 - fixtures
) -> None:
    """The admin path holds its row change uncommitted (PATCH: the UPDATE; hold clear: `FOR UPDATE` and the
    UPDATE) or, `DELETE`, stands after its usage count on an equivalent deactivated before; it holds no
    advisory lock (asserted). Money then arrives.

    QUEUES ON THE ADMIN'S ROW (PATCH against both; hold clear against the clearing): money queues with its
    `FOR SHARE` on the ADMIN'S transaction, already holding its equivalent lock (`ShareLock` /
    `ExclusiveLock` on the waiter: the order equivalent lock -> row), meets 40001 when the admin commits,
    and finishes on its retry with what the admin outcome implies: refused after a stop, run after a lifted
    hold.

    FINISHES WHILE THE ADMIN IS PARKED (`DELETE` against both; hold clear against the payment): the money
    meets a COMMITTED change of the row first - the deactivation before the DELETE, the hold before the
    clear, both committed after the admitted payment's snapshot - so its first `FOR SHARE` fails with 40001
    at once, and its retry re-checks on a fresh snapshot while the admin is still uncommitted: the stop,
    or the hold still in force, refuses it. Nothing the parked admin holds is waited for (nobody queues
    behind it). This is the T1544/T1546 "change first, money refused" outcome; the hold clear's "run after
    a lifted hold" is the clearing's schedule here, whose snapshot is taken after the hold.

    THE PAYMENT SIDE. A payment is one transaction per attempt through `PaymentService.pay()` - here the
    API entry, with a FRESH session per attempt from `factory`. (Not `create_payment_internal`: it lends
    ONE session to every attempt (`_borrowed_session`), and its retry's best-effort stop/hold pre-check was
    measured, 2026-09-25, to read the hold sometimes from the ORM state of the previous attempt and
    sometimes afresh - an outcome that decides whether this schedule queues, so it cannot be the stand.)
    The payment is ADMITTED (routing and the stop/hold pre-check pass while the equivalent is active and
    not held) and held at the entry of its binding phase (`PaymentService._bind_payment`), i.e. before its
    equivalent lock. Only then is the admin precondition committed (held with a later PASS; deactivated
    for DELETE) and the admin path started; then the payment is released. Setting the precondition before
    the payment started would make its pre-check refuse it before admission. An admitted payment refused
    later is stored `ABORTED`.

    RED if money read the row before its equivalent lock (the waiter would hold no advisory lock), if it
    read the stop without `FOR SHARE` (its stale snapshot runs money after the stop), or if an admin path
    took the advisory lock (it would hold one, and money would queue on `advisory`).
    """
    client, gate = admin_api
    seed = await _seed_interlock_case()
    code, equivalent_id = seed["equivalent_code"], seed["equivalent_id"]
    tx_id = str(uuid.uuid4())
    completed: list[str] = []
    money_task = admin_task = None
    delete_paused, delete_release = asyncio.Event(), asyncio.Event()
    delete_pid: list[int] = []
    bind_reached, bind_release = asyncio.Event(), asyncio.Event()
    bind_hits: list[str] = []
    payment_pid: list[int] = []

    if money == "payment":
        original_bind = PaymentService._bind_payment

        async def _bind_after_barrier(self, bound_tx_id, routes, bound_equivalent_id):
            # The first attempt of THIS payment waits here, admitted and before its equivalent lock; a
            # retry of the same payment passes straight through.
            if bound_tx_id == tx_id and not bind_hits:
                bind_hits.append(bound_tx_id)
                payment_pid.append(await _pid(self.session))
                bind_reached.set()
                await bind_release.wait()
            return await original_bind(self, bound_tx_id, routes, bound_equivalent_id)

        monkeypatch.setattr(PaymentService, "_bind_payment", _bind_after_barrier)

    async def _admin_precondition() -> None:
        if admin_path == "hold_clear":
            await _set_held_with_a_later_pass(factory, equivalent_id)
        if admin_path == "delete":
            async with factory() as session:
                await session.execute(
                    update(Equivalent).where(Equivalent.id == equivalent_id).values(is_active=False)
                )
                await session.commit()

    if admin_path == "delete":
        original_counts = admin_module._equivalent_usage_counts

        async def _counts_then_hold(db, *, equivalent_id):
            counts = await original_counts(db, equivalent_id=equivalent_id)
            delete_pid.append(await _pid(db))
            delete_paused.set()
            await delete_release.wait()
            return counts

        monkeypatch.setattr(admin_module, "_equivalent_usage_counts", _counts_then_hold)
    else:
        gate.armed = True

    async def _money():
        if money == "payment":
            return await PaymentService.pay(
                factory,
                seed["participant_ids"][0],
                PaymentCreateRequest(
                    tx_id=tx_id,
                    to=seed["participant_pids"][1],
                    equivalent=code,
                    amount="5.00",
                    signature="__internal__",
                ),
                idempotency_key=tx_id,
                require_signature=False,
            )
        async with factory() as session:
            return await ClearingService(session).execute_clearing_with_amount(seed["cycle"])

    # Which schedules finish money while the admin is still parked (see the docstring).
    money_finishes_first = admin_path == "delete" or (admin_path == "hold_clear" and money == "payment")

    try:
        with caplog.at_level(logging.INFO):
            if money == "payment":
                # The payment is admitted first and held before its equivalent lock (see the docstring).
                money_task = asyncio.create_task(_money())
                money_task.add_done_callback(lambda _t: completed.append("money"))
                await asyncio.wait_for(bind_reached.wait(), timeout=20)
                assert await _state(factory, tx_id) is None, "premise: the held payment is visible"
                assert await _advisory_modes(payment_pid[0], equivalent_id) == [], (
                    "premise: the payment held at its binding entry already holds the equivalent lock"
                )
            await _admin_precondition()
            admin_task = asyncio.create_task(_admin_call(client, admin_path, code))
            admin_task.add_done_callback(lambda _t: completed.append("admin"))
            if admin_path == "delete":
                await asyncio.wait_for(delete_paused.wait(), timeout=20)
                admin_pid = delete_pid[0]
            else:
                await asyncio.wait_for(gate.reached.wait(), timeout=20)
                admin_pid = gate.pid
            assert admin_pid is not None
            assert await _advisory_modes(admin_pid, equivalent_id) == [], (
                f"the admin {admin_path} holds the equivalent advisory lock: since T1909 it takes none"
            )

            if money == "payment":
                bind_release.set()
            else:
                money_task = asyncio.create_task(_money())
                money_task.add_done_callback(lambda _t: completed.append("money"))

            if money_finishes_first:
                # Money meets the committed change first and is refused on its re-check; nothing the parked
                # admin holds is waited for.
                await asyncio.wait_for(asyncio.wait([money_task]), timeout=30)
                assert money_task.done(), f"the money did not finish while the admin {admin_path} was parked"
                assert not admin_task.done()
                assert await _waiters_behind(admin_pid, timeout=0) == [], (
                    f"something queued behind the parked admin {admin_path}"
                )
            else:
                money_pid = _assert_row_wait(
                    await _waiters_behind(admin_pid), what=f"the {money}", behind=f"the admin {admin_path}"
                )
                if money == "payment":
                    assert money_pid == payment_pid[0], (money_pid, payment_pid)
                assert await _advisory_modes(money_pid, equivalent_id) == _MONEY_MODE[money], (
                    f"the {money} queued on the admin's row without holding its equivalent lock "
                    f"({_MONEY_MODE[money]}): it touched the row before the lock"
                )
                assert not money_task.done()

            gate.release.set()
            delete_release.set()
            outcomes = await asyncio.wait_for(
                asyncio.gather(admin_task, money_task, return_exceptions=True), timeout=60
            )

        response, money_outcome = outcomes
        _no_deadlock(outcomes, caplog)
        expected_order = ["money", "admin"] if money_finishes_first else ["admin", "money"]
        assert completed == expected_order, completed
        expected_admin = {"patch": 200, "hold_clear": 200, "delete": 409}[admin_path]
        assert response.status_code == expected_admin, response.text
        if admin_path == "delete":
            assert response.json()["error"]["details"]["trustlines"] > 0, response.text

        if admin_path == "hold_clear":
            if money == "payment":
                # The hold was still in force when the payment re-checked: refused by it, stored ABORTED;
                # the clear answered after, and nothing of the payment moved.
                assert isinstance(money_outcome, ConflictException), repr(money_outcome)
                assert money_outcome.details.get("reason") == MoneyBoundary.EQUIVALENT_INTEGRITY_HOLD_REASON, (
                    money_outcome.details
                )
                assert await _state(factory, tx_id) == "ABORTED"
            else:
                # The hold is lifted before the clearing's retry runs: the clearing runs.
                assert money_outcome == _CYCLE_AMOUNT, money_outcome
            assert (await _equivalent_row(factory, equivalent_id)).integrity_hold_result_id is None
        else:
            # The equivalent is stopped before money runs: money is refused by the stop.
            assert isinstance(money_outcome, ConflictException), repr(money_outcome)
            assert money_outcome.details.get("reason") == MoneyBoundary.EQUIVALENT_INACTIVE_REASON, (
                money_outcome.details
            )
            if money == "payment":
                assert await _state(factory, tx_id) == "ABORTED"
        if admin_path != "delete" or money == "payment":
            # The waiter's snapshot predates the admin change it met: the outcome came through 40001 and
            # the retry owner (a clearing started after a committed deactivation reads it on its first try).
            assert _retries_on_40001(caplog, _RETRY_EVENT[money]), (
                f"premise: the {money} did not meet 40001 on the changed row and retry"
            )
        await _no_advisory_lock_is_held(caplog)
        if money == "payment":
            assert bind_hits == [tx_id], f"premise: the payment's barrier was not reached once: {bind_hits}"
    finally:
        gate.release.set()
        delete_release.set()
        bind_release.set()
        await _finish(admin_task, money_task)
        PaymentRouter.invalidate_cache(code)
