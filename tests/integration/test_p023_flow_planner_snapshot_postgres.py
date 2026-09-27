"""Programme 023, slice (a): the planner's snapshot read on PostgreSQL - eligibility and balances.

Spec decision 1 and Verification plan §2 ("балансы только по допустимому подграфу"):

* ELIGIBILITY PARITY - consent is `ClearingService._policy_flag` exactly (the 020 whitespace stand: every
  whitespace-wrapped "false" refused, "true"/"yes" admitted), statuses active/frozen only, `amount > 0`.
* BALANCES ON THE ELIGIBLE SUBGRAPH - a triangle and, attached to its vertices, excluded debts (closed line,
  refused consent, an endpoint outside the perimeter) that change every whole-equivalent balance. The plan
  clears exactly the triangle and names no excluded debt. Counter-check: were the excluded debts admitted,
  the plan would differ (they close a larger cycle), so the filter is what decides, not the graph.
* PERIMETER - `None` applies none, an empty set admits nobody, pids resolving to nobody admit nobody.
* LARGE ATOMS - the column maximum `999999999999.99999999` is read as exactly 99999999999999999999 atoms.
"""

from __future__ import annotations

import pytest

from app.core.clearing.flow_planner import PlanEdge, load_snapshot, plan_clearing, plan_for_equivalent
from app.core.clearing.service import ClearingService
from app.utils.exceptions import GeoException
from tests.p020_support import Edge, debt_uuid, participant_uuid, ring, seed_graph

_SQL_TRIM = " \t\n\r\f\v"
_PY_ONLY_WHITESPACE = [c for c in map(chr, range(0x110000)) if c.isspace() and c not in _SQL_TRIM]


def _consents(value) -> bool:
    return ClearingService._policy_flag({"auto_clearing": value}, "auto_clearing", default=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("scoped", [False, True])
async def test_the_snapshot_admits_consent_exactly_as_production(db_session, scoped) -> None:
    edges, refused, admitted = [], set(), set()
    values = [f"{c}false{c}" for c in _PY_ONLY_WHITESPACE] + [" true ", "　yes", False, "false", True, 0, 1, "off"]
    for n, value in enumerate(values):
        e = Edge(debt_uuid(0x2340, n), f"p023w{n:03d}a", f"p023w{n:03d}b", "5", consent=value)
        edges.append(e)
        (admitted if _consents(value) else refused).add(e.debt_id)
    for n, status in enumerate(("frozen", "closed")):
        e = Edge(debt_uuid(0x2341, n), f"p023s{n}a", f"p023s{n}b", "5", status=status)
        edges.append(e)
        (admitted if status == "frozen" else refused).add(e.debt_id)
    # Controls: the stand holds both classes in quantity.
    assert len(refused) == len(_PY_ONLY_WHITESPACE) + 5 and len(admitted) == 5
    await seed_graph(db_session, "PQW", edges)

    perimeter = {p for e in edges for p in (e.debtor, e.creditor)} if scoped else None
    loaded = {e.debt_id for e in await load_snapshot(db_session, "PQW", allowed_participant_pids=perimeter)}

    assert loaded == admitted, (sorted(map(str, loaded - admitted)), sorted(map(str, admitted - loaded)))


def _balance_stand():
    tri = ring(["p023ba", "p023bb", "p023bc"], ["4"] * 3, [debt_uuid(0x2350, k) for k in range(3)])
    # Excluded debts leaving and entering the triangle's vertices: together with a triangle edge they close
    # a 4-cycle p023ba -> p023bb -> p023bx -> p023by -> p023ba (amount 9), so admitting them changes the plan.
    closed = Edge(debt_uuid(0x2351, 0), "p023bb", "p023bx", "9", status="closed")
    refused = Edge(debt_uuid(0x2351, 1), "p023bx", "p023by", "9", consent="no")
    outside = Edge(debt_uuid(0x2351, 2), "p023by", "p023ba", "9")
    return tri, [closed, refused, outside]


@pytest.mark.asyncio
async def test_balances_come_from_the_eligible_subgraph_only(db_session) -> None:
    tri, excluded = _balance_stand()
    await seed_graph(db_session, "PQX", tri + excluded)
    perimeter = {"p023ba", "p023bb", "p023bc", "p023bx"}  # p023by outside: its debts are out

    plan = await plan_for_equivalent(db_session, "PQX", allowed_participant_pids=perimeter)

    excluded_ids = {e.debt_id for e in excluded}
    assert {e.debt_id for e in plan.edges} == {e.debt_id for e in tri}
    assert not excluded_ids & {e.debt_id for c in plan.cycles for e in c.edges}
    assert [(len(c.edges), c.atoms) for c in plan.cycles] == [(3, 4 * 10**8)]
    assert (plan.v_edge, plan.v_cyc) == (12 * 10**8, 4 * 10**8)

    # Counter-check (anti-vacuum): the same debts ADMITTED give a different plan - the filter decides.
    admitted = list(plan.edges) + [
        PlanEdge(e.debt_id, participant_uuid(e.debtor), participant_uuid(e.creditor), 9 * 10**8) for e in excluded
    ]
    wider = plan_clearing(admitted)
    assert wider.v_edge > plan.v_edge and any(len(c.edges) == 4 for c in wider.cycles), wider


@pytest.mark.asyncio
async def test_the_perimeter_has_three_states(db_session) -> None:
    tri = ring(["p023pa", "p023pb", "p023pc"], ["2"] * 3, [debt_uuid(0x2360, k) for k in range(3)])
    await seed_graph(db_session, "PQP", tri)

    assert len(await load_snapshot(db_session, "PQP")) == 3  # None: no perimeter applied
    assert await load_snapshot(db_session, "PQP", allowed_participant_pids=set()) == []
    assert await load_snapshot(db_session, "PQP", allowed_participant_pids={"nobody-p023"}) == []
    two = await load_snapshot(db_session, "PQP", allowed_participant_pids={"p023pa", "p023pb"})
    assert [(e.debtor_id, e.creditor_id) for e in two] == [(participant_uuid("p023pa"), participant_uuid("p023pb"))]
    with pytest.raises(GeoException):
        await load_snapshot(db_session, "PQZ")


@pytest.mark.asyncio
async def test_the_column_maximum_is_read_as_exact_atoms(db_session) -> None:
    top = "999999999999.99999999"
    tri = ring(["p023la", "p023lb", "p023lc"], [top, top, "999999999999.99999998"], [debt_uuid(0x2370, k) for k in range(3)])
    await seed_graph(db_session, "PQL", tri, precision=8)

    plan = await plan_for_equivalent(db_session, "PQL")

    assert sorted(e.atoms for e in plan.edges) == [99999999999999999998, 99999999999999999999, 99999999999999999999]
    assert [c.atoms for c in plan.cycles] == [99999999999999999998]
    assert plan.v_edge == 3 * 99999999999999999998
