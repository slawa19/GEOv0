"""035 A5 (`F-035-4`): `POST /integrity/verify` does not answer 200 when its audit row could not be prepared.

The handler builds the `IntegrityAuditLog` object and adds it inside `try: ... except Exception: pass`
(`app/api/v1/integrity.py`), so a failure BEFORE `db.add` is dropped and the operator reads a verified state that
left no trace. The failure here is the construction of the audit object itself - not a database refusal at the
commit, which is outside that `except` and already surfaces.

The injection is on the model's constructor, so it holds wherever the preparation lives after the fix.

What this does not see: the byte equality of `status`/`verify` answers before and after, and a failure at the commit.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.config import settings
from app.db.models.audit_log import IntegrityAuditLog
from app.db.models.equivalent import Equivalent
from app.main import app

ADMIN = {"X-Admin-Token": settings.ADMIN_TOKEN}


async def _verify_rows(db_session, code: str) -> int:
    return (await db_session.execute(
        select(func.count()).select_from(IntegrityAuditLog).where(
            IntegrityAuditLog.equivalent_code == code, IntegrityAuditLog.operation_type == "INTEGRITY_VERIFY")
    )).scalar_one()


@pytest.mark.asyncio
async def test_a_verify_whose_audit_row_cannot_be_prepared_does_not_answer_200(client, db_session, monkeypatch):
    code = f"AU{uuid.uuid4().hex[:8].upper()}"
    db_session.add(Equivalent(code=code, description=code, precision=2))
    await db_session.commit()

    # Anti-vacuum: an ordinary verify of this equivalent writes exactly one audit row.
    healthy = await client.post("/api/v1/integrity/verify", json={"equivalent": code}, headers=ADMIN)
    assert healthy.status_code == 200, healthy.text
    assert await _verify_rows(db_session, code) == 1

    def _unpreparable(self, **_kwargs):
        raise ValueError("p035-a5: the audit object cannot be built")

    monkeypatch.setattr(IntegrityAuditLog, "__init__", _unpreparable)
    # The fixture's client re-raises an exception the application answered with 500; this one returns the answer.
    # The dependency overrides of `client` are still in place.
    async with AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test") as ac:
        response = await ac.post("/api/v1/integrity/verify", json={"equivalent": code}, headers=ADMIN)
    monkeypatch.undo()

    rows = await _verify_rows(db_session, code)
    if response.status_code == 200:
        assert rows == 2, (
            f"POST /integrity/verify answered 200 without its audit row: actual={rows} INTEGRITY_VERIFY rows, "
            f"expected=2 (or an error answer with request_id)"
        )
    else:
        assert response.json()["error"]["request_id"], response.text
