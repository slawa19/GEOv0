"""Programme 015, phase B step 3: the inject stages, its owner commits.

WHAT CHANGED. `InjectExecutor.apply_inject_event` committed a transaction it had not opened. The
equivalent owner lock is transactional, so that commit released the lock the tick orchestrator
held, and the next inject event of the tick wrote `debts` unlocked
(`tests/integration/test_p015_inject_holds_the_owner_lock_postgres.py` is the PostgreSQL
reproducer). The executor now only STAGES (`stage_inject_event`) and PUBLISHES
(`publish_committed_inject`); `RealRunnerImpl._apply_due_scenario_events` owns every boundary.

WHAT THIS FILE PROVES, on the default SQLite tier, and always through the database: every effect
is read back through a NEW session (`TestingSessionLocal()`), never through the session under test
and never through a counter standing in for the row. SQLite takes no advisory lock, so the lock
itself is proven on PostgreSQL; the lock SET check is enforced on every dialect and proven here.

A NOTE ON THE STAND. `db_session` on SQLite is a plain session. The owner rolls back, and a
rollback expires every instance in the session, so ids and pids are captured as plain values right
after seeding and ORM instances are not touched afterwards.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import delete, event, select
from sqlalchemy.exc import DBAPIError

from app.core.simulator.inject_executor import InjectOwnerLockSetTooNarrow
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine
from app.db.sqlite_transaction_control import sqlite_busy_error_name
from tests.conftest import TestingSessionLocal, engine as _test_engine
from tests.unit.test_scenario_inject_topology import _make_run, _make_runner, _nonce

from tests.debt_setup import debt_fixture_setup


# ---------------------------------------------------------------------------
# Stand
# ---------------------------------------------------------------------------


class _Orig(Exception):
    """A DBAPI-level error carrying a SQLSTATE the way the PostgreSQL drivers expose it."""

    def __init__(self, *, sqlstate: str | None = None, pgcode: str | None = None) -> None:
        super().__init__(f"fake driver error sqlstate={sqlstate} pgcode={pgcode}")
        self.sqlstate = sqlstate
        self.pgcode = pgcode


def _db_error(*, sqlstate: str | None = None, pgcode: str | None = None) -> DBAPIError:
    return DBAPIError("COMMIT", None, _Orig(sqlstate=sqlstate, pgcode=pgcode))


@dataclass(frozen=True)
class _World:
    eq_id: uuid.UUID
    eq_code: str
    creditor_id: uuid.UUID
    creditor_pid: str
    debtor_id: uuid.UUID
    debtor_pid: str


async def _seed_debt_world(db_session, *, existing: Decimal | None = None) -> _World:
    n = _nonce()
    eq = Equivalent(code=f"W{n}".upper()[:16], precision=2, is_active=True)
    creditor = Participant(
        pid=f"OWC_{n}", display_name="Creditor", public_key=f"pk_owc_{n}"[:64],
        type="person", status="active",
    )
    debtor = Participant(
        pid=f"OWD_{n}", display_name="Debtor", public_key=f"pk_owd_{n}"[:64],
        type="person", status="active",
    )
    db_session.add_all([eq, creditor, debtor])
    await db_session.flush()
    db_session.add(
        TrustLine(
            from_participant_id=creditor.id,
            to_participant_id=debtor.id,
            equivalent_id=eq.id,
            limit=Decimal("100.00"),
            status="active",
        )
    )
    if existing is not None:
        async with debt_fixture_setup(db_session, label="existing-debt"):
            db_session.add(
                Debt(
                    debtor_id=debtor.id,
                    creditor_id=creditor.id,
                    equivalent_id=eq.id,
                    amount=existing,
                )
            )
    await db_session.commit()
    return _World(eq.id, eq.code, creditor.id, creditor.pid, debtor.id, debtor.pid)


def _debt_event(world: _World, amount: str = "10.00") -> dict[str, Any]:
    return {
        "type": "inject",
        "time": 0,
        "effects": [
            {
                "op": "inject_debt",
                "from": world.creditor_pid,
                "to": world.debtor_pid,
                "equivalent": world.eq_code,
                "amount": amount,
            }
        ],
    }


def _debt_scenario(world: _World, *events: dict[str, Any]) -> dict[str, Any]:
    return {
        "participants": [{"id": world.creditor_pid}, {"id": world.debtor_pid}],
        "trustlines": [
            {
                "from": world.creditor_pid,
                "to": world.debtor_pid,
                "equivalent": world.eq_code,
                "limit": "100.00",
                "status": "active",
            }
        ],
        "events": list(events) or [_debt_event(world)],
    }


def _debt_run(world: _World):
    return _make_run(
        participants=[(world.creditor_id, world.creditor_pid), (world.debtor_id, world.debtor_pid)],
        equivalents=[world.eq_code],
    )


async def _fresh_debt(world: _World) -> Decimal | None:
    async with TestingSessionLocal() as s:
        value = (
            await s.execute(
                select(Debt.amount).where(
                    Debt.debtor_id == world.debtor_id,
                    Debt.creditor_id == world.creditor_id,
                    Debt.equivalent_id == world.eq_id,
                )
            )
        ).scalar_one_or_none()
    return None if value is None else Decimal(str(value))


async def _fresh_participant_ids(pid: str) -> list[uuid.UUID]:
    async with TestingSessionLocal() as s:
        return list(
            (await s.execute(select(Participant.id).where(Participant.pid == pid))).scalars().all()
        )


def _notes(arts, event_index: int = 0) -> list[str]:
    return [
        str(p["scenario"]["description"])
        for p in arts.payloads
        if p.get("type") == "note" and p.get("scenario", {}).get("event_index") == event_index
    ]


class _StageSpy:
    """Counts calls to the real `stage_inject_event`, records what each was given, and can fail."""

    def __init__(self, runner, *, fail_on_calls: dict[int, BaseException] | None = None) -> None:
        self._real = runner._inject_executor.stage_inject_event
        self._fail_on_calls = dict(fail_on_calls or {})
        self.calls = 0
        self.locked_sets: list[frozenset[uuid.UUID]] = []
        self.pid_maps: list[dict[str, uuid.UUID]] = []
        runner._inject_executor.stage_inject_event = self  # instance attribute, this runner only

    async def __call__(self, session, **kwargs):
        self.calls += 1
        self.locked_sets.append(frozenset(kwargs["locked_equivalent_ids"]))
        self.pid_maps.append(dict(kwargs["pid_to_participant_id"]))
        staged = await self._real(session, **kwargs)
        failure = self._fail_on_calls.get(self.calls)
        if failure is not None:
            # After the real staging, so the owner's rollback has real staged writes to discard.
            raise failure
        return staged


def _fail_commits_carrying_writes(monkeypatch, session, failures: list[BaseException]) -> list[int]:
    """Make the next commits of a transaction that carries ORM writes raise, in order.

    Only such a commit is intercepted: the owner's other boundaries (the lock-set read, the end of
    publish's read) carry no writes, so this selects the unit of work's own commit without
    depending on when the owner marks the event fired.

    "Carries writes" is tracked by a `before_flush` listener, not read from `session.new` at commit
    time: the owner flushes the staged writes explicitly before committing, so by then the pending
    lists are empty. Reading them here would stop intercepting anything, and every test built on
    this helper would pass without its failure ever being injected.
    """

    real_commit = session.commit
    seen: list[int] = []
    carries_writes = {"now": False}

    def _before_flush(sess, _ctx, _instances) -> None:
        if sess.new or sess.dirty or sess.deleted:
            carries_writes["now"] = True

    def _after_transaction_end(sess, trans) -> None:
        if trans.parent is None:
            carries_writes["now"] = False

    event.listen(session.sync_session, "before_flush", _before_flush)
    event.listen(session.sync_session, "after_transaction_end", _after_transaction_end)

    async def commit() -> None:
        pending = session.new or session.dirty or session.deleted
        if failures and (carries_writes["now"] or pending):
            seen.append(1)
            raise failures.pop(0)
        await real_commit()

    monkeypatch.setattr(session, "commit", commit)
    return seen


# ---------------------------------------------------------------------------
# 1. Ownership: staging never ends the caller's transaction
# ---------------------------------------------------------------------------


async def _stage_debt_with_a_caller_row(db_session, runner, world: _World) -> str:
    caller_pid = f"CALLER_{_nonce()}"
    db_session.add(
        Participant(
            pid=caller_pid, display_name="Caller", public_key=f"pk_{caller_pid}"[:64],
            type="person", status="active",
        )
    )
    await db_session.flush()  # the caller's transaction is really open, with a write in it

    staged = await runner._inject_executor.stage_inject_event(
        db_session,
        scenario=_debt_scenario(world),
        event=_debt_event(world, "10.00"),
        pid_to_participant_id={world.creditor_pid: world.creditor_id, world.debtor_pid: world.debtor_id},
        locked_equivalent_ids={world.eq_id},
    )
    assert staged.applied == 1, staged
    return caller_pid


@pytest.mark.asyncio
async def test_staging_leaves_the_transaction_to_its_caller_rollback(db_session) -> None:
    world = await _seed_debt_world(db_session)
    runner, _arts = _make_runner()

    caller_pid = await _stage_debt_with_a_caller_row(db_session, runner, world)
    await db_session.rollback()

    assert await _fresh_debt(world) is None, (
        "the injected debt survived the CALLER's rollback: staging committed a transaction it "
        "does not own"
    )
    assert await _fresh_participant_ids(caller_pid) == [], (
        "the caller's own row survived its rollback: staging committed the caller's work"
    )


@pytest.mark.asyncio
async def test_staging_leaves_the_transaction_to_its_caller_commit_control(db_session) -> None:
    """Control: the same staging, committed by the caller, lands both rows - exactly."""
    world = await _seed_debt_world(db_session)
    runner, _arts = _make_runner()

    caller_pid = await _stage_debt_with_a_caller_row(db_session, runner, world)
    await db_session.commit()

    assert await _fresh_debt(world) == Decimal("10.00")
    assert len(await _fresh_participant_ids(caller_pid)) == 1


# ---------------------------------------------------------------------------
# 2. The owner's contract with its caller
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_owner_refuses_a_session_with_unflushed_changes(db_session) -> None:
    world = await _seed_debt_world(db_session)
    runner, arts = _make_runner()
    run = _debt_run(world)
    stray_pid = f"STRAY_{_nonce()}"
    db_session.add(
        Participant(
            pid=stray_pid, display_name="Stray", public_key=f"pk_{stray_pid}"[:64],
            type="person", status="active",
        )
    )

    with pytest.raises(RuntimeError, match="unflushed"):
        await runner._apply_due_scenario_events(
            db_session, run_id="r1", run=run, scenario=_debt_scenario(world)
        )

    assert run._real_fired_scenario_event_indexes == set()
    assert arts.payloads == []
    assert await _fresh_debt(world) is None
    assert await _fresh_participant_ids(stray_pid) == [], "the owner committed the caller's work"
    db_session.expunge_all()


@pytest.mark.asyncio
async def test_the_owner_returns_with_no_transaction_open(db_session) -> None:
    world = await _seed_debt_world(db_session)
    runner, _arts = _make_runner()
    run = _debt_run(world)

    # Hand over an OPEN read transaction, as the orchestrator does after loading participants.
    await db_session.execute(select(Equivalent.id))
    assert db_session.in_transaction()

    await runner._apply_due_scenario_events(
        db_session, run_id="r1", run=run, scenario=_debt_scenario(world)
    )

    assert not db_session.in_transaction()
    assert await _fresh_debt(world) == Decimal("10.00")
    assert run._real_fired_scenario_event_indexes == {0}


# ---------------------------------------------------------------------------
# 3. A transient failure restarts the whole unit of work, once
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("where", "error_kwargs"),
    [
        ("staging", {"sqlstate": "40001"}),
        ("staging", {"pgcode": "40P01"}),
        ("commit", {"sqlstate": "40001"}),
        # The owner lock not obtained in time: nothing was written, so the event must not be
        # dropped as "failed" - it restarts, and after the retry budget it stays pending.
        ("staging", {"sqlstate": "55P03"}),
    ],
)
async def test_a_transient_failure_is_retried_and_applied_exactly_once(
    db_session, monkeypatch, where, error_kwargs
) -> None:
    world = await _seed_debt_world(db_session, existing=Decimal("5.12345678"))
    runner, arts = _make_runner()
    run = _debt_run(world)

    if where == "staging":
        spy = _StageSpy(runner, fail_on_calls={1: _db_error(**error_kwargs)})
    else:
        spy = _StageSpy(runner)
        _fail_commits_carrying_writes(monkeypatch, db_session, [_db_error(**error_kwargs)])

    await runner._apply_due_scenario_events(
        db_session, run_id="r1", run=run, scenario=_debt_scenario(world)
    )

    assert spy.calls == 2, f"expected one retry of the whole unit of work, stage ran {spy.calls}x"
    assert await _fresh_debt(world) == Decimal("15.12345678"), (
        "the injected 10.00 must land exactly once on 5.12345678"
    )
    assert run._real_fired_scenario_event_indexes == {0}
    assert _notes(arts) == ["inject applied"]
    assert not db_session.in_transaction()


@pytest.mark.asyncio
@pytest.mark.parametrize("where", ["staging", "commit"])
async def test_a_second_transient_failure_propagates_and_leaves_the_event_pending(
    db_session, monkeypatch, where
) -> None:
    world = await _seed_debt_world(db_session)
    runner, arts = _make_runner()
    run = _debt_run(world)
    later = _debt_event(world, "1.00")

    if where == "staging":
        spy = _StageSpy(
            runner,
            fail_on_calls={1: _db_error(sqlstate="40001"), 2: _db_error(sqlstate="40001")},
        )
    else:
        spy = _StageSpy(runner)
        _fail_commits_carrying_writes(
            monkeypatch, db_session, [_db_error(sqlstate="40001"), _db_error(sqlstate="40P01")]
        )

    with pytest.raises(DBAPIError):
        await runner._apply_due_scenario_events(
            db_session, run_id="r1", run=run, scenario=_debt_scenario(world, _debt_event(world), later)
        )

    assert spy.calls == 2, "the later event must not run after the failure propagated"
    assert run._real_fired_scenario_event_indexes == set()
    assert await _fresh_debt(world) is None
    assert _notes(arts, 0) == [] and _notes(arts, 1) == []
    assert not db_session.in_transaction()


class _CommitFromAnotherSession:
    """Commits from a SECOND session between the owner's staging reads and the owner's flush.

    This is the real interleaving T1525 created, not a synthetic error. By the time staging returns,
    the unit of work has READ (its lock set, the rows staging touches) and has not yet written, so a
    commit by anyone else leaves it on a stale snapshot - and its next write, the explicit
    `await session.flush()`, is refused outright with SQLITE_BUSY_SNAPSHOT. The driver raises it;
    nothing here fabricates an error or its code.
    """

    def __init__(self, runner, *, on_calls: set[int]) -> None:
        self._real = runner._inject_executor.stage_inject_event
        self._on_calls = set(on_calls)
        self.calls = 0
        self.interleaved = 0
        runner._inject_executor.stage_inject_event = self  # this runner only

    async def __call__(self, session, **kwargs):
        self.calls += 1
        staged = await self._real(session, **kwargs)
        if self.calls in self._on_calls:
            n = _nonce()
            async with TestingSessionLocal() as other:
                other.add(
                    Participant(
                        pid=f"BUSY_{n}", display_name="Busy writer",
                        public_key=f"pk_busy_{n}"[:64], type="person", status="active",
                    )
                )
                await other.commit()
            self.interleaved += 1
        return staged


@pytest.mark.asyncio
async def test_a_stale_snapshot_is_transient_and_the_inject_lands_exactly_once(db_session) -> None:
    """T1525: a SQLite stale snapshot must restart the unit of work, not drop the inject.

    `_is_transient_inject_db_error` matched PostgreSQL SQLSTATEs only. On SQLite the owner reads its
    lock set and then writes, so a concurrent commit makes the write fail with SQLITE_BUSY_SNAPSHOT
    - and classified as an ordinary error it reopens exactly the loss mode 55P03 was added for: the
    owner records "inject failed (db error)", marks the event FIRED, and the inject is dropped.
    """
    assert _test_engine.dialect.name == "sqlite", (
        f"this stand forces a SQLite stale snapshot; the test engine is {_test_engine.dialect.name}"
    )
    world = await _seed_debt_world(db_session, existing=Decimal("5.12345678"))
    runner, arts = _make_runner()
    run = _debt_run(world)

    spy = _CommitFromAnotherSession(runner, on_calls={1})

    await runner._apply_due_scenario_events(
        db_session, run_id="r1", run=run, scenario=_debt_scenario(world)
    )

    assert spy.interleaved == 1, "non-vacuity: the concurrent commit never happened"
    assert spy.calls == 2, f"expected one retry of the whole unit of work, stage ran {spy.calls}x"
    assert await _fresh_debt(world) == Decimal("15.12345678"), (
        "the injected 10.00 must land exactly once on 5.12345678"
    )
    assert run._real_fired_scenario_event_indexes == {0}
    assert _notes(arts) == ["inject applied"]
    assert not db_session.in_transaction()


@pytest.mark.asyncio
async def test_a_stale_snapshot_on_both_attempts_leaves_the_event_pending(db_session) -> None:
    """The budget is finite, and a spent budget must leave the event PENDING, never fired."""
    assert _test_engine.dialect.name == "sqlite", _test_engine.dialect.name
    world = await _seed_debt_world(db_session)
    runner, arts = _make_runner()
    run = _debt_run(world)
    later = _debt_event(world, "1.00")

    spy = _CommitFromAnotherSession(runner, on_calls={1, 2})

    with pytest.raises(DBAPIError) as refusal:
        await runner._apply_due_scenario_events(
            db_session, run_id="r1", run=run, scenario=_debt_scenario(world, _debt_event(world), later)
        )

    # The error that propagated is the real one, by code - not something else that also fails.
    assert sqlite_busy_error_name(refusal.value) == "SQLITE_BUSY_SNAPSHOT", refusal.value
    assert spy.interleaved == 2, "non-vacuity: both attempts must have been beaten"
    assert spy.calls == 2, "the later event must not run after the failure propagated"
    assert run._real_fired_scenario_event_indexes == set()
    assert await _fresh_debt(world) is None
    assert _notes(arts, 0) == [] and _notes(arts, 1) == []
    assert not db_session.in_transaction()


@pytest.mark.asyncio
async def test_a_non_transient_staging_error_is_recorded_not_retried(db_session) -> None:
    """Anti-vacuum for the retry predicate: only the transient set restarts the unit of work.

    That set is 40001/40P01/55P03 and, since T1525, the SQLite busy family. A driver error outside
    it is recorded and the event fired, exactly as before.
    """
    world = await _seed_debt_world(db_session)
    runner, arts = _make_runner()
    run = _debt_run(world)
    spy = _StageSpy(runner, fail_on_calls={1: _db_error(sqlstate="23505")})

    await runner._apply_due_scenario_events(
        db_session, run_id="r1", run=run, scenario=_debt_scenario(world)
    )

    assert spy.calls == 1
    assert await _fresh_debt(world) is None
    assert run._real_fired_scenario_event_indexes == {0}
    assert _notes(arts) == ["inject failed (db error)"]


# ---------------------------------------------------------------------------
# 4. The lock set widens once when staging discovers an equivalent outside it
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _FreezeWorld:
    run_eq_id: uuid.UUID
    run_eq_code: str
    other_eq_id: uuid.UUID
    target_id: uuid.UUID
    target_pid: str
    other_id: uuid.UUID
    other_pid: str
    tl_id: uuid.UUID


async def _seed_freeze_world(db_session) -> _FreezeWorld:
    n = _nonce()
    run_eq = Equivalent(code=f"FR{n}".upper()[:16], precision=2, is_active=True)
    other_eq = Equivalent(code=f"FO{n}".upper()[:16], precision=2, is_active=True)
    target = Participant(
        pid=f"FZT_{n}", display_name="Target", public_key=f"pk_fzt_{n}"[:64],
        type="person", status="active",
    )
    other = Participant(
        pid=f"FZO_{n}", display_name="Other", public_key=f"pk_fzo_{n}"[:64],
        type="person", status="active",
    )
    db_session.add_all([run_eq, other_eq, target, other])
    await db_session.flush()
    # The only incident trustline lives in an equivalent the run does NOT list.
    tl = TrustLine(
        from_participant_id=other.id,
        to_participant_id=target.id,
        equivalent_id=other_eq.id,
        limit=Decimal("50"),
        status="active",
    )
    db_session.add(tl)
    await db_session.commit()
    return _FreezeWorld(
        run_eq.id, run_eq.code, other_eq.id, target.id, target.pid, other.id, other.pid, tl.id
    )


def _freeze_scenario(w: _FreezeWorld) -> dict[str, Any]:
    return {
        "participants": [{"id": w.target_pid}, {"id": w.other_pid}],
        "trustlines": [],
        "events": [
            {
                "type": "inject",
                "time": 0,
                "effects": [
                    {"op": "freeze_participant", "participant_id": w.target_pid},
                ],
            }
        ],
    }


async def _fresh_freeze_state(w: _FreezeWorld) -> tuple[str, str]:
    async with TestingSessionLocal() as s:
        p_status = (
            await s.execute(select(Participant.status).where(Participant.id == w.target_id))
        ).scalar_one()
        tl_status = (
            await s.execute(select(TrustLine.status).where(TrustLine.id == w.tl_id))
        ).scalar_one()
    return str(p_status), str(tl_status)


@pytest.mark.asyncio
async def test_staging_a_freeze_outside_the_lock_set_raises_before_staging_it(db_session) -> None:
    w = await _seed_freeze_world(db_session)
    runner, _arts = _make_runner()

    with pytest.raises(InjectOwnerLockSetTooNarrow) as raised:
        await runner._inject_executor.stage_inject_event(
            db_session,
            scenario=_freeze_scenario(w),
            event=_freeze_scenario(w)["events"][0],
            pid_to_participant_id={},
            locked_equivalent_ids={w.run_eq_id},
        )

    assert raised.value.missing_equivalent_ids == frozenset({w.other_eq_id})
    assert not (db_session.new or db_session.dirty or db_session.deleted), (
        "the freeze staged changes before discovering it lacked a lock: "
        f"new={db_session.new} dirty={db_session.dirty}"
    )
    await db_session.rollback()
    assert await _fresh_freeze_state(w) == ("active", "active")


@pytest.mark.asyncio
async def test_the_owner_locks_a_freezes_incident_equivalents_before_staging(db_session) -> None:
    """The owner reads the incident equivalents first: one attempt, no expansion needed."""
    w = await _seed_freeze_world(db_session)
    runner, arts = _make_runner()
    run = _make_run(
        participants=[(w.target_id, w.target_pid), (w.other_id, w.other_pid)],
        equivalents=[w.run_eq_code],
    )
    spy = _StageSpy(runner)

    await runner._apply_due_scenario_events(
        db_session, run_id="r1", run=run, scenario=_freeze_scenario(w)
    )

    assert spy.locked_sets == [frozenset({w.run_eq_id, w.other_eq_id})], spy.locked_sets
    assert await _fresh_freeze_state(w) == ("suspended", "frozen")
    assert run._real_fired_scenario_event_indexes == {0}
    assert _notes(arts) == ["inject applied"]


@pytest.mark.asyncio
async def test_the_owner_widens_the_lock_set_once_for_a_trustline_created_after_its_read(
    db_session,
) -> None:
    """The expansion is the backstop for a race: a trustline appears between the read and staging.

    Modelled exactly - the incident trustline is committed by another session right after the
    owner's lock-set read - rather than by hoping two tasks interleave.
    """
    w = await _seed_freeze_world(db_session)
    async with TestingSessionLocal() as s:  # start with no incident trustline at all
        await s.execute(delete(TrustLine).where(TrustLine.id == w.tl_id))
        await s.commit()
    runner, arts = _make_runner()
    run = _make_run(
        participants=[(w.target_id, w.target_pid), (w.other_id, w.other_pid)],
        equivalents=[w.run_eq_code],
    )
    spy = _StageSpy(runner)

    real_resolve = runner._resolve_inject_owner_lock_ids
    created: list[uuid.UUID] = []

    async def _resolve_then_a_trustline_appears(session, **kwargs):
        lock_ids = await real_resolve(session, **kwargs)
        if not created:
            async with TestingSessionLocal() as s:
                tl = TrustLine(
                    from_participant_id=w.other_id,
                    to_participant_id=w.target_id,
                    equivalent_id=w.other_eq_id,
                    limit=Decimal("50"),
                    status="active",
                )
                s.add(tl)
                await s.commit()
                created.append(tl.id)
        return lock_ids

    runner._resolve_inject_owner_lock_ids = _resolve_then_a_trustline_appears  # type: ignore[method-assign]

    await runner._apply_due_scenario_events(
        db_session, run_id="r1", run=run, scenario=_freeze_scenario(w)
    )

    assert created, "non-vacuity: the racing trustline was never created"
    assert spy.locked_sets == [
        frozenset({w.run_eq_id}),
        frozenset({w.run_eq_id, w.other_eq_id}),
    ], spy.locked_sets
    async with TestingSessionLocal() as s:
        tl_status = (
            await s.execute(select(TrustLine.status).where(TrustLine.id == created[0]))
        ).scalar_one()
        p_status = (
            await s.execute(select(Participant.status).where(Participant.id == w.target_id))
        ).scalar_one()
    assert (str(p_status), str(tl_status)) == ("suspended", "frozen")
    assert run._real_fired_scenario_event_indexes == {0}
    assert _notes(arts) == ["inject applied"]


@pytest.mark.asyncio
async def test_a_freeze_of_two_participants_in_two_outside_equivalents_completes(
    db_session,
) -> None:
    """External review of step 3, the countercheck: RED before the owner read incident equivalents.

    Two participants, each with its only incident trustline in a different equivalent the run
    does not list. Staging stops at the first missing equivalent; the one allowed expansion is
    spent on it; the retry stops at the second - on a topology nobody is changing - and the event
    stayed pending on every tick.
    """
    n = _nonce()
    run_eq = Equivalent(code=f"MR{n}".upper()[:16], precision=2, is_active=True)
    eq_a = Equivalent(code=f"MA{n}".upper()[:16], precision=2, is_active=True)
    eq_b = Equivalent(code=f"MB{n}".upper()[:16], precision=2, is_active=True)
    people = [
        Participant(
            pid=f"MF{i}_{n}", display_name=f"P{i}", public_key=f"pk_mf{i}_{n}"[:64],
            type="person", status="active",
        )
        for i in range(4)
    ]
    db_session.add_all([run_eq, eq_a, eq_b, *people])
    await db_session.flush()
    target_a, peer_a, target_b, peer_b = people
    tl_a = TrustLine(
        from_participant_id=peer_a.id, to_participant_id=target_a.id,
        equivalent_id=eq_a.id, limit=Decimal("50"), status="active",
    )
    tl_b = TrustLine(
        from_participant_id=peer_b.id, to_participant_id=target_b.id,
        equivalent_id=eq_b.id, limit=Decimal("50"), status="active",
    )
    db_session.add_all([tl_a, tl_b])
    await db_session.commit()

    runner, arts = _make_runner()
    run = _make_run(
        participants=[(p.id, p.pid) for p in people], equivalents=[run_eq.code]
    )
    spy = _StageSpy(runner)
    scenario = {
        "participants": [{"id": p.pid} for p in people],
        "trustlines": [],
        "events": [
            {
                "type": "inject",
                "time": 0,
                "effects": [
                    {"op": "freeze_participant", "participant_id": target_a.pid},
                    {"op": "freeze_participant", "participant_id": target_b.pid},
                ],
            }
        ],
    }

    await runner._apply_due_scenario_events(db_session, run_id="r1", run=run, scenario=scenario)

    assert spy.locked_sets == [frozenset({run_eq.id, eq_a.id, eq_b.id})], spy.locked_sets
    async with TestingSessionLocal() as s:
        statuses = dict(
            (
                await s.execute(
                    select(Participant.pid, Participant.status).where(
                        Participant.id.in_([target_a.id, target_b.id])
                    )
                )
            ).all()
        )
        tl_statuses = set(
            (
                await s.execute(
                    select(TrustLine.status).where(TrustLine.id.in_([tl_a.id, tl_b.id]))
                )
            ).scalars().all()
        )
    assert statuses == {target_a.pid: "suspended", target_b.pid: "suspended"}, statuses
    assert tl_statuses == {"frozen"}, tl_statuses
    assert run._real_fired_scenario_event_indexes == {0}
    assert _notes(arts) == ["inject applied"]


@pytest.mark.asyncio
async def test_a_second_lock_set_expansion_leaves_the_event_pending(db_session) -> None:
    world = await _seed_debt_world(db_session)
    runner, arts = _make_runner()
    run = _debt_run(world)
    missing = frozenset({uuid.uuid4()})
    spy = _StageSpy(
        runner,
        fail_on_calls={
            1: InjectOwnerLockSetTooNarrow(missing),
            2: InjectOwnerLockSetTooNarrow(frozenset({uuid.uuid4()})),
        },
    )

    with pytest.raises(InjectOwnerLockSetTooNarrow):
        await runner._apply_due_scenario_events(
            db_session, run_id="r1", run=run, scenario=_debt_scenario(world)
        )

    assert spy.calls == 2
    assert missing <= spy.locked_sets[1]
    assert run._real_fired_scenario_event_indexes == set()
    assert await _fresh_debt(world) is None
    assert _notes(arts) == []


@pytest.mark.asyncio
async def test_a_flush_error_is_a_known_rollback_not_an_unknown_outcome(
    db_session, monkeypatch
) -> None:
    """A write refused while flushing never reached the commit: "failed", not "outcome unknown".

    Before the owner flushed explicitly, the flush ran inside `commit()` and its error was reported
    as a commit whose outcome could not be known.
    """
    world = await _seed_debt_world(db_session)
    runner, arts = _make_runner()
    run = _debt_run(world)
    spy = _StageSpy(runner)

    real_flush = db_session.flush
    flush_failures = [_db_error(sqlstate="23505")]

    async def flush(*args, **kwargs) -> None:
        if flush_failures and (db_session.new or db_session.dirty):
            raise flush_failures.pop(0)
        await real_flush(*args, **kwargs)

    monkeypatch.setattr(db_session, "flush", flush)

    await runner._apply_due_scenario_events(
        db_session, run_id="r1", run=run, scenario=_debt_scenario(world)
    )

    assert not flush_failures, "non-vacuity: the flush failure was never injected"
    assert spy.calls == 1
    assert await _fresh_debt(world) is None
    assert run._real_fired_scenario_event_indexes == {0}
    assert _notes(arts) == ["inject failed (db error)"]
    assert not db_session.in_transaction()


# ---------------------------------------------------------------------------
# 5. At most once: an unknown commit outcome is never re-applied
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_non_transient_commit_error_keeps_the_event_fired_and_is_not_retried(
    db_session, monkeypatch
) -> None:
    world = await _seed_debt_world(db_session)
    runner, arts = _make_runner()
    run = _debt_run(world)
    spy = _StageSpy(runner)
    _fail_commits_carrying_writes(monkeypatch, db_session, [_db_error(sqlstate="08006")])

    await runner._apply_due_scenario_events(
        db_session, run_id="r1", run=run, scenario=_debt_scenario(world)
    )

    assert spy.calls == 1, "a commit whose outcome is unknown must not be staged again"
    assert run._real_fired_scenario_event_indexes == {0}
    assert _notes(arts) == ["inject outcome unknown (commit error)"]
    assert not db_session.in_transaction()


@pytest.mark.asyncio
async def test_cancellation_during_the_commit_keeps_the_event_fired(
    db_session, monkeypatch
) -> None:
    world = await _seed_debt_world(db_session)
    runner, arts = _make_runner()
    run = _debt_run(world)
    spy = _StageSpy(runner)
    _fail_commits_carrying_writes(monkeypatch, db_session, [asyncio.CancelledError()])

    with pytest.raises(asyncio.CancelledError):
        await runner._apply_due_scenario_events(
            db_session, run_id="r1", run=run, scenario=_debt_scenario(world)
        )

    assert spy.calls == 1
    assert run._real_fired_scenario_event_indexes == {0}, (
        "the commit may have landed; the event must not be applied again on the next tick"
    )
    assert _notes(arts) == []
    await db_session.rollback()


@pytest.mark.asyncio
async def test_cancellation_while_staging_rolls_back_and_leaves_the_event_pending(
    db_session,
) -> None:
    world = await _seed_debt_world(db_session)
    runner, arts = _make_runner()
    run = _debt_run(world)
    spy = _StageSpy(runner, fail_on_calls={1: asyncio.CancelledError()})

    with pytest.raises(asyncio.CancelledError):
        await runner._apply_due_scenario_events(
            db_session, run_id="r1", run=run, scenario=_debt_scenario(world)
        )

    assert spy.calls == 1
    assert run._real_fired_scenario_event_indexes == set()
    assert not db_session.in_transaction()
    assert await _fresh_debt(world) is None
    assert _notes(arts) == []


# ---------------------------------------------------------------------------
# 6. Publication failing after the commit does not undo the commit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("failing", ["edge_patch_builder", "artifacts"])
async def test_a_publish_failure_after_commit_keeps_the_committed_inject(
    db_session, monkeypatch, failing
) -> None:
    world = await _seed_debt_world(db_session)
    runner, arts = _make_runner()
    run = _debt_run(world)
    spy = _StageSpy(runner)

    async def _broken_builder(**_kwargs):
        raise RuntimeError("edge patch builder failed")

    def _broken_enqueue(_run_id, _payload):
        raise RuntimeError("artifacts failed")

    if failing == "edge_patch_builder":
        monkeypatch.setattr(runner, "_build_edge_patch_for_equivalent", _broken_builder)
    else:
        monkeypatch.setattr(arts, "enqueue_event_artifact", _broken_enqueue)

    await runner._apply_due_scenario_events(
        db_session, run_id="r1", run=run, scenario=_debt_scenario(world)
    )

    assert await _fresh_debt(world) == Decimal("10.00")
    assert run._real_fired_scenario_event_indexes == {0}
    assert spy.calls == 1
    assert not db_session.in_transaction()


# ---------------------------------------------------------------------------
# 7. A rolled-back participant id never reaches the shared pid map
# ---------------------------------------------------------------------------


def _add_participant_event(sponsor_pid: str, new_pid: str, eq_code: str) -> dict[str, Any]:
    return {
        "type": "inject",
        "time": 0,
        "effects": [
            {
                "op": "add_participant",
                "participant": {"id": new_pid, "name": "Newcomer"},
                "initial_trustlines": [
                    {"sponsor": sponsor_pid, "equivalent": eq_code, "limit": "20"}
                ],
            }
        ],
    }


@pytest.mark.asyncio
async def test_staging_does_not_touch_the_shared_pid_map(db_session) -> None:
    world = await _seed_debt_world(db_session)
    runner, _arts = _make_runner()
    new_pid = f"NEWP_{_nonce()}"
    shared = {world.creditor_pid: world.creditor_id}
    before = dict(shared)

    staged = await runner._inject_executor.stage_inject_event(
        db_session,
        scenario={"participants": [], "trustlines": []},
        event=_add_participant_event(world.creditor_pid, new_pid, world.eq_code),
        pid_to_participant_id=shared,
        locked_equivalent_ids={world.eq_id},
    )
    assert new_pid in staged.pid_additions
    assert shared == before, "staging wrote a participant id that does not exist yet into the shared map"

    await db_session.rollback()
    assert await _fresh_participant_ids(new_pid) == []
    assert shared == before


@pytest.mark.asyncio
async def test_a_rolled_back_add_participant_is_not_seen_by_the_retry_but_a_committed_one_is(
    db_session, monkeypatch
) -> None:
    world = await _seed_debt_world(db_session)
    runner, _arts = _make_runner()
    run = _debt_run(world)
    new_pid = f"NEWP_{_nonce()}"
    scenario = {
        "participants": [{"id": world.creditor_pid}, {"id": world.debtor_pid}],
        "trustlines": [],
        "events": [
            _add_participant_event(world.creditor_pid, new_pid, world.eq_code),
            _debt_event(world, "1.00"),
        ],
    }
    spy = _StageSpy(runner)
    _fail_commits_carrying_writes(monkeypatch, db_session, [_db_error(sqlstate="40001")])

    await runner._apply_due_scenario_events(db_session, run_id="r1", run=run, scenario=scenario)

    committed_ids = await _fresh_participant_ids(new_pid)
    assert len(committed_ids) == 1, committed_ids
    assert spy.calls == 3  # add_participant twice (retry), then the debt event once
    assert new_pid not in spy.pid_maps[1], (
        "the retry of add_participant was handed the id its rolled-back first attempt staged"
    )
    assert spy.pid_maps[2].get(new_pid) == committed_ids[0], (
        "the next event must see the participant id that was actually committed"
    )
    assert run._real_fired_scenario_event_indexes == {0, 1}
