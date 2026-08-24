"""T1211: the shared-edge ORDER pin, on the tier where debt-id spellings coincide.

The default-tier module (`tests/unit/test_p012_t1210_detector_union_default_tier.py`) holds
the reasoning and BOTH pins - the union's order and auto_clear's final ledger.  This twin
re-runs the ORDER pin on PostgreSQL because the merge's dedup and tiebreak are keyed on
debt-id spelling, and the two tiers spell debt ids differently - a fix that holds on one
tier has already passed for the wrong reason once in this wave.

The OUTCOME pin (auto_clear leaves b->c/c->a) deliberately has no PG twin here: the clearing
execution path refuses the connection-bound session the shared fixture provides
("PostgreSQL clearing requires an engine-bound AsyncSession"), and an engine-bound stand
commits rows that need a hand-rolled cleanup of debts, trustlines, transactions and audit
rows.  The outcome is order times the executor contract - "execute the first cycle that
succeeds", pinned tier-independently elsewhere - so the tier-sensitive half is the order,
and that is what runs here.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.core.clearing.service import ClearingService
from tests.unit.test_p012_t1210_detector_union_default_tier import (
    _EQ,
    _SHARED_EDGE,
    _seed_graph,
)

pytestmark = pytest.mark.postgres


@pytest.mark.asyncio
async def test_within_a_length_the_largest_executable_cycle_comes_first_pg(db_session) -> None:
    await _seed_graph(db_session, edges=_SHARED_EDGE)

    service = ClearingService(db_session)
    cycles = await service.find_cycles(_EQ, max_depth=6)

    assert len(cycles) == 2
    amounts = [min(Decimal(e["amount"]) for e in c) for c in cycles]
    assert amounts == [Decimal("100"), Decimal("10")], (
        f"union must rank same-length cycles by executable amount descending on this tier "
        f"too; got {amounts!r}"
    )
