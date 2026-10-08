"""Programme 015, phase B step 3: every debt a simulator-run money writer writes is written under the owner lock.

027 STAGE 2 (`T2704`): the "owner lock" is the line locks, read from `trust_lines.xmax` (any (sub)transaction xid of the writing backend).

030 STAGE S3b: the simulator's `inject_debt` effect is deleted, so the writer under observation is the real
one - `PaymentService.pay` - and the tick half keeps only the payments phase's debt snapshot. The stand
and its helpers (`observed_factory`, `_seed`, `_run`, `_runner`, `_stored`, `_Artifacts`, `_observations`)
stay: other modules import them.

WHAT WAS WRONG (015, phase B step 3). The equivalent owner lock was a transactional `pg_advisory_xact_lock`
(`app/core/payments/engine.py`). The tick orchestrator took it for the run's equivalents and handed its
session to the due-events phase, where a nested `session.commit()` released it; a second writer in the
same tick then wrote `debts` with no owner lock at all, and the payments phase read its debt snapshot
before anything took it again. Phase B orders its journal, per-equivalent counter and checksum chain under
exactly this lock, so a writer outside it would make the chain's order a matter of luck.

HOW THE STAND SEES IT. A `before_flush` listener on the writing session asks `pg_locks`/`trust_lines.xmax`,
through that session's own connection, whether the transaction holds every non-closed line of the debt's
pair in its equivalent. Observations are recorded and asserted AFTER the call returns: an assertion raised
inside a commit would be swallowed by the writer's own error handling and read as "payment failed", which
is the kind of green this programme exists to remove.

WHY THE STAND IS BUILT THIS WAY, and each choice is load-bearing:

* Its own engine with `isolation_level` of the application (`settings.DB_POSTGRES_ISOLATION_LEVEL`). The
  shared test engine runs READ COMMITTED while the application runs SERIALIZABLE (`app/db/session.py`).
* A real pool of two connections, not NullPool and not the savepoint-wrapped `db_session`. Under
  `db_session` an outer transaction survives every "commit", so a transaction-level lock would never
  be released and a writer that lost its lock would still look locked.
* Two equivalents, so "some lock is held" cannot pass for "this debt's lines are locked".
* Non-vacuity: debts must actually be flushed in both equivalents, a debt flush under no lock must be
  observed as NOT held (the control), and an empty observation list must not read as compliance.
"""
from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import event, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session

from app.config import settings
from app.core.payments.router import PaymentRouter
from app.core.payments.service import PaymentService
from app.core.simulator.models import RunRecord
from app.core.simulator.real_runner_impl import RealRunnerImpl
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine
from app.schemas.payment import PaymentCreateRequest
from tests.debt_setup import add_debts


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


# A row lock stamps `xmax` with the xid of the (sub)transaction that took it. The payment takes its line locks inside the
# operation's SAVEPOINT, so the stamp is a SUBtransaction xid, which `pg_current_xact_id()` (top level) never equals;
# every assigned (sub)xid of this backend holds a `transactionid` lock, which is what "mine" is read from.
_HOLDS_LINES_SQL = """
    SELECT count(*) FILTER (WHERE xmax IN (
        SELECT transactionid FROM pg_locks
        WHERE locktype = 'transactionid' AND pid = pg_backend_pid() AND granted)), count(*) FROM trust_lines
    WHERE equivalent_id = :eq AND status <> 'closed'"""
_PAIR_SQL = " AND from_participant_id IN (:a, :b) AND to_participant_id IN (:a, :b)"


@dataclass
class _Observation:
    where: str
    equivalent_id: uuid.UUID
    backend_pid: int | None
    held: bool
    error: str | None = None


class _ObservedSession(Session):
    """A sync session class of its own, so the listener sees only sessions this stand created."""


_observations: list[_Observation] = []


def _holds(sync_session: Session, equivalent_id: uuid.UUID, pair=None) -> tuple[int | None, bool, str | None]:
    try:
        conn = sync_session.connection()
        pid = int(conn.execute(text("SELECT pg_backend_pid()")).scalar_one())
        params = {"eq": equivalent_id, **({"a": pair[0], "b": pair[1]} if pair else {})}
        mine, total = conn.execute(text(_HOLDS_LINES_SQL + (_PAIR_SQL if pair else "")), params).one()
        return pid, int(total) > 0 and mine == total, None
    except Exception as exc:  # recorded, asserted after the call
        return None, False, repr(exc)


@event.listens_for(_ObservedSession, "before_flush")
def _observe_debt_writes(sync_session: Session, _flush_context, _instances) -> None:
    for obj in list(sync_session.new) + list(sync_session.dirty):
        if isinstance(obj, Debt) and obj.equivalent_id is not None:
            pid, held, error = _holds(sync_session, obj.equivalent_id, (obj.debtor_id, obj.creditor_id))
            _observations.append(_Observation("debt flush", obj.equivalent_id, pid, held, error))


@pytest_asyncio.fixture
async def observed_factory(committed_database):
    # ON A DISPOSABLE CLONE (018 B0b): the tests commit for real, and the clone's drop is the only
    # disposal of what they wrote - nothing is deleted row by row. A module that imports this fixture
    # gets the same `committed_database` its other fixtures ask for.
    eng = create_async_engine(
        committed_database.url,
        pool_size=2,
        max_overflow=0,
        pool_timeout=10,
        isolation_level=settings.DB_POSTGRES_ISOLATION_LEVEL,
    )
    factory = async_sessionmaker(
        bind=eng,
        class_=AsyncSession,
        sync_session_class=_ObservedSession,
        expire_on_commit=False,
        autoflush=False,
    )
    _observations.clear()
    try:
        yield factory
    finally:
        _observations.clear()
        await eng.dispose()


@dataclass
class _World:
    equivalents: list[Equivalent]
    creditor: Participant
    debtor: Participant


async def _seed(factory) -> _World:
    n = uuid.uuid4().hex[:8]
    async with factory() as s:
        eqs = [
            Equivalent(code=f"OL{i}{n}".upper()[:16], precision=2, is_active=True) for i in (1, 2)
        ]
        creditor = Participant(
            pid=f"OLC_{n}", display_name="Creditor", public_key=f"pk_olc_{n}", type="person",
            status="active",
        )
        debtor = Participant(
            pid=f"OLD_{n}", display_name="Debtor", public_key=f"pk_old_{n}", type="person",
            status="active",
        )
        s.add_all([*eqs, creditor, debtor])
        await s.flush()
        for eq in eqs:
            s.add(
                TrustLine(
                    from_participant_id=creditor.id,
                    to_participant_id=debtor.id,
                    equivalent_id=eq.id,
                    limit=Decimal("100.00"),
                    status="active",
                )
            )
        await s.commit()
    _observations.clear()  # the seed's own flushes are not under test
    return _World(eqs, creditor, debtor)


_AMOUNTS = (Decimal("3.00"), Decimal("5.00"))


class _Artifacts:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def write_real_tick_artifact(self, *a, **kw) -> None:
        pass

    def enqueue_event_artifact(self, _run_id: str, payload: dict[str, Any]) -> None:
        self.events.append(payload)


class _Sse:
    def next_event_id(self, run: RunRecord) -> str:
        run._event_seq += 1
        return f"e{run._event_seq}"

    def broadcast(self, run_id: str, payload: dict) -> None:
        pass


def _runner(run: RunRecord, scenario: dict[str, Any], artifacts: _Artifacts) -> RealRunnerImpl:
    runner = RealRunnerImpl(
        lock=threading.RLock(),
        get_run=lambda _rid: run,
        get_scenario_raw=lambda _sid: scenario,
        sse=_Sse(),
        artifacts=artifacts,
        utc_now=_utc_now,
        publish_run_status=lambda _rid: None,
        db_enabled=lambda: True,
        actions_per_tick_max=5,
        clearing_every_n_ticks=25,
        real_max_consec_tick_failures_default=3,
        real_max_timeouts_per_tick_default=10,
        real_max_errors_total_default=50,
        logger=logging.getLogger("test.p015.inject_owner_lock"),
    )
    runner._real_enable_inject = True
    return runner


def _run(world: _World, run_id: str) -> RunRecord:
    run = RunRecord(run_id=run_id, scenario_id="p015-owner-lock", mode="real", state="running")
    run.seed = 7
    run.tick_index = 1  # not a clearing tick
    run.sim_time_ms = 1_000
    run.intensity_percent = 0  # the tick plans no payments of its own
    run._real_seeded = True
    run._real_participants = [(world.creditor.id, world.creditor.pid), (world.debtor.id, world.debtor.pid)]
    run._real_equivalents = sorted(eq.code for eq in world.equivalents)
    run._edges_by_equivalent = {}
    run._real_viz_by_eq = {}
    return run


async def _stored(factory, world: _World) -> dict[uuid.UUID, Decimal]:
    async with factory() as s:
        rows = (
            await s.execute(
                select(Debt.equivalent_id, Debt.amount).where(
                    Debt.equivalent_id.in_([eq.id for eq in world.equivalents])
                )
            )
        ).all()
    return {eq_id: Decimal(str(amount)) for eq_id, amount in rows}


def _assert_every_debt_write_was_locked(world: _World) -> None:
    errors = [o for o in _observations if o.error]
    assert not errors, f"the stand could not read pg_locks: {errors}"
    written = {o.equivalent_id for o in _observations}
    expected = {eq.id for eq in world.equivalents}
    assert written == expected, (
        f"non-vacuity: expected a debt write in each of both equivalents, observed {written}; "
        f"without both writes this test proves nothing"
    )
    unlocked = [o for o in _observations if not o.held]
    assert not unlocked, (
        "debts were written without the owner lock of their equivalent held by the writing "
        f"transaction: {unlocked}"
    )


async def _pay(factory, sender_id: uuid.UUID, receiver_pid: str, eq_code: str, amount: Decimal):
    request = PaymentCreateRequest(
        tx_id="tx-" + uuid.uuid4().hex, to=receiver_pid, equivalent=eq_code, amount=str(amount),
        signature="__internal__",
    )
    try:
        return await PaymentService.pay(factory, sender_id, request, require_signature=False)
    finally:
        PaymentRouter.invalidate_cache(eq_code)


@pytest.mark.asyncio
async def test_a_payment_writes_each_debt_under_the_line_locks_of_its_pair(observed_factory) -> None:
    """The real money writer, through the observed factory: every `Debt` flush of a payment - the INSERT of a
    new debt in each equivalent and the UPDATE that a reducing payment makes to an existing one - happens in a
    transaction that holds every non-closed line of the debt's pair.

    The debtor pays the creditor on the creditor's line in both equivalents (a debt grows: INSERT), then the
    creditor pays part of it back in the first one (the debt shrinks: UPDATE).
    """
    world = await _seed(observed_factory)
    eq1, eq2 = world.equivalents
    creditor, debtor = world.creditor, world.debtor

    for eq, amount in zip(world.equivalents, _AMOUNTS):
        paid = await _pay(observed_factory, debtor.id, creditor.pid, eq.code, amount)
        assert paid.status == "COMMITTED", paid
    reduced = await _pay(observed_factory, creditor.id, debtor.pid, eq1.code, Decimal("1.00"))
    assert reduced.status == "COMMITTED", reduced

    stored = await _stored(observed_factory, world)
    assert stored == {eq1.id: _AMOUNTS[0] - Decimal("1.00"), eq2.id: _AMOUNTS[1]}, stored
    _assert_every_debt_write_was_locked(world)
    assert len([o for o in _observations if o.equivalent_id == eq1.id]) >= 2, (
        "non-vacuity: the first equivalent's debt was both created and reduced, two flushes at least; "
        f"observed {_observations}"
    )


@pytest.mark.asyncio
async def test_the_observer_sees_a_debt_written_without_the_line_locks(observed_factory) -> None:
    """CONTROL (anti-vacuum) of the test above: a `Debt` flushed by a session that holds no line lock is
    observed as NOT held. Without it, "every observation is held" could pass because `_holds` always says so."""
    world = await _seed(observed_factory)
    eq = world.equivalents[0]
    async with observed_factory() as session:
        await add_debts(
            session,
            [Debt(debtor_id=world.debtor.id, creditor_id=world.creditor.id, equivalent_id=eq.id,
                  amount=Decimal("1.00"))],
            label="p030_s3b_unlocked_write",
        )
        await session.flush()
        await session.commit()

    assert [(o.equivalent_id, o.held, o.error) for o in _observations] == [(eq.id, False, None)], _observations


@pytest.mark.asyncio
async def test_a_real_tick_plans_before_its_money_transaction_and_writes_debts_under_the_line_locks(
    observed_factory, monkeypatch
) -> None:
    """A real tick, through the orchestrator's own PostgreSQL branch, with payments of its own in both equivalents:

    1. its planning reads are finished, and their session has given its connection back, BEFORE the money
       transaction takes a connection and the line locks;
    2. its payments really change the debts, by exactly what was planned;
    3. every debt it writes is written under the line locks of the debt's pair (015 step 3);
    4. all of that is OBSERVED inside the wrappers and ASSERTED after the tick has returned.

    CHANGED 2026-10-08 (034 `F-034-2`, §15 review of `62cce627`; was
    `test_a_real_tick_reads_its_payments_snapshot_under_the_owner_lock`). The test used to assert that the planning
    debt snapshot is read inside a transaction holding the lines. That was never the 015 invariant: step 3 is about
    WRITES leaving the journal's order when a nested commit released the owner lock (the module docstring, "WHAT
    WAS WRONG"); the snapshot is advisory - the planner may run without it, and the payment service takes the pair's
    lines, reads the debts behind them and checks capacity itself (`PaymentService._bind_payment`, `._segment`).
    Reading it under the locks made the tick hold `FOR UPDATE` while it waited for a second pooled connection. So
    claim 1 replaces the old one, and claims 2-3 are new here: the old tick planned nothing (`intensity_percent` 0)
    and could not show anything about a tick's own writes. The control "a write without the lock is observed as not
    held" is the test above, unchanged.
    """
    import app.core.simulator.storage as simulator_storage
    import app.db.session as app_db_session

    world = await _seed(observed_factory)
    async with observed_factory() as session:
        await add_debts(
            session,
            [
                Debt(debtor_id=world.debtor.id, creditor_id=world.creditor.id, equivalent_id=eq.id, amount=amount)
                for eq, amount in zip(world.equivalents, _AMOUNTS)
            ],
            label="p030_s3b_tick_debts",
        )
        await session.commit()
    _observations.clear()  # the fixture's own flushes are not under test

    # The debtor pays the creditor on the creditor's line in each equivalent; the amounts are bounded far below what
    # the lines (100.00) have left, so every planned payment is carried.
    scenario: dict[str, Any] = {
        "equivalents": [eq.code for eq in world.equivalents],
        "participants": [{"id": world.creditor.pid, "behaviorProfileId": "payer"},
                         {"id": world.debtor.pid, "behaviorProfileId": "payer"}],
        "trustlines": [{"from": world.creditor.pid, "to": world.debtor.pid, "equivalent": eq.code,
                        "limit": "100.00", "status": "active"} for eq in world.equivalents],
        "behaviorProfiles": [{"id": "payer", "props": {
            "amount_model": {eq.code: {"min": "1.00", "max": "4.00"} for eq in world.equivalents}}}],
        "events": [],
    }
    run = _run(world, "p015-real-tick")
    run.intensity_percent = 100  # this tick plans payments of its own
    artifacts = _Artifacts()
    runner = _runner(run, scenario, artifacts)

    async def _noop(*_a, **_kw):
        return None

    for name in ("write_tick_metrics", "write_tick_bottlenecks", "sync_artifacts", "upsert_run"):
        monkeypatch.setattr(simulator_storage, name, _noop)
    monkeypatch.setattr(app_db_session, "AsyncSessionLocal", observed_factory)

    # Observed inside the wrappers, asserted below: an assertion raised in here would be swallowed by the tick's own
    # handling of a failed planning read and surface as something else.
    pool = observed_factory.kw["bind"].sync_engine.pool
    order: list[dict[str, Any]] = []
    plans: list[list[Any]] = []
    original_snapshot = runner._load_debt_snapshot_by_pid
    original_lock = PaymentService.lock_staged_lines
    original_plan = runner._plan_real_payments

    async def _observed_snapshot(session, participants, equivalents):
        order.append({"event": "snapshot", "session": session})
        return await original_snapshot(session, participants, equivalents)

    async def _observed_lock(service, *args, **kwargs):
        order.append({
            "event": "lock",
            "planning_open": [o["session"].in_transaction() for o in order if o["event"] == "snapshot"],
            "connections_out": pool.checkedout(),
            "money_begun": service.session.in_transaction(),
            "planning_is_money": any(o["session"] is service.session for o in order if o["event"] == "snapshot"),
        })
        return await original_lock(service, *args, **kwargs)

    def _recorded_plan(*args, **kwargs):
        planned = original_plan(*args, **kwargs)
        plans.append(list(planned))
        return planned

    monkeypatch.setattr(runner, "_load_debt_snapshot_by_pid", _observed_snapshot)
    monkeypatch.setattr(PaymentService, "lock_staged_lines", _observed_lock)
    monkeypatch.setattr(runner, "_plan_real_payments", _recorded_plan)

    await runner.tick_real_mode(run.run_id)
    stored = await _stored(observed_factory, world)

    # 1. Planning first, on its own session, released before the money transaction takes anything.
    assert run._real_money_replays_total == 0 and run.last_error is None, (run._real_money_replays_total, run.last_error)
    assert [o["event"] for o in order] == ["snapshot", "lock"], (
        f"the planning snapshot and the line locks came in the order {[o['event'] for o in order]}: the snapshot "
        "must be read before the money transaction takes its line locks (034 F-034-2)"
    )
    lock = order[1]
    assert (lock["planning_open"], lock["connections_out"], lock["money_begun"], lock["planning_is_money"]) == (
        [False], 0, False, False), (
        f"when the money transaction took its line locks: {dict(lock)}; expected the planning session ended, no "
        "pooled connection held by the tick, the money transaction not begun, and planning on a session of its own"
    )

    # 2. The tick's own payments moved the debts, in both equivalents, by what it planned.
    assert len(plans) == 1 and plans[0], plans
    code_to_id = {eq.code: eq.id for eq in world.equivalents}
    expected = {eq.id: amount for eq, amount in zip(world.equivalents, _AMOUNTS)}
    for action in plans[0]:
        assert (action.sender_pid, action.receiver_pid) == (world.debtor.pid, world.creditor.pid), action
        expected[code_to_id[action.equivalent]] += Decimal(action.amount)
    assert all(expected[eq.id] > amount for eq, amount in zip(world.equivalents, _AMOUNTS)), (
        f"non-vacuity: the tick planned no payment in one of the equivalents: {plans[0]}"
    )
    assert stored == expected, f"the tick's payments left {stored}, planned {expected}; artifacts: {artifacts.events}"

    # 3. Every one of those writes held the line locks of its pair.
    _assert_every_debt_write_was_locked(world)
    assert len(_observations) >= len(plans[0]), (len(_observations), plans[0])
