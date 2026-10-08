"""035 A2a: two readers that moved off the retired detectors, each held against a stand where the difference shows.

* THE SEED (`scripts/seed_recipe.py::_clearing_view`, decision D1). A recipe's `execute` command needs its cycle
  whole in the planner's SNAPSHOT; `assert_clearable` and the final acceptance need it among the cycles of the PLAN.
  On a stand where a triangle shares an edge with a more valuable cycle the two differ: every edge of the triangle is
  eligible, and the plan holds only the long cycle. The mode logic itself is held with doubles in
  `tests/unit/test_p017_t1711_seed_recipe_refuses.py`; this is the real view on a real graph.
* THE CONTROL (`tests/p023_support.py::assert_named_cycles_are_in_the_snapshot`, decision D2). It replaced
  `found == {...}` over the detectors in the 020/023 reproducers. A control that cannot fail proves nothing, so each
  of its four clauses is shown to refuse the stand it exists to refuse.

What this does not see: the seed end to end (`test_p017_t1711_seed_recipe_postgres.py` runs it), and execution.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

import scripts.seed_recipe as seed
from tests.integration.test_p020_selection_amount_first_unique_cycles_postgres import _ladder_edges
from tests.p020_support import Edge, debt_uuid, ring, seed_graph
from tests.p023_support import assert_named_cycles_are_in_the_snapshot

_EQ = "PZS"


def _pairs(cycle) -> frozenset[tuple[str, str]]:
    return frozenset((edge.debtor, edge.creditor) for edge in cycle)


@pytest.mark.asyncio
async def test_the_seed_view_tells_a_cycle_in_the_snapshot_from_a_cycle_in_the_plan(db_session) -> None:
    edges, tri, long_cycle = _ladder_edges(5)
    await seed_graph(db_session, _EQ, edges)

    eligible, planned = await seed._clearing_view(db_session, _EQ)

    # The snapshot holds every edge of BOTH cycles, each with its own debt's amount at the equivalent's precision.
    assert set(eligible) == {(e.debtor, e.creditor) for e in edges}
    assert {pair: edge["amount"] for pair, edge in eligible.items()} == {
        (e.debtor, e.creditor): f"{Decimal(e.amount):.2f}" for e in edges
    }
    assert {pair: edge["debt_id"] for pair, edge in eligible.items()} == {
        (e.debtor, e.creditor): str(e.debt_id) for e in edges
    }
    # The plan holds the long cycle only: the shared edge's volume goes round it (the flow optimum).
    assert seed._match_cycle(planned, _pairs(long_cycle)) is not None
    assert seed._match_cycle(planned, _pairs(tri)) is None, (
        "the triangle is one of the plan's cycles: this stand no longer separates the snapshot from the plan"
    )
    assert len(planned) == 1
    # What the plan's cycle carries is the DEBTS' amounts (all 100 here), in cycle order.
    assert [edge["amount"] for edge in planned[0]] == ["100.00"] * 5
    for edge, following in zip(planned[0], planned[0][1:] + planned[0][:1]):
        assert edge["creditor"] == following["debtor"]


@pytest.mark.asyncio
async def test_the_seed_view_is_empty_where_nothing_is_eligible(db_session) -> None:
    refused = [Edge(e.debt_id, e.debtor, e.creditor, e.amount, consent=False)
               for e in ring(["p035sa", "p035sb", "p035sc"], ["10"] * 3, [debt_uuid(0x35A2, k) for k in range(3)])]
    await seed_graph(db_session, _EQ, refused)

    assert await seed._clearing_view(db_session, _EQ) == ({}, [])


# ------------------------------------------------------------------------------------------------ the control


def _two_rings():
    first = ring(["p035ca", "p035cb", "p035cc"], ["10", "20", "30"], [debt_uuid(0x35C1, k) for k in range(3)])
    second = ring(["p035cx", "p035cy", "p035cz"], ["10", "20", "30"], [debt_uuid(0x35C2, k) for k in range(3)])
    return first, second


@pytest.mark.asyncio
async def test_the_control_accepts_the_cycles_the_stand_really_holds(db_session) -> None:
    first, second = _two_rings()
    await seed_graph(db_session, _EQ, first + second)

    await assert_named_cycles_are_in_the_snapshot(db_session, _EQ, [first, second])
    # A rotation is the same cycle.
    await assert_named_cycles_are_in_the_snapshot(db_session, _EQ, [first[1:] + first[:1]])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "broken, says",
    [
        ("chain", "the cycle is not closed"),
        ("closure only", "the cycle is not closed"),
        ("amount", "the stand declares"),
        ("endpoint", "not p035ca -> p035cc"),
        ("too short", "three or more distinct debts"),
        ("repeated debt", "three or more distinct debts"),
        ("not eligible", "are not edges of the snapshot"),
        ("other equivalent", "are not edges of the snapshot"),
    ],
)
async def test_the_control_refuses_a_cycle_the_stand_does_not_hold(db_session, broken, says) -> None:
    first, second = _two_rings()
    # A real debt from the first ring's last vertex to a vertex of the second ring: a -> b -> c -> x is a chain of
    # three eligible debts in which ONLY the return to the first vertex is missing.
    stray = Edge(debt_uuid(0x35C4, 0), "p035cc", "p035cx", "30")
    seeded = list(first + second) + [stray]
    if broken == "not eligible":  # one line of the first ring refuses auto-clearing: its debt is not in the snapshot
        seeded[0] = Edge(first[0].debt_id, first[0].debtor, first[0].creditor, first[0].amount, consent=False)
    await seed_graph(db_session, _EQ, seeded)

    named = {
        # the last edge is the other ring's: three real debts, broken in the middle
        "chain": first[:2] + second[2:],
        # every edge follows the one before it; the last one does not come back to the first
        "closure only": first[:2] + [stray],
        # the stand says 11 where the snapshot holds 10
        "amount": [Edge(first[0].debt_id, first[0].debtor, first[0].creditor, "11")] + first[1:],
        # a real debt named with another creditor
        "endpoint": [Edge(first[0].debt_id, first[0].debtor, "p035cc", first[0].amount)] + first[1:],
        "too short": first[:2],
        "repeated debt": [first[0], first[1], first[0]],
        "not eligible": first,
        "other equivalent": first,
    }[broken]
    code = _EQ
    if broken == "other equivalent":
        await seed_graph(db_session, "PZT", ring(["p035ck", "p035cl", "p035cm"], ["1"] * 3,
                                                 [debt_uuid(0x35C3, k) for k in range(3)]))
        code = "PZT"

    with pytest.raises(AssertionError, match=says):
        await assert_named_cycles_are_in_the_snapshot(db_session, code, [named])
