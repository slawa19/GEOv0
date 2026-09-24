"""F-015-12: the simulator's real-mode inject must not create a debt opposite to an existing one.

Protocol `docs/ru/02-protocol-spec.md` §11.2.4: between a pair, in one equivalent, debt exists in one
direction only. `InjectExecutor.stage_inject_event` (`op_inject_debt`) read only the requested edge
(debtor, creditor) and never the reverse one, so an `inject_debt` against an existing debt stored a
second, opposing row. Both paths are covered: the reverse debt committed before the event, and the
reverse debt staged by an earlier effect of the SAME event (the session runs with `autoflush=False`,
so without a flush a plain read does not see it).

THE CHOSEN SHAPE IS REFUSAL, not netting: netting would write a decrease, and the INJECT rule of
criterion (b) (`app/core/ledger/reconciliation.py::_inject_subset`) accepts increases only, so a
netted inject would read FAILED and hold the equivalent. The refused effect is counted in `skipped`,
exactly like an effect over the trust limit.

THE STAND: a mode-B clone (`committed_database`), its own engine at SERIALIZABLE like the
application, and the owner's REAL unit of work (`RealRunner._apply_due_scenario_events`: owner locks,
envelope, staging, flush, commit). Every assertion reads committed rows on a fresh session, and the
INJECT operation is reconciled against a baseline taken after the fixture.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.ledger.reconciliation import PASSED, take_baseline, verify_journal_equals_change
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine
from tests.debt_setup import debt_fixture_setup
from tests.integration.test_p015_inject_holds_the_owner_lock_postgres import (
    _Artifacts,
    _run,
    _runner,
    _World,
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


async def _seed(factory) -> _World:
    """One equivalent, two participants, an ACTIVE line in each direction.

    Both lines are load-bearing: without the line for the opposite direction the opposing effect
    would be skipped by the trustline check, and the reproducer would pass on the old code.
    """
    n = uuid.uuid4().hex[:8]
    async with factory() as s:
        eq = Equivalent(code=f"OP{n}".upper(), precision=2, is_active=True)
        a = Participant(
            pid=f"OPA_{n}", display_name="A", public_key=f"pk_opa_{n}", type="person",
            status="active",
        )
        b = Participant(
            pid=f"OPB_{n}", display_name="B", public_key=f"pk_opb_{n}", type="person",
            status="active",
        )
        s.add_all([eq, a, b])
        await s.flush()
        for frm, to in ((a, b), (b, a)):
            s.add(
                TrustLine(
                    from_participant_id=frm.id,
                    to_participant_id=to.id,
                    equivalent_id=eq.id,
                    limit=Decimal("100.00"),
                    status="active",
                )
            )
        await s.commit()
    # `creditor` = A, `debtor` = B: the line A -> B lets B owe A.
    return _World([eq], a, b)


async def _existing_debt(factory, world: _World, *, debtor, creditor, amount: Decimal) -> None:
    eq = world.equivalents[0]
    async with factory() as s:
        async with debt_fixture_setup(s, label="f01512-setup"):
            s.add(
                Debt(
                    debtor_id=debtor.id, creditor_id=creditor.id, equivalent_id=eq.id, amount=amount
                )
            )
        await s.commit()


async def _baseline(factory, world: _World) -> None:
    async with factory() as s:
        await take_baseline(s, world.equivalents[0].id)
        await s.commit()


def _effect(world: _World, *, creditor, debtor, amount: str) -> dict[str, Any]:
    # Contract: `from -> to` is creditor -> debtor.
    return {
        "op": "inject_debt",
        "from": creditor.pid,
        "to": debtor.pid,
        "equivalent": world.equivalents[0].code,
        "amount": amount,
    }


async def _inject(factory, world: _World, effects: list[dict[str, Any]], run_id: str) -> dict:
    a, b = world.creditor.pid, world.debtor.pid
    code = world.equivalents[0].code
    scenario = {
        "equivalents": [code],
        "participants": [{"id": a}, {"id": b}],
        "trustlines": [
            {"from": a, "to": b, "equivalent": code, "limit": "100.00", "status": "active"},
            {"from": b, "to": a, "equivalent": code, "limit": "100.00", "status": "active"},
        ],
        "behaviorProfiles": [],
        "events": [{"type": "inject", "time": 0, "effects": effects}],
    }
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
    assert [n["description"] for n in notes] == ["inject applied"], notes
    return notes[0]["stats"]


async def _debts(factory, world: _World) -> dict[tuple[str, str], Decimal]:
    names = {world.creditor.id: "A", world.debtor.id: "B"}
    async with factory() as s:
        rows = (
            await s.execute(
                select(Debt.debtor_id, Debt.creditor_id, Debt.amount).where(
                    Debt.equivalent_id == world.equivalents[0].id
                )
            )
        ).all()
    # (debtor, creditor) -> amount
    return {(names[d], names[c]): Decimal(str(amt)) for d, c, amt in rows}


async def _assert_reconciled(factory, world: _World) -> None:
    eq_id = world.equivalents[0].id
    async with factory() as s:
        outcome = await verify_journal_equals_change(s, eq_id)
        hold = (
            await s.execute(select(Equivalent.integrity_hold_result_id).where(Equivalent.id == eq_id))
        ).scalar_one()
    assert outcome.status == PASSED, outcome.findings or outcome.missing_evidence
    assert any(kind == "INJECT" for _level, kind, _count in outcome.criterion_b_coverage), (
        f"non-vacuity: the INJECT operation must be among the reconciled ones, "
        f"coverage {outcome.criterion_b_coverage}"
    )
    assert hold is None


@pytest.mark.asyncio
async def test_an_inject_opposite_to_an_existing_debt_is_refused(factory) -> None:
    """RED before the fix: B owes A 5.00, an inject makes A owe B 3.00, and both rows are stored."""
    world = await _seed(factory)
    a, b = world.creditor, world.debtor
    await _existing_debt(factory, world, debtor=b, creditor=a, amount=Decimal("5.00"))
    await _baseline(factory, world)

    stats = await _inject(
        factory, world, [_effect(world, creditor=b, debtor=a, amount="3.00")], "f01512-opposing"
    )

    assert await _debts(factory, world) == {("B", "A"): Decimal("5.00")}
    assert stats == {"applied": 0, "skipped": 1, "total_amount": "0"}, stats
    await _assert_reconciled(factory, world)


@pytest.mark.asyncio
async def test_an_opposing_effect_of_the_same_event_is_refused(factory) -> None:
    """RED before the fix: the reverse debt is only STAGED by an earlier effect of the same event."""
    world = await _seed(factory)
    a, b = world.creditor, world.debtor
    await _baseline(factory, world)

    stats = await _inject(
        factory,
        world,
        [
            _effect(world, creditor=a, debtor=b, amount="3.00"),
            _effect(world, creditor=b, debtor=a, amount="2.00"),
        ],
        "f01512-same-event",
    )

    assert await _debts(factory, world) == {("B", "A"): Decimal("3.00")}
    assert stats == {"applied": 1, "skipped": 1, "total_amount": "3.00"}, stats
    await _assert_reconciled(factory, world)


@pytest.mark.asyncio
async def test_counter_check_an_inject_in_the_same_direction_still_increases_the_debt(
    factory,
) -> None:
    """Anti-vacuum: the refusal must not catch the existing debt's own direction."""
    world = await _seed(factory)
    a, b = world.creditor, world.debtor
    await _existing_debt(factory, world, debtor=b, creditor=a, amount=Decimal("5.00"))
    await _baseline(factory, world)

    stats = await _inject(
        factory, world, [_effect(world, creditor=a, debtor=b, amount="3.00")], "f01512-same-dir"
    )

    assert await _debts(factory, world) == {("B", "A"): Decimal("8.00")}
    assert stats == {"applied": 1, "skipped": 0, "total_amount": "3.00"}, stats
    await _assert_reconciled(factory, world)


@pytest.mark.asyncio
async def test_counter_check_an_inject_on_a_pair_without_debt_still_creates_it(factory) -> None:
    """Anti-vacuum: no reverse debt, so the opposite-looking direction is simply created."""
    world = await _seed(factory)
    a, b = world.creditor, world.debtor
    await _baseline(factory, world)

    stats = await _inject(
        factory, world, [_effect(world, creditor=b, debtor=a, amount="3.00")], "f01512-fresh-pair"
    )

    assert await _debts(factory, world) == {("A", "B"): Decimal("3.00")}
    assert stats == {"applied": 1, "skipped": 0, "total_amount": "3.00"}, stats
    await _assert_reconciled(factory, world)
