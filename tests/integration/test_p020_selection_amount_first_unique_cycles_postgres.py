"""R-020-1 (programme 020, stage 2) - SETTLED BY PROGRAMME 023 SLICE (d), 2026-09-28 (spec 023, Verification plan §3).

023's disposition, as the spec fixes it: the strict 020 markers are removed; the LADDER test is rewritten against
the flow objective (V_edge on the debts equals the exhaustive oracle's optimum, through `POST /clearing/auto`) and
carries no marker; the OVERFLOW and FULL-TIE-KEY tests are deleted with a record where they stood - their target
(the top 100 by amount, ordered by the full identity) belonged to the "large amount first" rule the owner replaced
on 2026-09-26. The historical description of the 020 rule follows, unchanged.

THE RULE (owner's decision 2026-09-25, spec "Решения"): per detection, at the caller's FULL depth, up to 100
UNIQUE eligible cycles ordered by clear amount DESC regardless of length, ties by the FULL canonical identity
(the sorted tuple of every debt UUID of the cycle); after a success the list is dropped and detection runs
again at full depth. This module encodes that rule. It is RED on the current tree by design and carries the
strict 020 marker (`tests/p020_support.py`) until programme 023 decides their fate (020 stage 3 superseded).

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
)
from tests.p023_support import auto_clear_http, fresh_read, oracle_max_volume, positive_debt_total

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


# DELETED 2026-09-28, programme 023 slice (d) (spec 023, Verification plan §3, R-020-1): the strict test
# `test_overflow_returns_the_100_largest_unique_cycles_in_order` asked `find_cycles` for the top 100 unique cycles
# by amount - the target of the 020 rule "large amount first", replaced by the owner on 2026-09-26 with the flow
# objective. Nothing executes from that list any more (execution is the flow plan), so the target has no owner.
# What survives of its ordinary controls: `test_retention_overflow_global_result_is_not_empty` (retention module) -
# the diagnostic answer on 101 eligible triangles is non-empty and made of seeded triangles only. The stand
# (`_overflow_edges`, `_TRIANGLES`) stays: the retention module imports it.


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


@MODE_B
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "long_len",
    [
        pytest.param(5, id="reproducer_5cycle"),
        pytest.param(4, id="additional_4cycle_not_a_ladder_discriminator"),
    ],
)
async def test_amount_first_executes_the_long_cycle_over_a_shared_edge(db_session, client, auth_headers, long_len) -> None:
    """REWRITTEN 2026-09-28, programme 023 slice (d) (spec 023, Verification plan §3, R-020-1): the target is no
    longer "amount first" but the flow objective - `V_edge` on the debts equals the exhaustive oracle's optimum -
    through the production entry `POST /clearing/auto`. On this stand the optimum IS the long cycle at 100 (500 for
    the 5-cycle against the ladder's 30 + 450), so the observable remainder the 020 rule named survives unchanged:
    the triangle's own edges at 10, one occurrence. The strict 020 marker is replaced by the 023 one until the switch.
    """

    edges, tri, long_cycle = _ladder_edges(long_len)
    await seed_graph(db_session, _LADDER_EQ, edges)
    service = ClearingService(db_session)

    # Controls: both cycles are eligible and visible to the diagnostic; the oracle's optimum is the long cycle.
    found = {identity(c) for c in await service.find_cycles(_LADDER_EQ, max_depth=6)}
    assert found == {identity_of(e.debt_id for e in tri), identity_of(e.debt_id for e in long_cycle)}, found
    oracle = oracle_max_volume([(e.debt_id, e.debtor, e.creditor, int(Decimal(e.amount))) for e in edges])[0]
    assert oracle == 100 * long_len, oracle

    before = await fresh_read(db_session, positive_debt_total, _LADDER_EQ)
    response = await auto_clear_http(client, auth_headers, _LADDER_EQ)
    assert response.status_code == 200, response.text
    cleared = response.json()["cleared_cycles"]
    occurrences = await fresh_read(db_session, _committed_clearings)
    remaining = await fresh_read(db_session, _remaining)
    v_edge = before - await fresh_read(db_session, positive_debt_total, _LADDER_EQ)
    # Control: the run completed and its count agrees with the durable occurrences.
    assert cleared == occurrences and cleared >= 1, (cleared, occurrences)

    expected_remaining = sorted((e.debtor, e.creditor, Decimal("10")) for e in tri[1:])
    require_target(
        v_edge == Decimal(oracle) and remaining == expected_remaining and occurrences == 1,
        f"V_edge {v_edge} against the oracle's {oracle}; occurrences={occurrences} remaining={remaining!r}",
    )


# DELETED 2026-09-28, programme 023 slice (d) (R-020-1): `test_equal_amounts_are_ordered_by_the_full_canonical_
# identity` asked the diagnostic list to order equal-amount cycles by the full identity regardless of length - an
# ordering rule of the replaced "large amount first" selection; the flow plan does not select by order. Its
# retained property - same-length ties follow the full identity at depths 4 and 6 - is kept, green, by
# `test_retention_same_length_ties_follow_the_full_identity` in the retention module.
