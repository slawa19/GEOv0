"""Balance reads over real committed payments, including a READ COMMITTED reversal.

The barrier delays the reader after PostgreSQL has materialized its first debt
SELECT. The other transaction then reverses the pair's debt through PaymentService;
no monetary rows or query results are fabricated. This promises one debt snapshot,
not a snapshot shared with the earlier trustline queries.
"""

from __future__ import annotations

import asyncio
import base64
import uuid

import pytest
from nacl.signing import SigningKey
from sqlalchemy import select, text

from app.config import settings
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from tests.conftest import MODE_B, sessionmaker_of
from tests.integration.test_p026_s2_limit_below_used_postgres import _debts, _patch_limit, _pay
from tests.integration.test_scenarios import (
    _sign_trustline_create_request,
    expected_state_of,
    register_and_login,
    signed_trustline_close,
)


async def _world(client, db_session):
    code = "BR" + uuid.uuid4().hex[:8].upper()
    db_session.add(Equivalent(code=code, precision=2, is_active=True))
    await db_session.commit()
    people = {name: await register_and_login(client, "Balance" + name) for name in "ABC"}
    ids = dict((await db_session.execute(select(Participant.pid, Participant.id).where(
        Participant.pid.in_([p["pid"] for p in people.values()])))).all())
    for person in people.values():
        person["id"] = ids[person["pid"]]
    await db_session.commit()
    return code, people, sessionmaker_of(db_session)


async def _line(client, creditor, debtor, code):
    key = SigningKey(base64.b64decode(creditor["priv"]))
    response = await client.post("/api/v1/trustlines", headers=creditor["headers"], json={
        "to": debtor["pid"], "equivalent": code, "limit": "100",
        "signature": _sign_trustline_create_request(
            signing_key=key, to_pid=debtor["pid"], equivalent=code, limit="100"),
    })
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _is_debt_select(statement):
    return any(d.get("entity") is Debt for d in getattr(statement, "column_descriptions", []))


@MODE_B
@pytest.mark.parametrize("endpoint", ["balance", "balance/debts", "participants/me"])
@pytest.mark.asyncio
async def test_reversal_between_reads_never_combines_two_committed_debt_states(
    client, db_session, monkeypatch, endpoint,
):
    monkeypatch.setattr(settings, "BALANCE_SUMMARY_CACHE_TTL_SECONDS", 0)
    code, people, factory = await _world(client, db_session)
    a, b = people["A"], people["B"]
    await _line(client, a, b, code)
    await _line(client, b, a, code)
    assert await _pay(factory, a, b, code, "10")
    assert await _debts(factory, code) == {(a["id"], b["id"]): 10}
    assert (await db_session.execute(text("SHOW transaction_isolation"))).scalar_one() == "read committed"

    first_debt_fetched, reversal_committed = asyncio.Event(), asyncio.Event()
    original_execute = db_session.execute
    debt_reads = 0

    async def execute_with_barrier(statement, *args, **kwargs):
        nonlocal debt_reads
        result = await original_execute(statement, *args, **kwargs)
        if _is_debt_select(statement):
            debt_reads += 1
            if debt_reads == 1:
                first_debt_fetched.set()
                await asyncio.wait_for(reversal_committed.wait(), 20)
        return result

    async def reverse_payment():
        await asyncio.wait_for(first_debt_fetched.wait(), 20)
        assert await _pay(factory, b, a, code, "20")
        reversal_committed.set()

    monkeypatch.setattr(db_session, "execute", execute_with_barrier)
    writer = asyncio.create_task(reverse_payment())
    try:
        params = {"equivalent": code, "direction": "all"} if endpoint == "balance/debts" else {}
        response = await asyncio.wait_for(client.get(
            "/api/v1/" + endpoint, headers=a["headers"], params=params,
        ), 30)
        await writer
    finally:
        if not writer.done():
            writer.cancel()
            await asyncio.gather(writer, return_exceptions=True)
    assert response.status_code == 200, response.text
    actual = response.json()
    if endpoint == "balance/debts":
        assert actual == {
            "outgoing": [{"creditor": b["pid"], "creditor_name": "BalanceB",
                          "equivalent": code, "amount": "10.00"}],
            "incoming": [],
        }, actual
    else:
        rows = actual["stats"]["per_equivalent"] if endpoint == "participants/me" else actual["equivalents"]
        row = next(row for row in rows if row.get("code", row.get("equivalent")) == code)
        assert {k: row[k] for k in ("total_debt", "total_credit", "net_balance")} == {
            "total_debt": "10.00", "total_credit": "0.00", "net_balance": "-10.00",
        }, row
    assert debt_reads == 1
    assert await _debts(factory, code) == {(b["id"], a["id"]): 10}

    # A fresh HTTP read sees the later committed state; each direction still
    # filters correctly after the combined query, including an empty side.
    monkeypatch.setattr(db_session, "execute", original_execute)
    for direction in ("outgoing", "incoming", "all"):
        fresh = await client.get("/api/v1/balance/debts", headers=a["headers"], params={
            "equivalent": code, "direction": direction,
        })
        assert fresh.status_code == 200, fresh.text
        assert fresh.json()["outgoing"] == []
        expected_incoming = [] if direction == "outgoing" else [{
            "debtor": b["pid"], "debtor_name": "BalanceB", "equivalent": code, "amount": "10.00",
        }]
        assert fresh.json()["incoming"] == expected_incoming


async def _summary_row(client, person, code):
    response = await client.get("/api/v1/balance", headers=person["headers"])
    assert response.status_code == 200, response.text
    return next((row for row in response.json()["equivalents"] if row["code"] == code), None)


async def _close(client, creditor, line_id):
    response = await client.request("DELETE", f"/api/v1/trustlines/{line_id}", headers=creditor["headers"], json=(
        signed_trustline_close(
            signing_key=SigningKey(base64.b64decode(creditor["priv"])), trustline_id=line_id,
            expected=await expected_state_of(client, creditor["headers"], line_id),
        )
    ))
    assert response.status_code == 200, response.text
    return response.json()["trustline"]


@MODE_B
@pytest.mark.parametrize("direction", ["spend", "receive"])
@pytest.mark.asyncio
async def test_overlimit_pair_does_not_subtract_an_independent_peers_capacity(
    client, db_session, monkeypatch, direction,
):
    monkeypatch.setattr(settings, "BALANCE_SUMMARY_CACHE_TTL_SECONDS", 0)
    code, people, factory = await _world(client, db_session)
    a, b, c = [people[name] for name in "ABC"]
    creditor, debtor = (b, a) if direction == "spend" else (a, b)
    first = await _line(client, creditor, debtor, code)
    await _line(client, c if direction == "spend" else a, a if direction == "spend" else c, code)
    assert await _pay(factory, debtor, creditor, code, "80")
    patch = await _patch_limit(client, creditor, first, "20")
    assert patch.status_code == 200, patch.text
    assert {k: patch.json()[k] for k in ("limit", "used", "available")} == {
        "limit": "20.00", "used": "80.00", "available": "-60.00",
    }
    assert await _debts(factory, code) == {(debtor["id"], creditor["id"]): 80}

    # The displayed capacity must match the independent pair's actual spendable
    # amount, even while the other pair is lawfully above its lowered trust.
    row = await _summary_row(client, a, code)
    payer, payee = (a, c) if direction == "spend" else (c, a)
    assert await _pay(factory, payer, payee, code, "100")
    assert row["available_to_" + direction] == "100.00", row
    assert await _debts(factory, code) == {
        (debtor["id"], creditor["id"]): 80, (payer["id"], payee["id"]): 100,
    }


@MODE_B
@pytest.mark.asyncio
async def test_positive_pair_and_offset_count_once_then_no_active_pair_carries_nothing(
    client, db_session, monkeypatch,
):
    monkeypatch.setattr(settings, "BALANCE_SUMMARY_CACHE_TTL_SECONDS", 0)
    code, people, factory = await _world(client, db_session)
    a, b = people["A"], people["B"]
    assert await _summary_row(client, a, code) is None
    line_id = await _line(client, b, a, code)
    assert await _pay(factory, a, b, code, "25")
    row = await _summary_row(client, a, code)
    assert (row["available_to_spend"], row["available_to_receive"]) == ("75.00", "25.00")
    assert await _pay(factory, b, a, code, "25")
    assert await _debts(factory, code) == {}
    closed = await _close(client, b, line_id)
    assert closed["status"] == "closed", closed
    assert await _summary_row(client, a, code) is None


@MODE_B
@pytest.mark.asyncio
async def test_summary_keeps_the_documented_requested_close_capacity_exception(
    client, db_session, monkeypatch,
):
    monkeypatch.setattr(settings, "BALANCE_SUMMARY_CACHE_TTL_SECONDS", 0)
    code, people, factory = await _world(client, db_session)
    a, b = people["A"], people["B"]
    requested_id = await _line(client, a, b, code)
    await _line(client, b, a, code)
    assert await _pay(factory, b, a, code, "40")
    requested = await _close(client, a, requested_id)
    assert requested["status"] == "active" and requested["close_requested_at"] is not None, requested
    row = await _summary_row(client, a, code)
    # Protocol §6.3.1: summary does not apply pending_pair_capacity (79.99
    # here); the exact executable amount belongs to /payments/capacity.
    assert (row["available_to_spend"], row["available_to_receive"]) == ("140.00", "0.00")
    assert await _debts(factory, code) == {(b["id"], a["id"]): 40}
