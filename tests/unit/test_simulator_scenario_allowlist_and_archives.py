import pytest

from app.core.simulator.runtime import runtime
from app.utils.exceptions import NotFoundException


def test_list_scenarios_default_allowlist_contains_only_canonical_presets() -> None:
    scenario_ids = [s.scenario_id for s in runtime.list_scenarios()]
    assert scenario_ids == [
        "clearing-demo-10",
        "greenfield-village-100-realistic-v2",
        "riverside-town-50-realistic-v2",
    ]


def test_archived_fixture_scenarios_are_not_loaded() -> None:
    with pytest.raises(NotFoundException):
        runtime.get_scenario("clearing-demo-manual")

    with pytest.raises(NotFoundException):
        runtime.get_scenario("greenfield-village-100")
