"""028 E6, F-028-44 (owner В-7): «Только администратор. Участнику можно выводить только статус целостности по
результатам последней проверки». `status`, `verify`, `checksum`, `audit-log` answer a participant 403 and the admin
token as before (the Admin UI, `admin-ui/src/api/realApi.ts:687-691`). `GET /integrity/summary` gives per equivalent
`{equivalent, status, checked_at, hold}` from the last STORED check - it recomputes and writes nothing; no stored
check is `warning` with `checked_at = null`.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import event, insert, select, update

from app.config import settings
from app.core.ledger.reconciliation import FAILED, PASSED
from app.db.models.equivalent import Equivalent
from app.db.reconciliation_tables import debt_reconciliation_results
from tests.integration.test_scenarios import register_and_login

ADMIN = {"X-Admin-Token": settings.ADMIN_TOKEN}
CLOSED = [("get", "/api/v1/integrity/status", None), ("get", "/api/v1/integrity/audit-log", None),
          ("post", "/api/v1/integrity/verify", {}), ("get", "/api/v1/integrity/checksum/E6A", None)]
AT = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


async def hold(db_session, code: str, status: str = FAILED, held: bool = True) -> None:
    """The equivalent's latest stored reconciliation result (`status` at `AT`), and the hold on it."""

    eq_id = (await db_session.execute(select(Equivalent.id).where(Equivalent.code == code))).scalar_one()
    rid = uuid.uuid4()
    await db_session.execute(insert(debt_reconciliation_results).values(
        id=rid, equivalent_id=eq_id, status=status, fingerprint="e" * 64, detail={}, checked_at=AT,
        last_checked_at=AT, is_latest=True))
    if held:
        await db_session.execute(update(Equivalent).where(Equivalent.id == eq_id).values(integrity_hold_result_id=rid))
    await db_session.commit()


async def _equivalents(db_session, *codes: str) -> None:
    db_session.add_all([Equivalent(code=c, description=c, precision=2) for c in codes])
    await db_session.commit()


@pytest.mark.asyncio
async def test_a_participant_is_refused_the_checks_and_the_admin_is_not(client, db_session) -> None:
    await _equivalents(db_session, "E6A")
    user = await register_and_login(client, "E6IntegrityUser")
    refused = {url: (await client.request(m, url, json=b, headers=user["headers"])).status_code for m, url, b in CLOSED}
    assert refused == {url: 403 for _m, url, _b in CLOSED}, refused
    for method, url, body in CLOSED[:3]:  # the checksum needs a stored checkpoint; its 403 is the subject above
        resp = await client.request(method, url, json=body, headers=ADMIN)
        assert resp.status_code == 200, (url, resp.text)


@pytest.mark.asyncio
async def test_the_summary_reads_the_last_stored_check_and_writes_nothing(client, db_session) -> None:
    await _equivalents(db_session, "E6U", "E6P", "E6H")
    await hold(db_session, "E6P", PASSED, held=False)
    await hold(db_session, "E6H")
    user = await register_and_login(client, "E6SummaryUser")

    statements: list[str] = []
    connection = await db_session.connection()

    def _record(_conn, _cursor, statement, *_args):
        statements.append(statement.lstrip().split(None, 1)[0].upper())

    event.listen(connection.sync_connection, "before_cursor_execute", _record)
    try:
        first = await client.get("/api/v1/integrity/summary", headers=user["headers"])
        second = await client.get("/api/v1/integrity/summary", headers=user["headers"])
    finally:
        event.remove(connection.sync_connection, "before_cursor_execute", _record)
    assert first.status_code == 200 and first.json() == second.json(), first.text
    rows = {row["equivalent"]: row for row in first.json()["equivalents"]}
    assert rows["E6U"] == {"equivalent": "E6U", "status": "warning", "checked_at": None, "hold": False}, rows
    assert (rows["E6P"]["status"], rows["E6P"]["hold"]) == ("healthy", False), rows
    assert datetime.fromisoformat(rows["E6P"]["checked_at"].replace("Z", "+00:00")) == AT, rows
    assert (rows["E6H"]["status"], rows["E6H"]["hold"]) == ("critical", True), rows
    assert statements and set(statements) <= {"SELECT"}, f"the summary wrote or recomputed: {statements}"
