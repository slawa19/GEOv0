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

REMOVED 2026-10-09 (035 A2b): the text above is history. `find_cycles`, both SQL queries and
`_debt_id_key` are gone from `app/`. Pin 1's property (two independent cycles are both offered)
was moved to the planner in A2a and stays; pin 2 and the union's order within a length had no
subject but the detectors' merge and were removed with it (see the notes where they stood).
What remains in this module is asked of the planner or of the production pass.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine

from tests.debt_setup import debt_fixture_setup
from tests.conftest import MODE_B
from tests.p023_support import planned_cycles

_EQ = "DUX"


async def _seed_graph(
    db_session,
    cycles: list[list[str]] | None = None,
    *,
    edges: list[tuple[str, str, str]] | None = None,
) -> None:
    """Seed one equivalent and a debt graph.

    Either `cycles` (each a list of pids, closed implicitly, every edge amount 10) or
    `edges` (explicit `(debtor, creditor, amount)` triples - the form for graphs with a
    SHARED edge, which must be ONE debt row, not one per cycle).

    Every debt edge debtor -> creditor gets the LIVE creditor -> debtor trustline the
    detection queries join on, with `auto_clearing: True` so the policy filter admits it.
    """

    edge_list: list[tuple[str, str, str]] = list(edges or [])
    for cycle in cycles or []:
        for debtor, creditor in zip(cycle, cycle[1:] + cycle[:1]):
            edge_list.append((debtor, creditor, "10"))

    eq = Equivalent(code=_EQ, precision=2, is_active=True)
    db_session.add(eq)

    pids = {pid for edge in edge_list for pid in edge[:2]}
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

    for debtor, creditor, amount in edge_list:
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
        async with debt_fixture_setup(db_session, label="setup"):
            db_session.add(
                Debt(
                    debtor_id=people[debtor].id,
                    creditor_id=people[creditor].id,
                    equivalent_id=eq.id,
                    amount=Decimal(amount),
                )
            )
    await db_session.commit()


@pytest.mark.asyncio
async def test_a_triangle_does_not_hide_a_disjoint_quadrangle(db_session) -> None:
    """One triangle plus one disjoint 4-ring: the answer holds BOTH.

    Measured before the 012 fix at depth 4: one cycle, lengths [3] - the quadrangle query never
    ran because the triangle query was non-empty, and the early return declared that answer
    complete.

    MOVED 2026-10-09 (035 A2a, decision D3): the union of the detectors is their mechanics and leaves with them;
    the property - two independent eligible cycles are both offered - is asked of the planner
    (`planned_cycles`), which has no depth, so the three depths this ran at are one run. The assertion
    `lengths == [3, 4]` is unchanged.
    """

    await _seed_graph(db_session, [["t1", "t2", "t3"], ["q1", "q2", "q3", "q4"]])

    cycles = await planned_cycles(db_session, _EQ)
    lengths = sorted(len(c) for c in cycles)

    assert lengths == [3, 4], (
        f"the graph holds one triangle and one disjoint quadrangle, and the answer must hold both; "
        f"got lengths={lengths!r}"
    )


# REMOVED 2026-10-09 (035 A2b): `test_the_merged_answer_reports_one_cycle_once` pinned `_debt_id_key` - the merge of
# the SQL detectors' answer with the DFS's must not count one triangle twice when the two spell its debt ids
# differently. Both detectors and the merge are removed; one producer has nothing to merge. That the planner offers
# a single triangle exactly once is asserted where its answer is read on the wire
# (`tests/integration/test_p012_money_form_and_detector_reach_postgres.py::
# test_an_ordinary_debt_has_one_exact_string_on_the_diagnostic`, precondition `== [triangle]`).


# The reviewer's reproducer (T1211): two same-length cycles SHARING the edge a->b. It was built for the order of
# the detectors' union (below); it now feeds the production-pass test that follows.
_SHARED_EDGE = [
    ("a", "b", "100"),  # shared edge
    ("b", "c", "10"),   # low cycle a-b-c-a, executable amount 10, seeded first
    ("c", "a", "10"),
    ("b", "d", "100"),  # high cycle a-b-d-a, executable amount 100
    ("d", "a", "100"),
]


# REMOVED 2026-10-09 (035 A2b): `test_within_a_length_the_largest_executable_cycle_comes_first` pinned the ORDER of
# the detectors' union within one length (amount descending - `_cycle_order_key`), because `auto_clear` used to
# execute the first cycle that succeeded. Nothing has executed from that list since 023 slice (d) and the list
# itself is removed. What the order protected - which debts survive over a shared edge - is the next test's subject,
# on the production pass and against the exhaustive oracle.


async def _pass(db_session):
    """The production pass since programme 023 slice (d): the common runner on the session's database."""

    from app.core.clearing.runner import run_clearing_pass
    from tests.conftest import sessionmaker_of

    result = await run_clearing_pass(sessionmaker_of(db_session), _EQ)
    await db_session.commit()  # a fresh snapshot for the reads below
    return result


async def _remaining(db_session) -> list[tuple[str, str, Decimal]]:
    creditor = Participant.__table__.alias("creditor")
    rows = (
        await db_session.execute(
            select(Participant.pid, creditor.c.pid, Debt.amount)
            .join(Participant, Participant.id == Debt.debtor_id)
            .join(creditor, creditor.c.id == Debt.creditor_id)
            .where(Debt.amount > 0)
        )
    ).all()
    return sorted((d, c, Decimal(a)) for d, c, a in rows)


@MODE_B
@pytest.mark.asyncio
async def test_auto_clear_over_a_shared_edge_clears_the_large_cycle_and_leaves_the_small(
    db_session,
) -> None:
    """The outcome pin over a shared edge, MOVED TO THE FLOW OBJECTIVE 2026-09-28 (programme 023 slice (d)).

    Before 023 (d) this pinned the ladder's order - the amount-100 triangle first, one clearing, residual
    b->c/c->a. The executor is now the flow plan (spec 023, Verification plan §3: "the remainder on a shared edge
    -> the flow objective"): on this graph BOTH ways of using the shared edge reach the same optimum,
    `V_edge = 300` (the amount-100 triangle alone: 3 x 100; or the small triangle at 10 plus the large at 90:
    30 + 270), so the plan's choice between them is not a property to pin. What is pinned: the volume on the debts
    equals the exhaustive oracle's optimum, and the residual is exactly 20 of debt on the triangle the plan did not
    use - never a mixture that would mean volume was lost.
    """

    from tests.p023_support import oracle_max_volume

    await _seed_graph(db_session, edges=_SHARED_EDGE)
    optimum = oracle_max_volume([(k, d, c, int(a)) for k, (d, c, a) in enumerate(_SHARED_EDGE)])[0]
    assert optimum == 300, optimum

    result = await _pass(db_session)
    remaining = await _remaining(db_session)
    cleared = Decimal(320) - sum(amount for *_, amount in remaining)

    assert result.status == "complete" and cleared == optimum, (result, remaining)
    assert [(d, c) for d, c, _ in remaining] in ([("b", "c"), ("c", "a")], [("b", "d"), ("d", "a")]), remaining
    assert all(amount == Decimal("10") for *_, amount in remaining), remaining


# DELETED 2026-09-28, programme 023 slice (d): `test_auto_clear_orders_the_union_when_the_sql_path_is_down` pinned
# the ORDER in which `auto_clear` executed the union of the detectors' answers (SQL detectors down, the union sort
# alone deciding which cycle ran first). Nothing executes from that list any more - the executor is the flow plan -
# so the execution half has no subject. The union sort itself kept a pin on the diagnostic answer
# (`test_within_a_length_the_largest_executable_cycle_comes_first`) until 035 A2b removed the union.


@MODE_B
@pytest.mark.asyncio
async def test_the_ladder_widens_when_short_cycles_exist_but_none_executes(
    db_session, monkeypatch
) -> None:
    """A long cycle is reachable when short ones exist - MOVED 2026-09-28 (programme 023 slice (d)).

    Before 023 (d) this staged a triangle the executor refused and required `auto_clear`'s ladder to widen to the
    5-cycle. The ladder is gone (decision R4) and the execution has no depth: the property the spec keeps
    ("reachability of the long cycle is preserved", Verification plan §3) is that the production pass clears the
    5-cycle next to a triangle - here both, disjoint, in one pass.
    """

    await _seed_graph(db_session, [["t1", "t2", "t3"], ["f1", "f2", "f3", "f4", "f5"]])

    result = await _pass(db_session)

    lengths = sorted(len(o.edges) for o in result.committed)
    assert result.status == "complete" and lengths == [3, 5], (result, lengths)
    assert await _remaining(db_session) == []
