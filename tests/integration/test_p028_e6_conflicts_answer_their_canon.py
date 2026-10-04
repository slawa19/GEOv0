"""028 E6: conflicts answered in the shape the canon declares.

* F-028-6: an Interact update/close whose line another writer closes while the action waits for its row lock answers
  the flat 409 `TRUSTLINE_CLOSED` (`SimulatorActionError`). REAL SCHEDULE (no injected SQLSTATE): a second connection
  closes the line and holds the row; the action blocks on `FOR UPDATE` and reads `closed` after the commit. Mode B.
* F-028-20: `POST /clearing/auto` with clearing off is 409/E008 `clearing_disabled`. `T2864`: resume/restart declare 409.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import yaml
from sqlalchemy import select, text

from app.config import settings
from app.core.trustlines.service import TrustLineService
from app.db.models.trustline import TrustLine
from app.schemas.trustline import TrustLineCloseRequest
from tests.conftest import MODE_B, sessionmaker_of
from tests.integration.test_scenarios import register_and_login
from tests.unit.test_p021_interact_trust_line_actions_wire import TRIPLE, _post, stand  # noqa: F401 - fixture

CANON = Path(__file__).resolve().parents[2] / "api" / "openapi.yaml"


async def _wait_for_lock_waiter(sessions) -> None:
    async with sessions() as probe:
        for _ in range(200):
            waiting = await probe.scalar(text(
                "select count(*) from pg_stat_activity where wait_event_type = 'Lock' and datname = current_database()"))
            if waiting:
                return
            await asyncio.sleep(0.025)
    raise AssertionError("the action never waited for the line's row lock")


@MODE_B
@pytest.mark.asyncio
@pytest.mark.parametrize("action,body", [("trustline-update", {"new_limit": "5"}), ("trustline-close", {})])
async def test_a_line_closed_while_the_action_waits_answers_the_flat_conflict(client, stand, action, body) -> None:
    assert (await _post(client, "trustline-create", {**TRIPLE, "limit": "10"})).status_code == 200
    sessions = sessionmaker_of(stand["db"])
    async with sessions() as other:
        line = (await other.execute(select(TrustLine))).scalar_one()
        service = TrustLineService(other)
        batch = service.begin_internal_batch()
        await service.execute_close(batch, line.id, line.from_participant_id,
                                    TrustLineCloseRequest(signature="__internal__"), require_signature=False)
        await batch.finish()
        pending = asyncio.create_task(_post(client, action, {**TRIPLE, **body}))
        await _wait_for_lock_waiter(sessions)
        await other.commit()
    resp = await pending
    assert resp.status_code == 409, resp.text
    assert resp.json()["code"] == "TRUSTLINE_CLOSED" and "error" not in resp.json(), resp.text


@pytest.mark.asyncio
async def test_clearing_switched_off_is_a_declared_conflict(client, db_session, monkeypatch) -> None:
    user = await register_and_login(client, "E6Clearing")
    monkeypatch.setattr(settings, "CLEARING_ENABLED", False)
    resp = await client.post("/api/v1/clearing/auto", params={"equivalent": "UAH"}, headers=user["headers"])
    assert resp.status_code == 409, resp.text
    error = resp.json()["error"]
    assert (error["code"], error["details"]["reason"]) == ("E008", "clearing_disabled"), error


def test_resume_and_restart_declare_their_conflict() -> None:
    paths = yaml.safe_load(CANON.read_text(encoding="utf-8"))["paths"]
    for op in ("resume", "restart"):
        assert "409" in paths[f"/simulator/runs/{{run_id}}/{op}"]["post"]["responses"], op
