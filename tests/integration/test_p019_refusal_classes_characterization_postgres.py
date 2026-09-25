"""Programme 019, `T1902`: the table "refusal class -> row in the database -> answer to a replay",
measured separately for the API path and the staged (simulator) path, on the current tree.

WHY A TABLE. FORK-4 of the spec keeps a definitive refusal durable (`ABORTED`, the same refusal on a
replay of the signed `tx_id`) and asks `T1902` which classes are durable TODAY, per path, before any
code moves. This module is that measurement. It is CHARACTERIZATION: green on the current tree, and the
stages that change a row of it change the expected table here, visibly.

Each row is produced by a real schedule, nothing is injected into the driver:

API path (`POST /payments`; since stage 3, `T1904`, ONE transaction per attempt of `PaymentService.pay`,
which retries a retryable conflict on a fresh snapshot):
* `routing_before_new` - the amount exceeds what is left; the router refuses before the insert.
* `recheck_after_new` - the creditor lowers the line (a committed UPDATE) after routing and the insert,
  before prepare. Before stage 3 prepare ran in its own transaction, saw it and refused (`E002`,
  stored `ABORTED`). Since stage 3 the payment is one SERIALIZABLE snapshot taken before the UPDATE:
  prepare re-checks capacity against THAT snapshot and the payment commits - the serial order
  "payment, then the lowering" - so this is no longer a refusal. The row stays in the table to show it.
* `stop_before_new` / `hold_before_new` - the operator stop / integrity hold is in force when the
  request arrives; the service pre-check refuses before the insert.
* `stop_at_commit` - the real `PATCH` deactivating the equivalent is started after `prepare`, before
  the commit phase. Before stage 3 it landed there (the owner lock was released with the durable
  `PREPARED`) and the commit guard refused. Since stage 3 the payment holds the owner lock from
  `prepare` to its one commit, so the `PATCH` QUEUES on it (asserted: an advisory waiter exists while
  the payment is in flight), the payment commits, and the stop applies after it. This is the spec's
  "third behaviour change" (the disappearing "stop at commit" schedule), established by the
  implemented order.
* `hold_at_commit` - the hold is written after `prepare` by a writer that takes no owner lock. The
  commit guard's `FOR SHARE` meets a row changed behind the payment's snapshot: `40001`, `pay()`
  retries the whole attempt, and the retry's pre-check refuses the hold BEFORE its insert. The request
  was ADMITTED by the first attempt (it reached the payment operation), and `pay()` remembers that for
  the same identity (spec, "Допуск", FORK-5; `T1905`): the refusal is definitive - stored `ABORTED` with
  the hold's error, and the replay after the hold is lifted answers it. Between `T1904` and `T1905`
  this row was "no row, the replay executes"; before stage 3 it was a stored `ABORTED` from the commit
  guard itself.
* `timeout_confirmed_rollback` - the commit guard's `FOR SHARE` waits on the row lock of an operator's
  slow `PATCH` of the equivalent's description; the payment times out, `pay()` rolls the attempt back
  and only then records `ABORTED` in a short transaction of its own.
Each replay is made after the cause is lifted, so an `ABORTED` answer can only be the stored one, and a
`COMMITTED` answer shows the `tx_id` was executed afresh.

Staged path (SERIALIZABLE tick, `real_payments_executor.py:421`):
* `stop_before_tick` / `hold_before_tick` - the service pre-check refuses the planned payment before
  `NEW`: the executor counts a rejection, `tx.failed` is published, no row; the same `tx_id` executes
  afresh once the cause is lifted.
The staged rows AFTER `NEW` are in `test_p019_staged_refusal_is_durable_postgres.py` (and the stop that
races the tick is `test_p015_t1544_operator_stop_races_postgres.py`: `40001`, replay, pre-check refusal,
no row). The retryable conflict is `test_p019_retryable_conflict_is_not_stored_aborted_postgres.py`.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import insert, text, update
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.core.money_boundary import MoneyBoundary
from app.core.payments.service import PaymentService, _constraint_name
from app.db.models.equivalent import Equivalent
from app.db.models.transaction import Transaction
from app.db.models.trustline import TrustLine
from tests.integration.p019_stand import (  # noqa: F401 - `api` and `factory` are fixtures
    ADMIN,
    ApiWorld,
    api,
    build_api_world,
    debts,
    factory,
    finish,
    payment_body,
    set_integrity_hold,
    tx_row,
    with_session_hook,
)
from tests.integration.test_p015_p1_money_replay_postgres import (
    _Sse,
    _forget_the_route_cache,
    _install,
    _run_record,
    _runner,
    _scenario,
    _seed,
)


def _answer(resp) -> tuple[Any, ...]:
    payload = resp.json()
    if resp.status_code == 200:
        return (200, payload["status"], (payload.get("error") or {}).get("code"))
    error = payload["error"]
    return (resp.status_code, error["code"], (error.get("details") or {}).get("reason"))


async def _set_limit(factory, world: ApiWorld, limit: str) -> None:  # noqa: F811
    async with factory() as s:
        await s.execute(
            update(TrustLine)
            .where(
                TrustLine.from_participant_id == world.ids[world.bob["pid"]],
                TrustLine.to_participant_id == world.ids[world.alice["pid"]],
                TrustLine.equivalent_id == world.equivalent_id,
            )
            .values(limit=Decimal(limit))
        )
        await s.commit()


async def _set_active(api, world: ApiWorld, active: bool) -> None:
    resp = await api.patch(
        f"/api/v1/admin/equivalents/{world.code}",
        json={"is_active": active, "reason": "p019 t1902"},
        headers=ADMIN,
    )
    assert resp.status_code == 200, resp.text


async def _clear_hold(factory, world: ApiWorld) -> None:  # noqa: F811
    async with factory() as s:
        await s.execute(
            update(Equivalent)
            .where(Equivalent.id == world.equivalent_id)
            .values(integrity_hold_result_id=None)
        )
        await s.commit()


def _hook_before_engine_call(
    monkeypatch, factory, method: str, tx_id: str, action  # noqa: F811
) -> list[str | None]:
    """Run `action()` once, right before the payment's `method` phase for `tx_id`; record the durable
    state of the payment at that moment as another transaction sees it.

    Since 019 stage 4 the phases are the direct execution's: `prepare` is the binding phase
    (`PaymentService._bind_payment`, first argument the tx id), `commit` the money phase
    (`PaymentService._apply_payment`, first argument the declaration) - where the engine's `prepare`
    and `commit` were entered before."""

    target = {"prepare": "_bind_payment", "commit": "_apply_payment"}[method]
    original = getattr(PaymentService, target)
    fired: list[str | None] = []

    async def hooked(self, first, *args, **kwargs):
        if getattr(first, "tx_id", first) == tx_id and not fired:
            row = await tx_row(factory, tx_id)
            fired.append(None if row is None else row[0])
            await action()
        return await original(self, first, *args, **kwargs)

    monkeypatch.setattr(PaymentService, target, hooked)
    return fired


class _CommitGate:
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


async def _advisory_waiter_exists(factory, *, timeout: float = 10.0) -> bool:  # noqa: F811
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    async with factory() as observer:
        while True:
            waiting = await observer.scalar(
                text("SELECT EXISTS (SELECT 1 FROM pg_locks WHERE NOT granted AND locktype = 'advisory')")
            )
            await observer.rollback()
            if waiting:
                return True
            if loop.time() > deadline:
                return False
            await asyncio.sleep(0.02)


async def _row_lock_waiter_exists(factory, *, timeout: float = 10.0) -> bool:  # noqa: F811
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    async with factory() as observer:
        while True:
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


@pytest.mark.asyncio
async def test_the_api_path_refusal_table(api, factory, monkeypatch, caplog) -> None:  # noqa: F811
    people = await build_api_world(api, factory)
    observed: dict[str, dict[str, Any]] = {}
    premises: dict[str, Any] = {}

    async def run_class(
        name: str, world: ApiWorld, body: dict, *, cause, lift, amount: str, refused: bool = True
    ) -> None:
        before = await debts(factory, world)
        first = await cause(body)
        stored = await tx_row(factory, body["tx_id"])
        await lift()
        mid = await debts(factory, world)
        replay = await api.post("/api/v1/payments", json=body, headers=world.alice["headers"])
        after = await debts(factory, world)
        key = (world.alice["pid"], world.bob["pid"])
        moved = after.get(key, Decimal("0")) - mid.get(key, Decimal("0"))
        if refused:
            assert mid == before, (name, before, mid)  # the refusal itself moved nothing
        else:
            first_moved = mid.get(key, Decimal("0")) - before.get(key, Decimal("0"))
            assert first_moved == Decimal(amount), (name, before, mid)  # the payment itself paid
        observed[name] = {
            "first": _answer(first),
            "stored": None if stored is None else (stored[0], (stored[1] or {}).get("code")),
            "replay": _answer(replay),
            "replay_moved": moved == Decimal(amount),
        }
        assert moved in (Decimal("0"), Decimal(amount)), (name, moved)

    async def post(world: ApiWorld, body: dict):
        return await api.post("/api/v1/payments", json=body, headers=world.alice["headers"])

    # routing_before_new ──────────────────────────────────────────────────────────────────────
    w = await build_api_world(api, factory, people=people)
    used = await post(w, payment_body(w, w.alice, w.bob, "60.00"))
    assert used.json()["status"] == "COMMITTED", used.text
    body = payment_body(w, w.alice, w.bob, "60.00")
    await run_class(
        "routing_before_new", w, body,
        cause=lambda b, w=w: post(w, b),
        lift=lambda w=w: _set_limit(factory, w, "200.00"),
        amount="60.00",
    )

    # recheck_after_new ───────────────────────────────────────────────────────────────────────
    w = await build_api_world(api, factory, people=people)
    body = payment_body(w, w.alice, w.bob, "10.00")
    with monkeypatch.context() as m:
        fired = _hook_before_engine_call(
            m, factory, "prepare", body["tx_id"], lambda w=w: _set_limit(factory, w, "0.00")
        )
        await run_class(
            "recheck_after_new", w, body,
            cause=lambda b, w=w: post(w, b),
            lift=lambda w=w: _set_limit(factory, w, "100.00"),
            amount="10.00",
            refused=False,
        )
    premises["recheck_after_new"] = fired

    # stop_before_new ─────────────────────────────────────────────────────────────────────────
    w = await build_api_world(api, factory, people=people)
    body = payment_body(w, w.alice, w.bob, "10.00")
    await _set_active(api, w, False)
    await run_class(
        "stop_before_new", w, body,
        cause=lambda b, w=w: post(w, b), lift=lambda w=w: _set_active(api, w, True), amount="10.00",
    )

    # stop_at_commit ──────────────────────────────────────────────────────────────────────────
    # The PATCH is started from the hook and NOT awaited there: since stage 3 it queues on the owner
    # lock this payment holds until its commit, and awaiting it inside the payment would deadlock the
    # stand, not the application.
    w = await build_api_world(api, factory, people=people)
    body = payment_body(w, w.alice, w.bob, "10.00")
    stop_patch: list[asyncio.Task] = []

    async def start_the_stop(w=w) -> None:
        stop_patch.append(asyncio.create_task(_set_active(api, w, False)))
        premises["stop_at_commit_queued"] = await _advisory_waiter_exists(factory)

    async def pay_then_let_the_stop_land(b, w=w):
        resp = await post(w, b)
        await asyncio.wait_for(stop_patch[0], timeout=20)
        return resp

    try:
        with monkeypatch.context() as m:
            fired = _hook_before_engine_call(m, factory, "commit", body["tx_id"], start_the_stop)
            await run_class(
                "stop_at_commit", w, body,
                cause=pay_then_let_the_stop_land,
                lift=lambda w=w: _set_active(api, w, True),
                amount="10.00",
                refused=False,
            )
    finally:
        for task in stop_patch:
            await finish(task)
    premises["stop_at_commit"] = fired

    # hold_before_new ─────────────────────────────────────────────────────────────────────────
    w = await build_api_world(api, factory, people=people)
    body = payment_body(w, w.alice, w.bob, "10.00")
    await set_integrity_hold(factory, w.equivalent_id)
    await run_class(
        "hold_before_new", w, body,
        cause=lambda b, w=w: post(w, b), lift=lambda w=w: _clear_hold(factory, w), amount="10.00",
    )

    # hold_at_commit ──────────────────────────────────────────────────────────────────────────
    w = await build_api_world(api, factory, people=people)
    body = payment_body(w, w.alice, w.bob, "10.00")
    with monkeypatch.context() as m:
        fired = _hook_before_engine_call(
            m, factory, "commit", body["tx_id"], lambda w=w: set_integrity_hold(factory, w.equivalent_id)
        )
        with caplog.at_level(logging.WARNING, logger="app.core.payments.service"):
            caplog.clear()
            await run_class(
                "hold_at_commit", w, body,
                cause=lambda b, w=w: post(w, b), lift=lambda w=w: _clear_hold(factory, w), amount="10.00",
            )
            premises["hold_at_commit_retried"] = [
                r.getMessage().split(" pgcode=")[1].split(" ")[0]
                for r in caplog.records
                if "event=payment.attempt_retry " in r.getMessage()
            ]
    premises["hold_at_commit"] = fired

    # timeout_confirmed_rollback ──────────────────────────────────────────────────────────────
    w = await build_api_world(api, factory, people=people)
    body = payment_body(w, w.alice, w.bob, "10.00")
    gate = _CommitGate()
    patch_task = None

    async def slow_patch_then_pay(b, w=w):
        nonlocal patch_task
        with with_session_hook(gate):
            patch_task = asyncio.create_task(
                api.patch(
                    f"/api/v1/admin/equivalents/{w.code}",
                    json={"description": "p019 slow operator edit", "reason": "p019 t1902"},
                    headers=ADMIN,
                )
            )
        await asyncio.wait_for(gate.reached.wait(), timeout=20)
        pay = asyncio.create_task(post(w, b))
        premises["timeout_confirmed_rollback"] = await _row_lock_waiter_exists(factory)
        return await asyncio.wait_for(pay, timeout=30)

    async def release_patch():
        gate.release.set()
        resp = await asyncio.wait_for(patch_task, timeout=20)
        assert resp.status_code == 200, resp.text

    try:
        with monkeypatch.context() as m:
            m.setattr(settings, "COMMIT_TIMEOUT_SECONDS", 0.5)
            await run_class(
                "timeout_confirmed_rollback", w, body,
                cause=slow_patch_then_pay, lift=release_patch, amount="10.00",
            )
    finally:
        gate.release.set()
        await finish(patch_task)

    # ── the uniqueness a same-tx_id race meets first (spec, "Идентичность tx_id") ──────────────
    # By the migrated schema's catalogue, and by what a REAL duplicate insert reports: the name the
    # stage-3 identity resolver must match exactly (the migration leaves it unnamed, so PostgreSQL's
    # default applies - `001_initial_schema.py:170`).
    async with factory() as s:
        catalogued = await s.scalar(
            text(
                "SELECT c.conname FROM pg_constraint c "
                "JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey) "
                "WHERE c.conrelid = 'transactions'::regclass AND c.contype = 'u' "
                "AND array_length(c.conkey, 1) = 1 AND a.attname = 'tx_id'"
            )
        )
    async with factory() as s:
        with pytest.raises(IntegrityError) as duplicate:
            await s.execute(
                insert(Transaction).values(
                    id=uuid.uuid4(),
                    tx_id=body["tx_id"],
                    type="PAYMENT",
                    initiator_id=w.ids[w.alice["pid"]],
                    payload={},
                    # Terminal: migration 030's CHECK is evaluated before the unique index, so a NEW
                    # payment would be refused by the fence (23514), not by the identity (23505).
                    state="ABORTED",
                )
            )
        await s.rollback()
    assert catalogued == "transactions_tx_id_key", catalogued
    assert _constraint_name(duplicate.value) == catalogued
    assert getattr(duplicate.value.orig, "sqlstate", None) == "23505"

    # ── the mechanism of every row was reached ────────────────────────────────────────────────
    assert premises == {
        # the cause landed once, between routing and prepare / between prepare and the commit phase,
        # and - one transaction since stage 3 - another transaction saw no row of the payment then
        "recheck_after_new": [None],
        "stop_at_commit": [None],
        "hold_at_commit": [None],
        # the deactivating PATCH queued on the owner lock while the payment was in flight
        "stop_at_commit_queued": True,
        # the hold behind the snapshot was met as a real 40001, and pay() retried the whole attempt
        "hold_at_commit_retried": ["40001"],
        # the payment really waited on the PATCH's row lock
        "timeout_confirmed_rollback": True,
    }, premises

    # ── the table ─────────────────────────────────────────────────────────────────────────────
    inactive, hold = MoneyBoundary.EQUIVALENT_INACTIVE_REASON, MoneyBoundary.EQUIVALENT_INTEGRITY_HOLD_REASON
    assert observed == {
        "routing_before_new": {
            "first": (400, "E002", None), "stored": None,
            "replay": (200, "COMMITTED", None), "replay_moved": True,
        },
        "recheck_after_new": {
            "first": (200, "COMMITTED", None), "stored": ("COMMITTED", None),
            "replay": (200, "COMMITTED", None), "replay_moved": False,
        },
        "stop_before_new": {
            "first": (409, "E008", inactive), "stored": None,
            "replay": (200, "COMMITTED", None), "replay_moved": True,
        },
        "stop_at_commit": {
            "first": (200, "COMMITTED", None), "stored": ("COMMITTED", None),
            "replay": (200, "COMMITTED", None), "replay_moved": False,
        },
        "hold_before_new": {
            "first": (409, "E008", hold), "stored": None,
            "replay": (200, "COMMITTED", None), "replay_moved": True,
        },
        "hold_at_commit": {
            "first": (409, "E008", hold), "stored": ("ABORTED", "E008"),
            "replay": (200, "ABORTED", "E008"), "replay_moved": False,
        },
        "timeout_confirmed_rollback": {
            "first": (504, "E007", None), "stored": ("ABORTED", "E007"),
            "replay": (200, "ABORTED", "E007"), "replay_moved": False,
        },
    }, observed


@pytest.mark.asyncio
async def test_the_staged_path_refusal_table(factory, monkeypatch) -> None:  # noqa: F811
    """Stop / hold in force when the tick runs: rejected before `NEW`, no row, executes once lifted."""

    observed: dict[str, dict[str, Any]] = {}
    calls: list[dict[str, Any]] = []
    original = PaymentService.create_payment_internal_staged

    async def recording(self_, sender_id, **kwargs):
        entry = {"sender_id": sender_id, **kwargs}
        calls.append(entry)
        try:
            staged = await original(self_, sender_id, **kwargs)
        except BaseException as exc:
            entry["raised"] = (type(exc).__name__, (getattr(exc, "details", None) or {}).get("reason"))
            raise
        entry["status"] = staged.result.status
        return staged

    monkeypatch.setattr(PaymentService, "create_payment_internal_staged", recording)
    _install(monkeypatch, factory)

    for name in ("stop_before_tick", "hold_before_tick"):
        world = await _seed(factory)
        sse = _Sse()
        run = _run_record(world, f"p019-table-{uuid.uuid4().hex[:8]}")
        runner = _runner(run, _scenario(world), sse)
        if name == "stop_before_tick":
            async with factory() as s:
                await s.execute(
                    update(Equivalent).where(Equivalent.id == world.equivalent.id).values(is_active=False)
                )
                await s.commit()
        else:
            await set_integrity_hold(factory, world.equivalent.id)
        calls.clear()
        try:
            await asyncio.wait_for(runner.tick_real_mode(run.run_id), 90.0)
        finally:
            _forget_the_route_cache(world)
        assert len(calls) == 1, (name, calls)
        [call] = calls
        tx_id = str(call["idempotency_key"])
        stored = await tx_row(factory, tx_id)
        failed = [e for e in sse.events if e.get("type") == "tx.failed"]

        async with factory() as s:
            await s.execute(
                update(Equivalent)
                .where(Equivalent.id == world.equivalent.id)
                .values(is_active=True, integrity_hold_result_id=None)
            )
            await s.commit()
        async with factory() as session:
            async with session.begin_nested():
                replay = await original(
                    PaymentService(session),
                    world.sender.id,
                    to_pid=call["to_pid"],
                    equivalent=call["equivalent"],
                    amount=call["amount"],
                    allowed_participant_pids=call.get("allowed_participant_pids"),
                    idempotency_key=tx_id,
                )
            await session.commit()
        _forget_the_route_cache(world)
        observed[name] = {
            "staged": call.get("raised"),
            "tick_committed": run._real_money_committed_ticks_total,
            "counted": (run.rejected_total, run.errors_total),
            "tx.failed": len(failed),
            "stored": stored,
            "replay": str(replay.result.status),
        }

    inactive, hold = MoneyBoundary.EQUIVALENT_INACTIVE_REASON, MoneyBoundary.EQUIVALENT_INTEGRITY_HOLD_REASON
    assert observed == {
        "stop_before_tick": {
            "staged": ("ConflictException", inactive), "tick_committed": 1, "counted": (1, 0),
            "tx.failed": 1, "stored": None, "replay": "COMMITTED",
        },
        "hold_before_tick": {
            "staged": ("ConflictException", hold), "tick_committed": 1, "counted": (1, 0),
            "tx.failed": 1, "stored": None, "replay": "COMMITTED",
        },
    }, observed
