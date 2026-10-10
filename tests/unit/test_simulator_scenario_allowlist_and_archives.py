from itertools import combinations

import pytest

from app.core.simulator.runtime import runtime
from app.core.simulator.runtime_impl import _scenario_allowlist
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


# --- Scenarios offered together name different participants (D1, 2026-10-10) ------------------------------------------
#
# A real-mode run stores its scenario's participants under the scenario's ids, and the seeder adopts a stored simulated
# participant by id alone (`real_scenario_seeder.py`: the pseudo key says the simulator made the row, not which scenario
# it belongs to). Two scenarios that name one id therefore share that participant in one database - its name, type,
# status, trust lines and debts. The two realistic-v2 scenarios did until 2026-10-10.


def declared_participant_ids(scenario: dict) -> set[str]:
    """Every participant id a scenario declares: its `participants`, and whoever an `inject` event adds
    (`add_participant`). An equal name or status on both sides is not an exemption: the row would still be one."""

    ids = {str(p.get("id") or "") for p in scenario.get("participants") or []}
    for event in scenario.get("events") or []:
        for effect in event.get("effects") or []:
            if isinstance(effect, dict) and effect.get("op") == "add_participant":
                ids.add(str((effect.get("participant") or {}).get("id") or ""))
    return ids - {""}


def shared_participant_ids(scenarios: dict[str, dict]) -> dict[tuple[str, str], list[str]]:
    """`{(scenario, scenario): [shared ids]}` for every pair that shares at least one participant id."""

    ids = {scenario_id: declared_participant_ids(raw) for scenario_id, raw in scenarios.items()}
    return {(a, b): sorted(ids[a] & ids[b]) for a, b in combinations(sorted(ids), 2) if ids[a] & ids[b]}


def test_the_scenarios_of_the_default_allowlist_share_no_participant_id(monkeypatch) -> None:
    monkeypatch.setattr("app.config.settings.SIMULATOR_SCENARIO_ALLOWLIST", "")
    allowlist = _scenario_allowlist()
    assert allowlist is not None and len(allowlist) >= 2, "the default allowlist no longer names scenarios to compare"
    scenarios = {scenario_id: runtime.get_scenario(scenario_id).raw for scenario_id in sorted(allowlist)}
    # Non-empty coverage: every scenario of the list was loaded and declares somebody.
    assert all(declared_participant_ids(raw) for raw in scenarios.values()), sorted(scenarios)

    shared = shared_participant_ids(scenarios)

    assert shared == {}, (
        "Scenarios of the default allowlist (app/core/simulator/runtime_impl.py, _scenario_allowlist) name the same "
        f"participant ids: { {pair: ids[:3] for pair, ids in shared.items()} } (the first three of each pair). Run "
        "one after the other in one database and the second adopts the participants of the first - their names, "
        "statuses, trust lines and debts. Give each scenario ids of its own: the generated ones use "
        "'<scenario_id>:<community pid>' (scripts/generate_simulator_seed_scenarios.py), a hand-written one uses a "
        "prefix, as 'cs_' in community-story-10. WHAT THIS GUARD DOES NOT SEE: scenarios enabled by "
        "SIMULATOR_SCENARIO_ALLOWLIST (ids or '*') or uploaded through POST /simulator/scenarios; it reads the "
        "default list only. To list the colliding ids, compare 'participants[].id' of the two scenario.json files "
        "named in the pair."
    )


def test_the_shared_id_check_finds_a_seeded_and_an_injected_collision() -> None:
    """Anti-vacuum: the check above is not empty-handed. A deliberately colliding set, in both declared forms."""
    seeded = {"participants": [{"id": "PID_A", "name": "Anna", "status": "active"}, {"id": "PID_B"}]}
    same_person_elsewhere = {"participants": [{"id": "PID_A", "name": "Anna", "status": "active"}, {"id": "PID_C"}]}
    injects_b = {
        "participants": [{"id": "PID_D"}],
        "events": [
            {"type": "stress", "effects": [{"op": "mult", "field": "tx_rate", "scope": "all", "value": 2}]},
            {"type": "inject", "effects": [{"op": "add_participant", "participant": {"id": "PID_B"}}]},
        ],
    }
    disjoint = {"participants": [{"id": "x:PID_A"}, {"id": "x:PID_B"}]}

    assert shared_participant_ids(
        {"one": seeded, "two": same_person_elsewhere, "three": injects_b, "four": disjoint}
    ) == {("one", "two"): ["PID_A"], ("one", "three"): ["PID_B"]}
