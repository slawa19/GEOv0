"""Programme 023, slice (a): the baseline reproducers R-023-1..3 and R-023-4a (spec, Verification plan §1).

Red on the baseline BY DESIGN, each under the strict 023 marker (`tests/p023_support.py`): an expected
`TargetMismatch` raised only by the final comparison, after ordinary assertions have shown that the stand
is what it claims (seeded, eligible, the run completed, its count agrees with the durable occurrences). A
broken stand is an `AssertionError`, which the marker does not accept; a tree that meets the target XPASSes
and `strict=True` fails it.

The volume is measured ON THE DEBTS: `V_edge` = Σ positive debt amounts of the equivalent before the run
minus after. The target of R-023-1 is the exhaustive oracle's optimum (`oracle_max_volume`), not a number
typed into the test.

* R-023-1 - loss of volume at depth 6: a 7-ring sharing one edge with a triangle, every amount 2. The ladder
  executes the triangle (V_edge 6) and exhausts the shared edge; the 7-ring is invisible at depth 6 anyway.
  The oracle routes both units of the shared edge round the ring: 14.
* R-023-2 - the shared edge, 6 vs 8 (final.md P2-3): triangle A-B-C all 2; 5-cycle A-B-D-E-F sharing A->B,
  its own edges 1. Today V_edge 6; the target is 8 and the remainder is exactly B->C = 1, C->A = 1.
* R-023-3 - rings longer than six: a 7-ring called at depth 6 (the `/auto` default) is missed - and, as the
  control on a second equivalent, cleared at depth 7, so the red is the depth and nothing else; an 11-ring
  is missed at EVERY supported depth 3..10 (the control: the production detector does see it when allowed
  eleven edges, so the ring is eligible).
* R-023-4a - partial intent and independent identity are not honoured. The baseline executor takes neither
  an amount nor a plan (`service.py:1766`): the declared amount `c < min` travels in the only place the
  baseline call carries amounts - the rendered cycle - and the executor ignores it and clears the locked
  minimum (`service.py:1915`), deleting the rows. Identity: the baseline's occurrence id is `uuid5` of the
  debt-id SET (`service.py:283-285`), so two distinct occurrences on one set of debts get one id. Two
  partial commits are NOT claimed reproduced here - the baseline has no such path (spec, R-023-4a); that
  regression is R-023-4b, slice (b).
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.core.clearing.service import ClearingService
from app.db.models.transaction import Transaction
from tests.conftest import MODE_B
from tests.p020_support import debt_uuid, identity, identity_of, ring, seed_graph
from tests.p023_support import (
    oracle_max_volume,
    positive_debt_total,
    remaining_debts,
    require_target,
    target_xfail_023,
)


async def _committed_clearings(session) -> int:
    return (
        await session.execute(
            select(func.count())
            .select_from(Transaction)
            .where(Transaction.type == "CLEARING", Transaction.state == "COMMITTED")
        )
    ).scalar_one()


async def _run_auto_clear(session, code: str, depth: int) -> tuple[int, Decimal]:
    """`auto_clear` at `depth`; returns (count, V_edge measured on the debts). Asserts the run is sound."""

    before = await positive_debt_total(session, code)
    occurrences_before = await _committed_clearings(session)
    cleared = await ClearingService(session).auto_clear(code, max_depth=depth)
    session.expire_all()
    after = await positive_debt_total(session, code)
    occurrences = await _committed_clearings(session) - occurrences_before
    # Control: the run completed and its count agrees with the durable occurrences.
    assert cleared == occurrences, (cleared, occurrences)
    return cleared, before - after


def _oracle_units(edges) -> int:
    return oracle_max_volume([(e.debt_id, e.debtor, e.creditor, int(Decimal(e.amount))) for e in edges])[0]


# ------------------------------------------------------------------------------------------------ R-023-1


def _r1_edges():
    ring7_pids = [f"p023r1a{k}" for k in range(7)]
    ring7_ids = [debt_uuid(0x2301, k) for k in range(7)]
    ring7 = ring(ring7_pids, ["2"] * 7, ring7_ids)
    shared = ring7[0]  # p023r1a0 -> p023r1a1
    tri = ring(
        [ring7_pids[0], ring7_pids[1], "p023r1t"],
        ["2"] * 3,
        [shared.debt_id, debt_uuid(0x2301, 20), debt_uuid(0x2301, 21)],
    )
    return ring7 + tri[1:], ring7, tri


@target_xfail_023("(d)", "R-023-1: auto_clear(max_depth=6) loses volume the flow finds (6 vs the oracle's 14)")
@MODE_B
@pytest.mark.asyncio
async def test_r023_1_depth_six_loses_volume_the_flow_finds(db_session) -> None:
    edges, ring7, tri = _r1_edges()
    await seed_graph(db_session, "PQA", edges)
    oracle = _oracle_units(edges)
    # Controls: the oracle's optimum is the ring carrying both units of the shared edge; both cycles are
    # eligible and visible to the production detector when it may look seven edges deep.
    assert oracle == 14, oracle
    found = {identity(c) for c in await ClearingService(db_session).find_cycles("PQA", max_depth=7)}
    assert found == {identity_of(e.debt_id for e in ring7), identity_of(e.debt_id for e in tri)}, found

    cleared, v_edge = await _run_auto_clear(db_session, "PQA", 6)
    assert cleared >= 1, "control: the run executed something"

    require_target(
        v_edge == Decimal(oracle),
        f"depth 6: V_edge on the debts {v_edge}, the oracle's optimum {oracle} ({cleared} occurrence(s))",
    )


# ------------------------------------------------------------------------------------------------ R-023-2


def _r2_edges():
    shared = debt_uuid(0x2302, 1)
    tri = ring(["p023r2a", "p023r2b", "p023r2c"], ["2", "2", "2"], [shared, debt_uuid(0x2302, 2), debt_uuid(0x2302, 3)])
    five_pids = ["p023r2a", "p023r2b", "p023r2d", "p023r2e", "p023r2f"]
    five = ring(five_pids, ["2", "1", "1", "1", "1"], [shared] + [debt_uuid(0x2302, 10 + k) for k in range(4)])
    return tri + five[1:], tri, five


@target_xfail_023("(d)", "R-023-2: the shared edge - today 6, the flow 8")
@MODE_B
@pytest.mark.asyncio
async def test_r023_2_shared_edge_six_versus_eight(db_session) -> None:
    edges, tri, five = _r2_edges()
    await seed_graph(db_session, "PQB", edges)
    # Controls: the optimum is 8 and both cycles are eligible at depth 6.
    assert _oracle_units(edges) == 8
    found = {identity(c) for c in await ClearingService(db_session).find_cycles("PQB", max_depth=6)}
    assert found == {identity_of(e.debt_id for e in tri), identity_of(e.debt_id for e in five)}, found

    cleared, v_edge = await _run_auto_clear(db_session, "PQB", 6)
    assert cleared >= 1, "control: the run executed something"
    remaining = await remaining_debts(db_session, "PQB")

    expected_remaining = sorted(
        (str(e.debt_id), e.debtor, e.creditor, Decimal("1")) for e in tri[1:]  # B->C and C->A at 1
    )
    require_target(
        v_edge == Decimal(8) and remaining == expected_remaining,
        f"V_edge {v_edge} (target 8), remaining {remaining!r} (target {expected_remaining!r})",
    )


# ------------------------------------------------------------------------------------------------ R-023-3


def _ring_edges(tag: str, group: int, length: int):
    return ring([f"p023{tag}{k:02d}" for k in range(length)], ["1"] * length, [debt_uuid(group, k) for k in range(length)])


@target_xfail_023("(d)", "R-023-3: a 7-ring called at depth 6 is missed")
@MODE_B
@pytest.mark.asyncio
async def test_r023_3_seven_ring_is_missed_at_depth_six(db_session) -> None:
    control_ring = _ring_edges("r3c", 0x2330, 7)
    target_ring = _ring_edges("r3s", 0x2331, 7)
    await seed_graph(db_session, "PQC", control_ring)
    await seed_graph(db_session, "PQD", target_ring)

    # Control on an identical ring in its own equivalent: at depth 7 today's code clears it completely,
    # so the ring is eligible and executable and the only difference below is the depth.
    cleared, v_edge = await _run_auto_clear(db_session, "PQC", 7)
    assert (cleared, v_edge) == (1, Decimal(7)), (cleared, v_edge)

    cleared, v_edge = await _run_auto_clear(db_session, "PQD", 6)
    require_target(v_edge == Decimal(7), f"depth 6: V_edge {v_edge} ({cleared} occurrences), target 7")


@target_xfail_023("(d)", "R-023-3: an 11-ring is missed at every supported depth")
@MODE_B
@pytest.mark.asyncio
@pytest.mark.parametrize("depth", list(range(3, 11)))
async def test_r023_3_eleven_ring_is_missed_at_every_supported_depth(db_session, depth) -> None:
    edges = _ring_edges("r3e", 0x2332, 11)
    await seed_graph(db_session, "PQE", edges)
    # Control: the ring is eligible - the production detector returns it when allowed eleven edges.
    found = [identity(c) for c in await ClearingService(db_session).find_cycles("PQE", max_depth=11)]
    assert found == [identity_of(e.debt_id for e in edges)], found

    cleared, v_edge = await _run_auto_clear(db_session, "PQE", depth)
    require_target(v_edge == Decimal(11), f"depth {depth}: V_edge {v_edge} ({cleared} occurrences), target 11")


# ----------------------------------------------------------------------------------------------- R-023-4a


@target_xfail_023("(b)", "R-023-4a: a declared amount c < min is not honoured; the minimum is cleared")
@MODE_B
@pytest.mark.asyncio
async def test_r023_4a_declared_partial_amount_is_not_honoured(db_session) -> None:
    edges = ring(["p023r4a", "p023r4b", "p023r4c"], ["5"] * 3, [debt_uuid(0x2304, k) for k in range(3)])
    await seed_graph(db_session, "PQF", edges)
    service = ClearingService(db_session)
    [cycle] = await service.find_cycles("PQF", max_depth=6)
    assert identity(cycle) == identity_of(e.debt_id for e in edges)
    declared = Decimal("2")
    # The declared amount goes where the baseline call carries amounts: the rendered cycle.
    intent = [{**edge, "amount": "2.00"} for edge in cycle]

    cleared = await service.execute_clearing_with_amount(intent)
    db_session.expire_all()
    # Controls: the occurrence executed and committed once.
    assert cleared is not None, "control: the baseline executed the cycle"
    assert await _committed_clearings(db_session) == 1

    remaining = await remaining_debts(db_session, "PQF")
    expected = sorted((str(e.debt_id), e.debtor, e.creditor, Decimal("5") - declared) for e in edges)
    require_target(
        remaining == expected,
        f"declared {declared}: executor cleared {cleared}; remaining {remaining!r}, target {expected!r}",
    )


@target_xfail_023("(b)", "R-023-4a: two distinct occurrences on one debt set share the baseline identity")
def test_r023_4a_distinct_occurrences_get_distinct_identities() -> None:
    debt_ids = [debt_uuid(0x2305, k) for k in range(3)]
    # Two occurrences of ONE debt set: e.g. ordinal 0 of plan A and ordinal 0 of plan B, each declaring a
    # partial amount on the same surviving rows. The baseline identity takes the debt set and nothing else.
    first = ClearingService._execution_tx_id(debt_ids)
    second = ClearingService._execution_tx_id(list(reversed(debt_ids)))
    # Control: the function is the one the executor uses and it is deterministic.
    assert first == ClearingService._execution_tx_id(debt_ids)
    require_target(first != second, f"both occurrences are {first}: the second would replay as the first")
