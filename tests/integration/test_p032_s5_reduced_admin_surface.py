"""032 S5 (F-1, F-2, F-4, A-4, B-9, B-10): the admin surface the owner reduced on 2026-10-07.

The routes removed by the owner's decision answer 404; the two narrowed reads carry only the fields
that survive - the liquidity summary its six per-equivalent fields, the participant metrics only
`balance_rows`; the graph reads no longer carry an `incidents` collection; the runtime config no longer
lists the three inert recovery keys. The byte-for-byte equality of the surviving fields with the
pre-slice code is proved once on a cloned community database (032 Changelog), not here.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.config import settings
from app.db.models.audit_log import AuditLog
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.transaction import Transaction
from app.db.models.trustline import TrustLine
from tests.debt_setup import debt_fixture_setup

_ADMIN = {"X-Admin-Token": settings.ADMIN_TOKEN}


async def _seed(db_session) -> None:
    alice = Participant(pid="s5_alice", display_name="Alice", public_key="A" * 64, type="person", status="active")
    bob = Participant(pid="s5_bob", display_name="Bob", public_key="B" * 64, type="person", status="active")
    uah = Equivalent(code="UAH", symbol="UAH", description="Hryvnia", precision=2, metadata_={}, is_active=True)
    # Inactive: the summary of a stopped equivalent still carries its sums (D-3 is the client's precision).
    hour = Equivalent(code="HOUR", symbol="h", description="Hour", precision=1, metadata_={}, is_active=False)
    db_session.add_all([alice, bob, uah, hour])
    await db_session.flush()
    db_session.add_all(
        [
            TrustLine(from_participant_id=alice.id, to_participant_id=bob.id, equivalent_id=uah.id,
                      limit=Decimal("100.00"), policy={}, status="active"),
            TrustLine(from_participant_id=bob.id, to_participant_id=alice.id, equivalent_id=hour.id,
                      limit=Decimal("10.0"), policy={}, status="active"),
        ]
    )
    async with debt_fixture_setup(db_session, label="setup"):
        db_session.add_all(
            [
                Debt(debtor_id=bob.id, creditor_id=alice.id, equivalent_id=uah.id, amount=Decimal("95.00")),
                Debt(debtor_id=alice.id, creditor_id=bob.id, equivalent_id=hour.id, amount=Decimal("2.5")),
            ]
        )
    await db_session.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/api/v1/admin/incidents"),
        ("get", "/api/v1/admin/clearing/cycles"),
        ("get", "/api/v1/admin/trustlines/bottlenecks"),
    ],
)
async def test_removed_admin_routes_answer_404(client, method, path):
    kwargs = {"json": {"reason": "r"}} if method == "post" else {}
    r = await getattr(client, method)(path, headers=_ADMIN, **kwargs)
    assert r.status_code == 404, (method, path, r.status_code, r.text)


@pytest.mark.asyncio
async def test_admin_abort_is_gone_even_for_an_aborted_transaction(client, db_session):
    # Before S5 an ABORTED row answered 200 `aborted` with an audit row; an unknown tx_id answered 404 anyway,
    # so the row is what makes this a check of the route's absence and not of the 404 for a missing transaction.
    await _seed(db_session)
    alice = (await db_session.execute(select(Participant).where(Participant.pid == "s5_alice"))).scalar_one()
    db_session.add(Transaction(tx_id="s5_tx_aborted", idempotency_key=None, type="PAYMENT", initiator_id=alice.id,
                               payload={"equivalent": "UAH"}, signatures=[], state="ABORTED", error=None))
    await db_session.commit()
    r = await client.post("/api/v1/admin/transactions/s5_tx_aborted/abort", headers=_ADMIN, json={"reason": "r"})
    assert r.status_code == 404, r.text
    assert (await db_session.execute(select(AuditLog).where(AuditLog.object_id == "s5_tx_aborted"))).first() is None


@pytest.mark.asyncio
async def test_the_public_clearing_cycles_diagnostic_stays(client):
    # Counter-check of the 404 above: only the admin copy was removed (F-1), the public diagnostic stays.
    r = await client.get("/api/v1/clearing/cycles", params={"equivalent": "UAH"})
    assert r.status_code != 404, r.text


@pytest.mark.asyncio
async def test_liquidity_summary_carries_only_the_six_per_equivalent_fields(client, db_session):
    await _seed(db_session)
    started = datetime.now(timezone.utc)
    r = await client.get("/api/v1/admin/liquidity/summary", headers=_ADMIN, params={"equivalent": "HOUR"})
    finished = datetime.now(timezone.utc)
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {
        "equivalent", "updated_at", "active_trustlines", "total_limit", "total_used", "total_available",
    }
    # The stopped equivalent's own sums - never another equivalent's.
    assert body["equivalent"] == "HOUR"
    assert body["active_trustlines"] == 1
    assert (Decimal(body["total_limit"]), Decimal(body["total_used"]), Decimal(body["total_available"])) == (
        Decimal("10.0"), Decimal("2.5"), Decimal("7.5"),
    )
    updated_at = datetime.fromisoformat(body["updated_at"].replace("Z", "+00:00"))
    assert started <= updated_at <= finished

    # Without an equivalent only the lines are counted; money is never summed across equivalents (028 F-028-37).
    r_all = await client.get("/api/v1/admin/liquidity/summary", headers=_ADMIN)
    assert r_all.status_code == 200
    all_body = r_all.json()
    assert set(all_body) == set(body)
    assert all_body["equivalent"] is None
    assert all_body["active_trustlines"] == 2
    assert (all_body["total_limit"], all_body["total_used"], all_body["total_available"]) == (None, None, None)


@pytest.mark.asyncio
async def test_participant_metrics_carry_only_balance_rows(client, db_session):
    await _seed(db_session)
    r = await client.get("/api/v1/admin/participants/s5_alice/metrics", headers=_ADMIN, params={"equivalent": "UAH"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"pid", "equivalent", "balance_rows"}
    assert body["equivalent"] == "UAH"
    [row] = body["balance_rows"]
    assert row["equivalent"] == "UAH"
    assert Decimal(row["net"]) == Decimal("95.00")

    r_all = await client.get("/api/v1/admin/participants/s5_alice/metrics", headers=_ADMIN)
    assert r_all.status_code == 200
    assert set(r_all.json()) == {"pid", "equivalent", "balance_rows"}
    assert [row["equivalent"] for row in r_all.json()["balance_rows"]] == ["HOUR", "UAH"]


@pytest.mark.asyncio
async def test_participant_metrics_declared_refusals(client, db_session):
    await _seed(db_session)
    unknown_pid = await client.get("/api/v1/admin/participants/nobody/metrics", headers=_ADMIN)
    assert unknown_pid.status_code == 404
    unknown_eq = await client.get(
        "/api/v1/admin/participants/s5_alice/metrics", headers=_ADMIN, params={"equivalent": "NOPE"}
    )
    assert unknown_eq.status_code == 404
    malformed_eq = await client.get(
        "/api/v1/admin/participants/s5_alice/metrics", headers=_ADMIN, params={"equivalent": "bad code!"}
    )
    assert malformed_eq.status_code == 400


@pytest.mark.asyncio
async def test_graph_reads_carry_no_incidents_collection(client, db_session):
    await _seed(db_session)
    snap = await client.get(
        "/api/v1/admin/graph/snapshot", headers=_ADMIN, params={"include": "incidents,audit_log"}
    )
    assert snap.status_code == 200
    assert "incidents" not in snap.json()
    # Anti-vacuum: the include mechanism itself still works for the collections that stay.
    assert snap.json()["included"] == ["audit_log"]
    ego = await client.get(
        "/api/v1/admin/graph/ego", headers=_ADMIN, params={"pid": "s5_alice", "include": "incidents"}
    )
    assert ego.status_code == 200
    assert "incidents" not in ego.json()
    assert ego.json()["included"] == []


@pytest.mark.asyncio
async def test_runtime_config_no_longer_lists_the_inert_recovery_keys(client):
    r = await client.get("/api/v1/admin/config", headers=_ADMIN)
    assert r.status_code == 200
    keys = {item["key"] for item in r.json()["items"]}
    assert not keys & {"RECOVERY_ENABLED", "RECOVERY_INTERVAL_SECONDS", "PAYMENT_TX_STUCK_TIMEOUT_SECONDS"}
    # Anti-vacuum: the listing itself is not empty.
    assert "ROUTING_MAX_HOPS" in keys
