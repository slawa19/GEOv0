"""028 stage E7 (owner В-8, В-9; F-028-45, F-028-46) and F-028-16.

F-028-45: a clearing records no initiator - `transactions.initiator_id IS NULL` - and the Admin graph still lists it,
with `initiator_pid = null`; a PAYMENT keeps its initiator (the CHECK of migration 036 refuses one without).
F-028-46: `/api/v1/ws` is gone - no route, and a handshake with a valid token of an ACTIVE participant (accepted before
the removal, `tests/integration/test_p024_websocket_checks_the_participant_postgres.py`) is refused.
F-028-16: the canon names the two audit metadata keys the trust-line writers put into `affected_participants`.
"""

from __future__ import annotations

import os
import uuid
from decimal import Decimal

import pytest
import pytest_asyncio
import yaml
from fastapi.testclient import TestClient
from httpx import AsyncClient
from sqlalchemy import select, text
from starlette.websockets import WebSocketDisconnect

from app.core.admin.graph import fetch_graph_transactions
from app.core.trustlines.service import CHECKPOINT_SCOPE_CALLER_TRANSACTION, INITIAL_STATUSES
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.transaction import Transaction
from app.db.models.trustline import TrustLine
from app.main import app
from app.utils.security import create_access_token
from tests.conftest import MODE_B
from tests.debt_setup import debt_fixture_setup
from tests.integration.test_scenarios import register_and_login


async def _triangle(db_session, code: str) -> dict[str, uuid.UUID]:
    eq = Equivalent(code=code, description=code, precision=2)
    db_session.add(eq)
    people = {name: Participant(pid=f"E7{name}{uuid.uuid4().hex[:6]}", display_name=name, public_key=uuid.uuid4().hex * 2,
                                type="person", status="active") for name in "ABC"}
    db_session.add_all(people.values())
    await db_session.commit()
    edges = [("A", "B"), ("B", "C"), ("C", "A")]  # debtor -> creditor; the line is creditor -> debtor
    for debtor, creditor in edges:
        db_session.add(TrustLine(from_participant_id=people[creditor].id, to_participant_id=people[debtor].id,
                                 equivalent_id=eq.id, limit=Decimal("100.00"), status="active",
                                 policy={"auto_clearing": True, "can_be_intermediate": True}))
    await db_session.commit()
    for debtor, creditor in edges:
        async with debt_fixture_setup(db_session, label="setup"):
            db_session.add(Debt(debtor_id=people[debtor].id, creditor_id=people[creditor].id, equivalent_id=eq.id,
                                amount=Decimal("5.00")))
    await db_session.commit()
    return {name: p.id for name, p in people.items()} | {"eq": eq.id}


@MODE_B
@pytest.mark.asyncio
async def test_a_clearing_records_no_initiator_and_the_admin_graph_still_lists_it(client: AsyncClient, db_session):
    user = await register_and_login(client, "E7 clearing")
    code = f"E7{uuid.uuid4().hex[:4].upper()}"
    await _triangle(db_session, code)

    resp = await client.post("/api/v1/clearing/auto", params={"equivalent": code}, headers=user["headers"])
    assert resp.status_code == 200, resp.text
    assert resp.json()["cleared_cycles"] == 1, resp.json()

    db_session.expire_all()
    rows = (await db_session.execute(select(Transaction).where(Transaction.type == "CLEARING"))).scalars().all()
    rows = [r for r in rows if (r.payload or {}).get("equivalent") == code]
    assert len(rows) == 1, rows
    items = [i for i in await fetch_graph_transactions(db_session, limit=1000) if i["tx_id"] == rows[0].tx_id]
    assert len(items) == 1, "the Admin graph dropped the clearing"  # an inner join on the initiator would
    # The row and the Admin graph item, together: neither names an initiator.
    assert (rows[0].initiator_id, items[0]["initiator_pid"]) == (None, None)
    assert len(items[0]["edges"]) == 3, items[0]  # who took part is still said - by the edges


@MODE_B
@pytest.mark.asyncio
async def test_a_payment_row_without_an_initiator_is_refused_and_a_clearing_row_is_not(db_session):
    sender = Participant(pid=f"E7P{uuid.uuid4().hex[:6]}", display_name="p", public_key=uuid.uuid4().hex * 2,
                         type="person", status="active")
    db_session.add(sender)
    await db_session.commit()
    insert = text("INSERT INTO transactions (id, tx_id, type, initiator_id, payload, state) "
                  "VALUES (:id, :tx, :type, :ini, '{}', 'COMMITTED')")
    # Counter-check: the CLEARING row with no initiator is accepted.
    await db_session.execute(insert, {"id": uuid.uuid4(), "tx": f"e7-c-{uuid.uuid4().hex}", "type": "CLEARING",
                                      "ini": None})
    await db_session.execute(insert, {"id": uuid.uuid4(), "tx": f"e7-p-{uuid.uuid4().hex}", "type": "PAYMENT",
                                      "ini": sender.id})
    await db_session.commit()
    with pytest.raises(Exception, match="chk_transaction_payment_has_initiator"):
        await db_session.execute(insert, {"id": uuid.uuid4(), "tx": f"e7-n-{uuid.uuid4().hex}", "type": "PAYMENT",
                                          "ini": None})
    await db_session.rollback()


@pytest_asyncio.fixture
async def ws_db(committed_database, monkeypatch):
    import app.db.session as app_db_session

    monkeypatch.setattr(app_db_session, "AsyncSessionLocal", committed_database.sessionmaker)
    return committed_database


@pytest.mark.asyncio
async def test_the_ws_route_is_gone_and_its_handshake_is_refused(ws_db) -> None:
    pid = f"E7_WS_{uuid.uuid4().hex[:8]}"
    async with ws_db.sessionmaker() as s:
        s.add(Participant(pid=pid, display_name=pid, public_key=uuid.uuid4().hex * 2, type="person", status="active",
                          profile={}))
        await s.commit()

    assert "/api/v1/ws" not in {getattr(r, "path", None) for r in app.routes}
    # The same token of an ACTIVE participant was accepted with `hello` before the removal; an HTTP 404 would not
    # prove the WebSocket route gone, so the handshake itself is what must fail.
    with pytest.raises(WebSocketDisconnect):
        with TestClient(app).websocket_connect("/api/v1/ws", subprotocols=["bearer", create_access_token(pid)]) as ws:
            ws.receive_json()


def test_the_canon_names_the_trust_line_audit_metadata() -> None:
    path = os.path.join(os.path.dirname(__file__), "..", "..", "api", "openapi.yaml")
    with open(path, encoding="utf-8") as handle:
        schema = yaml.safe_load(handle)["components"]["schemas"]["IntegrityAuditLogAffectedParticipants"]
    props = schema["properties"]
    assert "checkpoint_scope" in props and "initial_status" in props, sorted(props)
    assert CHECKPOINT_SCOPE_CALLER_TRANSACTION in props["checkpoint_scope"]["enum"]
    assert set(INITIAL_STATUSES) <= set(props["initial_status"]["enum"])
