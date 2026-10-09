"""036 slice A fix-delta (review finding 8): the REST detail and the Simulator UI decoder read ONE set of cases.

`api/scenario-detail-conformance.json` holds scenarios and the `detail` the route answers for each. This test uploads every
scenario and requires the route's answer to equal `detail` (`created_at`, the upload time, is compared as null) and to
conform to the canon; `simulator-ui/v2/src/api/simulatorApi.contract.test.ts` requires the decoder to accept every `detail`
unchanged. A backend that answers something the decoder rejects, or a decoder that rejects something the backend can
answer, fails one of the two on the same file. The expectations are authored, not captured from the backend.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.config import settings
from app.core.simulator.runtime import runtime
from app.core.simulator.scenario_registry import ScenarioRegistry
from tests.contract.openapi_response_conformance import load_canon, validate_body

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFORMANCE = json.loads((REPO_ROOT / "api" / "scenario-detail-conformance.json").read_text(encoding="utf-8"))
ORIGIN = {"Origin": "http://localhost:5176"}


@pytest.fixture
def registry(monkeypatch, tmp_path: Path):
    reg = ScenarioRegistry(
        lock=threading.RLock(),
        scenarios=runtime._scenarios,
        fixtures_dir=tmp_path / "fixtures",
        schema_path=REPO_ROOT / "fixtures" / "simulator" / "scenario.schema.json",
        local_state_dir=tmp_path / "state",
        utc_now=lambda: datetime.now(timezone.utc),
        logger=logging.getLogger(__name__),
    )
    monkeypatch.setattr(runtime, "_scenario_registry", reg)
    monkeypatch.setattr(settings, "SIMULATOR_CSRF_ORIGIN_ALLOWLIST", ORIGIN["Origin"])
    try:
        yield reg
    finally:
        for key in [k for k in runtime._scenarios if k.startswith("conformance-")]:
            runtime._scenarios.pop(key, None)


@pytest.mark.asyncio
async def test_the_route_answers_every_case_of_the_shared_set(client, registry) -> None:
    cases = CONFORMANCE["cases"]
    assert len(cases) >= 4, "the shared conformance set is empty or truncated"  # non-vacuity
    client.cookies.clear()
    assert (await client.post("/api/v1/simulator/session/ensure")).status_code == 200

    for case in cases:
        scenario_id = case["scenario"]["scenario_id"]
        uploaded = await client.post("/api/v1/simulator/scenarios", headers=ORIGIN, json={"scenario": case["scenario"]})
        assert uploaded.status_code == 200, f"{case['name']}: upload answered {uploaded.status_code}: {uploaded.text[:300]}"

        response = await client.get(f"/api/v1/simulator/scenarios/{scenario_id}")

        assert response.status_code == 200, f"{case['name']}: {response.status_code}: {response.text[:300]}"
        body = response.json()
        assert isinstance(body["created_at"], str), case["name"]  # the upload time, whatever it is
        body["created_at"] = None
        assert body == case["detail"], case["name"]
        assert validate_body(load_canon(), "/components/schemas/ScenarioDetail", body) == [], case["name"]


def test_the_set_holds_the_boundaries_the_review_found() -> None:
    """Anti-vacuum: the cases that made the decoder and the backend disagree on `1afe0b09` are in the file."""

    text = json.dumps(CONFORMANCE["cases"])
    assert "999999999999.99999999" in text  # the largest storable amount: 12 integer + 8 fraction digits
    assert "1.000000000000000000" in text and "0" * 49 + "1" in text  # 18 fraction digits; 50 digits in all
    assert '"tx.failed"' in text and '"clearing.done"' in text and '"topology.changed"' in text and '"tx.updated"' in text
    assert "3000.0" in json.dumps(CONFORMANCE["cases"][3]["scenario"])  # an integral float time, normalised on upload
