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

from decimal import Decimal

import pytest

from app.config import settings
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine
from tests.debt_setup import debt_fixture_setup

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
    assert query_door.json()["error"]["code"] == path_door.json()["error"]["code"] == "E009"
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


# --- a populated world: two equivalents, so that a lower-case code which reaches only SOME of the branches shows ---


async def _two_equivalents(db_session) -> None:
    """UAH: alice->bob 100.00 (bob owes alice 30.00), bob->carol 50.00 (carol owes bob 10.00).
    USD: alice->dave 200.00 (dave owes alice 70.00), carol->dave 5.00 (no debt).  All active, precision 2."""
    people = {
        pid: Participant(pid=pid, display_name=pid.title(), public_key=pid[0].upper() * 63 + str(n), type="person", status="active")
        for n, pid in enumerate(("alice", "bob", "carol", "dave"))
    }
    uah = Equivalent(code="UAH", symbol="H", description="Hryvnia", precision=2, metadata_={}, is_active=True)
    usd = Equivalent(code="USD", symbol="$", description="Dollar", precision=2, metadata_={}, is_active=True)
    db_session.add_all([*people.values(), uah, usd])
    await db_session.flush()

    def line(creditor: str, debtor: str, eq: Equivalent, limit: str) -> TrustLine:
        return TrustLine(
            from_participant_id=people[creditor].id,
            to_participant_id=people[debtor].id,
            equivalent_id=eq.id,
            limit=Decimal(limit),
            policy={"auto_clearing": True, "can_be_intermediate": True},
            status="active",
        )

    db_session.add_all(
        [line("alice", "bob", uah, "100.00"), line("bob", "carol", uah, "50.00"),
         line("alice", "dave", usd, "200.00"), line("carol", "dave", usd, "5.00")]
    )
    await db_session.flush()
    async with debt_fixture_setup(db_session, label="setup"):
        db_session.add_all(
            [
                Debt(debtor_id=people["bob"].id, creditor_id=people["alice"].id, equivalent_id=uah.id, amount=Decimal("30.00")),
                Debt(debtor_id=people["carol"].id, creditor_id=people["bob"].id, equivalent_id=uah.id, amount=Decimal("10.00")),
                Debt(debtor_id=people["dave"].id, creditor_id=people["alice"].id, equivalent_id=usd.id, amount=Decimal("70.00")),
            ]
        )
    await db_session.commit()


SPELLINGS = ["UAH", "uah", " uah "]


def _lines(body) -> list[tuple[str, str, str, Decimal, Decimal]]:
    # The money spelling (stored scale) is another contract (029); the amounts are what is asserted here.
    return sorted((t["equivalent"], t["from"], t["to"], Decimal(t["limit"]), Decimal(t["used"])) for t in body["trustlines"])


def _nets(body) -> dict[str, str]:
    return {p["pid"]: p["net_balance_atoms"] for p in body["participants"]}


@pytest.mark.asyncio
@pytest.mark.parametrize("spelling", SPELLINGS)
async def test_snapshot_in_any_spelling_nets_one_equivalent_and_keeps_every_line(client, db_session, spelling: str) -> None:
    await _two_equivalents(db_session)

    response = await client.get("/api/v1/admin/graph/snapshot", headers=HEADERS, params={"equivalent": spelling})

    assert response.status_code == 200, response.text
    body = response.json()
    # UAH nets (atoms, precision 2): alice +30.00, bob owes 30.00 and is owed 10.00, carol owes 10.00, dave has no UAH.
    assert _nets(body) == {"alice": "3000", "bob": "-2000", "carol": "-1000", "dave": "0"}
    assert len(_lines(body)) == 4  # the snapshot filters no line


@pytest.mark.asyncio
@pytest.mark.parametrize("spelling", SPELLINGS)
async def test_ego_in_any_spelling_filters_neighbours_lines_and_nets_by_that_equivalent(client, db_session, spelling: str) -> None:
    await _two_equivalents(db_session)

    response = await client.get("/api/v1/admin/graph/ego", headers=HEADERS, params={"pid": "alice", "equivalent": spelling})

    assert response.status_code == 200, response.text
    body = response.json()
    # Alice's UAH neighbour is bob (dave is her neighbour in USD only); the one line inside the scope is hers.
    assert sorted(_nets(body)) == ["alice", "bob"]
    assert _lines(body) == [("UAH", "alice", "bob", Decimal("100.00"), Decimal("30.00"))]
    # A node's net counts its debts to nodes outside the scope too: bob is owed 10.00 by carol.
    assert _nets(body) == {"alice": "3000", "bob": "-2000"}
    usd = await client.get("/api/v1/admin/graph/ego", headers=HEADERS, params={"pid": "alice", "equivalent": "USD"})
    assert sorted(_nets(usd.json())) == ["alice", "dave"]  # the control: the other equivalent selects other neighbours


@pytest.mark.asyncio
@pytest.mark.parametrize("spelling", SPELLINGS)
async def test_liquidity_summary_in_any_spelling_sums_one_equivalent(client, db_session, spelling: str) -> None:
    await _two_equivalents(db_session)

    response = await client.get("/api/v1/admin/liquidity/summary", headers=HEADERS, params={"equivalent": spelling})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["equivalent"] == "UAH"
    assert body["active_trustlines"] == 2
    assert Decimal(body["total_limit"]) == Decimal("150.00")
    assert Decimal(body["total_used"]) == Decimal("40.00")
    assert Decimal(body["total_available"]) == Decimal("110.00")


@pytest.mark.asyncio
@pytest.mark.parametrize("spelling", SPELLINGS)
async def test_metrics_in_any_spelling_return_the_one_row_of_that_equivalent(client, db_session, spelling: str) -> None:
    await _two_equivalents(db_session)

    response = await client.get(
        "/api/v1/admin/participants/alice/metrics", headers=HEADERS, params={"equivalent": spelling}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["equivalent"] == "UAH"
    (row,) = body["balance_rows"]
    assert row["equivalent"] == "UAH"
    assert Decimal(row["outgoing_limit"]) == Decimal("100.00")
    assert Decimal(row["outgoing_used"]) == Decimal("30.00")
    assert Decimal(row["net"]) == Decimal("30.00")
