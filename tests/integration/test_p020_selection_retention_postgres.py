"""RETENTION, programme 020 stage 2: detector properties that hold TODAY and must survive the switch.

Green on the current tree, no marker. A replacement detector (programme 023; 020 stage 3 superseded) must keep them green; they are
the spec's Verification plan §2 invariants that the current code already satisfies, kept apart from the
red R-020-1 characterization (`test_p020_selection_amount_first_unique_cycles_postgres.py`) so a red here
is a regression, never an expected failure.

* DEPTH REACH - REMOVED 2026-10-09 (035 A2b) with the detectors: nothing takes a depth any more.
* ADMISSION BEFORE THE LIMIT - 101 EXCLUDED triangles with larger amounts than one eligible triangle, per
  exclusion class (consent refused, line `closed`, one vertex outside the perimeter). The eligible
  triangle is still returned (an excluded cycle does not consume the limit), and - the counter-check -
  no excluded triangle is.
* SAME-LENGTH TIES - REMOVED 2026-10-09 (035 A2b): the order of the detectors' list (`_cycle_order_key`).
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import func, select

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
from tests.p023_support import auto_clear_http, fresh_read, planned_cycles, require_target

# REMOVED 2026-10-09 (035 A2b): `test_retention_each_depth_finds_its_lengths_and_nothing_longer` (five depths) and
# its stand `_reach_edges`. It asked `find_cycles(max_depth=d)` for exactly the lengths 3..d; the detector and its
# depth are removed, and no caller has a depth. That a cycle longer than the old SQL reach is offered and cleared
# with no depth asked is held by `tests/integration/test_clearing_max_depth_controls_long_cycles.py` (a 5-cycle,
# over HTTP) and `tests/unit/test_p012_t1210_detector_union_default_tier.py::
# test_the_ladder_widens_when_short_cycles_exist_but_none_executes` (the production pass). Lengths 6..10 on a
# database stand are not pinned by a moved test: the planner's decomposition has no length parameter to regress.

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


# 035 A2a (2026-10-08): the offer is read from the PLANNER (`planned_cycles`), not from the retired detectors. The
# property that survives is the admission: an excluded cycle is never offered and never stands in the way of an
# eligible one. The detectors' limit of 100 and their depth are gone with them, so the two depths this test ran at
# are one run; the assertions are unchanged.
@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["consent", "closed", "perimeter"])
async def test_retention_excluded_cycles_do_not_consume_the_limit(db_session, kind) -> None:
    edges, excluded, eligible = _admission_edges(kind)
    await seed_graph(db_session, "PZX", edges)

    perimeter = None
    if kind == "perimeter":
        # Two of three vertices of every excluded triangle are inside: the per-edge endpoint rule, not
        # a vertex count, is what must exclude them.
        perimeter = {"p020xoka", "p020xokb", "p020xokc"} | {
            f"p020x{i:03d}{v}" for i in range(_EXCLUDED) for v in "ab"
        }

    cycles = await planned_cycles(db_session, "PZX", allowed_participant_pids=perimeter)
    got = [identity(c) for c in cycles]

    assert eligible in got, f"{kind}: the eligible triangle is missing; got {len(got)}"
    assert not (set(got) & excluded), f"{kind}: an excluded triangle was returned"


# REMOVED 2026-10-09 (035 A2b): `test_retention_same_length_ties_follow_the_full_identity` (two depths). It pinned
# the tie-break of the detectors' list - two equal-amount triangles over a shared debt come out in the order of
# their full debt-id identity (`_cycle_order_key`). The list is removed and the plan gives the shared debt to ONE
# of the two, so "the order of both" has no subject. What the plan does over a shared edge is pinned against the
# oracle in `tests/unit/test_p012_t1210_detector_union_default_tier.py::
# test_auto_clear_over_a_shared_edge_clears_the_large_cycle_and_leaves_the_small`.

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

#
# MOVED TO THE NEW OBJECTIVE 2026-09-28, slice (d) (spec 023, Verification plan §3): the ladder values (two
# occurrences, the long cycle's own edges left at 10) are replaced by the flow optimum through the production entry:
# ONE occurrence of the long cycle at 100 (V_edge 100·L against the ladder's 30 + 90·L), the triangle's own edges
# left at 10. Under the strict 023 marker until the switch.

@MODE_B
@pytest.mark.asyncio
@pytest.mark.parametrize("long_len", [5, 4])
async def test_retention_ladder_occurrences_and_remainder_are_exact(db_session, client, auth_headers, long_len) -> None:
    edges, tri, long_cycle = _ladder_edges(long_len)
    await seed_graph(db_session, "PZL", edges)

    response = await auto_clear_http(client, auth_headers, "PZL")
    assert response.status_code == 200, response.text
    cleared = response.json()["cleared_cycles"]

    async def _occurrences(session) -> int:
        return (
            await session.execute(
                select(func.count())
                .select_from(Transaction)
                .where(Transaction.type == "CLEARING", Transaction.state == "COMMITTED")
            )
        ).scalar_one()

    occurrences = await fresh_read(db_session, _occurrences)
    remaining = await fresh_read(db_session, _remaining)
    assert cleared == occurrences, (cleared, occurrences)
    require_target(
        occurrences == 1 and remaining == sorted((e.debtor, e.creditor, Decimal("10")) for e in tri[1:]),
        f"flow optimum: one occurrence of the {long_len}-cycle at 100; got {occurrences}, remaining {remaining!r}",
    )


@pytest.mark.asyncio
async def test_retention_overflow_global_result_is_not_empty(db_session) -> None:
    """035 A2a: read from the planner (one run - the detectors' two depths are gone with them)."""

    edges, ids_by_triangle = _overflow_edges()
    await seed_graph(db_session, "PZO", edges)
    seeded = (await db_session.execute(select(func.count()).select_from(Debt))).scalar_one()
    assert seeded == 3 * _TRIANGLES

    got = [identity(c) for c in await planned_cycles(db_session, "PZO")]

    assert got, "101 eligible triangles and an empty global answer"
    assert set(got) <= {identity_of(ids) for ids in ids_by_triangle.values()}, "a returned cycle is not seeded"
