"""028 E5 (F-028-36..40, owner В-3): no sum, comparison or ranking across equivalents.

Written first; red on 67d3107c (quoted in the spec's Changelog). Sub-quantum amounts stand for rows
stored before the 028 step rule, which readers must still show truthfully.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import event, select

from app.config import settings
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine
from tests.debt_setup import debt_fixture_setup

_ADMIN = {"X-Admin-Token": settings.ADMIN_TOKEN}
_POLICY = {"auto_clearing": True, "can_be_intermediate": True}


def _person(pid: str, key: str) -> Participant:
    return Participant(pid=pid, display_name=pid.title(), public_key=key * 64, type="person", status="active")


async def _two_equivalents(db_session) -> tuple[Equivalent, Equivalent]:
    uah = Equivalent(code="UAH", symbol="₴", description="Hryvnia", precision=2, metadata_={}, is_active=True)
    hour = Equivalent(code="HOUR", symbol="h", description="Hour", precision=2, metadata_={}, is_active=True)
    db_session.add_all([uah, hour])
    await db_session.flush()
    return uah, hour


def _line(frm: Participant, to: Participant, eq: Equivalent, limit: str) -> TrustLine:
    ids = {"from_participant_id": frm.id, "to_participant_id": to.id, "equivalent_id": eq.id}
    return TrustLine(**ids, limit=Decimal(limit), policy=_POLICY, status="active")


def _debt(debtor: Participant, creditor: Participant, eq: Equivalent, amount: str) -> Debt:
    return Debt(debtor_id=debtor.id, creditor_id=creditor.id, equivalent_id=eq.id, amount=Decimal(amount))


async def _seed_debts(db_session, label: str, debts: list[Debt]) -> None:
    async with debt_fixture_setup(db_session, label=label):
        db_session.add_all(debts)
    await db_session.commit()


# F-028-36 - the participant's own figures and the public profile, per equivalent


@pytest.mark.asyncio
async def test_me_reports_each_equivalent_separately_and_loses_no_digit(client, db_session, auth_user):
    me = (await db_session.execute(select(Participant).where(Participant.pid == auth_user["pid"]))).scalar_one()
    peer, small = _person("peer", "P"), _person("small", "S")
    db_session.add_all([peer, small])
    uah, hour = await _two_equivalents(db_session)
    db_session.add_all([_line(peer, me, uah, "200"), _line(me, peer, hour, "10")])
    await db_session.flush()
    debts = [_debt(me, peer, uah, "100"), _debt(peer, me, hour, "5"), _debt(small, me, uah, "0.005")]
    await _seed_debts(db_session, "me-two-equivalents", debts)

    r = await client.get("/api/v1/participants/me", headers=auth_user["headers"])
    assert r.status_code == 200, r.text
    stats = r.json()["stats"]

    assert set(stats) == {"per_equivalent"}, f"/me stats must carry no cross-equivalent scalar: {stats}"
    assert stats["per_equivalent"] == [
        {
            "equivalent": "HOUR",
            "total_incoming_trust": "0.00",
            "total_outgoing_trust": "10.00",
            "total_debt": "0.00",
            "total_credit": "5.00",
            "net_balance": "5.00",
        },
        {
            "equivalent": "UAH",
            "total_incoming_trust": "200.00",
            "total_outgoing_trust": "0.00",
            "total_debt": "100.00",
            "total_credit": "0.005",
            "net_balance": "-99.995",
        },
    ]


@pytest.mark.asyncio
async def test_me_with_one_equivalent_has_one_element(client, db_session, auth_user):
    # No equivalent at all gives `[]`: tests/test_participants_me_and_auth_payloads.py.
    me = (await db_session.execute(select(Participant).where(Participant.pid == auth_user["pid"]))).scalar_one()
    peer = _person("peer1", "Q")
    db_session.add(peer)
    uah, _hour = await _two_equivalents(db_session)
    db_session.add(_line(peer, me, uah, "50"))
    await db_session.commit()

    r = await client.get("/api/v1/participants/me", headers=auth_user["headers"])
    assert [row["equivalent"] for row in r.json()["stats"]["per_equivalent"]] == ["UAH"]


@pytest.mark.asyncio
async def test_public_profile_shows_incoming_trust_per_equivalent_and_nothing_new(client, db_session, auth_user):
    bob, alice = _person("bob", "B"), _person("alice", "A")
    db_session.add_all([bob, alice])
    uah, hour = await _two_equivalents(db_session)
    db_session.add_all([_line(alice, bob, uah, "100"), _line(alice, bob, hour, "5"), _line(bob, alice, uah, "7")])
    await db_session.flush()
    await _seed_debts(db_session, "public-profile", [_debt(bob, alice, uah, "3")])

    r = await client.get("/api/v1/participants/bob", headers=auth_user["headers"])
    assert r.status_code == 200, r.text
    public = r.json()["public_stats"]
    assert set(public) == {"total_incoming_trust", "member_since"}, public
    assert public["total_incoming_trust"] == [
        {"equivalent": "HOUR", "amount": "5.00"},
        {"equivalent": "UAH", "amount": "100.00"},
    ]


# F-028-37 / F-028-38 - liquidity summary and bottlenecks without an equivalent


async def _liquidity_world(db_session) -> None:
    alice, bob, carol = _person("alice", "A"), _person("bob", "B"), _person("carol", "C")
    db_session.add_all([alice, bob, carol])
    uah, hour = await _two_equivalents(db_session)
    # UAH line: ratio 10/1000 = 0.01, available 10. HOUR line: ratio 0.5/10 = 0.05, available 0.5.
    db_session.add_all([_line(alice, bob, uah, "1000"), _line(carol, bob, hour, "10")])
    await db_session.flush()
    await _seed_debts(db_session, "liquidity", [_debt(bob, alice, uah, "990"), _debt(bob, carol, hour, "9.5")])


@pytest.mark.asyncio
async def test_liquidity_summary_without_an_equivalent_sums_no_money(client, db_session):
    await _liquidity_world(db_session)

    # §15 review of E5 (`T2899.4`, class 2): under ALL the money is not summed at all - not summed and then nulled.
    statements: list[str] = []
    connection = await db_session.connection()

    def _record(_conn, _cursor, statement, *_args):
        statements.append(statement.lower())

    event.listen(connection.sync_connection, "before_cursor_execute", _record)
    try:
        r = await client.get("/api/v1/admin/liquidity/summary", headers=_ADMIN)
    finally:
        event.remove(connection.sync_connection, "before_cursor_execute", _record)
    assert r.status_code == 200, r.text
    assert statements and not [s for s in statements if "sum(" in s], [s for s in statements if "sum(" in s]
    body = r.json()
    assert body["equivalent"] is None
    assert body["active_trustlines"] == 2
    assert (body["total_limit"], body["total_used"], body["total_available"]) == (None, None, None)
    # 032 S5 (F-2): the ranked net lists and the bottleneck edges left the summary with the Liquidity screen.

    # Positive control: one equivalent keeps its money.
    r = await client.get(
        "/api/v1/admin/liquidity/summary", headers=_ADMIN, params={"equivalent": "HOUR"}
    )
    body = r.json()
    assert (Decimal(body["total_limit"]), Decimal(body["total_used"]), Decimal(body["total_available"])) == (
        Decimal("10"),
        Decimal("9.5"),
        Decimal("0.5"),
    )


# F-028-39 / F-028-40 - graph net sign and the net ranking, within one equivalent


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["/api/v1/admin/graph/snapshot", "/api/v1/admin/graph/ego?pid=alice&depth=2"])
async def test_graph_net_sign_keeps_a_sub_quantum_net(client, db_session, route):
    alice, bob = _person("alice", "A"), _person("bob", "B")
    db_session.add_all([alice, bob])
    uah, _hour = await _two_equivalents(db_session)
    db_session.add(_line(alice, bob, uah, "100"))
    await db_session.flush()
    await _seed_debts(db_session, "graph-sign", [_debt(bob, alice, uah, "0.004")])

    r = await client.get(route, headers=_ADMIN, params={"equivalent": "UAH"})
    assert r.status_code == 200, r.text
    signs = {p["pid"]: (p["net_sign"], p["net_balance_atoms"]) for p in r.json()["participants"]}
    assert signs == {"alice": (1, "1"), "bob": (-1, "-1")}


