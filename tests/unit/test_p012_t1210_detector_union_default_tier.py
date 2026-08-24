"""T1210-bis: the SQL fast path is a UNION of its two queries, and the merge is duplicate-free.

Two pins on `find_cycles` that the postgres reach module (`test_p012_money_form_and_detector_
reach_postgres.py`) cannot hold, because both defects are invisible on that tier or at the
depths it probes.

**Pin 1 - a triangle must not hide a quadrangle, at ANY depth that asks for both.**  dbe8f4b
fixed "the SQL answer suppresses the DFS answer" for depths past the SQL detectors' reach, but
kept the inner gate `if max_depth >= 4 and not cycles:` - quadrangles ran only when the
filtered triangles came back EMPTY.  So at `max_depth=4` (a legal API input, `ge=3`) a single
triangle still hid every quadrangle from an early return the code called "complete", and at
5-6 it starved the SQL side down to triangles.  Same class, one step down - found by this
wave's own adversarial review of its own fix.

**Pin 2 - the dedup key is a VALUE, by intent.**  On SQLite the raw-SQL detectors see the
stored 32-hex debt-id spelling while the ORM DFS emits the hyphenated one; `_debt_id_key`
folds both to one key, so a cycle found by both detectors merges to one.  The only prior test
that reddened on reverting the key to raw strings was a *precondition* assert in a perimeter
test - a failure that reads as a broken fixture, not as a duplicate answer.  This is the
intent pin.

Default tier ON PURPOSE: the two spellings differ only on SQLite, and depth 4 with a
triangle+quadrangle pair needs no PostgreSQL behavior at all.

MUTATIONS THESE CATCH: restoring `and not cycles` on the quadrangle call (pin 1);
`_debt_id_key = str` / keying the dedup on the raw spelling (pin 2).
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from app.core.clearing.service import ClearingService
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine

_EQ = "DUX"


async def _seed_graph(db_session, cycles: list[list[str]]) -> None:
    """Seed one equivalent and the given debt cycles (each a list of pids, closed implicitly).

    Every debt edge debtor -> creditor gets the LIVE creditor -> debtor trustline the
    detection queries join on, with `auto_clearing: True` so the policy filter admits it.
    """

    eq = Equivalent(code=_EQ, precision=2, is_active=True)
    db_session.add(eq)

    pids = {pid for cycle in cycles for pid in cycle}
    people: dict[str, Participant] = {}
    for pid in sorted(pids):
        p = Participant(
            id=uuid.uuid4(),
            pid=pid,
            display_name=pid.upper(),
            public_key=pid * 16,
            type="person",
            status="active",
            profile={},
        )
        people[pid] = p
        db_session.add(p)
    await db_session.commit()

    for cycle in cycles:
        for debtor, creditor in zip(cycle, cycle[1:] + cycle[:1]):
            db_session.add(
                TrustLine(
                    from_participant_id=people[creditor].id,
                    to_participant_id=people[debtor].id,
                    equivalent_id=eq.id,
                    limit=Decimal("1000"),
                    policy={"auto_clearing": True},
                    status="active",
                )
            )
            db_session.add(
                Debt(
                    debtor_id=people[debtor].id,
                    creditor_id=people[creditor].id,
                    equivalent_id=eq.id,
                    amount=Decimal("10"),
                )
            )
    await db_session.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize("max_depth", [4, 5, 6])
async def test_a_triangle_does_not_hide_a_disjoint_quadrangle(db_session, max_depth) -> None:
    """One triangle plus one disjoint 4-ring: the answer holds BOTH, at depths 4 through 6.

    Measured before this fix at depth 4: one cycle, lengths [3] - the quadrangle query never
    ran because the triangle query was non-empty, and the early return declared that answer
    complete.  Whichever detector answers, the caller asked for cycles up to `max_depth`
    edges, and both cycles are within it.
    """

    await _seed_graph(db_session, [["t1", "t2", "t3"], ["q1", "q2", "q3", "q4"]])

    service = ClearingService(db_session)
    cycles = await service.find_cycles(_EQ, max_depth=max_depth)
    lengths = sorted(len(c) for c in cycles)

    assert lengths == [3, 4], (
        f"at max_depth={max_depth} the graph holds one triangle and one disjoint quadrangle, "
        f"and the answer must hold both; got lengths={lengths!r}. [3] means the quadrangle "
        f"query is gated on the triangles coming back empty again - a triangle anywhere in "
        f"the graph silences every 4-cycle."
    )


@pytest.mark.asyncio
async def test_the_merged_answer_reports_one_cycle_once(db_session) -> None:
    """One triangle, depth past the SQL reach: both detectors find it, the answer holds it ONCE.

    At `max_depth=5` the SQL fast path and the ORM DFS both run and both find the same
    triangle.  On SQLite they spell its debt ids differently (stored 32-hex against
    hyphenated), so a dedup keyed on the raw spelling counts the same cycle twice -- which is
    exactly how the first edition passed the Postgres tier (where the spellings coincide) and
    doubled every cycle on the default one.  `_debt_id_key` folds the spelling to the value.

    This is the INTENT pin for that key: the prior red on a revert was a fixture-precondition
    assert in `test_p1_clearing_run_perimeter.py`, whose failure message blames the stand.
    """

    await _seed_graph(db_session, [["t1", "t2", "t3"]])

    service = ClearingService(db_session)
    cycles = await service.find_cycles(_EQ, max_depth=5)

    assert len(cycles) == 1, (
        f"one triangle in the graph, one cycle in the answer; got {len(cycles)}. Two means "
        f"the merge is keyed on the debt-id SPELLING again (`_debt_id_key` reverted): the "
        f"raw-SQL path and the DFS spell the same debt differently on SQLite, and each "
        f"rendition passed as a distinct cycle."
    )
