"""Programme 015, phase B step 3: every debt the simulator's inject writes is written under the owner lock.

WHAT IS WRONG TODAY. The equivalent owner lock is a transactional `pg_advisory_xact_lock`
(`app/core/payments/engine.py`). The tick orchestrator takes it for the run's equivalents and then
hands its session to the due-events phase, where `InjectExecutor.apply_inject_event` calls
`session.commit()` on a transaction it did not open (`app/core/simulator/inject_executor.py`). That
commit releases the lock. A second inject event due in the same tick then writes `debts` with no
owner lock at all, and the payments phase reads its debt snapshot before anything takes it again.

Phase B orders its journal, per-equivalent counter and checksum chain under exactly this lock, so a
writer outside it would make the chain's order a matter of luck. The contract makes the repair a
precondition of activating the journal.

HOW THE STAND SEES IT. A `before_flush` listener on the writing session asks `pg_locks`, through that
session's own connection, whether the backend holds the owner lock of each debt's equivalent: the
exact key (`classid` = namespace, `objid` = key as unsigned, `objsubid` = 2 for the two-argument
form), `ExclusiveLock`, granted. Observations are recorded and asserted AFTER the call returns: an
assertion raised inside a commit would be swallowed by the inject's own error handling and read as
"inject failed", which is the kind of green this programme exists to remove.

WHY THE STAND IS BUILT THIS WAY, and each choice is load-bearing:

* Its own engine with `isolation_level="SERIALIZABLE"`. The shared test engine runs READ COMMITTED
  while the application runs SERIALIZABLE (`app/db/session.py`).
* A real pool of two connections, not NullPool and not the savepoint-wrapped `db_session`. Under
  `db_session` an outer transaction survives every "commit", so a transaction-level lock would never
  be released and a writer that lost its lock would still look locked. With NullPool a released
  connection is closed, so "the lock was released" could not be told from "the connection died".
* Two equivalents with two different lock keys, so "some advisory lock is held" cannot pass for
  "this debt's lock is held".
* Non-vacuity: both debts must actually be flushed and stored with their exact amounts. No flush,
  no observation - and an empty observation list must not read as compliance.
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
from sqlalchemy import delete, event, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session

from app.core.payments.engine import _EQUIVALENT_OWNER_LOCK_NAMESPACE, PaymentEngine
from app.core.simulator.models import RunRecord
from app.core.simulator.real_runner import RealRunner
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine
from tests.debt_setup import purge_test_ledger


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


_HOLDS_OWNER_LOCK_SQL = text(
    """
    SELECT count(*) FROM pg_locks
    WHERE locktype = 'advisory'
      AND pid = pg_backend_pid()
      AND database = (SELECT oid FROM pg_database WHERE datname = current_database())
      AND classid = :namespace
      AND objid = :objid
      AND objsubid = 2
      AND mode = 'ExclusiveLock'
      AND granted
    """
)


def _objid(equivalent_id: uuid.UUID) -> int:
    return PaymentEngine._equivalent_owner_lock_key(equivalent_id) & 0xFFFFFFFF


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


def _holds(sync_session: Session, equivalent_id: uuid.UUID) -> tuple[int | None, bool, str | None]:
    try:
        conn = sync_session.connection()
        pid = int(conn.execute(text("SELECT pg_backend_pid()")).scalar_one())
        count = conn.execute(
            _HOLDS_OWNER_LOCK_SQL,
            {"namespace": _EQUIVALENT_OWNER_LOCK_NAMESPACE, "objid": _objid(equivalent_id)},
        ).scalar_one()
        return pid, int(count) == 1, None
    except Exception as exc:  # recorded, asserted after the call
        return None, False, repr(exc)


@event.listens_for(_ObservedSession, "before_flush")
def _observe_debt_writes(sync_session: Session, _flush_context, _instances) -> None:
    for obj in list(sync_session.new) + list(sync_session.dirty):
        if isinstance(obj, Debt) and obj.equivalent_id is not None:
            pid, held, error = _holds(sync_session, obj.equivalent_id)
            _observations.append(_Observation("debt flush", obj.equivalent_id, pid, held, error))


@pytest_asyncio.fixture
async def observed_factory():
    from tests.conftest import TEST_DATABASE_URL, _ensure_schema_initialized

    await _ensure_schema_initialized()
    eng = create_async_engine(
        TEST_DATABASE_URL,
        pool_size=2,
        max_overflow=0,
        pool_timeout=10,
        isolation_level="SERIALIZABLE",
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


async def _cleanup(factory, world: _World) -> None:
    eq_ids = [eq.id for eq in world.equivalents]
    async with factory() as s:
        # The debts AND the journal rows that describe them, through the driver and BEFORE the
        # deletes below: `session.execute(delete(Debt))` is Core DML the write guard refuses
        # (that is `C2`), and `debt_operations.tx_id` RESTRICTs `transactions.tx_id`, so an
        # envelope still standing would block the transaction delete above it.
        await purge_test_ledger(s, equivalent_ids=eq_ids)
        await s.execute(delete(TrustLine).where(TrustLine.equivalent_id.in_(eq_ids)))
        await s.execute(delete(Equivalent).where(Equivalent.id.in_(eq_ids)))
        await s.execute(
            delete(Participant).where(Participant.id.in_([world.creditor.id, world.debtor.id]))
        )
        await s.commit()


_AMOUNTS = (Decimal("3.00"), Decimal("5.00"))


def _scenario(world: _World) -> dict[str, Any]:
    c, d = world.creditor.pid, world.debtor.pid
    return {
        "equivalents": [eq.code for eq in world.equivalents],
        "participants": [{"id": c}, {"id": d}],
        "trustlines": [
            {"from": c, "to": d, "equivalent": eq.code, "limit": "100.00", "status": "active"}
            for eq in world.equivalents
        ],
        "behaviorProfiles": [],
        # TWO inject events due in the same tick, one per equivalent. The second is the one that
        # writes after the first one's commit released the orchestrator's lock.
        "events": [
            {
                "type": "inject",
                "time": 0,
                "effects": [
                    {
                        "op": "inject_debt",
                        "from": c,
                        "to": d,
                        "equivalent": eq.code,
                        "amount": str(amount),
                    }
                ],
            }
            for eq, amount in zip(world.equivalents, _AMOUNTS)
        ],
    }


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


def _runner(run: RunRecord, scenario: dict[str, Any], artifacts: _Artifacts) -> RealRunner:
    runner = RealRunner(
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
    run.intensity_percent = 0  # no payments: the debts under test are the injected ones only
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


async def _advisory_owner_locks_of(factory, backend_pids: set[int]) -> int:
    """Owner-namespace advisory locks still held by the given backends, seen from a second session."""
    from tests.conftest import engine as shared_engine

    async with shared_engine.connect() as conn:
        return int(
            (
                await conn.execute(
                    text(
                        "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' "
                        "AND classid = :namespace AND pid = ANY(:pids)"
                    ),
                    {"namespace": _EQUIVALENT_OWNER_LOCK_NAMESPACE, "pids": list(backend_pids)},
                )
            ).scalar_one()
        )


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


@pytest.mark.asyncio
async def test_the_due_events_phase_writes_each_injected_debt_under_its_owner_lock(
    observed_factory,
) -> None:
    """RED before phase B step 3: nothing in the due-events phase takes the lock at all."""
    world = await _seed(observed_factory)
    try:
        scenario = _scenario(world)
        run = _run(world, "p015-due-events")
        artifacts = _Artifacts()
        runner = _runner(run, scenario, artifacts)

        async with observed_factory() as session:
            await runner._apply_due_scenario_events(
                session, run_id=run.run_id, run=run, scenario=scenario
            )

        stored = await _stored(observed_factory, world)
        assert stored == {eq.id: amount for eq, amount in zip(world.equivalents, _AMOUNTS)}, stored
        assert run._real_fired_scenario_event_indexes == {0, 1}
        _assert_every_debt_write_was_locked(world)

        pids = {o.backend_pid for o in _observations if o.backend_pid is not None}
        assert await _advisory_owner_locks_of(observed_factory, pids) == 0, (
            "an owner lock outlived the inject's transaction: it must be transaction-level"
        )
    finally:
        await _cleanup(observed_factory, world)


@pytest.mark.asyncio
async def test_a_real_tick_keeps_the_owner_lock_boundary_from_inject_to_payments(
    observed_factory, monkeypatch
) -> None:
    """RED before phase B step 3, through the orchestrator's own PostgreSQL branch.

    Nothing ran that branch before this test. The orchestrator takes the lock, the first inject's
    commit drops it, the second inject writes without it, and the payments phase reads its debt
    snapshot without it. After the repair: each inject is its own locked unit of work, and the
    payments phase reads its snapshot inside a transaction that holds every run equivalent's lock.
    """
    import app.core.simulator.storage as simulator_storage
    import app.db.session as app_db_session

    world = await _seed(observed_factory)
    try:
        scenario = _scenario(world)
        run = _run(world, "p015-real-tick")
        artifacts = _Artifacts()
        runner = _runner(run, scenario, artifacts)

        async def _noop(*_a, **_kw):
            return None

        for name in ("write_tick_metrics", "write_tick_bottlenecks", "sync_artifacts", "upsert_run"):
            monkeypatch.setattr(simulator_storage, name, _noop)
        monkeypatch.setattr(app_db_session, "AsyncSessionLocal", observed_factory)

        snapshot_locks: list[_Observation] = []
        original_snapshot = runner._load_debt_snapshot_by_pid

        async def _observed_snapshot(session, participants, equivalents):
            for eq in world.equivalents:
                pid, held, error = await session.run_sync(lambda s, _id=eq.id: _holds(s, _id))
                snapshot_locks.append(_Observation("debt snapshot", eq.id, pid, held, error))
            return await original_snapshot(session, participants, equivalents)

        monkeypatch.setattr(runner, "_load_debt_snapshot_by_pid", _observed_snapshot)

        await runner.tick_real_mode(run.run_id)

        stored = await _stored(observed_factory, world)
        assert stored == {eq.id: amount for eq, amount in zip(world.equivalents, _AMOUNTS)}, (
            f"both injects must be applied exactly once by the tick, got {stored}; "
            f"artifacts: {artifacts.events}"
        )
        _assert_every_debt_write_was_locked(world)

        assert {o.equivalent_id for o in snapshot_locks} == {eq.id for eq in world.equivalents}, (
            "non-vacuity: the payments phase never read its debt snapshot"
        )
        assert not [o for o in snapshot_locks if o.error], snapshot_locks
        unlocked = [o for o in snapshot_locks if not o.held]
        assert not unlocked, (
            "the payments phase read its debt snapshot without the owner lock it plans against: "
            f"{unlocked}"
        )
    finally:
        await _cleanup(observed_factory, world)
