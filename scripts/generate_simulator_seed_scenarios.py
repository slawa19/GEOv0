"""Generate the realistic-v2 simulator scenarios from the community descriptions.

A simulator scenario is *a community description plus the behaviour the scenario
adds on top of it*:

* **Structure** — who exists, which group each one belongs to, who trusts whom
  and up to which limit — comes from ``seeds/communities/<id>/community.json``.
  It is read, never re-stated here. There is one roster in the repository.
* **Behaviour** — profiles, recipient weights, amount models, flow chains,
  warm-up, trust drift and seasonal stress — belongs to the scenario and lives
  in this file.

The generator is deterministic: same description in, same bytes out. It writes
nothing that is not derived from the description or from the behaviour block
below, and it validates the result against
``fixtures/simulator/scenario.schema.json``.

Run:
  ./.venv/Scripts/python.exe scripts/generate_simulator_seed_scenarios.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]

if str(ROOT / "seeds" / "communities") not in sys.path:
    sys.path.insert(0, str(ROOT / "seeds" / "communities"))

from community_schema import load_community  # noqa: E402


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # newline="\n" rather than the platform default: `.gitattributes` keeps every
    # `.json` at LF in the worktree, so a CRLF write would make the scenario read
    # as modified after every run on Windows, and the output would stop being
    # byte-identical across platforms.
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def _validate_scenario_shape(scenario_path: Path) -> None:
    schema_path = ROOT / "fixtures" / "simulator" / "scenario.schema.json"
    schema = _read_json(schema_path)
    scenario = _read_json(scenario_path)

    pid_pattern = re.compile(schema["$defs"]["id"]["pattern"])

    participant_def = schema["$defs"]["participant"]
    participant_allowed = set(participant_def["properties"].keys())
    participant_required = set(participant_def["required"])
    participant_status_enum = set(participant_def["properties"]["status"]["enum"])

    trustline_def = schema["$defs"]["trustline"]
    trustline_allowed = set(trustline_def["properties"].keys())
    trustline_required = set(trustline_def["required"])

    if not {"schema_version", "scenario_id", "participants", "trustlines"}.issubset(scenario.keys()):
        raise RuntimeError(f"Missing required top-level keys in {scenario_path}")

    if ("equivalents" not in scenario) and ("baseEquivalent" not in scenario):
        raise RuntimeError(f"Missing equivalents/baseEquivalent in {scenario_path}")

    participants = scenario.get("participants") or []
    trustlines = scenario.get("trustlines") or []

    participant_ids: set[str] = set()
    for p in participants:
        extra = set(p.keys()) - participant_allowed
        if extra:
            raise RuntimeError(f"Extra participant keys {sorted(extra)} in {scenario_path}")
        missing = participant_required - set(p.keys())
        if missing:
            raise RuntimeError(f"Missing participant keys {sorted(missing)} in {scenario_path}")
        if not pid_pattern.match(p["id"]):
            raise RuntimeError(f"Bad participant.id '{p['id']}' in {scenario_path}")
        participant_ids.add(p["id"])
        if "groupId" in p and not pid_pattern.match(p["groupId"]):
            raise RuntimeError(f"Bad participant.groupId '{p['groupId']}' in {scenario_path}")
        if "behaviorProfileId" in p and not pid_pattern.match(p["behaviorProfileId"]):
            raise RuntimeError(f"Bad participant.behaviorProfileId '{p['behaviorProfileId']}' in {scenario_path}")
        if "status" in p and p["status"] not in participant_status_enum:
            raise RuntimeError(f"Bad participant.status '{p['status']}' in {scenario_path}")

    equivalents_list = scenario.get("equivalents") or ([] if "baseEquivalent" in scenario else [])
    equivalents_set = set(equivalents_list)
    base_eq = scenario.get("baseEquivalent")

    for t in trustlines:
        extra = set(t.keys()) - trustline_allowed
        if extra:
            raise RuntimeError(f"Extra trustline keys {sorted(extra)} in {scenario_path}")
        missing = trustline_required - set(t.keys())
        if missing:
            raise RuntimeError(f"Missing trustline keys {sorted(missing)} in {scenario_path}")
        if t["from"] not in participant_ids:
            raise RuntimeError(f"trustline.from '{t['from']}' not in participants in {scenario_path}")
        if t["to"] not in participant_ids:
            raise RuntimeError(f"trustline.to '{t['to']}' not in participants in {scenario_path}")
        eq = t.get("equivalent")
        if eq is not None:
            if equivalents_set and eq not in equivalents_set:
                raise RuntimeError(f"trustline.equivalent '{eq}' not in equivalents[] in {scenario_path}")
            if base_eq and eq != base_eq and not equivalents_set:
                raise RuntimeError(f"trustline.equivalent '{eq}' does not match baseEquivalent in {scenario_path}")


def _behavior_for_group(group_id: str, participant_index: int = 0) -> str:
    """Map group to behavior profile, with deterministic subtypes for large groups.

    ``participant_index`` is the roster index the description carries. It drives
    a deterministic assignment of subtypes so that the same description always
    produces the same profile distribution.
    """
    if group_id == "households":
        # Distribution: 60 % base, 20 % active, 20 % frugal
        mod = participant_index % 5
        if mod < 3:
            return "household"
        elif mod < 4:
            return "household_active"
        else:
            return "household_frugal"

    if group_id == "producers":
        # Distribution: 50 % base, 25 % large, 25 % small
        mod = participant_index % 4
        if mod < 2:
            return "producer"
        elif mod < 3:
            return "producer_large"
        else:
            return "producer_small"

    by_group = {
        "anchors": "anchor_hub",
        "retail": "retail",
        "services": "service",
        "agents": "agent",
    }
    if group_id not in by_group:
        # A description may declare a group this scenario has no behaviour for.
        # Refuse loudly instead of inventing a profile: an unmodelled group that
        # silently borrows someone else's behaviour is a run that means nothing.
        raise RuntimeError(
            f"No realistic-v2 behaviour profile for group '{group_id}'. "
            "Add one to _make_behavior_profiles_realistic_v2() and map it here."
        )
    return by_group[group_id]


def _make_behavior_profiles_realistic_v2() -> list[dict[str, Any]]:
    # Realistic-v2 intent:
    # - payments are typically 50..1500 UAH (bounded)
    # - lower tx_rate to avoid runaway balances over time
    # - flow_chains define preferred payment direction (cyclic economy)
    # - periodicity_factor: larger amounts → less frequent transactions
    return [
        {
            "id": "anchor_hub",
            "props": {
                "tx_rate": 0.02,
                "equivalent_weights": {"UAH": 1.0},
                "amount_model": {
                    "UAH": {"p50": 900, "p90": 1400, "min": 80, "max": 1500},
                },
            },
        },
        # --- Producer subtypes ---
        {
            "id": "producer",
            "props": {
                "tx_rate": 0.08,
                "equivalent_weights": {"UAH": 1.0},
                "recipient_group_weights": {
                    "retail": 0.35,
                    "services": 0.25,
                    "households": 0.25,
                    "anchors": 0.15,
                },
                "amount_model": {
                    "UAH": {"p50": 350, "p90": 1100, "min": 50, "max": 1500},
                },
                "flow_chains": [["producers", "retail"]],
            },
        },
        {
            "id": "producer_large",
            "props": {
                "tx_rate": 0.10,
                "equivalent_weights": {"UAH": 1.0},
                "recipient_group_weights": {
                    "retail": 0.30,
                    "services": 0.20,
                    "households": 0.30,
                    "anchors": 0.20,
                },
                "amount_model": {
                    "UAH": {"p50": 500, "p90": 1300, "min": 80, "max": 1500},
                },
                "flow_chains": [["producers", "retail"]],
                "periodicity_factor": 1.5,
            },
        },
        {
            "id": "producer_small",
            "props": {
                "tx_rate": 0.05,
                "equivalent_weights": {"UAH": 1.0},
                "recipient_group_weights": {
                    "retail": 0.40,
                    "services": 0.25,
                    "households": 0.20,
                    "anchors": 0.15,
                },
                "amount_model": {
                    "UAH": {"p50": 200, "p90": 600, "min": 50, "max": 1200},
                },
                "flow_chains": [["producers", "retail"]],
            },
        },
        {
            "id": "retail",
            "props": {
                "tx_rate": 0.10,
                "equivalent_weights": {"UAH": 1.0},
                "recipient_group_weights": {"anchors": 0.35, "producers": 0.4, "services": 0.25},
                "amount_model": {
                    "UAH": {"p50": 500, "p90": 1300, "min": 50, "max": 1500},
                },
                "flow_chains": [["retail", "producers"], ["retail", "households"]],
            },
        },
        {
            "id": "service",
            "props": {
                "tx_rate": 0.06,
                "equivalent_weights": {"UAH": 1.0},
                "recipient_group_weights": {"households": 0.45, "retail": 0.35, "anchors": 0.2},
                "amount_model": {
                    "UAH": {"p50": 300, "p90": 1000, "min": 50, "max": 1500},
                },
            },
        },
        # --- Household subtypes ---
        {
            "id": "household",
            "props": {
                "tx_rate": 0.09,
                "equivalent_weights": {"UAH": 1.0},
                "recipient_group_weights": {
                    "retail": 0.6,
                    "services": 0.2,
                    "producers": 0.1,
                    "households": 0.1,
                },
                "amount_model": {
                    "UAH": {"p50": 180, "p90": 650, "min": 50, "max": 1500},
                },
                "flow_chains": [["households", "retail"]],
            },
        },
        {
            "id": "household_active",
            "props": {
                "tx_rate": 0.14,
                "equivalent_weights": {"UAH": 1.0},
                "recipient_group_weights": {
                    "retail": 0.55,
                    "services": 0.25,
                    "producers": 0.10,
                    "households": 0.10,
                },
                "amount_model": {
                    "UAH": {"p50": 280, "p90": 900, "min": 50, "max": 1500},
                },
                "flow_chains": [["households", "retail"]],
            },
        },
        {
            "id": "household_frugal",
            "props": {
                "tx_rate": 0.04,
                "equivalent_weights": {"UAH": 1.0},
                "recipient_group_weights": {
                    "retail": 0.65,
                    "services": 0.15,
                    "producers": 0.10,
                    "households": 0.10,
                },
                "amount_model": {
                    "UAH": {"p50": 120, "p90": 400, "min": 50, "max": 1000},
                },
                "flow_chains": [["households", "retail"]],
                "periodicity_factor": 0.5,
            },
        },
        {
            "id": "agent",
            "props": {
                "tx_rate": 0.04,
                "equivalent_weights": {"UAH": 1.0},
                "amount_model": {
                    "UAH": {"p50": 500, "p90": 1300, "min": 50, "max": 1500},
                },
            },
        },
    ]


def _make_seasonal_stress_events() -> list[dict[str, Any]]:
    """Seasonal stress events for realistic-v2 scenarios.

    These model periodic demand fluctuations: weekend markets, quiet periods,
    harvest festivals, and winter lulls.
    """
    return [
        {
            "time": 80000,
            "type": "stress",
            "label": "weekend_market",
            "description": "Weekend market day — increased consumer activity",
            "params": {
                "multiplier": 1.3,
                "duration_ms": 30000,
                "label": "weekend_market",
            },
            "effects": [
                {"op": "mult", "field": "tx_rate", "scope": "group:households", "value": 1.5},
                {"op": "mult", "field": "tx_rate", "scope": "group:retail", "value": 1.3},
            ],
            "metadata": {"duration_ms": 30000},
        },
        {
            "time": 150000,
            "type": "stress",
            "label": "quiet_period",
            "description": "Midweek quiet period — reduced economic activity",
            "params": {
                "multiplier": 0.7,
                "duration_ms": 20000,
                "label": "quiet_period",
            },
            "effects": [
                {"op": "mult", "field": "tx_rate", "scope": "all", "value": 0.7},
            ],
            "metadata": {"duration_ms": 20000},
        },
        {
            "time": 200000,
            "type": "stress",
            "label": "harvest_festival",
            "description": "Harvest festival — peak seasonal demand, producers and retail booming",
            "params": {
                "multiplier": 1.8,
                "duration_ms": 40000,
                "label": "harvest_festival",
            },
            "effects": [
                {"op": "mult", "field": "tx_rate", "scope": "group:producers", "value": 2.0},
                {"op": "mult", "field": "tx_rate", "scope": "group:retail", "value": 1.8},
                {"op": "mult", "field": "tx_rate", "scope": "group:households", "value": 1.5},
            ],
            "metadata": {"duration_ms": 40000},
        },
        {
            "time": 250000,
            "type": "stress",
            "label": "winter_lull",
            "description": "Winter lull — minimal economic activity, people stay home",
            "params": {
                "multiplier": 0.5,
                "duration_ms": 15000,
                "label": "winter_lull",
            },
            "effects": [
                {"op": "mult", "field": "tx_rate", "scope": "all", "value": 0.5},
            ],
            "metadata": {"duration_ms": 15000},
        },
    ]


def _make_settings_realistic_v2() -> dict[str, Any]:
    return {
        "warmup": {"ticks": 50, "floor": 0.1},
        "trust_drift": {
            "enabled": True,
            "growth_rate": 0.05,
            "decay_rate": 0.02,
            "max_growth": 2.0,
            "min_limit_ratio": 0.3,
            "overload_threshold": 0.8,
        },
        "flow": {
            "enabled": True,
            "default_affinity": 0.7,
            "reciprocity_bonus": 0.15,
        },
    }


# Each entry is "this community, run with this behaviour". The community_id is
# the only structural input; everything else on the line is the scenario's own.
SCENARIOS: list[dict[str, Any]] = [
    {
        "scenario_id": "greenfield-village-100-realistic-v2",
        "community_id": "greenfield-village-100",
        # Realistic-v2 is a single-equivalent profile: the HOUR and EUR lines of
        # the description are left out of this scenario, not deleted from it.
        "equivalents": ["UAH"],
    },
    {
        "scenario_id": "riverside-town-50-realistic-v2",
        "community_id": "riverside-town-50",
        "equivalents": ["UAH"],
    },
]


def _scenario_from_community(
    *,
    scenario_id: str,
    community: dict[str, Any],
    equivalents: list[str],
) -> dict[str, Any]:
    active_codes = {eq["code"] for eq in community["equivalents"] if eq["is_active"]}
    unknown = sorted(set(equivalents) - active_codes)
    if unknown:
        raise RuntimeError(
            f"{scenario_id}: equivalents {unknown} are not active in community "
            f"'{community['community_id']}' (active: {sorted(active_codes)})"
        )

    pid_by_ref: dict[str, str] = {}
    scenario_participants: list[dict[str, Any]] = []
    for p in community["participants"]:
        pid_by_ref[p["ref"]] = p["pid"]
        scenario_participants.append(
            {
                "id": p["pid"],
                "name": p["name"],
                "type": p["type"],
                "status": p["status"],
                "groupId": p["group"],
                "behaviorProfileId": _behavior_for_group(p["group"], p["index"]),
            }
        )
    scenario_participants.sort(key=lambda x: x["id"])

    selected = set(equivalents)
    scenario_trustlines: list[dict[str, Any]] = []
    for t in community["trustlines"]:
        if t["equivalent"] not in selected:
            continue
        scenario_trustlines.append(
            {
                "from": pid_by_ref[t["from"]],
                "to": pid_by_ref[t["to"]],
                "limit": t["limit"],
                "equivalent": t["equivalent"],
                "policy": dict(t["policy"]),
            }
        )
    if not scenario_trustlines:
        raise RuntimeError(f"{scenario_id}: no trustline survived the equivalent selection {equivalents}")
    scenario_trustlines.sort(key=lambda x: (x["equivalent"], x["from"], x["to"]))

    # Every declared group, in the order the description declares them. The
    # validator already refuses a group with no participants, so there is
    # nothing here to filter out — and a filter that never filters would only
    # look like a check.
    groups = [{"id": g["id"], "label": g["label"]} for g in community["groups"]]

    return {
        "schema_version": "scenario/1",
        "scenario_id": scenario_id,
        "name": scenario_id,
        "equivalents": list(equivalents),
        "participants": scenario_participants,
        "groups": groups,
        "behaviorProfiles": _make_behavior_profiles_realistic_v2(),
        "trustlines": scenario_trustlines,
        "events": _make_seasonal_stress_events(),
        "settings": _make_settings_realistic_v2(),
    }


def generate(spec: dict[str, Any], *, out_root: Path | None = None) -> Path:
    community = load_community(spec["community_id"], root=ROOT / "seeds" / "communities")
    scenario = _scenario_from_community(
        scenario_id=spec["scenario_id"],
        community=community,
        equivalents=spec["equivalents"],
    )

    base = out_root if out_root is not None else ROOT / "fixtures" / "simulator"
    out_path = base / spec["scenario_id"] / "scenario.json"
    _write_json(out_path, scenario)
    _validate_scenario_shape(out_path)
    return out_path


def main() -> int:
    for spec in SCENARIOS:
        print("Wrote", generate(spec))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
