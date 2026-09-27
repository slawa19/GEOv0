"""Programme 024, stage 0, F-024-4a (SIM-01): an uploaded `scenario_id` becomes a directory name.

`ScenarioRegistry.save_uploaded_scenario` joins the id onto `<local_state_dir>/scenarios/`. Without a
rule of form the id `../escape` wrote `scenario.json` outside that directory, and an id equal to a
bundled preset replaced the preset for every user (the upload path only checked for an existing
*uploaded* file). The code checks the form itself and the resolved containment, because the JSON
Schema validator is optional (`get_scenario_validator` returns `None` without a schema file).
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.core.simulator.scenario_registry import (
    ScenarioRegistry,
    get_scenario_validator,
    scenario_to_record,
)
from app.utils.exceptions import BadRequestException

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = REPO_ROOT / "fixtures" / "simulator" / "scenario.schema.json"


def _scenario(scenario_id: str) -> dict:
    return {
        "schema_version": "scenario/1",
        "scenario_id": scenario_id,
        "equivalents": ["UAH"],
        "participants": [{"id": "P1", "type": "person"}],
        "trustlines": [],
    }


def _registry(tmp_path: Path, *, schema_path: Path, scenarios: dict | None = None) -> ScenarioRegistry:
    return ScenarioRegistry(
        lock=threading.RLock(),
        scenarios=scenarios if scenarios is not None else {},
        fixtures_dir=tmp_path / "fixtures",
        schema_path=schema_path,
        local_state_dir=tmp_path / "state",
        utc_now=lambda: datetime.now(timezone.utc),
        logger=logging.getLogger(__name__),
    )


def _written_scenario_files(root: Path) -> list[Path]:
    return sorted(root.rglob("scenario.json"))


UNSAFE_IDS = [
    "../escape",
    "..\\escape",
    "../../escape",
    "a/b",
    "a\\b",
    "/abs",
    "C:\\abs",
    ".hidden",
    "..",
    "trailing.",
    " padded",
    "x" * 200,
]


@pytest.mark.parametrize("scenario_id", UNSAFE_IDS)
@pytest.mark.parametrize("with_schema", [True, False], ids=["schema", "no-schema"])
def test_unsafe_scenario_id_is_refused_and_nothing_is_written(
    tmp_path: Path, scenario_id: str, with_schema: bool
) -> None:
    # "no-schema" is the load-bearing case: the code must refuse on its own (AGENTS §9).
    schema_path = SCHEMA_PATH if with_schema else tmp_path / "missing.schema.json"
    registry = _registry(tmp_path, schema_path=schema_path)

    with pytest.raises(BadRequestException) as exc_info:
        registry.save_uploaded_scenario(_scenario(scenario_id))

    assert exc_info.value.status_code == 400
    assert _written_scenario_files(tmp_path) == []
    assert registry._scenarios == {}


@pytest.mark.parametrize("scenario_id", UNSAFE_IDS[:3])
def test_unsafe_scenario_id_names_the_field_in_details(tmp_path: Path, scenario_id: str) -> None:
    registry = _registry(tmp_path, schema_path=tmp_path / "missing.schema.json")

    with pytest.raises(BadRequestException) as exc_info:
        registry.save_uploaded_scenario(_scenario(scenario_id))

    assert exc_info.value.details.get("scenario_id") == scenario_id


def test_upload_cannot_replace_a_bundled_preset(tmp_path: Path) -> None:
    preset = scenario_to_record(
        _scenario("clearing-demo-10"),
        source_path=tmp_path / "fixtures" / "clearing-demo-10" / "scenario.json",
        created_at=None,
    )
    scenarios = {"clearing-demo-10": preset}
    registry = _registry(tmp_path, schema_path=SCHEMA_PATH, scenarios=scenarios)

    with pytest.raises(BadRequestException):
        registry.save_uploaded_scenario(_scenario("clearing-demo-10"))

    assert scenarios["clearing-demo-10"] is preset
    assert _written_scenario_files(tmp_path) == []


def test_uploaded_file_shadowing_a_preset_is_not_loaded_on_restart(tmp_path: Path) -> None:
    # A file written before this rule existed must not replace the preset after a restart either.
    fixtures = tmp_path / "fixtures" / "clearing-demo-10"
    fixtures.mkdir(parents=True)
    (fixtures / "scenario.json").write_text(json.dumps(_scenario("clearing-demo-10")), encoding="utf-8")
    shadow = tmp_path / "state" / "scenarios" / "clearing-demo-10"
    shadow.mkdir(parents=True)
    (shadow / "scenario.json").write_text(json.dumps(_scenario("clearing-demo-10")), encoding="utf-8")
    registry = _registry(tmp_path, schema_path=SCHEMA_PATH)

    registry.load_all()

    assert registry._scenarios["clearing-demo-10"].source_path == fixtures / "scenario.json"


@pytest.mark.parametrize(
    "scenario_id",
    ["golden-7_2-like", "missing_equivalent_fields", "greenfield-village-100-realistic-v2", "a", "v1.2"],
)
def test_ordinary_scenario_id_is_saved_as_before(tmp_path: Path, scenario_id: str) -> None:
    # Counter-check (anti-vacuum): the rule must still let real ids through, schema on.
    registry = _registry(tmp_path, schema_path=SCHEMA_PATH)

    rec = registry.save_uploaded_scenario(_scenario(scenario_id))

    expected = tmp_path / "state" / "scenarios" / scenario_id / "scenario.json"
    assert rec.source_path == expected
    assert expected.exists()
    assert registry._scenarios[scenario_id] is rec

    reloaded = _registry(tmp_path, schema_path=SCHEMA_PATH)
    reloaded.load_all()
    assert reloaded._scenarios[scenario_id].source_path == expected


def test_every_bundled_scenario_passes_the_schema_rule() -> None:
    # Counter-check: the schema pattern must not reject a scenario the repository ships.
    validator = get_scenario_validator(schema_path=SCHEMA_PATH)
    assert validator is not None
    paths = sorted((REPO_ROOT / "fixtures" / "simulator").glob("*/scenario.json"))
    assert len(paths) >= 5
    for path in paths:
        raw = json.loads(path.read_text(encoding="utf-8"))
        errors = [e.message for e in validator.iter_errors(raw) if list(e.path) == ["scenario_id"]]
        assert errors == [], (path, errors)


def test_schema_refuses_a_traversal_id() -> None:
    validator = get_scenario_validator(schema_path=SCHEMA_PATH)
    assert validator is not None
    errors = [e for e in validator.iter_errors(_scenario("../escape")) if list(e.path) == ["scenario_id"]]
    assert errors, "scenario.schema.json must carry the scenario_id pattern"
