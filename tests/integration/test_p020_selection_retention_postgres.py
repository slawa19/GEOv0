"""RETENTION, programme 020 stage 2: detector properties that hold TODAY and must survive the switch.

Green on the current tree, no marker. A replacement detector (programme 023; 020 stage 3 superseded) must keep them green; they are
the spec's Verification plan §2 invariants that the current code already satisfies, kept apart from the
red R-020-1 characterization (`test_p020_selection_amount_first_unique_cycles_postgres.py`) so a red here
is a regression, never an expected failure.

* DEPTH REACH - one disjoint cycle of every length 3..10; at depth d in {3, 4, 6, 7, 10} the answer holds
  exactly the lengths 3..d: each planted cycle of a supported length is found, nothing longer than d is.
* ADMISSION BEFORE THE LIMIT - 101 EXCLUDED triangles with larger amounts than one eligible triangle, per
  exclusion class (consent refused, line `closed`, one vertex outside the perimeter). The eligible
  triangle is still returned (an excluded cycle does not consume the limit), and - the counter-check -
  no excluded triangle is.
* SAME-LENGTH TIES - equal-amount triangles sharing their minimum debt id come out in full-identity order,
  identically at depths 4 and 6.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.core.clearing.service import ClearingService
from app.db.models.debt import Debt
from app.db.models.transaction import Transaction
from tests.conftest import MODE_B
from tests.integration.test_p020_selection_amount_first_unique_cycles_postgres import (
    _TRIANGLES,
    _ladder_edges,
    _overflow_edges,
    _remaining,
)
from tests.p020_support import Edge, debt_uuid, identity, identity_of, ring, seed_graph

_DEPTHS = [3, 4, 6, 7, 10]


def _reach_edges():
    edges, by_len = [], {}
    for length in range(3, 11):
        pids = [f"p020r{length:02d}{k}" for k in range(length)]
        ids = [debt_uuid(0xD0 + length, k) for k in range(length)]
        by_len[length] = identity_of(ids)
        edges += ring(pids, ["10"] * length, ids)
    return edges, by_len


@pytest.mark.asyncio
@pytest.mark.parametrize("max_depth", _DEPTHS)
async def test_retention_each_depth_finds_its_lengths_and_nothing_longer(db_session, max_depth) -> None:
    edges, by_len = _reach_edges()
    await seed_graph(db_session, "PZR", edges)

    cycles = await ClearingService(db_session).find_cycles("PZR", max_depth=max_depth)
    got = sorted(identity(c) for c in cycles)

    assert got == sorted(by_len[n] for n in range(3, max_depth + 1)), (
        f"depth {max_depth}: expected one cycle of each length 3..{max_depth}; "
        f"got lengths {sorted(len(c) for c in cycles)}"
    )


_EXCLUDED = 101


def _admission_edges(kind: str):
    """101 excluded triangles (amounts 1000+i) and one eligible triangle (amount 5)."""

    edges, excluded = [], set()
    for i in range(_EXCLUDED):
        pids = [f"p020x{i:03d}{v}" for v in "abc"]
        ids = [debt_uuid(0xE0, i * 4 + k) for k in range(3)]
        tri = ring(pids, [str(1000 + i)] * 3, ids)
        if kind == "consent":
            tri = [Edge(e.debt_id, e.debtor, e.creditor, e.amount, consent=False) for e in tri]
        elif kind == "closed":
            # One closed line is enough to make the cycle ineligible.
            e0 = tri[0]
            tri = [Edge(e0.debt_id, e0.debtor, e0.creditor, e0.amount, status="closed")] + tri[1:]
        edges += tri
        excluded.add(identity_of(ids))
    eligible_ids = [debt_uuid(0xE1, k) for k in range(3)]
    edges += ring(["p020xoka", "p020xokb", "p020xokc"], ["5"] * 3, eligible_ids)
    return edges, excluded, identity_of(eligible_ids)


@pytest.mark.asyncio
@pytest.mark.parametrize("max_depth", [4, 6])
@pytest.mark.parametrize("kind", ["consent", "closed", "perimeter"])
async def test_retention_excluded_cycles_do_not_consume_the_limit(db_session, kind, max_depth) -> None:
    edges, excluded, eligible = _admission_edges(kind)
    await seed_graph(db_session, "PZX", edges)

    perimeter = None
    if kind == "perimeter":
        # Two of three vertices of every excluded triangle are inside: the per-edge endpoint rule, not
        # a vertex count, is what must exclude them.
        perimeter = {"p020xoka", "p020xokb", "p020xokc"} | {
            f"p020x{i:03d}{v}" for i in range(_EXCLUDED) for v in "ab"
        }

    cycles = await ClearingService(db_session).find_cycles(
        "PZX", max_depth=max_depth, allowed_participant_pids=perimeter
    )
    got = [identity(c) for c in cycles]

    assert eligible in got, f"{kind}, depth {max_depth}: the eligible triangle is missing; got {len(got)}"
    assert not (set(got) & excluded), f"{kind}, depth {max_depth}: an excluded triangle was returned"


@pytest.mark.asyncio
@pytest.mark.parametrize("max_depth", [4, 6])
async def test_retention_same_length_ties_follow_the_full_identity(db_session, max_depth) -> None:
    g = 0xF0
    shared = debt_uuid(g, 1)
    first = ring(["p020ta", "p020tb", "p020tc"], ["30"] * 3, [shared, debt_uuid(g, 50), debt_uuid(g, 51)])
    second = ring(["p020ta", "p020tb", "p020td"], ["30"] * 3, [shared, debt_uuid(g, 20), debt_uuid(g, 21)])
    await seed_graph(db_session, "PZT", first + second[1:])

    cycles = await ClearingService(db_session).find_cycles("PZT", max_depth=max_depth)

    assert [identity(c) for c in cycles] == [
        identity_of(e.debt_id for e in second),
        identity_of(e.debt_id for e in first),
    ]


# ------------------------------------------------------------------ R-020-1's ordinary controls (023 slice (a))
#
# Programme 023's decision on R-020-1 (spec 023, Verification plan §3): the ordinary controls of the strict
# R-020-1 tests are extracted HERE as separate green tests with EXACT assertions, so a regression of today's
# behaviour is a red test and not a change hidden behind an expected failure. The R-020-1 module and its
# strict markers are unchanged until slice (d).
#
# THE EXPECTED VALUES BELOW ARE TEMPORARY. They are today's ladder (`auto_clear` rungs [4, 6]): triangle
# first, then the long cycle on what the shared edge has left. Slice (d) moves them to the flow objective
# (V_edge against the oracle); they do not become a permanent constraint.

@MODE_B
@pytest.mark.asyncio
@pytest.mark.parametrize("long_len", [5, 4])
async def test_retention_ladder_occurrences_and_remainder_are_exact(db_session, long_len) -> None:
    edges, tri, long_cycle = _ladder_edges(long_len)
    await seed_graph(db_session, "PZL", edges)

    cleared = await ClearingService(db_session).auto_clear("PZL", max_depth=6)
    db_session.expire_all()
    occurrences = (
        await db_session.execute(
            select(func.count())
            .select_from(Transaction)
            .where(Transaction.type == "CLEARING", Transaction.state == "COMMITTED")
        )
    ).scalar_one()
    remaining = await _remaining(db_session)

    # Today: the triangle clears 10 (its own edges go, the shared edge drops to 90), then the long cycle
    # clears 90 (the shared edge goes, its own edges drop to 10). Two occurrences - the loop does NOT stop
    # after the first triangle.
    assert (cleared, occurrences) == (2, 2), (cleared, occurrences)
    assert remaining == sorted((e.debtor, e.creditor, Decimal("10")) for e in long_cycle[1:]), remaining


@pytest.mark.asyncio
@pytest.mark.parametrize("max_depth", [4, 6])
async def test_retention_overflow_global_result_is_not_empty(db_session, max_depth) -> None:
    edges, ids_by_triangle = _overflow_edges()
    await seed_graph(db_session, "PZO", edges)
    seeded = (await db_session.execute(select(func.count()).select_from(Debt))).scalar_one()
    assert seeded == 3 * _TRIANGLES

    got = [identity(c) for c in await ClearingService(db_session).find_cycles("PZO", max_depth=max_depth)]

    assert got, f"depth {max_depth}: 101 eligible triangles and an empty global answer"
    assert set(got) <= {identity_of(ids) for ids in ids_by_triangle.values()}, "a returned cycle is not seeded"
