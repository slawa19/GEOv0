"""Programme 024, stage 0, SIM-11: the artifact index does not hand out the server's absolute path.

`GET /simulator/runs/{run_id}/artifacts` returned `artifact_path=str(base)`, the absolute local path
of the run's artifacts directory, to any run owner - anonymous included (AGENTS.md section 12: no
absolute local paths outward). The field stays (`api/openapi.yaml`, nullable string; no UI reads
it); its value is now the directory relative to the simulator's local state root
(`.local-run/simulator/`), in POSIX form.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from httpx import AsyncClient

from app.core.simulator.runtime_utils import local_state_dir, repo_root
from tests.conftest import MODE_B
from tests.p042_support import no_real_tick, pay_once, start_real_run  # noqa: F401 - `no_real_tick` is a fixture

pytestmark = MODE_B


async def _stopped_run_with_a_payment(client: AsyncClient, auth_headers, monkeypatch) -> str:
    """A stopped real-mode run with one real payment (042 S1: it was a `fixtures` run and that mode's first event)."""

    run_id = await start_real_run(client, auth_headers)
    await pay_once(client, auth_headers, run_id, monkeypatch)
    stop = await client.post(f"/api/v1/simulator/runs/{run_id}/stop", headers=auth_headers)
    assert stop.status_code == 200, stop.text
    return run_id


@pytest.mark.asyncio
async def test_artifact_index_path_is_relative_to_the_simulator_state_root(
    client: AsyncClient, auth_headers, monkeypatch, no_real_tick  # noqa: F811
) -> None:
    run_id = await _stopped_run_with_a_payment(client, auth_headers, monkeypatch)

    index = await client.get(f"/api/v1/simulator/runs/{run_id}/artifacts", headers=auth_headers)
    assert index.status_code == 200, index.text
    body = index.json()
    # Premise: the run really has artifacts, so the path is the one a real index carries.
    assert body["items"], body

    artifact_path = body["artifact_path"]
    assert isinstance(artifact_path, str) and artifact_path, body
    for absolute in (str(local_state_dir()), str(local_state_dir().resolve()), str(repo_root())):
        assert absolute not in artifact_path
        assert absolute.replace("\\", "/") not in artifact_path
    assert not artifact_path.startswith(("/", "\\")), artifact_path
    assert re.match(r"^[A-Za-z]:", artifact_path) is None, artifact_path
    assert "\\" not in artifact_path, artifact_path
    assert artifact_path == f"runs/{run_id}/artifacts"
    # And it still names the directory the listed files live in.
    assert (local_state_dir() / Path(artifact_path) / body["items"][0]["name"]).is_file()
