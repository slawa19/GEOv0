"""Programme 015, phase B step 3: the inject stages, its owner commits.

WHAT CHANGED. `InjectExecutor.apply_inject_event` committed a transaction it had not opened. The
equivalent owner lock is transactional, so that commit released the lock the tick orchestrator
held, and the next inject event of the tick wrote `debts` unlocked
(`tests/integration/test_p015_inject_holds_the_owner_lock_postgres.py` is the PostgreSQL
reproducer). The executor now only STAGES (`stage_inject_event`) and PUBLISHES
(`publish_committed_inject`); `RealRunnerImpl._apply_due_scenario_events` owns every boundary.

THE CARRIER is an inject `create_trustline` (030 S3b: the `inject_debt` effect that carried these properties is gone; ownership,
retry, lock-set and publication are the same for every effect of the event).

WHAT THIS FILE PROVES, always through the database: every effect is read back through a NEW session
(`world.sessions()`), never through the session under test and never through a counter standing in
for the row. The lock itself is proven by the PostgreSQL module named above; the lock SET check is
proven here.

MODE B, EVERY TEST THAT TAKES `db_session` (017 stage 2b, T1702). A new session sees only what was
COMMITTED. In mode A on PostgreSQL nothing the test commits is - `commit()` releases a SAVEPOINT
inside the fixture's outer transaction - so the reads below saw no seed at all: twelve tests failed
on it (`NoResultFound`, `assert None == Decimal('10.00')`), and every assertion of ABSENCE
(`_fresh_lines(world) == []`) passed whether or not the owner had rolled back, because the second
session could not have seen the row either way. One test hung for ever: its second session inserts a
trustline whose foreign keys wait on participants the first, still-open transaction holds (stage-2
catalogue, section 5). In mode B the commits are real, on a clone, and `world.sessions()` reaches that
clone. The two tests of SQLite's own busy refusal left with SQLite (017 stage 3, slice S3); a real
40001 restarting the whole unit of work is `tests/integration/
test_p015_inject_retries_a_serialization_failure_postgres.py`.

A NOTE ON THE STAND. The owner rolls back, and a rollback expires every instance in the session, so ids and pids are captured as plain values right
after seeding and ORM instances are not touched afterwards.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import event, select
from sqlalchemy.exc import DBAPIError

from app.core.simulator.inject_executor import InjectOwnerLockSetTooNarrow
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine
from tests.conftest import MODE_B, sessionmaker_of
from tests.unit.test_scenario_inject_topology import _make_run, _make_runner, _nonce


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
    #: Where a NEW session over this world's database comes from: the mode-B clone's sessionmaker.
    sessions: Any


async def _seed_line_world(db_session, *, sessions: Any = None) -> _World:
    """Two active participants and an equivalent, and NO line between them: the carrier of every
    test below is an inject `create_trustline` creditor -> debtor, so "the line exists exactly once"
    and "no line exists" are the two observable outcomes."""
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
    await db_session.commit()
    return _World(
        eq.id, eq.code, creditor.id, creditor.pid, debtor.id, debtor.pid,
        sessions if sessions is not None else sessionmaker_of(db_session),
    )


def _line_event(world: _World, limit: str = "100.00", *, reverse: bool = False) -> dict[str, Any]:
    frm, to = (
        (world.debtor_pid, world.creditor_pid) if reverse else (world.creditor_pid, world.debtor_pid)
    )
    return {
        "type": "inject",
        "time": 0,
        "effects": [
            {
                "op": "create_trustline",
                "from": frm,
                "to": to,
                "equivalent": world.eq_code,
                "limit": limit,
            }
        ],
    }


def _line_scenario(world: _World, *events: dict[str, Any]) -> dict[str, Any]:
    return {
        "participants": [{"id": world.creditor_pid}, {"id": world.debtor_pid}],
        "trustlines": [],
        "events": list(events) or [_line_event(world)],
    }


def _line_run(world: _World):
    return _make_run(
        participants=[(world.creditor_id, world.creditor_pid), (world.debtor_id, world.debtor_pid)],
        equivalents=[world.eq_code],
    )


async def _fresh_lines(world: _World) -> list[tuple[uuid.UUID, uuid.UUID, str, Decimal]]:
    """Every trust line between the world's two participants, in either direction, read through a NEW session."""
    ids = (world.creditor_id, world.debtor_id)
    async with world.sessions() as s:
        rows = (
            await s.execute(
                select(
                    TrustLine.from_participant_id,
                    TrustLine.to_participant_id,
                    TrustLine.status,
                    TrustLine.limit,
                ).where(
                    TrustLine.equivalent_id == world.eq_id,
                    TrustLine.from_participant_id.in_(ids),
                    TrustLine.to_participant_id.in_(ids),
                )
            )
        ).all()
    return sorted(
        ((r[0], r[1], str(r[2]), Decimal(str(r[3]))) for r in rows), key=lambda t: (str(t[0]), str(t[1]))
    )


def _the_line(world: _World, limit: str = "100.00") -> tuple[uuid.UUID, uuid.UUID, str, Decimal]:
    """What `_fresh_lines` returns once the event's one `create_trustline` has landed, exactly once."""
    return (world.creditor_id, world.debtor_id, "active", Decimal(limit))


async def _fresh_participant_ids(world: _World, pid: str) -> list[uuid.UUID]:
    async with world.sessions() as s:
        return list(
            (await s.execute(select(Participant.id).where(Participant.pid == pid))).scalars().all()
        )


def _notes(arts, event_index: int = 0) -> list[str]:
    return [
        str(p["scenario"]["description"])
        for p in arts.payloads
        if p.get("type") == "note" and p.get("scenario", {}).get("event_index") == event_index
    ]


def _stats(arts, event_index: int = 0) -> list[dict[str, Any]]:
    """The `stats` of each "inject applied" note of one event: the retry's own count of what it applied."""
    return [
        dict(p["scenario"]["stats"])
        for p in arts.payloads
        if p.get("type") == "note"
        and p.get("scenario", {}).get("event_index") == event_index
        and "stats" in p.get("scenario", {})
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


async def _stage_line_with_a_caller_row(db_session, runner, world: _World) -> str:
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
        scenario=_line_scenario(world),
        event=_line_event(world),
        pid_to_participant_id={
            world.creditor_pid: world.creditor_id,
            world.debtor_pid: world.debtor_id,
        },
        locked_equivalent_ids={world.eq_id},
    )
    assert staged.applied == 1, staged
    return caller_pid


@MODE_B
@pytest.mark.asyncio
async def test_staging_leaves_the_transaction_to_its_caller_rollback(db_session) -> None:
    world = await _seed_line_world(db_session)
    runner, _arts = _make_runner()

    caller_pid = await _stage_line_with_a_caller_row(db_session, runner, world)
    await db_session.rollback()

    assert await _fresh_lines(world) == [], (
        "the injected line survived the CALLER's rollback: staging committed a transaction it "
        "does not own"
    )
    assert await _fresh_participant_ids(world, caller_pid) == [], (
        "the caller's own row survived its rollback: staging committed the caller's work"
    )


@MODE_B
@pytest.mark.asyncio
async def test_staging_leaves_the_transaction_to_its_caller_commit_control(db_session) -> None:
    """Control: the same staging, committed by the caller, lands both rows - exactly."""
    world = await _seed_line_world(db_session)
    runner, _arts = _make_runner()

    caller_pid = await _stage_line_with_a_caller_row(db_session, runner, world)
    await db_session.commit()

    assert await _fresh_lines(world) == [_the_line(world)]
    assert len(await _fresh_participant_ids(world, caller_pid)) == 1


# ---------------------------------------------------------------------------
# 2. The owner's contract with its caller
# ---------------------------------------------------------------------------


@MODE_B
@pytest.mark.asyncio
async def test_the_owner_refuses_a_session_with_unflushed_changes(db_session) -> None:
    world = await _seed_line_world(db_session)
    runner, arts = _make_runner()
    run = _line_run(world)
    stray_pid = f"STRAY_{_nonce()}"
    db_session.add(
        Participant(
            pid=stray_pid, display_name="Stray", public_key=f"pk_{stray_pid}"[:64],
            type="person", status="active",
        )
    )

    with pytest.raises(RuntimeError, match="unflushed"):
        await runner._apply_due_scenario_events(
            db_session, run_id="r1", run=run, scenario=_line_scenario(world)
        )

    assert run._real_fired_scenario_event_indexes == set()
    assert arts.payloads == []
    assert await _fresh_lines(world) == []
    assert await _fresh_participant_ids(world, stray_pid) == [], "the owner committed the caller's work"
    db_session.expunge_all()


@MODE_B
@pytest.mark.asyncio
async def test_the_owner_returns_with_no_transaction_open(db_session) -> None:
    world = await _seed_line_world(db_session)
    runner, _arts = _make_runner()
    run = _line_run(world)

    # Hand over an OPEN read transaction, as the orchestrator does after loading participants.
    await db_session.execute(select(Equivalent.id))
    assert db_session.in_transaction()

    await runner._apply_due_scenario_events(
        db_session, run_id="r1", run=run, scenario=_line_scenario(world)
    )

    assert not db_session.in_transaction()
    assert await _fresh_lines(world) == [_the_line(world)]
    assert run._real_fired_scenario_event_indexes == {0}


# ---------------------------------------------------------------------------
# 3. A transient failure restarts the whole unit of work, once
# ---------------------------------------------------------------------------


@MODE_B
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
    world = await _seed_line_world(db_session)
    runner, arts = _make_runner()
    run = _line_run(world)

    if where == "staging":
        spy = _StageSpy(runner, fail_on_calls={1: _db_error(**error_kwargs)})
    else:
        spy = _StageSpy(runner)
        _fail_commits_carrying_writes(monkeypatch, db_session, [_db_error(**error_kwargs)])

    await runner._apply_due_scenario_events(
        db_session, run_id="r1", run=run, scenario=_line_scenario(world)
    )

    assert spy.calls == 2, f"expected one retry of the whole unit of work, stage ran {spy.calls}x"
    assert await _fresh_lines(world) == [_the_line(world)], (
        "the injected line must land exactly once"
    )
    # Anti-vacuum: a first attempt that was NOT rolled back would leave the line behind, the retry would
    # skip it as "already exists", and the single row above would be the first attempt's, not the retry's.
    assert _stats(arts) == [{"applied": 1, "skipped": 0}], _stats(arts)
    assert run._real_fired_scenario_event_indexes == {0}
    assert _notes(arts) == ["inject applied"]
    assert not db_session.in_transaction()


@MODE_B
@pytest.mark.asyncio
@pytest.mark.parametrize("where", ["staging", "commit"])
async def test_a_second_transient_failure_propagates_and_leaves_the_event_pending(
    db_session, monkeypatch, where
) -> None:
    world = await _seed_line_world(db_session)
    runner, arts = _make_runner()
    run = _line_run(world)
    later = _line_event(world, reverse=True)

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
            db_session, run_id="r1", run=run, scenario=_line_scenario(world, _line_event(world), later)
        )

    assert spy.calls == 2, "the later event must not run after the failure propagated"
    assert run._real_fired_scenario_event_indexes == set()
    assert await _fresh_lines(world) == []
    assert _notes(arts, 0) == [] and _notes(arts, 1) == []
    assert not db_session.in_transaction()


@MODE_B
@pytest.mark.asyncio
async def test_a_non_transient_staging_error_is_recorded_not_retried(db_session) -> None:
    """Anti-vacuum for the retry predicate: only the transient set restarts the unit of work.

    That set is 40001/40P01/55P03. A driver error outside it is recorded and the event fired,
    exactly as before.
    """
    world = await _seed_line_world(db_session)
    runner, arts = _make_runner()
    run = _line_run(world)
    spy = _StageSpy(runner, fail_on_calls={1: _db_error(sqlstate="23505")})

    await runner._apply_due_scenario_events(
        db_session, run_id="r1", run=run, scenario=_line_scenario(world)
    )

    assert spy.calls == 1
    assert await _fresh_lines(world) == []
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
    sessions: Any


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
        run_eq.id, run_eq.code, other_eq.id, target.id, target.pid, other.id, other.pid, tl.id,
        sessionmaker_of(db_session),
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
    async with w.sessions() as s:
        p_status = (
            await s.execute(select(Participant.status).where(Participant.id == w.target_id))
        ).scalar_one()
        tl_status = (
            await s.execute(select(TrustLine.status).where(TrustLine.id == w.tl_id))
        ).scalar_one()
    return str(p_status), str(tl_status)


@MODE_B
@pytest.mark.asyncio
async def test_a_freeze_names_no_equivalent_and_writes_no_line(db_session) -> None:
    """028 `F-028-29`: a freeze suspends the participant and writes no trust line, so the owner's set stays the run's
    (until then it read the incident equivalents first, and the line became `frozen`; the tests of that expansion
    were removed with it)."""
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

    assert spy.locked_sets == [frozenset({w.run_eq_id})], spy.locked_sets
    assert await _fresh_freeze_state(w) == ("suspended", "active")
    assert run._real_fired_scenario_event_indexes == {0}
    assert _notes(arts) == ["inject applied"]


@MODE_B
@pytest.mark.asyncio
async def test_a_second_lock_set_expansion_leaves_the_event_pending(db_session) -> None:
    world = await _seed_line_world(db_session)
    runner, arts = _make_runner()
    run = _line_run(world)
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
            db_session, run_id="r1", run=run, scenario=_line_scenario(world)
        )

    assert spy.calls == 2
    assert missing <= spy.locked_sets[1]
    assert run._real_fired_scenario_event_indexes == set()
    assert await _fresh_lines(world) == []
    assert _notes(arts) == []


@MODE_B
@pytest.mark.asyncio
async def test_a_flush_error_is_a_known_rollback_not_an_unknown_outcome(
    db_session, monkeypatch
) -> None:
    """A write refused while flushing never reached the commit: "failed", not "outcome unknown".

    Before the owner flushed explicitly, the flush ran inside `commit()` and its error was reported
    as a commit whose outcome could not be known.
    """
    world = await _seed_line_world(db_session)
    runner, arts = _make_runner()
    run = _line_run(world)
    spy = _StageSpy(runner)

    real_flush = db_session.flush
    flush_failures = [_db_error(sqlstate="23505")]

    async def flush(*args, **kwargs) -> None:
        if flush_failures and (db_session.new or db_session.dirty):
            raise flush_failures.pop(0)
        await real_flush(*args, **kwargs)

    monkeypatch.setattr(db_session, "flush", flush)

    await runner._apply_due_scenario_events(
        db_session, run_id="r1", run=run, scenario=_line_scenario(world)
    )

    assert not flush_failures, "non-vacuity: the flush failure was never injected"
    assert spy.calls == 1
    assert await _fresh_lines(world) == []
    assert run._real_fired_scenario_event_indexes == {0}
    assert _notes(arts) == ["inject failed (db error)"]
    assert not db_session.in_transaction()


# ---------------------------------------------------------------------------
# 5. At most once: an unknown commit outcome is never re-applied
# ---------------------------------------------------------------------------


@MODE_B
@pytest.mark.asyncio
async def test_a_non_transient_commit_error_keeps_the_event_fired_and_is_not_retried(
    db_session, monkeypatch
) -> None:
    world = await _seed_line_world(db_session)
    runner, arts = _make_runner()
    run = _line_run(world)
    spy = _StageSpy(runner)
    _fail_commits_carrying_writes(monkeypatch, db_session, [_db_error(sqlstate="08006")])

    await runner._apply_due_scenario_events(
        db_session, run_id="r1", run=run, scenario=_line_scenario(world)
    )

    assert spy.calls == 1, "a commit whose outcome is unknown must not be staged again"
    assert run._real_fired_scenario_event_indexes == {0}
    assert _notes(arts) == ["inject outcome unknown (commit error)"]
    assert not db_session.in_transaction()


@MODE_B
@pytest.mark.asyncio
async def test_cancellation_during_the_commit_keeps_the_event_fired(
    db_session, monkeypatch
) -> None:
    world = await _seed_line_world(db_session)
    runner, arts = _make_runner()
    run = _line_run(world)
    spy = _StageSpy(runner)
    _fail_commits_carrying_writes(monkeypatch, db_session, [asyncio.CancelledError()])

    with pytest.raises(asyncio.CancelledError):
        await runner._apply_due_scenario_events(
            db_session, run_id="r1", run=run, scenario=_line_scenario(world)
        )

    assert spy.calls == 1
    assert run._real_fired_scenario_event_indexes == {0}, (
        "the commit may have landed; the event must not be applied again on the next tick"
    )
    assert _notes(arts) == []
    await db_session.rollback()


@MODE_B
@pytest.mark.asyncio
async def test_cancellation_while_staging_rolls_back_and_leaves_the_event_pending(
    db_session,
) -> None:
    world = await _seed_line_world(db_session)
    runner, arts = _make_runner()
    run = _line_run(world)
    spy = _StageSpy(runner, fail_on_calls={1: asyncio.CancelledError()})

    with pytest.raises(asyncio.CancelledError):
        await runner._apply_due_scenario_events(
            db_session, run_id="r1", run=run, scenario=_line_scenario(world)
        )

    assert spy.calls == 1
    assert run._real_fired_scenario_event_indexes == set()
    assert not db_session.in_transaction()
    assert await _fresh_lines(world) == []
    assert _notes(arts) == []


# ---------------------------------------------------------------------------
# 6. Publication failing after the commit does not undo the commit
# ---------------------------------------------------------------------------


@MODE_B
@pytest.mark.asyncio
@pytest.mark.parametrize("failing", ["topology_broadcast", "artifacts"])
async def test_a_publish_failure_after_commit_keeps_the_committed_inject(
    db_session, monkeypatch, failing
) -> None:
    world = await _seed_line_world(db_session)
    runner, arts = _make_runner()
    run = _line_run(world)
    spy = _StageSpy(runner)

    def _broken_broadcast(**_kwargs):
        raise RuntimeError("topology broadcast failed")

    def _broken_enqueue(_run_id, _payload):
        raise RuntimeError("artifacts failed")

    if failing == "topology_broadcast":
        monkeypatch.setattr(runner._inject_executor, "broadcast_topology_changed", _broken_broadcast)
    else:
        monkeypatch.setattr(arts, "enqueue_event_artifact", _broken_enqueue)

    await runner._apply_due_scenario_events(
        db_session, run_id="r1", run=run, scenario=_line_scenario(world)
    )

    assert await _fresh_lines(world) == [_the_line(world)]
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


@MODE_B
@pytest.mark.asyncio
async def test_staging_does_not_touch_the_shared_pid_map(db_session) -> None:
    world = await _seed_line_world(db_session)
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
    assert await _fresh_participant_ids(world, new_pid) == []
    assert shared == before


@MODE_B
@pytest.mark.asyncio
async def test_a_rolled_back_add_participant_is_not_seen_by_the_retry_but_a_committed_one_is(
    db_session, monkeypatch
) -> None:
    world = await _seed_line_world(db_session)
    runner, _arts = _make_runner()
    run = _line_run(world)
    new_pid = f"NEWP_{_nonce()}"
    scenario = {
        "participants": [{"id": world.creditor_pid}, {"id": world.debtor_pid}],
        "trustlines": [],
        "events": [
            _add_participant_event(world.creditor_pid, new_pid, world.eq_code),
            _line_event(world),
        ],
    }
    spy = _StageSpy(runner)
    _fail_commits_carrying_writes(monkeypatch, db_session, [_db_error(sqlstate="40001")])

    await runner._apply_due_scenario_events(db_session, run_id="r1", run=run, scenario=scenario)

    committed_ids = await _fresh_participant_ids(world, new_pid)
    assert len(committed_ids) == 1, committed_ids
    assert spy.calls == 3  # add_participant twice (retry), then the line event once
    assert new_pid not in spy.pid_maps[1], (
        "the retry of add_participant was handed the id its rolled-back first attempt staged"
    )
    assert spy.pid_maps[2].get(new_pid) == committed_ids[0], (
        "the next event must see the participant id that was actually committed"
    )
    assert run._real_fired_scenario_event_indexes == {0, 1}
    assert await _fresh_lines(world) == [_the_line(world)], "the later event's line must land exactly once"
