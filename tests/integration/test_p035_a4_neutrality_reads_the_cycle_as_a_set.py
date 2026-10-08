"""035 A4 (`F-035-3`): `verify_clearing_neutrality` reads the net positions of a cycle as a set - two statements a
phase, not two per participant - and still tells a changed position from an untouched one.

A cycle of k=4 with distinct amounts, so every participant's net position over the cycle's `pairs` is its own
non-zero number. Counted with `after_cursor_execute` on the session's connection.

The two counterexamples are green today and must stay green: a constant answer passes neither.

What this does not see: the before-read of the same phase (`clearing/service.py`, `MoneyBoundary`), which this
programme replaces at its call site and does not change.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import event

from app.core.invariants import InvariantChecker
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.utils.exceptions import IntegrityViolationException
from tests.debt_setup import debt_fixture_setup

_K = 4
_AMOUNTS = [Decimal("10"), Decimal("20"), Decimal("30"), Decimal("40")]
_STATEMENTS_ALLOWED = 2


async def _cycle(db_session):
    """p0 -> p1 -> p2 -> p3 -> p0 with amounts 10, 20, 30, 40, and an outsider who is not on the cycle."""

    n = uuid.uuid4().hex[:8].upper()
    eq = Equivalent(code=f"NP{n}", symbol="NP", description=None, precision=2, metadata_={}, is_active=True)
    people = [Participant(pid=f"N{i}_{n}", display_name=f"N{i}", public_key=f"pkN{i}-{n}", type="person",
                          status="active", profile={}) for i in range(_K + 1)]
    db_session.add_all([eq, *people])
    await db_session.flush()
    ring, outsider = people[:_K], people[_K]
    debts = [Debt(debtor_id=ring[i].id, creditor_id=ring[(i + 1) % _K].id, equivalent_id=eq.id, amount=_AMOUNTS[i])
             for i in range(_K)]
    async with debt_fixture_setup(db_session, label="p035-a4"):
        db_session.add_all(debts)
    await db_session.flush()
    pairs = {(d.debtor_id, d.creditor_id) for d in debts}
    # net = credits - debts over the cycle: participant i is owed AMOUNTS[i-1] and owes AMOUNTS[i].
    before = {ring[i].id: _AMOUNTS[i - 1] - _AMOUNTS[i] for i in range(_K)}
    assert set(before.values()) == {Decimal("30"), Decimal("-10")} and Decimal("0") not in before.values()
    return eq, ring, outsider, debts, pairs, before


@pytest.mark.asyncio
async def test_a_cycle_of_four_is_verified_in_two_statements(db_session):
    eq, ring, _outsider, _debts, pairs, before = await _cycle(db_session)
    statements: list[str] = []
    connection = await db_session.connection()

    def _record(_conn, _cursor, statement, *_args):
        statements.append(statement)

    event.listen(connection.sync_connection, "after_cursor_execute", _record)
    try:
        verdict = await InvariantChecker(db_session).verify_clearing_neutrality(
            [p.id for p in ring], eq.id, before, pairs)
    finally:
        event.remove(connection.sync_connection, "after_cursor_execute", _record)

    assert verdict is True
    assert len(statements) <= _STATEMENTS_ALLOWED, (
        f"verify_clearing_neutrality on a cycle of k={_K}: actual={len(statements)} statements, "
        f"threshold={_STATEMENTS_ALLOWED}"
    )


@pytest.mark.asyncio
async def test_a_changed_position_of_a_cycle_participant_is_a_violation(db_session):
    eq, ring, _outsider, debts, pairs, before = await _cycle(db_session)
    async with debt_fixture_setup(db_session, label="p035-a4-skew"):
        debts[0].amount = _AMOUNTS[0] + Decimal("1")  # p0 owes p1 one more: both positions move
    await db_session.flush()

    with pytest.raises(IntegrityViolationException) as refused:
        await InvariantChecker(db_session).verify_clearing_neutrality([p.id for p in ring], eq.id, before, pairs)
    details = refused.value.details
    assert details["invariant"] == "CLEARING_NEUTRALITY_VIOLATION"
    assert {v["participant_id"]: v["delta"] for v in details["violations"]} == {
        str(ring[0].id): "-1.00000000", str(ring[1].id): "1.00000000"}, details


@pytest.mark.asyncio
async def test_a_neighbours_debt_outside_the_pairs_is_not_a_violation(db_session):
    """The operation answers for its own rows (027 stage 2): a debt of a cycle participant to someone off the cycle
    changes that participant's whole net position and none of the positions over `pairs`."""

    eq, ring, outsider, _debts, pairs, before = await _cycle(db_session)
    async with debt_fixture_setup(db_session, label="p035-a4-neighbour"):
        db_session.add(Debt(debtor_id=ring[0].id, creditor_id=outsider.id, equivalent_id=eq.id, amount=Decimal("5")))
    await db_session.flush()
    checker = InvariantChecker(db_session)

    assert await checker.verify_clearing_neutrality([p.id for p in ring], eq.id, before, pairs) is True
    # Anti-vacuum: the same state IS a violation once the scope is dropped, so `pairs` is what made the difference.
    with pytest.raises(IntegrityViolationException):
        await checker.verify_clearing_neutrality([p.id for p in ring], eq.id, before, None)
