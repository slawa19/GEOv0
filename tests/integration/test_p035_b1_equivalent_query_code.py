"""035 B1 (F-035-10, restated 2026-10-08): an equivalent code in an admin QUERY reads like the one in the PATH.

033 A item 5 made the four `/admin/equivalents/{code}...` routes read the path code through one rule
(`equivalents_core.canonical_code`: trim, upper-case, then `^[A-Z0-9_]{1,16}$` or a 400).  Of the admin routes that
take the same code in the query, `graph/snapshot` and `graph/ego` only validated (no case folding) and `liquidity/summary`
did not validate at all, so on `main` at `133d649e`:

- `GET /admin/liquidity/summary?equivalent=u@h` answered 200 with `"equivalent": "U@H"` (a code that cannot exist);
- `GET /admin/graph/snapshot?equivalent=uah` answered 400 while `/admin/equivalents/uah/usage` accepted it.

The spec's original wording ("`snapshot?equivalent=u@h` answers 200") was wrong: snapshot and ego already refuse it.
Those cases stay here as controls that must keep passing.
"""

from __future__ import annotations

import pytest

from app.config import settings
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant

HEADERS = {"X-Admin-Token": settings.ADMIN_TOKEN}
PATH_ROUTE = "/api/v1/admin/equivalents/{code}/usage"
QUERY_ROUTES = {
    "liquidity": ("/api/v1/admin/liquidity/summary", {}),
    "snapshot": ("/api/v1/admin/graph/snapshot", {}),
    "ego": ("/api/v1/admin/graph/ego", {"pid": "b1_alice"}),
    "metrics": ("/api/v1/admin/participants/b1_alice/metrics", {}),
}
CANNOT_EXIST = ["u@h", "uah!", "x" * 17, "a.b", ""]


async def _world(db_session) -> None:
    alice = Participant(pid="b1_alice", display_name="Alice", public_key="A" * 63 + "1", type="person", status="active")
    bob = Participant(pid="b1_bob", display_name="Bob", public_key="B" * 63 + "2", type="person", status="active")
    uah = Equivalent(code="UAH", symbol="H", description="Hryvnia", precision=2, metadata_={}, is_active=True)
    db_session.add_all([alice, bob, uah])
    await db_session.commit()


async def _get(client, route: str, **extra):
    url, base = QUERY_ROUTES[route]
    return await client.get(url, headers=HEADERS, params={**base, **extra})


@pytest.mark.asyncio
async def test_the_dashboard_row_refuses_a_code_that_cannot_exist(client, db_session) -> None:
    await _world(db_session)

    response = await _get(client, "liquidity", equivalent="u@h")

    assert response.status_code == 400, response.text


@pytest.mark.asyncio
async def test_the_graph_snapshot_reads_a_lowercase_code_as_the_stored_one(client, db_session) -> None:
    await _world(db_session)

    upper = await _get(client, "snapshot", equivalent="UAH")
    lower = await _get(client, "snapshot", equivalent="uah")
    padded = await _get(client, "snapshot", equivalent=" uah ")

    # Positive control: the equivalent really was selected, the nodes carry its net balance.
    assert upper.status_code == 200, upper.text
    assert {p["net_balance_atoms"] for p in upper.json()["participants"]} == {"0"}, upper.text
    assert lower.status_code == 200, lower.text
    assert lower.json() == upper.json()
    assert padded.status_code == 200, padded.text
    assert padded.json() == upper.json()


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", CANNOT_EXIST)
@pytest.mark.parametrize("route", sorted(QUERY_ROUTES))
async def test_every_admin_query_door_answers_the_path_doors_400(client, db_session, route: str, bad: str) -> None:
    await _world(db_session)

    # A blank code is a path segment of one space.
    path_door = await client.get(PATH_ROUTE.format(code=bad or "%20"), headers=HEADERS)
    query_door = await _get(client, route, equivalent=bad)

    assert path_door.status_code == 400, path_door.text
    assert query_door.status_code == 400, (route, query_door.text)
    assert query_door.json()["error"]["code"] == path_door.json()["error"]["code"]
    assert set(query_door.json()["error"]) == set(path_door.json()["error"])


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["liquidity", "snapshot", "ego", "metrics"])
async def test_a_valid_code_in_any_case_reads_the_same_equivalent_on_every_query_door(client, db_session, route: str) -> None:
    await _world(db_session)

    upper = await _get(client, route, equivalent="UAH")
    lower = await _get(client, route, equivalent="uah")

    assert upper.status_code == 200, upper.text
    assert lower.status_code == 200, (route, lower.text)
    # The summary stamps the time it was read; nothing else may differ.
    assert {**lower.json(), "updated_at": None} == {**upper.json(), "updated_at": None}
    if route == "liquidity":
        assert upper.json()["equivalent"] == "UAH"


@pytest.mark.asyncio
async def test_the_path_door_keeps_reading_a_lowercase_code(client, db_session) -> None:
    await _world(db_session)

    upper = await client.get(PATH_ROUTE.format(code="UAH"), headers=HEADERS)
    lower = await client.get(PATH_ROUTE.format(code="uah"), headers=HEADERS)

    assert upper.status_code == 200, upper.text
    assert lower.status_code == 200, lower.text
    assert lower.json() == upper.json()
