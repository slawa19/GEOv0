"""019 `T1903`: one lock order - the equivalent owner lock BEFORE any row - raced for real, three admin paths apart.

THE CONTRACT (`app/core/money_boundary.py`, module docstring; 019 spec, "Один порядок локов"). Payment,
clearing and admin all take the equivalent owner lock before they lock or change a row of that
equivalent. The reverse order in any one of them is a reachable deadlock: a payment holds the owner lock
and `FOR SHARE` on the equivalent row, an admin path that updated the row first then waits for the owner
lock, and each waits on the other. A deadlock retry does not replace the order.

WHAT EACH RACE OBSERVES, in `pg_locks` and `pg_stat_activity`, while one side holds the owner lock
paused at a barrier AFTER it has read/locked its rows:

* the other side waits on an ADVISORY lock (`wait_event = 'advisory'`), never on a row or a transaction,
  and `pg_blocking_pids` names exactly the paused holder - so it has not touched the row yet;
* the holder waits on nothing;
* when released, the holder completes first, the waiter completes after it with the outcome its path owes,
  no `40P01` reaches either outcome or the log, and no advisory lock is left behind (session-level
  release included, for clearing's pinned connection).

THE THREE ADMIN PATHS, raced separately against payment and against clearing, in both orders:
`PATCH` deactivation (`admin.py`, owner -> UPDATE), the integrity-hold clear (owner -> `FOR UPDATE`
-> UPDATE), and the equivalent `DELETE` (owner -> usage count -> delete). A DELETE that could delete
would need an unused equivalent, where no money can run; so its admin-first race pauses it after its
authoritative read under the lock and it answers the usage `409` - the order is what is raced.

THE STAND. The P1 SERIALIZABLE engine on a mode-B clone (`factory`), the real admin routes through
`admin_api` (its gate holds one request at its session commit), the interlock world for both money
operations (a three-edge cycle for clearing, the A -> B line for a payment). No sleeps on the clock:
waits are observed in `pg_locks`.

WHAT IT DOES NOT PROVE: that no OTHER interleaving deadlocks; that the order holds for writers not
listed here (inject, reconciliation - they take the same `acquire_staged_equivalent_owner_locks` entry,
covered by their own suites); anything at stage 5, when the owner lock is to be replaced.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import insert, select, text, update

from app.api.v1 import admin as admin_module
from app.config import settings
from app.core.clearing.service import ClearingService
from app.core.ledger.reconciliation import PASSED
from app.core.money_boundary import _EQUIVALENT_OWNER_LOCK_NAMESPACE, MoneyBoundary
from app.core.payments.router import PaymentRouter
from app.core.payments.service import PaymentService
from app.db.models.equivalent import Equivalent
from app.db.models.transaction import Transaction
from app.db.reconciliation_tables import debt_reconciliation_results
from app.utils.exceptions import ConflictException
from tests.integration.p019_interlock_support import (
    _no_advisory_lock_is_held,
    _seed_interlock_case,
)
from tests.integration.test_p015_p1_money_replay_postgres import factory  # noqa: F401 - fixture
from tests.integration.test_p015_t1544_operator_stop_races_postgres import (  # noqa: F401 - fixture
    ADMIN,
    admin_api,
)
from tests.unit.test_p015_step5c_reaction_and_hold import hold_directly

# MODE B: every commit of this module lands in a clone dropped after the test.
from tests.tier_on_a_clone import tier_sessions_on_a_clone  # noqa: E402,F401 - autouse fixture

ADMIN_PATHS = ("patch", "hold_clear", "delete")
MONEY = ("payment", "clearing")
_CYCLE_AMOUNT = Decimal("30.00000000")

_QUEUE_SQL = text(
    """
    SELECT l.pid, l.granted, a.wait_event_type, a.wait_event, pg_blocking_pids(l.pid) AS blockers
    FROM pg_locks l JOIN pg_stat_activity a ON a.pid = l.pid
    WHERE l.locktype = 'advisory'
      AND l.database = (SELECT oid FROM pg_database WHERE datname = current_database())
      AND l.classid = :namespace AND l.objid = :objid AND l.objsubid = 2
    ORDER BY l.granted DESC, l.pid
    """
)


@pytest.fixture(autouse=True)
def _barrier_budgets(monkeypatch):
    # The barriers hold the owner lock for a moment; the default lock budget is seconds and is not
    # what these races are about. A payment held before its owner lock waits inside its binding phase,
    # which runs under `PREPARE_TIMEOUT_SECONDS`.
    monkeypatch.setattr(settings, "PREPARE_TIMEOUT_SECONDS", 30)
    monkeypatch.setattr(settings, "COMMIT_TIMEOUT_SECONDS", 30)
    monkeypatch.setattr(settings, "PAYMENT_TOTAL_TIMEOUT_SECONDS", 60)


@dataclass
class _Queue:
    holder: int
    waiter: int
    waiter_event: tuple[str | None, str | None]
    waiter_blockers: list[int]
    holder_event: tuple[str | None, str | None]


async def _owner_queue(equivalent_id: uuid.UUID, *, timeout: float = 10.0) -> _Queue | None:
    """The owner lock's queue once it has one holder and one waiter, observed on its own connection."""

    from tests.conftest import TestingSessionLocal

    params = {
        "namespace": _EQUIVALENT_OWNER_LOCK_NAMESPACE,
        "objid": MoneyBoundary._equivalent_owner_lock_key(equivalent_id) & 0xFFFFFFFF,
    }
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    async with TestingSessionLocal() as observer:
        while True:
            rows = (await observer.execute(_QUEUE_SQL, params)).all()
            await observer.rollback()
            holders = [r for r in rows if r.granted]
            waiters = [r for r in rows if not r.granted]
            if holders and waiters:
                assert len(holders) == 1 and len(waiters) == 1, rows
                holder, waiter = holders[0], waiters[0]
                return _Queue(
                    holder=int(holder.pid),
                    waiter=int(waiter.pid),
                    waiter_event=(waiter.wait_event_type, waiter.wait_event),
                    waiter_blockers=[int(p) for p in waiter.blockers],
                    holder_event=(holder.wait_event_type, holder.wait_event),
                )
            if loop.time() > deadline:
                return None
            await asyncio.sleep(0.02)


async def _owner_holder(equivalent_id: uuid.UUID, *, timeout: float = 10.0) -> int | None:
    from tests.conftest import TestingSessionLocal

    params = {
        "namespace": _EQUIVALENT_OWNER_LOCK_NAMESPACE,
        "objid": MoneyBoundary._equivalent_owner_lock_key(equivalent_id) & 0xFFFFFFFF,
    }
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    async with TestingSessionLocal() as observer:
        while True:
            rows = (await observer.execute(_QUEUE_SQL, params)).all()
            await observer.rollback()
            granted = [int(r.pid) for r in rows if r.granted]
            if granted:
                return granted[0]
            if loop.time() > deadline:
                return None
            await asyncio.sleep(0.02)


def _assert_waits_behind(queue: _Queue | None, *, holder: int | None, what: str) -> None:
    assert queue is not None, f"premise: {what} never queued on the owner lock behind the holder"
    if holder is not None:
        assert queue.holder == holder, (queue, holder)
    assert queue.waiter != queue.holder, queue
    assert queue.waiter_event == ("Lock", "advisory"), (
        f"{what} waits on {queue.waiter_event}, not on the owner lock: it touched a row before the "
        f"owner lock - the inverted order the contract forbids"
    )
    assert queue.waiter_blockers == [queue.holder], (
        f"{what} is blocked by {queue.waiter_blockers}, expected only the owner-lock holder {queue.holder}"
    )
    assert queue.holder_event[0] != "Lock", f"the holder itself waits on {queue.holder_event}"


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


# ── money first: the money operation holds the owner lock and its rows; the admin path arrives ────────


@pytest.mark.asyncio
@pytest.mark.parametrize("admin_path", ADMIN_PATHS)
@pytest.mark.parametrize("money", MONEY)
async def test_an_admin_path_arriving_while_money_holds_its_rows_waits_on_the_owner_lock(
    factory, admin_api, monkeypatch, caplog, money, admin_path  # noqa: F811 - fixtures
) -> None:
    """Money has taken the owner lock and read/locked its rows (payment: the commit's `FOR SHARE` on the
    equivalent; clearing: its stop read and debt rows); the admin request must queue on the OWNER lock.

    RED if the admin path touches the equivalent row before the owner lock: it then waits on the payment's
    row lock (`wait_event` is not `advisory`), and with a payment still to lock the owner that is the
    deadlock of the inverted order.
    """
    client, _gate = admin_api
    seed = await _seed_interlock_case()
    code, equivalent_id = seed["equivalent_code"], seed["equivalent_id"]
    paused, release = asyncio.Event(), asyncio.Event()
    holder_pid: list[int] = []
    completed: list[str] = []
    money_task = admin_task = None

    if money == "payment":
        original_guard = MoneyBoundary.refuse_inactive_equivalents

        async def _guard_then_hold(self, equivalent_ids, *, row_lock):
            await original_guard(self, equivalent_ids, row_lock=row_lock)
            if row_lock and not paused.is_set():
                holder_pid.append(int(await self.session.scalar(text("SELECT pg_backend_pid()"))))
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
                    idempotency_key=str(uuid.uuid4()),
                )
    else:
        clearing_session = factory()
        service = ClearingService(clearing_session)
        original_pairs = service._locked_pairs_for_equivalent

        async def _pairs_then_hold(current_equivalent_id):
            pairs = await original_pairs(current_equivalent_id)
            holder_pid.append(int(await service.session.scalar(text("SELECT pg_backend_pid()"))))
            paused.set()
            await release.wait()
            return pairs

        monkeypatch.setattr(service, "_locked_pairs_for_equivalent", _pairs_then_hold)

        async def _money():
            try:
                return await service.execute_clearing_with_amount(seed["cycle"])
            finally:
                await clearing_session.close()

    try:
        with caplog.at_level(logging.INFO):
            money_task = asyncio.create_task(_money())
            money_task.add_done_callback(lambda _t: completed.append("money"))
            await asyncio.wait_for(paused.wait(), timeout=20)

            admin_task = asyncio.create_task(_admin_call(client, admin_path, code))
            admin_task.add_done_callback(lambda _t: completed.append("admin"))
            queue = await _owner_queue(equivalent_id)
            _assert_waits_behind(queue, holder=holder_pid[0], what=f"the admin {admin_path}")
            assert not admin_task.done()

            release.set()
            outcomes = await asyncio.wait_for(
                asyncio.gather(money_task, admin_task, return_exceptions=True), timeout=60
            )

        money_outcome, response = outcomes
        _no_deadlock(outcomes, caplog)
        assert completed == ["money", "admin"], completed
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
        await _no_advisory_lock_is_held(caplog)
    finally:
        release.set()
        await _finish(money_task, admin_task)
        PaymentRouter.invalidate_cache(code)


# ── admin first: the admin path holds the owner lock and its row; money arrives ─────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("admin_path", ADMIN_PATHS)
@pytest.mark.parametrize("money", MONEY)
async def test_money_arriving_while_an_admin_path_holds_its_row_waits_on_the_owner_lock(
    factory, admin_api, monkeypatch, caplog, money, admin_path  # noqa: F811 - fixtures
) -> None:
    """The admin path has the owner lock and has changed or read its row under it (PATCH: the UPDATE;
    hold clear: `FOR UPDATE` and the UPDATE; DELETE: the usage count); money must queue on the OWNER lock,
    then finish with what that admin outcome implies: refused after a stop, run after a lifted hold.

    THE PAYMENT SIDE SINCE 019 STAGE 4 (`T1906`). Until then a payment was prepared (durable `PREPARED`)
    before the race and `PaymentEngine.commit` was the waiter. A payment is now one transaction through
    `PaymentService`, so the real payment runs: it is ADMITTED (routing and the stop/hold pre-check pass
    while the equivalent is active and not held) and held at the entry of its binding phase
    (`PaymentService._bind_payment`), i.e. before its owner lock. Only then is the admin precondition
    committed (held with a later PASS; deactivated for DELETE) and the admin path started; once the admin
    holds the owner lock the payment is released into it and must queue. Setting the precondition before
    the payment started would make its pre-check refuse it before admission, and it would never reach the
    lock - the premise this race needs. After the admin commits, the payment's `FOR SHARE` on the changed
    equivalent row meets a serialization failure and `pay()` retries it on a fresh snapshot, where the stop
    refuses the admitted payment (stored `ABORTED`) or the lifted hold lets it commit.

    RED if money locks the equivalent row before the owner lock: it waits on the admin's row
    (`wait_event` is not `advisory`) while holding nothing the admin needs - and a money path that
    reads the row first and then asks for the owner lock is the deadlock of the inverted order.
    """
    client, gate = admin_api
    seed = await _seed_interlock_case()
    code, equivalent_id = seed["equivalent_code"], seed["equivalent_id"]
    tx_id = str(uuid.uuid4())
    completed: list[str] = []
    money_task = admin_task = None
    delete_paused, delete_release = asyncio.Event(), asyncio.Event()
    bind_reached, bind_release = asyncio.Event(), asyncio.Event()
    bind_hits: list[str] = []

    if money == "payment":
        original_bind = PaymentService._bind_payment

        async def _bind_after_barrier(self, bound_tx_id, routes, bound_equivalent_id):
            # The first attempt of THIS payment waits here, admitted and before its owner lock; a retry
            # of the same payment passes straight through.
            if bound_tx_id == tx_id and not bind_hits:
                bind_hits.append(bound_tx_id)
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
            delete_paused.set()
            await delete_release.wait()
            return counts

        monkeypatch.setattr(admin_module, "_equivalent_usage_counts", _counts_then_hold)
    else:
        gate.armed = True

    async def _money():
        if money == "payment":
            async with factory() as session:
                return await PaymentService(session).create_payment_internal(
                    seed["participant_ids"][0],
                    to_pid=seed["participant_pids"][1],
                    equivalent=code,
                    amount="5.00",
                    idempotency_key=tx_id,
                )
        async with factory() as session:
            return await ClearingService(session).execute_clearing_with_amount(seed["cycle"])

    try:
        with caplog.at_level(logging.INFO):
            if money == "payment":
                # The payment is admitted first and held before its owner lock (see the docstring).
                money_task = asyncio.create_task(_money())
                money_task.add_done_callback(lambda _t: completed.append("money"))
                await asyncio.wait_for(bind_reached.wait(), timeout=20)
                assert await _state(factory, tx_id) is None, "premise: the held payment is visible"
            await _admin_precondition()
            admin_task = asyncio.create_task(_admin_call(client, admin_path, code))
            admin_task.add_done_callback(lambda _t: completed.append("admin"))
            if admin_path == "delete":
                await asyncio.wait_for(delete_paused.wait(), timeout=20)
            else:
                await asyncio.wait_for(gate.reached.wait(), timeout=20)
            admin_pid = await _owner_holder(equivalent_id)
            assert admin_pid is not None, "premise: the admin path does not hold the owner lock"

            if money == "payment":
                bind_release.set()
            else:
                money_task = asyncio.create_task(_money())
                money_task.add_done_callback(lambda _t: completed.append("money"))
            queue = await _owner_queue(equivalent_id)
            _assert_waits_behind(queue, holder=admin_pid, what=f"the {money}")
            assert not money_task.done()

            gate.release.set()
            delete_release.set()
            outcomes = await asyncio.wait_for(
                asyncio.gather(admin_task, money_task, return_exceptions=True), timeout=60
            )

        response, money_outcome = outcomes
        _no_deadlock(outcomes, caplog)
        assert completed == ["admin", "money"], completed
        expected_admin = {"patch": 200, "hold_clear": 200, "delete": 409}[admin_path]
        assert response.status_code == expected_admin, response.text
        if admin_path == "delete":
            assert response.json()["error"]["details"]["trustlines"] > 0, response.text

        if admin_path == "hold_clear":
            # The hold is lifted before money runs: money runs.
            if money == "payment":
                assert getattr(money_outcome, "status", None) == "COMMITTED", money_outcome
                assert await _state(factory, tx_id) == "COMMITTED"
            else:
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
        await _no_advisory_lock_is_held(caplog)
        if money == "payment":
            assert bind_hits == [tx_id], f"premise: the payment's barrier was not reached once: {bind_hits}"
    finally:
        gate.release.set()
        delete_release.set()
        bind_release.set()
        await _finish(admin_task, money_task)
        PaymentRouter.invalidate_cache(code)
