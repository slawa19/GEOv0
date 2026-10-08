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

WHICH SLICE TURNS R-023-4a GREEN - (d), recorded 2026-09-27 with slice (b). Slice (b) is additive: it adds the
v2 entry (`execute_occurrence`, a declared amount, plan-scoped identity) beside the production executor and
leaves v1 as it is - decision 5 keeps `clear == min(pre)` for v1 and decision 6 keeps the v1 set-hash
namespace for historical occurrences.

RETARGETED 2026-09-28, slice (d), step 2 (red-first), recorded in the spec's (d) section. Every reproducer now
calls THE PRODUCTION ENTRY - `POST /api/v1/clearing/auto`, with no depth (decision 8 removes it from execution;
before the switch the route's default depth 6 applies) - instead of naming today's internals (`auto_clear`,
`execute_clearing_with_amount`, `_execution_tx_id`). That is what "turns green through the switch" means: the
same request and the same measurement on the debts; the switch changes what the production path IS. Moved:

* R-023-1/2: `service.auto_clear(code, max_depth=6)` -> `POST /clearing/auto` (the route's default was 6).
* R-023-3: the 7-ring's control at depth 7 becomes the diagnostic detector at depth 7 (the ring is eligible);
  the 11-ring's per-depth parametrisation 3..10 collapses into one request - execution has no depth after (d),
  and before it the route's only depth is its default 6 (the others were a service argument, not an entry).
* R-023-4a, partial intent: instead of handing the v1 executor a rendered cycle with a lowered amount (a path no
  product caller has after (d)), the stand is R-023-2's shared edge, whose optimum REQUIRES a partial
  occurrence: the triangle carries 1 while its locked minimum is 2. Target: the production pass commits an
  occurrence on the triangle's debts for 1 and its two exclusive edges survive at exactly 2 - 1.
* R-023-4a, identity: instead of calling the v1 `_execution_tx_id`, the production pass's own committed
  occurrence is read back: the stored `Transaction.tx_id` is the plan-scoped id (decision 6) of its plan,
  equivalent and ordinal, and the same debt set in another plan gets another id.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.db.models.transaction import Transaction
from tests.conftest import MODE_B
from tests.p020_support import debt_uuid, ring, seed_graph
from tests.p023_support import (
    assert_named_cycles_are_in_the_snapshot,
    auto_clear_http,
    fresh_read,
    oracle_max_volume,
    positive_debt_total,
    remaining_debts,
    require_auto_progress,
    require_target,
    v1_clearing_tx_id,
)


async def _committed_clearings(session) -> int:
    return (
        await session.execute(
            select(func.count())
            .select_from(Transaction)
            .where(Transaction.type == "CLEARING", Transaction.state == "COMMITTED")
        )
    ).scalar_one()


async def _run_auto_clear(client, headers, session, code: str) -> tuple[int, Decimal]:
    """`POST /clearing/auto` (the production entry); returns (count, V_edge measured on the debts)."""

    before = await fresh_read(session, positive_debt_total, code)
    occurrences_before = await fresh_read(session, _committed_clearings)
    response = await auto_clear_http(client, headers, code)
    assert response.status_code == 200, response.text
    cleared = response.json()["cleared_cycles"]
    after = await fresh_read(session, positive_debt_total, code)
    occurrences = await fresh_read(session, _committed_clearings) - occurrences_before
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


@MODE_B
@pytest.mark.asyncio
async def test_r023_1_depth_six_loses_volume_the_flow_finds(db_session, client, auth_headers) -> None:
    edges, ring7, tri = _r1_edges()
    await seed_graph(db_session, "PQA", edges)
    oracle = _oracle_units(edges)
    # Controls: the oracle's optimum is the ring carrying both units of the shared edge; both cycles are real
    # clearable alternatives on the snapshot (035 A2a, D2: read from the observed edges - the plan holds only the
    # optimum, so it cannot list them; until then the retired detector did, at depth 7).
    assert oracle == 14, oracle
    await assert_named_cycles_are_in_the_snapshot(db_session, "PQA", [ring7, tri])

    cleared, v_edge = await _run_auto_clear(client, auth_headers, db_session, "PQA")
    assert cleared >= 1, "control: the run executed something"

    require_target(
        v_edge == Decimal(oracle),
        f"/auto: V_edge on the debts {v_edge}, the oracle's optimum {oracle} ({cleared} occurrence(s))",
    )


# ------------------------------------------------------------------------------------------------ R-023-2


def _r2_edges():
    shared = debt_uuid(0x2302, 1)
    tri = ring(["p023r2a", "p023r2b", "p023r2c"], ["2", "2", "2"], [shared, debt_uuid(0x2302, 2), debt_uuid(0x2302, 3)])
    five_pids = ["p023r2a", "p023r2b", "p023r2d", "p023r2e", "p023r2f"]
    five = ring(five_pids, ["2", "1", "1", "1", "1"], [shared] + [debt_uuid(0x2302, 10 + k) for k in range(4)])
    return tri + five[1:], tri, five


@MODE_B
@pytest.mark.asyncio
async def test_r023_2_shared_edge_six_versus_eight(db_session, client, auth_headers) -> None:
    edges, tri, five = _r2_edges()
    await seed_graph(db_session, "PQB", edges)
    # Controls: the optimum is 8 and both cycles are real clearable alternatives on the snapshot (035 A2a, D2).
    assert _oracle_units(edges) == 8
    await assert_named_cycles_are_in_the_snapshot(db_session, "PQB", [tri, five])

    cleared, v_edge = await _run_auto_clear(client, auth_headers, db_session, "PQB")
    assert cleared >= 1, "control: the run executed something"
    remaining = await fresh_read(db_session, remaining_debts, "PQB")

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


@MODE_B
@pytest.mark.asyncio
async def test_r023_3_seven_ring_is_missed_at_depth_six(db_session, client, auth_headers) -> None:
    target_ring = _ring_edges("r3s", 0x2331, 7)
    await seed_graph(db_session, "PQD", target_ring)
    # Control: the ring is a real clearable cycle on the snapshot (035 A2a, D2; the retired detector showed it at 7).
    await assert_named_cycles_are_in_the_snapshot(db_session, "PQD", [target_ring])

    cleared, v_edge = await _run_auto_clear(client, auth_headers, db_session, "PQD")
    require_target(v_edge == Decimal(7), f"/auto: V_edge {v_edge} ({cleared} occurrences), target 7")


@MODE_B
@pytest.mark.asyncio
async def test_r023_3_eleven_ring_is_missed_at_every_supported_depth(db_session, client, auth_headers) -> None:
    edges = _ring_edges("r3e", 0x2332, 11)
    await seed_graph(db_session, "PQE", edges)
    # Control: the ring is a real clearable cycle on the snapshot (035 A2a, D2; the retired detector showed it at 11).
    await assert_named_cycles_are_in_the_snapshot(db_session, "PQE", [edges])

    cleared, v_edge = await _run_auto_clear(client, auth_headers, db_session, "PQE")
    require_target(v_edge == Decimal(11), f"/auto: V_edge {v_edge} ({cleared} occurrences), target 11")


# ----------------------------------------------------------------------------------------------- R-023-4a


@MODE_B
@pytest.mark.asyncio
async def test_r023_4a_declared_partial_amount_is_not_honoured(db_session, client, auth_headers) -> None:
    edges, tri, five = _r2_edges()
    await seed_graph(db_session, "PQF", edges)
    # Controls: the optimum needs the triangle at 1 while its locked minimum is 2 (the shared edge is split).
    assert _oracle_units(edges) == 8
    assert min(Decimal(e.amount) for e in tri) == Decimal("2")

    response = await auto_clear_http(client, auth_headers, "PQF")
    assert response.status_code == 200, response.text
    committed = require_auto_progress(response.json())
    tri_ids = {str(e.debt_id) for e in tri}
    on_triangle = [o for o in committed if {e["debt_id"] for e in o["edges"]} == tri_ids]
    remaining = await fresh_read(db_session, remaining_debts, "PQF")
    expected = sorted((str(e.debt_id), e.debtor, e.creditor, Decimal("1")) for e in tri[1:])
    require_target(
        [Decimal(o["amount"]) for o in on_triangle] == [Decimal("1")] and remaining == expected,
        f"the triangle's occurrence(s) {on_triangle!r}; remaining {remaining!r}, target {expected!r}",
    )


@MODE_B
@pytest.mark.asyncio
async def test_r023_4a_distinct_occurrences_get_distinct_identities(db_session, client, auth_headers) -> None:
    import uuid

    from app.core.clearing.service import ClearingOccurrence
    from app.db.models.equivalent import Equivalent

    edges = ring(["p023r4a", "p023r4b", "p023r4c"], ["5"] * 3, [debt_uuid(0x2304, k) for k in range(3)])
    await seed_graph(db_session, "PQG", edges)
    response = await auto_clear_http(client, auth_headers, "PQG")
    assert response.status_code == 200, response.text
    committed = require_auto_progress(response.json())
    require_target(len(committed) == 1, f"one occurrence expected, got {committed!r}")
    [occurrence] = committed

    async def _stored(session):
        eq_id = (await session.execute(select(Equivalent.id).where(Equivalent.code == "PQG"))).scalar_one()
        ids = (await session.execute(select(Transaction.tx_id).where(Transaction.type == "CLEARING"))).scalars().all()
        return eq_id, list(ids)

    equivalent_id, stored = await fresh_read(db_session, _stored)
    debt_ids = tuple(uuid.UUID(e["debt_id"]) for e in occurrence["edges"])

    def _occurrence(plan_id):
        return ClearingOccurrence(
            plan_id=plan_id,
            equivalent_id=equivalent_id,
            ordinal=occurrence["ordinal"],
            debt_ids=debt_ids,
            amount_atoms=5 * 10**8,
        )

    this_plan = _occurrence(uuid.UUID(occurrence["plan_id"]))
    other_plan = _occurrence(uuid.uuid4())
    # Control: the v1 set-hash gives one id to any two occurrences of this debt set - the case the plan scope
    # exists for.
    # (The v1 rule is the test copy since 024 `T2417` removed the application's.)
    assert v1_clearing_tx_id(debt_ids) == v1_clearing_tx_id(reversed(debt_ids))
    require_target(
        stored == [occurrence["occurrence_id"]] == [this_plan.occurrence_id]
        and other_plan.occurrence_id != this_plan.occurrence_id,
        f"stored {stored}, reported {occurrence['occurrence_id']}, plan-scoped {this_plan.occurrence_id}",
    )
