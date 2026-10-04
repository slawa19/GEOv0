"""Programme 023, slice (a): the planner's snapshot read on PostgreSQL - eligibility and balances.

Spec decision 1 and Verification plan §2 ("балансы только по допустимому подграфу"):

* ELIGIBILITY PARITY - consent is `ClearingService._policy_flag(policy, "auto_clearing", default=True)`
  exactly, over every encoding the JSON column can hold (the catalogue moved here from the 020 detector stand,
  025 gap 4): default `true` when there is no policy, no key or a JSON null (`docs/ru/02-protocol-spec.md`
  policy table, `docs/ru/09-decisions-and-defaults.md`); a boolean is itself; a number is non-zero; a string is
  trimmed and lower-cased, {false,0,no,off} refuse, {true,1,yes,on} admit, anything else falls back to the
  default; any other JSON value (array, object) is its truthiness. The expected outcome of each encoding is
  WRITTEN DOWN from that rule, not computed by the parser the snapshot calls. Statuses active only (028 `F-028-29`: `frozen` is gone).
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
from app.utils.exceptions import GeoException
from tests.p020_support import MISSING_KEY, NULL_POLICY, Edge, debt_uuid, participant_uuid, ring, seed_graph

_SQL_TRIM = " \t\n\r\f\v"
_PY_ONLY_WHITESPACE = [c for c in map(chr, range(0x110000)) if c.isspace() and c not in _SQL_TRIM]


# (stored `policy.auto_clearing`, admitted?) - the expectation is the documented rule read by hand (header).
_CONSENT_CATALOGUE = [
    (True, True), (False, False),
    ("false", False), (" False ", False), ("0", False), ("no", False), ("OFF", False), ("off", False),
    ("yes", True), ("on", True), ("1", True), (" true ", True), ("　yes", True),
    ("maybe", True), ("", True),  # an unknown string, the empty string: the default
    (0, False), (1, True), (0.0, False), (2.5, True),
    (None, True),  # JSON null under the key: the default
    ([], False), ([1], True), ({}, False), ({"a": 1}, True),
    (MISSING_KEY, True), (NULL_POLICY, True),  # a policy without the key, no policy at all: the default
]


@pytest.mark.asyncio
@pytest.mark.parametrize("scoped", [False, True])
async def test_the_snapshot_admits_consent_exactly_as_production(db_session, scoped) -> None:
    edges, refused, admitted = [], set(), set()
    # Every whitespace-wrapped "false" is refused: Python's `strip` trims more than SQL's (the 020 stand).
    catalogue = [(f"{c}false{c}", False) for c in _PY_ONLY_WHITESPACE] + _CONSENT_CATALOGUE
    for n, (value, expected) in enumerate(catalogue):
        e = Edge(debt_uuid(0x2340, n), f"p023w{n:03d}a", f"p023w{n:03d}b", "5", consent=value)
        edges.append(e)
        (admitted if expected else refused).add(e.debt_id)
    for n, status in enumerate(("active", "closed")):  # 028 `F-028-29`: was `frozen` (admitted), no longer a status
        e = Edge(debt_uuid(0x2341, n), f"p023s{n}a", f"p023s{n}b", "5", status=status)
        edges.append(e)
        (admitted if status == "active" else refused).add(e.debt_id)
    # Controls: the stand holds both classes in quantity.
    assert len(refused) == len(_PY_ONLY_WHITESPACE) + 12 and len(admitted) == 16
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
