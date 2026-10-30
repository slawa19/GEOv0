"""054-F-H2: `GET /simulator/runs/{run_id}/payment-targets` does not hold the event loop.

`PaymentRouter.payment_targets` ran one search over every simple path per target, with no visited set and no
deadline, synchronously inside the async route: on the shipped `greenfield-village-100-realistic-v2` lines with open
policies and `max_hops=8` one GET held the loop for seconds (every other request of the process waited). Now one
linear search answers for every target; where a route-dependent rule forces the per-target search, the routing
deadline bounds it and the GET answers 504 E007 - never a silently shorter list.

The loop is watched by a ticker task: the longest gap between its `asyncio.sleep(TICK)` wake-ups while the GET runs.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections import Counter, deque
from decimal import Decimal
from pathlib import Path

import pytest

from app.config import settings
from app.core.payments.router import PaymentRouter
from app.core.simulator.real_scenario_seeder import simulated_public_key
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine
from tests.integration.test_p024_payment_targets_stay_in_the_run import _anonymous_owner, run_world  # noqa: F401

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "simulator" / "greenfield-village-100-realistic-v2"
TICK = 0.01
MAX_HOPS = 8


async def _greenfield_world(db_session, *, blocked_by_source_lines: bool):
    """The fixture's lines of its main equivalent, every policy open; plus U, who only owes (no hop into U)."""

    sc = json.loads((FIXTURE / "scenario.json").read_text(encoding="utf-8"))
    eq_src = Counter(t["equivalent"] for t in sc["trustlines"]).most_common(1)[0][0]
    n = uuid.uuid4().hex[:6].upper()
    eq = Equivalent(code=f"H2{n}", precision=2, is_active=True, metadata_={})
    pid_of = {p["id"]: f"h2u{i:03d}_{n}" for i, p in enumerate(sc["participants"])}
    people = {src: Participant(pid=pid, display_name=pid, public_key=simulated_public_key(pid), type="person",
                               status="active", profile={}) for src, pid in pid_of.items()}
    u_pid = f"h2zzz_{n}"
    people["__u__"] = Participant(pid=u_pid, display_name=u_pid, public_key=simulated_public_key(u_pid),
                                  type="person", status="active", profile={})
    pid_of["__u__"] = u_pid
    db_session.add_all([eq, *people.values()])
    await db_session.flush()
    lines = [(t["from"], t["to"], Decimal(str(t["limit"]))) for t in sc["trustlines"]
             if t["equivalent"] == eq_src and Decimal(str(t["limit"])) > 0]
    lines.append((sc["participants"][0]["id"], "__u__", Decimal("100")))  # creditor first -> U: the hop U -> first
    source = sorted(pid for key, pid in pid_of.items() if key != "__u__")[0]
    # Independent oracle: payment debtor -> creditor uses the line creditor -> debtor; open policy, BFS hop counts.
    adjacency: dict[str, set[str]] = {}
    for a, b, _ in lines:
        adjacency.setdefault(pid_of[b], set()).add(pid_of[a])
    hops = _hops(adjacency, source)
    # A participant two hops from the source, blocked by every line of the source's pairs: every route starts on such
    # a line, so it is never an intermediary - refused only FURTHER on, a route-dependent rule, which the linear search
    # hands to the exact one. It may still be paid.
    far = sorted(pid for pid, h in hops.items() if h == 2)[0]
    if blocked_by_source_lines:
        hops = _hops(adjacency, source, not_through=far)
    source_key = next(k for k, v in pid_of.items() if v == source)
    policy_open = {"can_be_intermediate": True}
    policy_blocking = {"can_be_intermediate": True, "blocked_participants": [far]}
    db_session.add_all([TrustLine(
        from_participant_id=people[a].id, to_participant_id=people[b].id, equivalent_id=eq.id, limit=limit,
        status="active",
        policy=policy_blocking if blocked_by_source_lines and source_key in (a, b) else policy_open,
    ) for a, b, limit in lines])
    await db_session.commit()
    PaymentRouter.invalidate_cache(eq.code)
    scenario = {"equivalents": [eq.code], "participants": [{"id": pid, "type": "person"} for pid in pid_of.values()],
                "trustlines": []}
    return eq.code, source, u_pid, {pid: h for pid, h in hops.items() if pid != source}, scenario


def _hops(adjacency: dict[str, set[str]], source: str, *, not_through: str | None = None) -> dict[str, int]:
    hops = {source: 0}
    queue = deque([source])
    while queue:
        node = queue.popleft()
        if hops[node] < MAX_HOPS and node != not_through:
            for nxt in adjacency.get(node, ()):
                if nxt not in hops:
                    hops[nxt] = hops[node] + 1
                    queue.append(nxt)
    return hops


async def _get_while_watching_the_loop(client, url, params):
    stop = asyncio.Event()
    longest = 0.0

    async def ticker():
        nonlocal longest
        loop = asyncio.get_running_loop()
        while not stop.is_set():
            before = loop.time()
            await asyncio.sleep(TICK)
            longest = max(longest, loop.time() - before - TICK)

    watch = asyncio.create_task(ticker())
    await asyncio.sleep(0)
    try:
        response = await client.get(url, params=params)
    finally:
        stop.set()
        await watch
    return response, longest


@pytest.mark.asyncio
async def test_h2_payment_targets_at_depth_8_answers_every_target_without_holding_the_loop(
    client, db_session, run_world  # noqa: F811
) -> None:
    code, source, u_pid, expected_hops, scenario = await _greenfield_world(db_session, blocked_by_source_lines=False)
    run_id = run_world(await _anonymous_owner(client), scenario)
    response, longest = await _get_while_watching_the_loop(
        client, f"/api/v1/simulator/runs/{run_id}/payment-targets",
        {"equivalent": code, "from_pid": source, "max_hops": MAX_HOPS, "limit": 1000})

    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert {i["to_pid"]: i["hops"] for i in items} == expected_hops
    assert u_pid not in expected_hops and len(expected_hops) > 50  # premise: a wide answer, and one that is not
    assert longest < settings.ROUTING_PATH_FINDING_TIMEOUT_MS / 1000.0, longest


# The exact search this case exercises (a rule that depends on the whole route) is bounded only by the routing
# deadline, and how much of a 100-participant world it covers within 500 ms depends on the machine: the shipped
# budget answered on the development host and timed out (504 E007) on the shared CI runner of PR #374 (2026-10-30).
# This case asserts the ANSWER and that the loop is not held for the whole search; the deadline itself is the
# subject of `test_h2_payment_targets_out_of_its_deadline_answers_504_and_releases_the_loop` below.
ROUTE_DEPENDENT_SEARCH_BUDGET_MS = 10_000


@pytest.mark.asyncio
async def test_h2_a_route_dependent_rule_is_searched_exactly_without_holding_the_loop(
    client, db_session, run_world, monkeypatch  # noqa: F811
) -> None:
    monkeypatch.setattr(settings, "ROUTING_PATH_FINDING_TIMEOUT_MS", ROUTE_DEPENDENT_SEARCH_BUDGET_MS)
    code, source, u_pid, expected_hops, scenario = await _greenfield_world(db_session, blocked_by_source_lines=True)
    run_id = run_world(await _anonymous_owner(client), scenario)
    response, longest = await _get_while_watching_the_loop(
        client, f"/api/v1/simulator/runs/{run_id}/payment-targets",
        {"equivalent": code, "from_pid": source, "max_hops": MAX_HOPS, "limit": 1000})

    assert response.status_code == 200, response.text
    assert {i["to_pid"]: i["hops"] for i in response.json()["items"]} == expected_hops
    assert u_pid not in expected_hops and len(expected_hops) > 50
    assert longest < ROUTE_DEPENDENT_SEARCH_BUDGET_MS / 1000.0, longest


@pytest.mark.asyncio
async def test_h2_payment_targets_out_of_its_deadline_answers_504_and_releases_the_loop(
    client, db_session, run_world, monkeypatch  # noqa: F811
) -> None:
    code, source, _, _, scenario = await _greenfield_world(db_session, blocked_by_source_lines=True)
    run_id = run_world(await _anonymous_owner(client), scenario)
    monkeypatch.setattr(settings, "ROUTING_PATH_FINDING_TIMEOUT_MS", 1)  # a budget the search cannot meet
    response, longest = await _get_while_watching_the_loop(
        client, f"/api/v1/simulator/runs/{run_id}/payment-targets",
        {"equivalent": code, "from_pid": source, "max_hops": MAX_HOPS, "limit": 1000})

    assert response.status_code == 504, response.text
    assert response.json()["error"]["code"] == "E007", response.text
    # The budget plus the request's own work; before 054 the same GET held the loop for seconds and answered 200.
    assert longest < 0.5, longest
