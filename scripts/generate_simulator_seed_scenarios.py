from __future__ import annotations

import json
import re
import sys
import importlib.util
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


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


def _pid_index(pid: str) -> int | None:
    m = re.match(r"^PID_U(\d{4})_", pid)
    if not m:
        return None
    return int(m.group(1))


def _group_for_seed(seed_id: str, pid: str) -> str | None:
    if seed_id.endswith("-realistic-v2"):
        seed_id = seed_id[: -len("-realistic-v2")]
    if seed_id.endswith("-v2"):
        seed_id = seed_id[: -len("-v2")]

    idx = _pid_index(pid)
    if idx is None:
        return None

    if seed_id == "greenfield-village-100":
        if 1 <= idx <= 10:
            return "anchors"
        if 11 <= idx <= 35:
            return "producers"
        if 36 <= idx <= 45:
            return "retail"
        if 46 <= idx <= 60:
            return "services"
        if 61 <= idx <= 95:
            return "households"
        if 96 <= idx <= 100:
            return "agents"
        return None

    if seed_id == "riverside-town-50":
        if 1 <= idx <= 5:
            return "anchors"
        if 6 <= idx <= 15:
            return "producers"
        if 16 <= idx <= 23:
            return "retail"
        if 24 <= idx <= 33:
            return "services"
        if 34 <= idx <= 48:
            return "households"
        if 49 <= idx <= 50:
            return "agents"
        return None

    raise ValueError(f"Unknown seed_id: {seed_id}")


def _behavior_for_group(group_id: str, participant_index: int = 0) -> str:
    """Map group to behavior profile, with deterministic subtypes for large groups.

    ``participant_index`` is the numeric index extracted from PID (via
    ``_pid_index``).  It drives a deterministic assignment of subtypes so that
    the same seed always produces the same profile distribution.
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

    return {
        "anchors": "anchor_hub",
        "retail": "retail",
        "services": "service",
        "agents": "agent",
    }[group_id]


def _make_groups() -> list[dict[str, Any]]:
    return [
        {"id": "anchors", "label": "Anchors"},
        {"id": "producers", "label": "Producers"},
        {"id": "retail", "label": "Retail"},
        {"id": "services", "label": "Services"},
        {"id": "households", "label": "Households"},
        {"id": "agents", "label": "Agents"},
    ]


def _make_behavior_profiles() -> list[dict[str, Any]]:
    return [
        {"id": "anchor_hub"},
        {"id": "producer"},
        {"id": "producer_large"},
        {"id": "producer_small"},
        {"id": "retail"},
        {"id": "service"},
        {"id": "household"},
        {"id": "household_active"},
        {"id": "household_frugal"},
        {"id": "agent"},
    ]


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


def _equivalents_from(trustlines: Iterable[dict[str, Any]], eq_defs: Iterable[dict[str, Any]]) -> list[str]:
    active = {e["code"] for e in eq_defs if e.get("is_active") is True}
    used = {t.get("equivalent") for t in trustlines if t.get("equivalent")}
    out = sorted([e for e in used if e in active])
    if not out:
        raise RuntimeError("No active equivalents used by trustlines")
    return out


def _convert_to_scenario(*, seed_id: str, participants: list[dict[str, Any]], trustlines: list[dict[str, Any]], eq_defs: list[dict[str, Any]]) -> dict[str, Any]:
    scenario_participants: list[dict[str, Any]] = []
    for p in participants:
        pid = p["pid"]
        group_id = _group_for_seed(seed_id, pid)

        participant: dict[str, Any] = {
            "id": pid,
            "name": p.get("display_name"),
            "type": p.get("type"),
        }
        if p.get("status") is not None:
            participant["status"] = p.get("status")
        if group_id is not None:
            participant["groupId"] = group_id
            pidx = _pid_index(pid) or 0
            participant["behaviorProfileId"] = _behavior_for_group(group_id, pidx)

        scenario_participants.append(participant)

    scenario_participants.sort(key=lambda x: x["id"])

    allowed_status = {"active", "frozen"}
    scenario_trustlines: list[dict[str, Any]] = []
    for t in trustlines:
        if t.get("status") and t.get("status") not in allowed_status:
            continue

        out_t: dict[str, Any] = {
            "from": t["from"],
            "to": t["to"],
            "limit": t["limit"],
        }
        if t.get("equivalent"):
            out_t["equivalent"] = t["equivalent"]
        if isinstance(t.get("policy"), dict) and t["policy"]:
            out_t["policy"] = t["policy"]
        scenario_trustlines.append(out_t)

    scenario_trustlines.sort(key=lambda x: (x.get("equivalent") or "", x["from"], x["to"]))

    equivalents = _equivalents_from(trustlines, eq_defs)

    return {
        "schema_version": "scenario/1",
        "scenario_id": seed_id,
        "name": seed_id,
        "equivalents": equivalents,
        "participants": scenario_participants,
        "groups": _make_groups(),
        "behaviorProfiles": _make_behavior_profiles(),
        "trustlines": scenario_trustlines,
        "events": [],
    }


def _assert_basic_integrity(scenario: dict[str, Any]) -> None:
    pids = {p["id"] for p in scenario.get("participants", [])}
    for t in scenario.get("trustlines", []):
        if t["from"] not in pids:
            raise RuntimeError(f"trustline.from not in participants: {t['from']}")
        if t["to"] not in pids:
            raise RuntimeError(f"trustline.to not in participants: {t['to']}")


def _generate_greenfield() -> None:
    datasets = ROOT / "admin-fixtures" / "v1" / "datasets"
    participants = _read_json(datasets / "participants.json")
    trustlines = _read_json(datasets / "trustlines.json")
    eq_defs = _read_json(datasets / "equivalents.json")

    scenario = _convert_to_scenario(
        seed_id="greenfield-village-100",
        participants=participants,
        trustlines=trustlines,
        eq_defs=eq_defs,
    )
    _assert_basic_integrity(scenario)

    out_path = ROOT / "fixtures" / "simulator" / "greenfield-village-100" / "scenario.json"
    _write_json(out_path, scenario)
    _validate_scenario_shape(out_path)
    print("Wrote", out_path)
    print(" greenfield participants", len(scenario["participants"]))
    print(" greenfield trustlines", len(scenario["trustlines"]))
    print(" greenfield equivalents", scenario["equivalents"])


def _seed_module_by_path(path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load module spec for {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _scenario_from_seed_module(*, seed_id: str, seed_module) -> dict[str, Any]:
    participants_objs = seed_module.build_participants()
    trustlines = seed_module.build_trustlines(participants_objs)

    eq_defs = list(getattr(seed_module, "EQUIVALENTS", []))
    if not eq_defs:
        raise RuntimeError(f"Seed module has no EQUIVALENTS: {seed_module}")

    participants = [
        {
            "pid": p.pid,
            "display_name": p.display_name,
            "type": p.type,
            "status": p.status,
        }
        for p in participants_objs
    ]

    scenario = _convert_to_scenario(
        seed_id=seed_id,
        participants=participants,
        trustlines=trustlines,
        eq_defs=eq_defs,
    )
    _assert_basic_integrity(scenario)
    return scenario


def _generate_greenfield_v2() -> None:
    tools_dir = ROOT / "admin-fixtures" / "tools"
    seed_path = tools_dir / "generate_seed_greenfield_village_100_v2.py"
    seed = _seed_module_by_path(seed_path, "_seed_greenfield_village_100_v2")

    scenario = _scenario_from_seed_module(seed_id="greenfield-village-100-v2", seed_module=seed)

    out_path = ROOT / "fixtures" / "simulator" / "greenfield-village-100-v2" / "scenario.json"
    _write_json(out_path, scenario)
    _validate_scenario_shape(out_path)
    print("Wrote", out_path)
    print(" greenfield-v2 participants", len(scenario["participants"]))
    print(" greenfield-v2 trustlines", len(scenario["trustlines"]))
    print(" greenfield-v2 equivalents", scenario["equivalents"])


def _generate_riverside() -> None:
    tools_dir = ROOT / "admin-fixtures" / "tools"
    seed_path = tools_dir / "generate_seed_riverside_town_50.py"
    seed = _seed_module_by_path(seed_path, "_seed_riverside_town_50")

    scenario = _scenario_from_seed_module(seed_id="riverside-town-50", seed_module=seed)

    out_path = ROOT / "fixtures" / "simulator" / "riverside-town-50" / "scenario.json"
    _write_json(out_path, scenario)
    _validate_scenario_shape(out_path)
    print("Wrote", out_path)
    print(" riverside participants", len(scenario["participants"]))
    print(" riverside trustlines", len(scenario["trustlines"]))
    print(" riverside equivalents", scenario["equivalents"])


def _generate_riverside_v2() -> None:
    tools_dir = ROOT / "admin-fixtures" / "tools"
    seed_path = tools_dir / "generate_seed_riverside_town_50_v2.py"
    seed = _seed_module_by_path(seed_path, "_seed_riverside_town_50_v2")

    scenario = _scenario_from_seed_module(seed_id="riverside-town-50-v2", seed_module=seed)

    out_path = ROOT / "fixtures" / "simulator" / "riverside-town-50-v2" / "scenario.json"
    _write_json(out_path, scenario)
    _validate_scenario_shape(out_path)
    print("Wrote", out_path)
    print(" riverside-v2 participants", len(scenario["participants"]))
    print(" riverside-v2 trustlines", len(scenario["trustlines"]))
    print(" riverside-v2 equivalents", scenario["equivalents"])


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


def _generate_riverside_realistic_v2() -> None:
    tools_dir = ROOT / "admin-fixtures" / "tools"
    seed_path = tools_dir / "generate_seed_riverside_town_50_v2.py"
    seed = _seed_module_by_path(seed_path, "_seed_riverside_town_50_v2_realistic")

    scenario = _scenario_from_seed_module(seed_id="riverside-town-50-realistic-v2", seed_module=seed)
    scenario["name"] = "riverside-town-50-realistic-v2"
    scenario["equivalents"] = ["UAH"]
    # Drop non-UAH trustlines for the realistic-v2 profile.
    scenario["trustlines"] = [t for t in scenario.get("trustlines", []) if (t.get("equivalent") or "UAH") == "UAH"]
    scenario["behaviorProfiles"] = _make_behavior_profiles_realistic_v2()
    scenario["settings"] = {
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
    # Add seasonal stress events (appended to any existing events from seed)
    scenario.setdefault("events", []).extend(_make_seasonal_stress_events())

    out_path = ROOT / "fixtures" / "simulator" / "riverside-town-50-realistic-v2" / "scenario.json"
    _write_json(out_path, scenario)
    _validate_scenario_shape(out_path)
    print("Wrote", out_path)


def _generate_greenfield_realistic_v2() -> None:
    tools_dir = ROOT / "admin-fixtures" / "tools"
    seed_path = tools_dir / "generate_seed_greenfield_village_100_v2.py"
    seed = _seed_module_by_path(seed_path, "_seed_greenfield_village_100_v2_realistic")

    scenario = _scenario_from_seed_module(seed_id="greenfield-village-100-realistic-v2", seed_module=seed)
    scenario["name"] = "greenfield-village-100-realistic-v2"
    scenario["equivalents"] = ["UAH"]
    # Drop non-UAH trustlines for the realistic-v2 profile.
    scenario["trustlines"] = [t for t in scenario.get("trustlines", []) if (t.get("equivalent") or "UAH") == "UAH"]
    scenario["behaviorProfiles"] = _make_behavior_profiles_realistic_v2()
    scenario["settings"] = {
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
    # Add seasonal stress events (appended to any existing events from seed)
    scenario.setdefault("events", []).extend(_make_seasonal_stress_events())

    out_path = ROOT / "fixtures" / "simulator" / "greenfield-village-100-realistic-v2" / "scenario.json"
    _write_json(out_path, scenario)
    _validate_scenario_shape(out_path)
    print("Wrote", out_path)


def main() -> int:
    _generate_greenfield()
    _generate_greenfield_v2()
    _generate_greenfield_realistic_v2()
    _generate_riverside()
    _generate_riverside_v2()
    _generate_riverside_realistic_v2()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
