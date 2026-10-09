"""036 slice A, second fix-delta (review of `c2d84180`, N2): uploading a scenario reads NO row of the database.

`POST /simulator/scenarios` looked up the precision of the payment events' equivalents to refuse a payment finer than the
accounting step. That made STORAGE depend on the environment (PostgreSQL unreachable: a valid scenario answered 500
before it was validated) while the execution has to recheck the step anyway - the equivalent can be unknown at upload and
its precision can change after it. The arbiter's decision: upload keeps the lexical grammar, the value rule
(positive, storable in `Numeric(20, 8)`) and the references; the step check (`require_money_step` with the precision read
from the database) belongs to slice B, at the point of use.

The trap records every statement the request's session executes after the actor has been established. Red on `3b60dc03`:
the upload of a scenario with a payment ran one `SELECT ... FROM equivalents`.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from tests.integration.test_p036_a2_story_is_validated_at_upload import (  # noqa: F401 - `registry` is a fixture
    ORIGIN,
    PREFIX,
    _payment,
    _scenario,
    registry,
)


async def _upload_with_a_trap(client, db_session, monkeypatch, scenario: dict):
    client.cookies.clear()
    assert (await client.post("/api/v1/simulator/session/ensure")).status_code == 200  # the actor, before the trap
    statements: list[str] = []
    real_execute = db_session.execute

    async def recording_execute(statement, *args, **kwargs):
        statements.append(" ".join(str(statement).split())[:120])
        return await real_execute(statement, *args, **kwargs)

    monkeypatch.setattr(db_session, "execute", recording_execute)
    response = await client.post("/api/v1/simulator/scenarios", headers=ORIGIN, json={"scenario": scenario})
    return response, statements, recording_execute


@pytest.mark.asyncio
async def test_the_trap_sees_a_statement_when_there_is_one(db_session, monkeypatch) -> None:
    """Anti-vacuum: the recording wrapper records what runs through it, so an empty list below means 'nothing ran'."""

    seen: list[str] = []
    real = db_session.execute

    async def recording(statement, *a, **kw):
        seen.append(str(statement))
        return await real(statement, *a, **kw)

    monkeypatch.setattr(db_session, "execute", recording)
    await db_session.execute(select(1))

    assert len(seen) == 1


@pytest.mark.asyncio
async def test_control_a_scenario_without_a_payment_runs_no_statement(client, registry, db_session, monkeypatch) -> None:
    response, statements, _ = await _upload_with_a_trap(
        client, db_session, monkeypatch, _scenario("no-payment", [{"time": 0, "type": "clearing", "equivalent": "UAH"}])
    )

    assert response.status_code == 200, response.text
    assert statements == [], statements


@pytest.mark.asyncio
async def test_a_scenario_with_a_payment_is_stored_without_reading_the_database(
    client, registry, db_session, monkeypatch
) -> None:
    response, statements, _ = await _upload_with_a_trap(
        client, db_session, monkeypatch,
        _scenario("with-payment", [_payment("1.505")], equivalents=["UAH"], baseEquivalent="UAH"),
    )

    assert response.status_code == 200, response.text
    assert PREFIX + "with-payment" in registry._scenarios
    assert statements == [], f"the upload of a scenario with a payment executed: {statements}"
