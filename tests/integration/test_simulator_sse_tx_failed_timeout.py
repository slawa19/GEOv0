import asyncio
import json

import pytest
from httpx import AsyncClient

from app.core.payments.service import PaymentService
from app.core.simulator.runtime import runtime
from app.utils.exceptions import TimeoutException


@pytest.mark.asyncio
async def test_simulator_run_events_sse_real_mode_emits_tx_failed_on_timeout(
    client: AsyncClient, auth_headers, monkeypatch
):
    injected = 0
    monkeypatch.setattr(
        runtime._real_runner,
        "_real_max_timeouts_per_tick_limit",
        1,
    )
    fail_run_calls: list[tuple[str, str]] = []
    real_fail_run = runtime._real_runner.fail_run

    async def _record_real_fail_run(run_id: str, *, code: str, message: str) -> None:
        fail_run_calls.append((code, message))
        await real_fail_run(run_id, code=code, message=message)

    monkeypatch.setattr(runtime._real_runner, "fail_run", _record_real_fail_run)

    async def _always_timeout(
        self,
        sender_id,
        *,
        to_pid,
        equivalent,
        amount,
        idempotency_key,
    ):
        nonlocal injected
        injected += 1
        raise TimeoutException("timeout")

    monkeypatch.setattr(
        PaymentService,
        "create_payment_internal_staged",
        _always_timeout,
        raising=True,
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

    # The in-process ASGI transport cannot expose an open stream incrementally.
    # Wait on the real runtime broadcast until the post-commit tx.failed exists;
    # the HTTP assertion below then exercises the normal replay + terminal-close
    # path without any pytest-only branch in production code.
    observer = await runtime.subscribe(
        run_id,
        equivalent="UAH",
        after_event_id=f"evt_{run_id}_000000",
    )
    try:
        async def _wait_for_failed() -> None:
            while True:
                event = await observer.queue.get()
                if event.get("type") == "tx.failed":
                    return

        await asyncio.wait_for(_wait_for_failed(), timeout=10.0)
    finally:
        await runtime.unsubscribe(run_id, observer)

    url = f"/api/v1/simulator/runs/{run_id}/events"

    seen_run_status = False
    seen_failed = False
    seen_types: list[str] = []

    try:
        async with client.stream(
            "GET",
            url,
            headers={**auth_headers, "Last-Event-ID": f"evt_{run_id}_000000"},
            params={"equivalent": "UAH"},
        ) as r:
            assert r.status_code == 200

            async def _read_until() -> None:
                nonlocal seen_run_status, seen_failed
                async for line in r.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    payload = json.loads(line.removeprefix("data: "))
                    seen_types.append(str(payload.get("type") or ""))
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
    assert seen_failed, f"No tx.failed seen; types={seen_types}"
    assert injected > 0
    assert [code for code, _message in fail_run_calls] == [
        "REAL_MODE_TOO_MANY_TIMEOUTS"
    ]
