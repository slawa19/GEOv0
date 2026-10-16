"""011/T1109c: download the JSON artifacts through the real route, so the canon is measured.

`GET /simulator/runs/{run_id}/artifacts/{name}` used to declare `application/octet-stream`, a media
type it never sends: the handler passes no `media_type` to `FileResponse`, so the wire type is
whatever `mimetypes` guesses. The canon now declares what the route really serves, and the JSON half
of that is a `oneOf` over the four documents a run writes.

**Those four branches were transcribed from their writers and exercised only by a counter-check
holding literal dicts.** No test in the suite downloaded a JSON artifact, which is exactly the gap
that let the allowance row covering this route go unnoticed in live traffic for a whole review
round. This file closes it: the artifacts are fetched through the production route, so the
session-wide response-conformance harness validates the real bodies against the canon and a writer
that adds or renames a key makes the gate red.

Nothing here asserts the shape itself. The point is to produce the traffic - the check that the
bodies match `api/openapi.yaml` lives in
`tests/contract/test_p011_responses_conform_to_the_canon.py` and runs over the whole session. What
this file does assert is that the traffic really happened and really was JSON, because a test that
silently downloaded nothing would restore the very hole it exists to close.
"""

import json

import pytest
from httpx import AsyncClient

from tests.conftest import MODE_B
from tests.p042_support import no_real_tick, pay_once, start_real_run  # noqa: F401 - `no_real_tick` is a fixture

# 042 S1: the run's domain event is a real payment, which commits; a mode-B clone is dropped after the test.
pytestmark = [MODE_B, pytest.mark.usefixtures("no_real_tick")]


async def _finished_run(client: AsyncClient, auth_headers, monkeypatch) -> str:
    """A stopped real-mode run that made one real payment, which is when the JSON artifacts are on disk.

    Mirrors `test_simulator_artifacts_events_ndjson`: start (the run's own tick a no-op), one committed payment
    through the Interact route so the run has really produced something, then stop through the normal terminal path -
    `status.json` is rewritten as the `RunStatus` document on stop, and `summary.json` is written there too. (Before
    042 the run was a `fixtures` one and the something was that mode's random first event.)
    """

    run_id = await start_real_run(client, auth_headers)
    await pay_once(client, auth_headers, run_id, monkeypatch)
    stop = await client.post(f"/api/v1/simulator/runs/{run_id}/stop", headers=auth_headers)
    assert stop.status_code == 200, stop.text
    return run_id


@pytest.mark.asyncio
async def test_every_json_artifact_is_downloaded_through_the_real_route(
    client: AsyncClient, auth_headers, monkeypatch
) -> None:
    """Fetch each `.json` artifact the index lists, through the route the canon describes."""

    run_id = await _finished_run(client, auth_headers, monkeypatch)

    index = await client.get(
        f"/api/v1/simulator/runs/{run_id}/artifacts", headers=auth_headers
    )
    assert index.status_code == 200, index.text

    items = index.json().get("items") or []
    json_names = sorted(
        str(item.get("name")) for item in items if str(item.get("name", "")).endswith(".json")
    )
    assert json_names, (
        "the run produced no JSON artifact, so this test would have downloaded nothing and the "
        "canon's four-branch declaration would stay unexercised - which is the hole it exists to "
        f"close. Index held: {[item.get('name') for item in items]}"
    )
    assert "summary.json" in json_names, json_names

    for name in json_names:
        download = await client.get(
            f"/api/v1/simulator/runs/{run_id}/artifacts/{name}", headers=auth_headers
        )
        assert download.status_code == 200, (name, download.text[:200])

        # The canon declares `application/json` for this route because that is what mimetypes
        # guesses for a `.json` path. If that ever stops being true the canon must move with it,
        # and the failure should say so here rather than surfacing as an opaque skip.
        media_type = download.headers.get("content-type", "").split(";")[0].strip()
        assert media_type == "application/json", (
            f"{name} arrived as {media_type!r}; the canon declares application/json for this "
            "operation, and the response-conformance harness classifies on what actually arrives"
        )

        body = json.loads(download.content)
        assert isinstance(body, dict), (name, type(body).__name__)


@pytest.mark.asyncio
async def test_the_status_document_really_is_the_run_status_shape_after_stop(
    client: AsyncClient, auth_headers, monkeypatch
) -> None:
    """One of the four `oneOf` branches is only reachable after a stop - pin which.

    `status.json` has two shapes over a run's life: the init record written when the run is created,
    and the `RunStatus` that overwrites it on stop. The canon declares both, and a reader of that
    `oneOf` deserves to know the second branch is not hypothetical. Without this, a change that
    stopped rewriting the file would leave the extra branch in the canon describing nothing, and
    nothing would notice.
    """

    run_id = await _finished_run(client, auth_headers, monkeypatch)

    download = await client.get(
        f"/api/v1/simulator/runs/{run_id}/artifacts/status.json", headers=auth_headers
    )
    assert download.status_code == 200, download.text

    body = download.json()
    assert body.get("state"), (
        "after a stop, status.json is the RunStatus document, whose `state` the init record does "
        f"not have. Got keys: {sorted(body)}"
    )
    assert "seed" not in body, (
        "the two branches are told apart by their key sets and `additionalProperties: false`; a "
        "body carrying both `state` and `seed` would match neither branch of the canon's oneOf"
    )
