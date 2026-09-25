"""R-020-1 (programme 020, stage 2): candidate selection follows the owner's amount-first rule.

THE RULE (owner's decision 2026-09-25, spec "Решения"): per detection, at the caller's FULL depth, up to 100
UNIQUE eligible cycles ordered by clear amount DESC regardless of length, ties by the FULL canonical identity
(the sorted tuple of every debt UUID of the cycle); after a success the list is dropped and detection runs
again at full depth. This module encodes that rule. It is RED on the current tree by design and carries the
strict 020 marker (`tests/p020_support.py`) until the stage-3 switch (`T2003`) takes it off.

Three parts, as the spec's Verification plan §1 defines them:

* OVERFLOW - 101 disjoint triangles with fixed UUIDs and DISTINCT amounts. `find_cycles` at depth 4 AND 6
  must return exactly the 100 identities with the largest amounts, in order. Today: depth 4 is the triangle
  query's `LIMIT 100` over ROTATIONS (about 34 unique triangles), depth 6 adds the DFS's `> 50` raw-cycle
  cap; neither reaches 100.
* LADDER, OBSERVED ON DEBTS - a triangle and a 5-CYCLE share one edge, depth 6. Shared edge 100, the other
  triangle edges 10, the other 5-cycle edges 100. Amount-first executes the 5-cycle (100) in ONE occurrence
  and leaves the triangle's two exclusive edges at 10. Today the ladder `[4, 6]` executes the triangle first
  and then the 5-cycle at 90: two occurrences, the 5-cycle's four exclusive edges left at 10. A 4-cycle on
  the same scheme is ADDITIONAL coverage, not the reproducer: it is visible on the short rung, so a sort
  change alone would pass it with the ladder left in place.
* FULL TIE KEY - equal-amount cycles that SHARE THEIR MINIMUM DEBT UUID and differ further on. Mirrored
  groups (triangle-first and quadrangle-first by the full key, plus a same-length pair) so that no key
  short of the full identity - minimum id only, length, discovery order - orders all of them right. Today
  length leads the current key (`_cycle_order_key`), so every triangle precedes every quadrangle whatever
  the amounts (measured on this tree: positions 3-5 differ).

Every test first asserts, with ordinary assertions, that its stand is what it claims (the graph is seeded,
the cycles it plants are eligible, the run completed); only then is the outcome compared with the target,
and only that comparison raises `TargetMismatch`.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.core.clearing.service import ClearingService
from app.db.models.debt import Debt
from app.db.models.participant import Participant
from app.db.models.transaction import Transaction
from tests.conftest import MODE_B
from tests.p020_support import (
    debt_uuid,
    identity,
    identity_of,
    require_target,
    ring,
    seed_graph,
    target_xfail_020,
)

# ------------------------------------------------------------------------------------------------ overflow

_OVERFLOW_EQ = "PZA"
_TRIANGLES = 101


def _overflow_edges():
    """Triangle i: its min edge is `100 + i` (distinct per triangle), the other two `5000 + i`."""

    edges, ids_by_triangle = [], {}
    for i in range(_TRIANGLES):
        pids = [f"p020a{i:03d}{v}" for v in "xyz"]
        ids = [debt_uuid(0xA, i * 4 + k) for k in range(3)]
        ids_by_triangle[i] = ids
        edges += ring(pids, [str(100 + i), str(5000 + i), str(5000 + i)], ids)
    return edges, ids_by_triangle


@target_xfail_020("the top 100 unique cycles by amount, not 100 rotations / 50 raw DFS cycles")
@pytest.mark.asyncio
@pytest.mark.parametrize("max_depth", [4, 6])
async def test_overflow_returns_the_100_largest_unique_cycles_in_order(db_session, max_depth) -> None:
    edges, ids_by_triangle = _overflow_edges()
    await seed_graph(db_session, _OVERFLOW_EQ, edges)

    # Control: the stand holds 101 triangles, 303 positive debts.
    seeded = (await db_session.execute(select(func.count()).select_from(Debt))).scalar_one()
    assert seeded == 3 * _TRIANGLES, f"stand: expected {3 * _TRIANGLES} debts, got {seeded}"

    service = ClearingService(db_session)
    # Control (anti-vacuum): the one triangle the target drops - the smallest - IS eligible; it is the
    # limit that must drop it, not the policy. Its perimeter isolates it.
    smallest = await service.find_cycles(
        _OVERFLOW_EQ, max_depth=max_depth, allowed_participant_pids={f"p020a000{v}" for v in "xyz"}
    )
    assert [identity(c) for c in smallest] == [identity_of(ids_by_triangle[0])], smallest

    cycles = await service.find_cycles(_OVERFLOW_EQ, max_depth=max_depth)
    got = [identity(c) for c in cycles]
    seeded_identities = {identity_of(ids) for ids in ids_by_triangle.values()}
    # Control: whatever came back is made of seeded triangles only (no phantom cycles).
    assert set(got) <= seeded_identities, "a returned cycle is not a seeded triangle"

    expected = [identity_of(ids_by_triangle[i]) for i in range(_TRIANGLES - 1, 0, -1)]
    first_diff = next(
        (k for k, (a, b) in enumerate(zip(got, expected)) if a != b), min(len(got), len(expected))
    )
    require_target(
        got == expected,
        f"depth {max_depth}: expected the 100 largest of 101 triangles, amount DESC; got {len(got)} "
        f"cycles ({len(set(got))} unique), first difference at position {first_diff}",
    )


# ------------------------------------------------------------------------------ ladder, observed on debts

_LADDER_EQ = "PZB"


def _ladder_edges(long_len: int):
    """Triangle a-b-c and a `long_len`-cycle a-b-d-... sharing the edge a->b (ONE debt row).

    Shared edge 100; the triangle's own edges 10; the long cycle's own edges 100.
    """

    g = 0xB0 + long_len
    shared = debt_uuid(g, 1)
    tri = ring(["p020ba", "p020bb", "p020bc"], ["100", "10", "10"], [shared, debt_uuid(g, 2), debt_uuid(g, 3)])
    long_pids = ["p020ba", "p020bb"] + [f"p020bl{k}" for k in range(long_len - 2)]
    long_ids = [shared] + [debt_uuid(g, 10 + k) for k in range(long_len - 1)]
    long_cycle = ring(long_pids, ["100"] * long_len, long_ids)
    edges = tri + long_cycle[1:]  # the shared edge once
    return edges, tri, long_cycle


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


async def _committed_clearings(db_session) -> int:
    return (
        await db_session.execute(
            select(func.count())
            .select_from(Transaction)
            .where(Transaction.type == "CLEARING", Transaction.state == "COMMITTED")
        )
    ).scalar_one()


@target_xfail_020("the ladder executes the short cycle first; amount-first executes the long one")
@MODE_B
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "long_len",
    [
        pytest.param(5, id="reproducer_5cycle"),
        pytest.param(4, id="additional_4cycle_not_a_ladder_discriminator"),
    ],
)
async def test_amount_first_executes_the_long_cycle_over_a_shared_edge(db_session, long_len) -> None:
    edges, tri, long_cycle = _ladder_edges(long_len)
    await seed_graph(db_session, _LADDER_EQ, edges)
    service = ClearingService(db_session)

    # Control: both cycles are eligible and visible at the requested depth.
    found = {identity(c) for c in await service.find_cycles(_LADDER_EQ, max_depth=6)}
    assert found == {identity_of(e.debt_id for e in tri), identity_of(e.debt_id for e in long_cycle)}, found

    cleared = await service.auto_clear(_LADDER_EQ, max_depth=6)
    db_session.expire_all()
    occurrences = await _committed_clearings(db_session)
    remaining = await _remaining(db_session)
    # Control: the run completed and its count agrees with the durable occurrences.
    assert cleared == occurrences and cleared >= 1, (cleared, occurrences)

    expected_remaining = sorted((e.debtor, e.creditor, Decimal("10")) for e in tri[1:])
    require_target(
        remaining == expected_remaining and occurrences == 1,
        f"amount-first must execute the {long_len}-cycle (100) once and leave the triangle's own edges at "
        f"10; got occurrences={occurrences} remaining={remaining!r}",
    )


# ------------------------------------------------------------------------------------------ full tie key

_TIE_EQ = "PZC"


def _tie_edges():
    """Three components, each two equal-amount cycles through ONE shared edge = the minimum debt id.

    * group 30 - two TRIANGLES; the full key puts the second-seeded triangle first;
    * group 20 - triangle + quadrangle; the full key puts the TRIANGLE first;
    * group 10 - triangle + quadrangle; the full key puts the QUADRANGLE first.

    Group amounts differ, so the expected list is [group 30, group 20, group 10], each pair in full-key
    order. A key of "minimum id only" ties every pair; a length-first key gets group 10 wrong; a key that
    puts quadrangles first gets group 20 wrong; discovery order gets group 30 wrong.
    """

    def component(tag: str, amount: str, g: int, first_len: int, first_ids: list[int], second_len: int, second_ids: list[int]):
        shared = debt_uuid(g, 1)
        a, b = f"p020c{tag}a", f"p020c{tag}b"
        first_pids = [a, b] + [f"p020c{tag}f{k}" for k in range(first_len - 2)]
        second_pids = [a, b] + [f"p020c{tag}s{k}" for k in range(second_len - 2)]
        first = ring(first_pids, [amount] * first_len, [shared] + [debt_uuid(g, n) for n in first_ids])
        second = ring(second_pids, [amount] * second_len, [shared] + [debt_uuid(g, n) for n in second_ids])
        return first + second[1:], first, second

    # ids inside a group: the shared edge is 1 (the minimum); the second element of each full key decides.
    g30, t30a, t30b = component("30", "30", 0xC3, 3, [50, 51], 3, [20, 21])  # t30b (20..) first
    g20, t20, q20 = component("20", "20", 0xC2, 3, [20, 21], 4, [50, 51, 52])  # triangle first
    g10, t10, q10 = component("10", "10", 0xC1, 3, [50, 51], 4, [20, 21, 22])  # quadrangle first
    expected = [
        identity_of(e.debt_id for e in t30b),
        identity_of(e.debt_id for e in t30a),
        identity_of(e.debt_id for e in t20),
        identity_of(e.debt_id for e in q20),
        identity_of(e.debt_id for e in q10),
        identity_of(e.debt_id for e in t10),
    ]
    return g30 + g20 + g10, expected


@target_xfail_020("equal amounts are ordered by the full identity, not by length first")
@pytest.mark.asyncio
@pytest.mark.parametrize("max_depth", [4, 6])
async def test_equal_amounts_are_ordered_by_the_full_canonical_identity(db_session, max_depth) -> None:
    edges, expected = _tie_edges()
    await seed_graph(db_session, _TIE_EQ, edges)

    # Control: every pair really shares its minimum debt id and ties on amount.
    for k in range(0, len(expected), 2):
        assert expected[k][0] == expected[k + 1][0], "stand: the pair must share its minimum debt id"
        assert expected[k][1:] != expected[k + 1][1:]

    cycles = await ClearingService(db_session).find_cycles(_TIE_EQ, max_depth=max_depth)
    got = [identity(c) for c in cycles]
    # Control: exactly the six planted cycles came back (order aside).
    assert sorted(got) == sorted(expected), got

    require_target(
        got == expected,
        f"depth {max_depth}: equal-amount cycles must follow the full canonical identity; "
        f"positions differing: {[k for k, (a, b) in enumerate(zip(got, expected)) if a != b]}",
    )
