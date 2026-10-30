"""054-D-1: admin metrics and graph read both debt roles of a participant from ONE statement snapshot.

Same form as OMR-2 (`test_balance_read_consistency_and_capacity.py`).  Under READ COMMITTED every statement
takes its own snapshot, so reading "debts where I am the debtor" and "debts where I am the creditor" with two
SELECTs lets a payment that reverses the pair's debt land between them: the reader then sees the old debt AND
the new credit at once (``total_debt 10 / total_credit 10 / net 0``, a neutral graph node) - a balance that no
committed state ever had.

Why the test is not vacuous:
- the barrier fires after PostgreSQL materialised the FIRST role read of the endpoint (``hits == 1``), and a real
  ``PaymentService`` payment in another session reverses the debt while the reader is parked there;
- with two role SELECTs (the code before the fix) the second one runs after the reversal and the response mixes
  two states - this assertion was RED on that code;
- after the fix there is exactly one role read (``role_reads == 1``), so nothing reads after the barrier and the
  response must equal a single committed state; the fresh read after the reversal is the control that the
  reversal really happened and is visible to the same endpoint (``0 / 10 / +10``, ``net_balance_atoms 1000``).
"""

from __future__ import annotations

import asyncio
import re
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.config import settings
from tests.conftest import MODE_B
from tests.integration.test_balance_read_consistency_and_capacity import _line, _world
from tests.integration.test_p026_s2_limit_below_used_postgres import _debts, _pay

ADMIN = {"X-Admin-Token": settings.ADMIN_TOKEN}

_FROM_DEBTS = re.compile(r"\bFROM debts\b")


def _is_role_read(statement) -> bool:
    """A statement whose FROM is the debts table and which does not resolve participants.

    That is the per-role debt read of both endpoints (metrics totals, graph net viz) - before and after the fix.
    It excludes the trustline aggregates (``FROM trust_lines ... JOIN debts AS``) and the graph's debt list
    (joins ``participants`` for the pids), which are not role reads.
    """
    sql = str(statement)
    return bool(_FROM_DEBTS.search(sql)) and "participants" not in sql


@MODE_B
@pytest.mark.parametrize("endpoint", ["metrics", "graph"])
@pytest.mark.asyncio
async def test_p054_d1_admin_role_reads_share_one_snapshot(client, db_session, monkeypatch, endpoint):
    code, people, factory = await _world(client, db_session)
    a, b = people["A"], people["B"]
    await _line(client, a, b, code)
    await _line(client, b, a, code)
    assert await _pay(factory, a, b, code, "10")
    assert await _debts(factory, code) == {(a["id"], b["id"]): 10}
    assert (await db_session.execute(text("SHOW transaction_isolation"))).scalar_one() == "read committed"

    first_fetched, reversal_committed = asyncio.Event(), asyncio.Event()
    original_execute = db_session.execute
    role_reads = 0

    async def execute_with_barrier(statement, *args, **kwargs):
        nonlocal role_reads
        result = await original_execute(statement, *args, **kwargs)
        if _is_role_read(statement):
            role_reads += 1
            if role_reads == 1:
                first_fetched.set()
                await asyncio.wait_for(reversal_committed.wait(), 20)
        return result

    async def reverse_payment():
        await asyncio.wait_for(first_fetched.wait(), 20)
        assert await _pay(factory, b, a, code, "20")
        reversal_committed.set()

    if endpoint == "metrics":
        url, params = f"/api/v1/admin/participants/{a['pid']}/metrics", {"equivalent": code}
    else:
        url, params = "/api/v1/admin/graph/snapshot", {"equivalent": code}

    monkeypatch.setattr(db_session, "execute", execute_with_barrier)
    writer = asyncio.create_task(reverse_payment())
    try:
        response = await asyncio.wait_for(client.get(url, headers=ADMIN, params=params), 30)
        await writer
    finally:
        if not writer.done():
            writer.cancel()
            await asyncio.gather(writer, return_exceptions=True)
    monkeypatch.setattr(db_session, "execute", original_execute)
    assert response.status_code == 200, response.text
    # Anti-vacuum: the barrier fired, and the reversal really committed while the reader was parked.
    assert role_reads >= 1, "the barrier never fired: no role read was recognised"
    assert await _debts(factory, code) == {(b["id"], a["id"]): 10}

    # Control: a fresh read after the reversal shows the new state (A is now the creditor of 10).
    fresh = await client.get(url, headers=ADMIN, params=params)
    assert fresh.status_code == 200, fresh.text
    body = response.json()
    if endpoint == "metrics":
        fresh_row = next(r for r in fresh.json()["balance_rows"] if r["equivalent"] == code)
        assert {k: Decimal(fresh_row[k]) for k in ("total_debt", "total_credit", "net")} == {
            "total_debt": Decimal(0),
            "total_credit": Decimal(10),
            "net": Decimal(10),
        }, fresh_row
        row = next(r for r in body["balance_rows"] if r["equivalent"] == code)
        observed = {k: Decimal(row[k]) for k in ("total_debt", "total_credit", "net")}
        # Intended: one debt snapshot -> before (-10) or after (+10); never both directions at once.
        assert observed in (
            {"total_debt": Decimal(10), "total_credit": Decimal(0), "net": Decimal(-10)},
            {"total_debt": Decimal(0), "total_credit": Decimal(10), "net": Decimal(10)},
        ), f"metrics combined two committed debt states: {observed}"
    else:
        fresh_node = next(p for p in fresh.json()["participants"] if p["pid"] == a["pid"])
        assert fresh_node["net_balance_atoms"] == "1000", fresh_node
        node = next(p for p in body["participants"] if p["pid"] == a["pid"])
        observed = {k: node.get(k) for k in ("net_balance_atoms", "net_sign", "viz_color_key")}
        assert observed["net_balance_atoms"] in ("-1000", "1000"), f"graph node combined two states: {observed}"
    # Mechanism: the endpoint reads both roles in one statement, so no role read can follow the barrier.
    assert role_reads == 1, f"{endpoint}: {role_reads} separate role reads (both roles must come from one SELECT)"
