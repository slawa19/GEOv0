"""030 S6 (`F-030-16`): the cycles `GET /clearing/cycles` offers are the cycles the planner would plan.

The planner leaves out every edge with a participant that is not `active` (`flow_planner.py`, 028 `F-028-28`) and
execution skips such an occurrence; diagnostics did not read the status. Three detectors answer `find_cycles` - SQL
triangles, SQL quadrangles, the DFS - each its own reader, so each cell reaches a different one. Red on `9ed439de`:
every cell with a participant that is not active (10)."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import update

from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine
from tests.debt_setup import debt_fixture_setup
from tests.p023_support import planned_cycles


async def _add_ring(db_session, eq, people, amount: str) -> None:
    """Debts p0 -> p1 -> ... -> p0, every edge with its consenting line (`creditor -> debtor`)."""

    ring = [(debtor, people[(i + 1) % len(people)]) for i, debtor in enumerate(people)]
    db_session.add_all([TrustLine(from_participant_id=creditor.id, to_participant_id=debtor.id, equivalent_id=eq.id,
                                  limit=Decimal("100"), status="active", policy={"auto_clearing": True})
                        for debtor, creditor in ring])
    debts = [Debt(debtor_id=debtor.id, creditor_id=creditor.id, equivalent_id=eq.id, amount=Decimal(amount))
             for debtor, creditor in ring]
    async with debt_fixture_setup(db_session, label="p030-s6"):
        db_session.add_all(debts)
    await db_session.commit()


def _people(prefix: str, n: str, size: int) -> list:
    return [Participant(pid=f"{prefix}{i}_{n}", display_name=f"{prefix}{i}", public_key=f"pk{prefix}{i}-{n}",
                        type="person", status="active", profile={}) for i in range(size)]


async def _ring(db_session, size: int, *, tag: str):
    """A fresh equivalent and `size` participants in a debt ring of 10.00."""

    n = uuid.uuid4().hex[:8].upper()
    eq = Equivalent(code=f"{tag}{n}", symbol=tag, description=None, precision=2, metadata_={}, is_active=True)
    people = _people("P", n, size)
    db_session.add_all([eq, *people])
    await db_session.flush()
    await _add_ring(db_session, eq, people, "10")
    return eq, people


async def _set_status(db_session, participant, status: str) -> None:
    await db_session.execute(update(Participant).where(Participant.id == participant.id).values(status=status))
    await db_session.commit()


# 035 A2a (2026-10-08): the offer is read from the PLANNER (`planned_cycles`), which is what `GET /clearing/cycles`
# answers with since 035 A1. Until then three detectors answered and each ring size reached another one (SQL
# triangles, SQL quadrangles, the DFS - the old ids of these cells); there is one reader now, and the three sizes
# stay as three stands. The assertions are unchanged.
_RING_SIZES = [3, 4, 5]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["suspended", "left", "deleted"])
@pytest.mark.parametrize("size", _RING_SIZES, ids=[f"ring of {size}" for size in _RING_SIZES])
async def test_a_cycle_through_a_participant_that_is_not_active_is_not_offered(db_session, size, status):
    eq, people = await _ring(db_session, size, tag="DG")
    # Anti-vacuum: this very ring is offered while everyone is active.
    offered = await planned_cycles(db_session, eq.code)
    assert [len(c) for c in offered] == [size], (size, offered)

    await _set_status(db_session, people[1], status)  # an intermediate, not the first vertex
    assert await planned_cycles(db_session, eq.code) == [], (size, status)


@pytest.mark.asyncio
async def test_a_frozen_participant_removes_only_its_own_cycles(db_session):
    """The rule drops edges of non-active participants, not the equivalent: a disjoint active triangle is still offered."""

    eq, ring = await _ring(db_session, 3, tag="DH")
    other = _people("Q", uuid.uuid4().hex[:8].upper(), 3)  # a second triangle, disjoint from the first
    db_session.add_all(other)
    await db_session.flush()
    await _add_ring(db_session, eq, other, "7")
    assert len(await planned_cycles(db_session, eq.code)) == 2

    await _set_status(db_session, ring[0], "suspended")
    left = await planned_cycles(db_session, eq.code)
    assert [{e["debtor"] for e in c} for c in left] == [{p.pid for p in other}], left
