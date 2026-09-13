"""T1545, 2026-09-13: `GET /payments/max-flow?to=<own pid>` hung the worker.

Measured before the fix: an ordinary registered participant's request never returned on its own -
a bound stopped it after 2.0 s and ~143k appended paths, with no other task on the event loop
running in that window. The refusal must be the one a payment to yourself already gets.

The bound on `MaxFlowPath` is what keeps a regression from hanging this runner: it turns the loop
into a red test instead.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select

import app.core.payments.router as router_mod
from app.db.models.equivalent import Equivalent
from tests.integration.test_scenarios import register_and_login


@pytest.mark.asyncio
async def test_max_flow_to_own_pid_is_refused_like_a_payment_to_yourself(
    client: AsyncClient, db_session, monkeypatch
):
    if not (
        await db_session.execute(select(Equivalent).where(Equivalent.code == "USD"))
    ).scalar_one_or_none():
        db_session.add(Equivalent(code="USD", description="USD", precision=2))
        await db_session.commit()

    alice = await register_and_login(client, "Alice_T1545")

    appended = 0
    original = router_mod.MaxFlowPath

    def bounded(*args, **kwargs):
        nonlocal appended
        appended += 1
        if appended > 1000:
            raise AssertionError("calculate_max_flow did not terminate")
        return original(*args, **kwargs)

    monkeypatch.setattr(router_mod, "MaxFlowPath", bounded)

    resp = await client.get(
        "/api/v1/payments/max-flow",
        headers=alice["headers"],
        params={"to": alice["pid"], "equivalent": "USD"},
    )

    assert resp.status_code == 400, resp.text
    error = resp.json()["error"]
    assert error["code"] == "E009"
    assert error["message"] == "Cannot pay to yourself"
