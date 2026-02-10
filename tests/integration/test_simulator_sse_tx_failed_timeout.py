import asyncio
import json

import pytest
from httpx import AsyncClient

from app.core.payments.service import PaymentService
from app.utils.exceptions import TimeoutException


@pytest.mark.asyncio
async def test_simulator_run_events_sse_real_mode_emits_tx_failed_on_timeout(
    client: AsyncClient, auth_headers, monkeypatch
):
    async def _always_timeout(
        self,
        sender_id,
        *,
        to_pid,
        equivalent,
        amount,
        idempotency_key,
        commit: bool = True,
    ):
        raise TimeoutException("timeout")

    monkeypatch.setattr(
        PaymentService, "create_payment_internal", _always_timeout, raising=True
    )

    resp = await client.post(
        "/api/v1/simulator/runs",
        headers=auth_headers,
        json={
            "scenario_id": "greenfield-village-100-realistic-v2",
            "mode": "real",
            "intensity_percent": 90,
        },
    )
    assert resp.status_code == 200, resp.text
    run_id = resp.json()["run_id"]

    url = f"/api/v1/simulator/runs/{run_id}/events"

    seen_run_status = False
    seen_failed = False

    try:
        async with client.stream(
            "GET",
            url,
            headers=auth_headers,
            params={"equivalent": "UAH"},
        ) as r:
            assert r.status_code == 200

            async def _read_until() -> None:
                nonlocal seen_run_status, seen_failed
                async for line in r.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    payload = json.loads(line.removeprefix("data: "))
                    if payload.get("type") == "run_status":
                        seen_run_status = True
                    if payload.get("type") == "tx.failed":
                        seen_failed = True
                        error = payload.get("error")
                        assert isinstance(error, dict)
                        assert error.get("code") == "PAYMENT_TIMEOUT"
                        assert isinstance(error.get("message"), str) and error.get(
                            "message"
                        )
                        assert isinstance(error.get("at"), str) and error.get("at")
                    if seen_run_status and seen_failed:
                        return

            await asyncio.wait_for(_read_until(), timeout=10.0)
    finally:
        # Stop the background run to avoid holding DB locks across tests.
        await client.post(
            f"/api/v1/simulator/runs/{run_id}/stop",
            headers=auth_headers,
        )

    assert seen_run_status
    assert seen_failed
