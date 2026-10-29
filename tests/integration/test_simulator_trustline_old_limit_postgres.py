"""OMR-5: the Interact before-value belongs to its serialized trust-line update.

Real ASGI requests use separate PostgreSQL sessions (mode B). The first service
call pauses with its real row lock held; pg_blocking_pids proves the second
request queued before the first commits. No money state is synthesized.
"""

import asyncio
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select, text

from app.api.deps import get_db
from app.core.trustlines.service import TrustLineService
from app.db.models.trustline import TrustLine
from app.main import app
from tests.conftest import MODE_B, sessionmaker_of
from tests.integration.test_p030_s6b_lock_order_and_budget_postgres import _waits_on
from tests.integration.test_p044_s0_two_visitors_runs_are_isolated import (
    _visitor,
    started,
    two_visitors,
)
from tests.p042_support import no_real_tick  # noqa: F401

pytestmark = MODE_B


async def _line(client, started):
    visitor = await _visitor(client, started)
    run_id = await visitor.start("minimal")
    ids = await visitor.ids(run_id)
    payload = {"from_pid": ids["Coffee Shop"], "to_pid": ids["Ivan"], "equivalent": "UAH"}
    created = await visitor.post(
        f"/runs/{run_id}/actions/trustline-create", json={**payload, "limit": "100.00"}
    )
    assert created.status_code == 200, created.text
    return visitor, run_id, payload, uuid.UUID(created.json()["trustline_id"])


def _request_sessions(db_session, monkeypatch):
    sessions = sessionmaker_of(db_session)

    async def request_session():
        async with sessions() as session:
            yield session

    monkeypatch.setitem(app.dependency_overrides, get_db, request_session)
    return sessions


@pytest.mark.asyncio
async def test_pending_update_reports_the_limit_immediately_before_its_mutation(
    client, db_session, started, monkeypatch
):
    visitor, run_id, payload, line_id = await _line(client, started)
    sessions = _request_sessions(db_session, monkeypatch)
    first_holds, release_first = asyncio.Event(), asyncio.Event()
    original = TrustLineService.execute_update
    original_eq_code = TrustLineService._equivalent_code
    holder_pid, locked_limits = [], []

    async def observe_locked_limit(self, equivalent_id):
        # The real service calls this AFTER its populate_existing FOR UPDATE,
        # BEFORE assigning the limit. get() observes its loaded row identity.
        row = await self.session.get(TrustLine, line_id)
        locked_limits.append(row.limit)
        return await original_eq_code(self, equivalent_id)

    async def hold_first(self, batch, trustline_id, user_id, data, **kwargs):
        result = await original(self, batch, trustline_id, user_id, data, **kwargs)
        if Decimal(data.limit) == 150:
            holder_pid.append(await self.session.scalar(text("SELECT pg_backend_pid()")))
            first_holds.set()
            await release_first.wait()
        return result

    monkeypatch.setattr(TrustLineService, "_equivalent_code", observe_locked_limit)
    monkeypatch.setattr(TrustLineService, "execute_update", hold_first)
    path = f"/runs/{run_id}/actions/trustline-update"
    first = asyncio.create_task(visitor.post(path, json={**payload, "new_limit": "150.00"}))
    second = None
    try:
        await asyncio.wait_for(first_holds.wait(), 10)
        # A bad step must still be refused BEFORE attempting the row lock.
        invalid = await asyncio.wait_for(
            visitor.post(path, json={**payload, "new_limit": "200.005"}), 10
        )
        assert invalid.status_code == 400, invalid.text
        assert invalid.json()["details"]["reason"] == "amount_precision_exceeded", invalid.text
        assert not first.done(), "the first mutation must still hold the row lock"
        second = asyncio.create_task(visitor.post(path, json={**payload, "new_limit": "200.00"}))
        assert await _waits_on(sessions, holder_pid[0]), (
            "second HTTP update never queued on the first handler's PostgreSQL lock"
        )
    finally:
        release_first.set()
        results = await asyncio.wait_for(asyncio.gather(first, *([second] if second else [])), 15)

    r150, r200 = results
    assert r150.status_code == r200.status_code == 200, [r.text for r in results]
    assert r150.json()["old_limit"] == "100.00", r150.text
    assert r150.json()["new_limit"] == "150.00", r150.text
    assert locked_limits == [Decimal("100"), Decimal("150")], locked_limits
    async with sessions() as session:
        final = await session.scalar(select(TrustLine.limit).where(TrustLine.id == line_id))
    assert final == Decimal("200"), final
    assert r200.json()["new_limit"] == "200.00", r200.text
    assert r200.json()["old_limit"] == "150.00", r200.text


@pytest.mark.asyncio
async def test_update_queued_behind_a_close_keeps_the_closed_line_conflict(
    client, db_session, started, monkeypatch
):
    visitor, run_id, payload, line_id = await _line(client, started)
    sessions = _request_sessions(db_session, monkeypatch)
    first_holds, release_first = asyncio.Event(), asyncio.Event()
    original = TrustLineService.execute_close
    holder_pid = []

    async def hold_close(self, *args, **kwargs):
        result = await original(self, *args, **kwargs)
        holder_pid.append(await self.session.scalar(text("SELECT pg_backend_pid()")))
        first_holds.set()
        await release_first.wait()
        return result

    monkeypatch.setattr(TrustLineService, "execute_close", hold_close)
    first = asyncio.create_task(visitor.post(f"/runs/{run_id}/actions/trustline-close", json=payload))
    second = None
    try:
        await asyncio.wait_for(first_holds.wait(), 10)
        second = asyncio.create_task(visitor.post(
            f"/runs/{run_id}/actions/trustline-update", json={**payload, "new_limit": "200.00"}
        ))
        assert await _waits_on(sessions, holder_pid[0]), (
            "HTTP update never queued on the close's PostgreSQL lock"
        )
    finally:
        release_first.set()
        results = await asyncio.wait_for(asyncio.gather(first, *([second] if second else [])), 15)

    closed, refused = results
    assert closed.status_code == 200, closed.text
    assert closed.json()["status"] == "closed", closed.text
    assert refused.status_code == 409, refused.text
    assert refused.json()["code"] == "TRUSTLINE_CLOSED", refused.text
    assert refused.json()["details"] == {**payload, "trustline_id": str(line_id)}, refused.text
    async with sessions() as session:
        row = await session.get(TrustLine, line_id)
        assert row.status == "closed" and row.limit == Decimal("0"), (row.status, row.limit)
