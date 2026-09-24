"""A mixed inject event is ONE operation whose effects run in source order - characterised.

Programme 018, stage A, `T1802` ("Композиция: одна операция на событие инжекта"). An `INJECT` event is
one envelope (`real_runner_impl.py`, `run_id:event_index`) in which the effects run in the order the
scenario lists them and see each other: `add_participant` flushes, `create_trustline` and
`freeze_participant` stage ORM changes that the debt handler's trust-line read sees only once
something has flushed (the application session runs with `autoflush=False`). Moving the debt writer
into `Book` must not change any of that, so this test pins the observable outcome of one event that
interleaves all four effect kinds - per-effect acceptance (read from the debts and the counters), the
counters themselves, the journal entries and EXACTLY ONE `INJECT` envelope - and it is required to
be identical before and after the move.

WHY THE ORDER IS LOAD-BEARING HERE, effect by effect (A and B have active lines both ways, limit
100; C and D are created by the event):

0. `inject_debt` A->B 3.00: applied - B owes A 3.00.
1. `add_participant` C: applied; flushes.
2. `create_trustline` A->C, 50: applied; staged, NOT flushed.
3. `inject_debt` A->C 4.00: SKIPPED - the trust-line read does not see the unflushed line of (2).
4. `freeze_participant` B: applied; B and its lines are frozen IN THE SESSION only.
5. `inject_debt` A->B 2.00: applied - the line read still sees `active` from the database; the
   handler's own flush then sends (2) and (4). B owes A 5.00.
6. `add_participant` D: applied.
7. `inject_debt` A->C 1.00: applied - the line of (2) is in the database now.
8. `inject_debt` A->B 1.00: SKIPPED - the frozen line of (4) is in the database now.

Collecting the debt effects at the start of the event would apply (3) and (8) and skip nothing;
collecting them at the end would skip (5); splitting them into their own operations changes the
envelope count. Each of those is a failure of this test.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models.debt import Debt
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine
from tests.integration.test_p015_f01512_inject_refuses_an_opposing_debt_postgres import (
    _assert_reconciled,
    _baseline,
    _seed,
)
from tests.integration.test_p015_inject_holds_the_owner_lock_postgres import (
    _Artifacts,
    _run,
    _runner,
)

# MODE B: every commit of this module lands in a clone dropped after the test.
from tests.tier_on_a_clone import tier_sessions_on_a_clone  # noqa: E402,F401 - autouse fixture


@pytest_asyncio.fixture
async def factory(committed_database):
    url = committed_database.url
    if not url.startswith("postgresql"):
        raise RuntimeError(f"a mode-B clone must be PostgreSQL, got {url!r}")
    engine = create_async_engine(
        url, pool_size=2, max_overflow=0, pool_timeout=10, isolation_level="SERIALIZABLE"
    )
    try:
        yield async_sessionmaker(
            bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
        )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_mixed_inject_event_is_one_operation_in_source_order(factory) -> None:
    world = await _seed(factory)
    a, b = world.creditor, world.debtor
    eq = world.equivalents[0]
    await _baseline(factory, world)
    n = uuid.uuid4().hex[:8]
    c_pid, d_pid = f"MXC_{n}", f"MXD_{n}"

    def debt(creditor: str, debtor: str, amount: str) -> dict[str, Any]:
        return {"op": "inject_debt", "from": creditor, "to": debtor, "equivalent": eq.code,
                "amount": amount}

    effects = [
        debt(a.pid, b.pid, "3.00"),
        {"op": "add_participant", "participant": {"id": c_pid, "name": "C"}},
        {"op": "create_trustline", "from": a.pid, "to": c_pid, "equivalent": eq.code,
         "limit": "50"},
        debt(a.pid, c_pid, "4.00"),
        {"op": "freeze_participant", "participant_id": b.pid},
        debt(a.pid, b.pid, "2.00"),
        {"op": "add_participant", "participant": {"id": d_pid, "name": "D"}},
        debt(a.pid, c_pid, "1.00"),
        debt(a.pid, b.pid, "1.00"),
    ]
    scenario = {
        "equivalents": [eq.code],
        "participants": [{"id": a.pid}, {"id": b.pid}],
        "trustlines": [
            {"from": a.pid, "to": b.pid, "equivalent": eq.code, "limit": "100.00",
             "status": "active"},
            {"from": b.pid, "to": a.pid, "equivalent": eq.code, "limit": "100.00",
             "status": "active"},
        ],
        "behaviorProfiles": [],
        "events": [{"type": "inject", "time": 0, "effects": effects}],
    }
    run_id = f"p018-mixed-{n}"
    run = _run(world, run_id)
    artifacts = _Artifacts()
    runner = _runner(run, scenario, artifacts)
    async with factory() as session:
        await runner._apply_due_scenario_events(
            session, run_id=run.run_id, run=run, scenario=scenario
        )
        assert not session.in_transaction()
    assert run._real_fired_scenario_event_indexes == {0}
    notes = [p["scenario"] for p in artifacts.events if p.get("type") == "note"]
    assert [note["description"] for note in notes] == ["inject applied"], notes

    # The counters: 9 effects, 7 applied (3 debts + 2 participants + 1 line + 1 freeze), 2 skipped.
    assert notes[0]["stats"] == {"applied": 7, "skipped": 2, "total_amount": "6.00"}, notes[0]

    async with factory() as s:
        ids = dict(
            (
                await s.execute(
                    select(Participant.pid, Participant.id).where(
                        Participant.pid.in_([a.pid, b.pid, c_pid, d_pid])
                    )
                )
            ).all()
        )
        names = {v: k for k, v in {"A": ids[a.pid], "B": ids[b.pid], "C": ids[c_pid]}.items()}
        debts = {
            (names[d], names[cr]): Decimal(str(amount))
            for d, cr, amount in (
                await s.execute(
                    select(Debt.debtor_id, Debt.creditor_id, Debt.amount).where(
                        Debt.equivalent_id == eq.id
                    )
                )
            ).all()
        }
        statuses = dict(
            (
                await s.execute(
                    select(Participant.pid, Participant.status).where(
                        Participant.pid.in_([b.pid, c_pid, d_pid])
                    )
                )
            ).all()
        )
        line_ab = (
            await s.execute(
                select(TrustLine.status).where(
                    TrustLine.from_participant_id == a.id,
                    TrustLine.to_participant_id == b.id,
                    TrustLine.equivalent_id == eq.id,
                )
            )
        ).scalar_one()
        envelopes = (
            await s.execute(
                text(
                    "SELECT id, state FROM debt_operations WHERE kind = 'INJECT' "
                    "AND identity LIKE :prefix"
                ),
                {"prefix": f"{run_id}:%"},
            )
        ).all()
        entries = (
            await s.execute(
                text(
                    "SELECT debtor_id, creditor_id, amount_before, amount_after "
                    "FROM debt_journal_entries WHERE operation_id = :op"
                ),
                {"op": envelopes[0][0]} if envelopes else {"op": uuid.uuid4()},
            )
        ).all()

    # Per-effect acceptance, read from the ledger: (0)+(5) on A->B, (7) on A->C; (3) and (8) skipped.
    assert debts == {("B", "A"): Decimal("5.00"), ("C", "A"): Decimal("1.00")}, debts
    assert statuses == {b.pid: "suspended", c_pid: "active", d_pid: "active"}, statuses
    assert line_ab == "frozen"

    # Exactly one envelope for the whole event, identity `run_id:event_index`.
    assert [(state,) for _id, state in envelopes] == [("COMPLETED",)], envelopes
    recorded = sorted(
        (names[d], names[cr], Decimal(str(before or 0)), Decimal(str(after or 0)))
        for d, cr, before, after in entries
    )
    # One entry per edge per flush: A->B written by two flushes (0->3 at effect 5's flush, 3->5 at
    # the envelope's completion flush), A->C once.
    assert recorded == sorted(
        [
            ("B", "A", Decimal("0"), Decimal("3.00")),
            ("B", "A", Decimal("3.00"), Decimal("5.00")),
            ("C", "A", Decimal("0"), Decimal("1.00")),
        ]
    ), recorded
    await _assert_reconciled(factory, world)
