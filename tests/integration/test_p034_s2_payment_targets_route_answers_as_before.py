"""034 S2 (F-034-11): `GET /simulator/runs/{run_id}/payment-targets` answers the same bytes over the public router.

The route narrowed the router with the private `PaymentService._confine_router_to_perimeter` and asked the private
`PaymentRouter._bfs_single_path` for every vertex. 035 A6 gave the router public names for both
(`confine_to_participants`, `payment_targets`); S2 moves the route to them. Nothing a client sees may change.

HOW IT IS HELD. `_expected_body` below is the route's old computation, copied as it stood on `0d153390` (narrow, BFS
per vertex, sort, cut by `limit`, max-flow AFTER the cut, `max_available` as a string or null), and serialised the way
the route's response is. The HTTP response must equal it BYTE FOR BYTE for every source, depth, limit and
`include_max_available` on a populated stand - the stand of 035 A6 (a ring with chords, spent lines, a hop that may
not be passed through, a blocked participant, a spur), with the run's perimeter a strip of it. This module was green
on `0d153390` before the route was touched: that is what makes the copy an oracle of the old route and not of itself.

Also held: the refusals around the computation (a source outside the perimeter, an unknown equivalent, `limit=0`,
which HTTP refuses although the old loop read zero as "no cut") and a source of the perimeter that has no line.

Not seen here: seeding of an unseeded run by this GET (the stand's run is already seeded), the actions feature flag
and run ownership (`tests/unit/test_simulator_actions_feature_flag.py`, `tests/integration/test_p024_*`).
"""

from __future__ import annotations

import json
import uuid
from decimal import Decimal

import pytest

from app.core.payments.router import PaymentRouter
from app.core.simulator.real_scenario_seeder import simulated_public_key
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine
from tests.debt_setup import debt_fixture_setup
from tests.integration.test_p024_payment_targets_stay_in_the_run import _anonymous_owner, run_world  # noqa: F401
from tests.integration.test_p035_a6_payment_targets_public_router_method import (
    _as_the_route_computes,
    _narrow_as_on_213c84e3,
)


async def _world(db_session):
    """035 A6's graph (see its `_world`), built of participants the simulator may own, in one equivalent."""

    n = uuid.uuid4().hex[:8].upper()
    eq = Equivalent(code=f"S2T{n}", precision=2, is_active=True, metadata_={})
    people = [Participant(pid=f"s2t{i:02d}_{n}", display_name=f"s2t{i}", public_key=simulated_public_key(f"s2t{i:02d}_{n}"),
                          type="person", status="active", profile={}) for i in range(15)]
    db_session.add_all([eq, *people])
    await db_session.flush()

    def line(creditor: int, debtor: int, limit: str = "100", **policy) -> TrustLine:
        return TrustLine(from_participant_id=people[creditor].id, to_participant_id=people[debtor].id,
                         equivalent_id=eq.id, limit=Decimal(limit), status="active",
                         policy={"can_be_intermediate": True, **policy})

    lines = [line((i + 1) % 12, i) for i in range(12)]
    lines += [line(i, (i + 1) % 12, "50") for i in (0, 3, 6, 9)]
    lines += [line(5, 0), line(9, 2), line(1, 7), line(11, 4)]
    lines += [line(8, 3, can_be_intermediate=False)]
    lines += [line(10, 6, blocked_participants=[people[0].pid])]
    lines += [line(4, 10, "10"), line(2, 8, "0")]
    lines += [line(12, 3, can_be_intermediate=False), line(13, 12)]
    db_session.add_all(lines)
    debts = [Debt(debtor_id=people[10].id, creditor_id=people[4].id, equivalent_id=eq.id, amount=Decimal("10")),
             Debt(debtor_id=people[2].id, creditor_id=people[3].id, equivalent_id=eq.id, amount=Decimal("40"))]
    async with debt_fixture_setup(db_session, label="p034-s2"):
        db_session.add_all(debts)
    await db_session.commit()
    PaymentRouter.invalidate_cache(eq.code)
    return eq.code, [p.pid for p in people]  # people[14] has no line at all


def _scenario(code: str, pids: list[str]) -> dict:
    return {"equivalents": [code], "participants": [{"id": pid, "type": "person"} for pid in pids], "trustlines": []}


async def _expected_body(db_session, code: str, perimeter: set[str], src: str, *, max_hops: int, limit: int,
                         include_max_available: bool) -> bytes:
    """The body the route gave on `0d153390`, by its own steps."""

    router = PaymentRouter(db_session)
    await router.build_graph(code)
    _narrow_as_on_213c84e3(router, perimeter)
    items = []
    for hops, dst, _path in _as_the_route_computes(router, src, max_hops=max_hops, limit=limit):
        max_available = None
        if include_max_available:
            try:
                max_available = str(getattr(router.calculate_max_flow(src, dst), "max_amount", None) or "0")
            except Exception:
                max_available = None
        items.append({"to_pid": dst, "hops": int(hops), "max_available": max_available})
    return json.dumps({"items": items}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _url(run_id: str) -> str:
    return f"/api/v1/simulator/runs/{run_id}/payment-targets"


@pytest.mark.asyncio
async def test_the_route_answers_byte_for_byte_what_it_answered(client, db_session, run_world) -> None:  # noqa: F811
    code, pids = await _world(db_session)
    perimeter = set(pids[1:12]) | {pids[14]}  # a strip: pt00, the spur (12, 13) are outside; 14 is in, with no line
    run_id = run_world(await _anonymous_owner(client), _scenario(code, sorted(perimeter)))

    compared = with_targets = cut = with_capacity = 0
    answers: dict[tuple, bytes] = {}
    for src in sorted(perimeter):
        for max_hops in (1, 2, 3, 6, 8):
            for limit in (1, 3, 200):
                for include in (False, True):
                    if include and (max_hops, limit) not in {(8, 200), (2, 3)}:
                        continue  # max-flow per target is the slow part; two shapes of it are enough
                    response = await client.get(_url(run_id), params={
                        "equivalent": code, "from_pid": src, "max_hops": max_hops, "limit": limit,
                        "include_max_available": "true" if include else "false"})
                    assert response.status_code == 200, response.text
                    expected = await _expected_body(db_session, code, perimeter, src, max_hops=max_hops, limit=limit,
                                                    include_max_available=include)
                    assert response.content == expected, (
                        f"from {src}, max_hops {max_hops}, limit {limit}, include_max_available {include}:\n"
                        f"  the route: {response.content!r}\n  before S2: {expected!r}")
                    answers[(src, max_hops, limit, include)] = response.content
                    items = json.loads(expected)["items"]
                    compared += 1
                    with_targets += bool(items)
                    cut += len(items) == limit
                    with_capacity += sum(1 for item in items if item["max_available"] not in (None, "0"))

    # Anti-vacuum: the stand has answers, the limit cut some, capacities were really computed and differ, the
    # depth and the perimeter each decide an answer, and a participant without a line has no target.
    assert compared > 150 and with_targets > 100 and cut > 30 and with_capacity > 20, (
        compared, with_targets, cut, with_capacity)
    src = pids[2]
    assert answers[(src, 1, 200, False)] != answers[(src, 8, 200, False)]
    capacities = {item["max_available"] for item in json.loads(answers[(src, 8, 200, True)])["items"]}
    assert len(capacities) > 1, capacities
    assert answers[(pids[14], 8, 200, False)] == b'{"items":[]}'
    whole = PaymentRouter(db_session)
    await whole.build_graph(code)
    unconfined = {t.to_pid for t in whole.payment_targets(src, max_hops=8)}
    confined = {item["to_pid"] for item in json.loads(answers[(src, 8, 200, False)])["items"]}
    assert confined < unconfined and confined <= perimeter, (confined, unconfined)


@pytest.mark.asyncio
async def test_the_refusals_around_the_computation_are_the_same(client, db_session, run_world) -> None:  # noqa: F811
    code, pids = await _world(db_session)
    perimeter = set(pids[1:12])
    run_id = run_world(await _anonymous_owner(client), _scenario(code, sorted(perimeter)))

    outside = await client.get(_url(run_id), params={"equivalent": code, "from_pid": pids[0]})
    assert outside.status_code == 404 and pids[1] not in outside.text, outside.text
    unknown_eq = await client.get(_url(run_id), params={"equivalent": "NOSUCHEQ", "from_pid": pids[2]})
    assert unknown_eq.status_code == 404, unknown_eq.text
    for bad in ({"limit": 0}, {"limit": 1001}, {"max_hops": 0}, {"max_hops": 9}):
        refused = await client.get(_url(run_id), params={"equivalent": code, "from_pid": pids[2], **bad})
        assert refused.status_code == 422, (bad, refused.text)
    # Control: the same request without the bad parameter is answered.
    good = await client.get(_url(run_id), params={"equivalent": code, "from_pid": pids[2]})
    assert good.status_code == 200 and good.json()["items"], good.text
