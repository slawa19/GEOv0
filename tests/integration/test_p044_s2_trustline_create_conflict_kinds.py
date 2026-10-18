"""044 stage 2 (advisor F1, 2026-10-18): the simulator's trust-line create answers a refusal of the service by its kind.

`POST /simulator/runs/{run_id}/actions/trustline-create` caught EVERY `ConflictException` of
`TrustLineService.execute_create` and answered `TRUSTLINE_EXISTS` / `CONCURRENT_TRUSTLINE_CREATE` - also when the
equivalent was stopped by the operator or a party was suspended. Nothing moved (the handler rolls back), but the
refusal said "the line already exists". TARGET: mapped by `details.reason` - a stopped/held equivalent or a suspended
party answers like the clearing action (409, `code` CONFLICT, the core's `details`); a live line found by the service
(after the action's own check) or met at the unique index is still `TRUSTLINE_EXISTS`.

The production route on PostgreSQL; the run perimeter is a registered run (the harness of
`test_interact_actions_backend_p1.py`). The two concurrent-create counter-checks place the competing line inside the
handler's own transaction, at the point a concurrent commit would land - before the service's existence check, or
between it and the INSERT - so the service raises exactly the two conflicts a real race raises.
WHAT IT DOES NOT SEE: the integrity hold (`equivalent_integrity_hold`) - setting one needs a reconciliation result row;
it is the same `MONEY_STOP_REASONS` branch as the stop, and the reason is passed through unchanged.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.config import settings
from app.core.trustlines.service import TrustLineService, TrustLineWriteBatch
from app.db.models.trustline import TrustLine
from tests.unit.test_interact_actions_backend_p1 import _seed_alice_bob_uah, interact_actions_enabled  # noqa: F401

URL = "/api/v1/simulator/runs/test-run/actions/trustline-create"
HEADERS = {"X-Admin-Token": settings.ADMIN_TOKEN}
BODY = {"from_pid": "alice", "to_pid": "bob", "equivalent": "UAH", "limit": "10", "client_action_id": "p044s2-tl"}


async def _lines(db_session) -> int:
    return int(await db_session.scalar(select(func.count()).select_from(TrustLine)) or 0)


@pytest.mark.asyncio
async def test_a_create_in_a_stopped_equivalent_is_not_answered_as_an_existing_line(
    client, db_session, interact_actions_enabled  # noqa: F811
) -> None:
    _alice, _bob, uah = await _seed_alice_bob_uah(db_session)
    uah.is_active = False
    await db_session.commit()

    r = await client.post(URL, headers=HEADERS, json=BODY)

    assert r.status_code == 409, r.text
    body = r.json()
    assert body.get("code") == "CONFLICT", body
    assert (body.get("details") or {}).get("reason") == "equivalent_inactive", body
    assert (body.get("details") or {}).get("equivalents") == ["UAH"], body
    assert await _lines(db_session) == 0, "nothing was written"


@pytest.mark.asyncio
async def test_a_create_to_a_suspended_participant_is_not_answered_as_an_existing_line(
    client, db_session, interact_actions_enabled  # noqa: F811
) -> None:
    _alice, bob, _uah = await _seed_alice_bob_uah(db_session)
    bob.status = "suspended"
    await db_session.commit()

    r = await client.post(URL, headers=HEADERS, json=BODY)

    assert r.status_code == 409, r.text
    body = r.json()
    assert body.get("code") == "CONFLICT", body
    assert (body.get("details") or {}).get("reason") == "participant_suspended", body
    assert await _lines(db_session) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("lands", ["before the service's existence check", "between the check and the INSERT"])
async def test_counter_check_a_concurrent_create_is_still_trustline_exists(
    client, db_session, interact_actions_enabled, monkeypatch, lands: str  # noqa: F811
) -> None:
    alice, bob, uah = await _seed_alice_bob_uah(db_session)
    for row in (alice, bob, uah):
        await db_session.refresh(row)
    ids = (alice.id, bob.id, uah.id)  # read now: the rows expire at every commit
    real_create, real_touch = TrustLineService.execute_create, TrustLineWriteBatch._touch
    placed: list[str] = []

    async def competing_line(session) -> None:
        if placed:
            return
        placed.append(lands)
        session.add(TrustLine(from_participant_id=ids[0], to_participant_id=ids[1], equivalent_id=ids[2],
                              limit=5, policy={}, status="active"))
        await session.flush()

    async def create(self, *args, **kwargs):
        if lands == "before the service's existence check":
            await competing_line(self.session)
        return await real_create(self, *args, **kwargs)

    async def touch(self, *args, **kwargs):
        if lands == "between the check and the INSERT":
            await competing_line(self.session)
        return await real_touch(self, *args, **kwargs)

    monkeypatch.setattr(TrustLineService, "execute_create", create)
    monkeypatch.setattr(TrustLineWriteBatch, "_touch", touch)

    r = await client.post(URL, headers=HEADERS, json=BODY)

    assert placed == [lands], "the competing line was placed (non-vacuity)"
    assert r.status_code == 409, r.text
    body = r.json()
    assert body.get("code") == "TRUSTLINE_EXISTS", body
    assert (body.get("details") or {}).get("reason") == "CONCURRENT_TRUSTLINE_CREATE", body
