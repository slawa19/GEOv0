"""A run's artifacts include `events.ndjson` with its domain events, downloadable with the bundle.

The domain event is a REAL payment (042 S1: the server mode `fixtures`, whose random first event this test used to wait
for, is gone): `pay_once` commits p2 -> p1 through the Interact route, which publishes `tx.updated`. The run's own tick is
a no-op, so that payment is the run's only domain event. Mode B: the seeding and the payment commit.
"""

import asyncio
import json

import pytest
from httpx import AsyncClient

from tests.conftest import MODE_B
from tests.p042_support import no_real_tick, pay_once, run_identities, start_real_run  # noqa: F401 - `no_real_tick` is a fixture

pytestmark = MODE_B


@pytest.mark.asyncio
async def test_simulator_artifacts_include_events_ndjson(
    client: AsyncClient, auth_headers, monkeypatch, no_real_tick  # noqa: F811
):
    run_id = await start_real_run(client, auth_headers)
    payment = await pay_once(client, auth_headers, run_id, monkeypatch)

    # Close the run through the normal terminal status so HTTPX's buffering ASGI transport receives a finite production
    # SSE response.
    stop_resp = await client.post(
        f"/api/v1/simulator/runs/{run_id}/stop", headers=auth_headers
    )
    assert stop_resp.status_code == 200, stop_resp.text

    # Exercise the actual HTTP replay path after the domain event is journaled.
    url = f"/api/v1/simulator/runs/{run_id}/events"
    async with client.stream(
        "GET",
        url,
        headers={**auth_headers, "Last-Event-ID": f"evt_{run_id}_000000"},
        params={"equivalent": "UAH"},
    ) as r:
        assert r.status_code == 200

        async def _read_one_event() -> None:
            async for line in r.aiter_lines():
                if line.startswith("data: "):
                    return

        await asyncio.wait_for(_read_one_event(), timeout=5.0)

    # Artifacts index must include events.ndjson.
    idx = await client.get(
        f"/api/v1/simulator/runs/{run_id}/artifacts", headers=auth_headers
    )
    assert idx.status_code == 200, idx.text

    items = idx.json().get("items") or []
    names = {it.get("name") for it in items}
    assert "events.ndjson" in names
    assert "summary.json" in names
    assert "bundle.zip" in names

    assert (
        idx.json().get("bundle_url")
        == f"/api/v1/simulator/runs/{run_id}/artifacts/bundle.zip"
    )

    events_item = next(it for it in items if it.get("name") == "events.ndjson")
    assert events_item.get("content_type") == "application/x-ndjson"

    # Download and validate it contains JSON lines.
    dl_url = events_item["url"]
    dl = await client.get(dl_url, headers=auth_headers)
    assert dl.status_code == 200, dl.text
    # 029 F-029-7: the download answers the media type its index entry names (was `text/plain`).
    assert dl.headers["content-type"].split(";")[0] == events_item["content_type"]

    lines = [ln for ln in dl.text.splitlines() if ln.strip()]
    assert len(lines) >= 1

    first = json.loads(lines[0])
    assert first.get("type") == "tx.updated", first
    assert str(first.get("event_id") or "").startswith("evt_")
    # It is the payment's event, not just any line - between the run's own participants (044 `T4405`: identities of
    # the run, `p2`/`p1` of the scenario).
    ids = await run_identities(client, auth_headers, run_id)
    assert (first.get("from"), first.get("to"), first.get("amount")) == (ids["p2"], ids["p1"], payment["amount"]), first

    # run_status is intentionally not exported into events.ndjson (too noisy).
    assert all(json.loads(ln).get("type") != "run_status" for ln in lines)

    # Bundle.zip must be downloadable.
    bundle = await client.get(
        f"/api/v1/simulator/runs/{run_id}/artifacts/bundle.zip", headers=auth_headers
    )
    assert bundle.status_code == 200
    assert bundle.headers["content-type"] == "application/zip"
    assert bundle.content.startswith(b"PK")
