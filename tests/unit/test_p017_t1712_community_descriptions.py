"""The community description is the one roster, and it says what it means.

Programme 017, `T1712`. Three things are asserted here, and each of them is a
thing that used to be true only by accident:

1. The descriptions under ``seeds/communities/`` still carry exactly the v2
   topology that lived in ``admin-fixtures/tools/generate_seed_*_v2.py``
   (100 participants / 523 trustlines and 50 / 316). While those generators are
   still in the tree this is a real comparison against them; it is what makes
   their later deletion a deletion and not a loss.
2. The committed simulator scenarios are generated from the descriptions, byte
   for byte, and generating twice gives the same bytes. Nobody keeps a second
   copy of the same people by hand.
3. The validator refuses descriptions that lie. A validator that accepts
   everything would make the first two assertions worthless (AGENTS.md §9,
   anti-vacuum), so every rule it owns is shown rejecting a real violation.
"""

from __future__ import annotations

import copy
import filecmp
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
COMMUNITIES_DIR = REPO_ROOT / "seeds" / "communities"
TOOLS_DIR = REPO_ROOT / "admin-fixtures" / "tools"
SCENARIOS_DIR = REPO_ROOT / "fixtures" / "simulator"

if str(COMMUNITIES_DIR) not in sys.path:
    sys.path.insert(0, str(COMMUNITIES_DIR))

from community_schema import (  # noqa: E402
    CommunityError,
    load_community,
    validate_community,
)


# The topology carried forward is v2, measured on 2026-09-21 and re-measured by
# `test_the_description_still_equals_the_v2_generator_topology` below. If a
# number here moves, the description changed: update the constant *and* say why.
EXPECTED: dict[str, dict[str, Any]] = {
    "greenfield-village-100": {
        "participants": 100,
        "trustlines": 523,
        "by_equivalent": {"UAH": 432, "HOUR": 87, "EUR": 4},
        "groups": {
            "anchors": 10,
            "producers": 25,
            "retail": 10,
            "services": 15,
            "households": 35,
            "agents": 5,
        },
        "households_the_name_rule_misses": 13,
        "generator": "generate_seed_greenfield_village_100_v2.py",
        "scenario_id": "greenfield-village-100-realistic-v2",
    },
    "riverside-town-50": {
        "participants": 50,
        "trustlines": 316,
        "by_equivalent": {"UAH": 218, "HOUR": 96, "EUR": 2},
        "groups": {
            "anchors": 5,
            "producers": 10,
            "retail": 8,
            "services": 10,
            "households": 15,
            "agents": 2,
        },
        "households_the_name_rule_misses": 0,
        "generator": "generate_seed_riverside_town_50_v2.py",
        "scenario_id": "riverside-town-50-realistic-v2",
    },
}

COMMUNITY_IDS = sorted(EXPECTED)


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _scenario_generator():
    return _load_module(REPO_ROOT / "scripts" / "generate_simulator_seed_scenarios.py", "_t1712_scenario_generator")


@pytest.fixture(scope="module")
def descriptions() -> dict[str, dict[str, Any]]:
    return {cid: load_community(cid, root=COMMUNITIES_DIR) for cid in COMMUNITY_IDS}


@pytest.mark.parametrize("community_id", COMMUNITY_IDS)
def test_the_committed_description_is_valid(community_id: str, descriptions) -> None:
    doc = descriptions[community_id]
    assert doc["community_id"] == community_id


@pytest.mark.parametrize("community_id", COMMUNITY_IDS)
def test_the_description_carries_the_expected_v2_counts(community_id: str, descriptions) -> None:
    doc = descriptions[community_id]
    expected = EXPECTED[community_id]

    assert len(doc["participants"]) == expected["participants"]
    assert len(doc["trustlines"]) == expected["trustlines"]

    by_equivalent: dict[str, int] = {}
    for t in doc["trustlines"]:
        by_equivalent[t["equivalent"]] = by_equivalent.get(t["equivalent"], 0) + 1
    assert by_equivalent == expected["by_equivalent"]

    by_group: dict[str, int] = {}
    for p in doc["participants"]:
        by_group[p["group"]] = by_group.get(p["group"], 0) + 1
    assert by_group == expected["groups"]


@pytest.mark.parametrize("community_id", COMMUNITY_IDS)
def test_group_membership_is_written_down_and_not_inferred(community_id: str, descriptions) -> None:
    """The old rule read the group off a name substring or a PID index range.

    Both are now gone from the reader's side, so the description has to be able
    to disagree with them. This asserts the *explicit* field is what carries the
    meaning: every participant names a declared group, and a household is a
    household because the description says so, not because its name ends in
    "(Household)".
    """
    doc = descriptions[community_id]
    declared = {g["id"] for g in doc["groups"]}

    for p in doc["participants"]:
        assert p["group"] in declared, p

    households = {p["ref"] for p in doc["participants"] if p["group"] == "households"}
    named_household = {p["ref"] for p in doc["participants"] if "(Household)" in p["name"]}
    assert named_household <= households

    # Measured, not assumed. In Greenfield the substring rule under-counts by 13
    # people - the odd-jobs and home-work neighbours the seed document also files
    # under households - so there the explicit field carries meaning the old rule
    # could not. In Riverside every household happens to be named "(Household)",
    # the rule coincides, and this number is 0: that is why the guard is pinned
    # to a measured constant instead of a strict-subset assertion that would be
    # false for one of the two communities.
    assert len(households - named_household) == EXPECTED[community_id]["households_the_name_rule_misses"]


@pytest.mark.parametrize("community_id", COMMUNITY_IDS)
def test_limits_are_decimal_strings_and_never_floats(community_id: str) -> None:
    """Money stays out of binary floating point (AGENTS.md §8).

    The validator enforces this, so this test does not go through the validator:
    it reads the raw file and refuses to parse a JSON float at all.
    """

    def _no_floats(raw: str) -> float:  # pragma: no cover - only runs on failure
        raise AssertionError(f"community.json contains a JSON float: {raw!r}")

    text = (COMMUNITIES_DIR / community_id / "community.json").read_text(encoding="utf-8")
    doc = json.loads(text, parse_float=_no_floats)

    precision = {eq["code"]: eq["precision"] for eq in doc["equivalents"]}
    for t in doc["trustlines"]:
        assert isinstance(t["limit"], str)
        assert re.fullmatch(r"(0|[1-9][0-9]*)(\.[0-9]+)?", t["limit"]), t
        assert len(t["limit"].partition(".")[2]) <= precision[t["equivalent"]], t


@pytest.mark.parametrize("community_id", COMMUNITY_IDS)
def test_the_description_holds_no_invented_result(community_id: str, descriptions) -> None:
    """Debts, balances and history are produced by operations, not written here.

    The v2 fixtures carried `used = limit × ((n % 17) + 1) / 20` with hand-placed
    93 % bottlenecks, an `available` derived from it and a `created_at` spread
    over 90 days. None of that survives the extraction.
    """
    doc = descriptions[community_id]
    forbidden = {"used", "available", "created_at", "debt", "balance", "from_display_name", "to_display_name"}
    for t in doc["trustlines"]:
        assert not (forbidden & set(t)), t
    assert "debts" not in doc
    assert "transactions" not in doc


@pytest.mark.parametrize("community_id", COMMUNITY_IDS)
def test_the_v2_routing_and_clearing_policy_is_what_the_seed_documents_claim(
    community_id: str, descriptions
) -> None:
    """v2 rewrote policy on the UAH lines only, and the rule is about the creditor.

    The v2 seed documents used to say `can_be_intermediate` was true "only for
    business <-> business". It never was: the router applies the policy of the
    creditor line to the payment-flow edge, so a `business -> person` line is
    exactly what lets a `person -> business` payment route through that business.
    This pins the rule the documents now state, in both halves - what v2 set and
    what it deliberately did not touch.
    """
    doc = descriptions[community_id]
    type_by_ref = {p["ref"]: p["type"] for p in doc["participants"]}

    uah = [t for t in doc["trustlines"] if t["equivalent"] == "UAH"]
    assert uah, "the UAH half of the rule needs UAH lines to be about"
    for t in uah:
        assert t["policy"]["auto_clearing"] is True, t
        assert t["policy"]["can_be_intermediate"] is (type_by_ref[t["from"]] == "business"), t

    # The other half: v2 left HOUR and EUR alone, so they are *not* clearing-first
    # and they do carry person creditors allowed to be intermediates. If that ever
    # stops being true it is a change in the carried-forward topology, not noise.
    other = [t for t in doc["trustlines"] if t["equivalent"] != "UAH"]
    assert other, "the untouched half needs non-UAH lines to be about"
    assert any(t["policy"]["auto_clearing"] is False for t in other)
    assert any(
        t["policy"]["can_be_intermediate"] and type_by_ref[t["from"]] == "person" for t in other
    )


# --- The description still equals the v2 topology it was extracted from -------
#
# This is a bridge assertion and it dies with the generators it reads. Until
# they are deleted it is the evidence that the extraction lost nothing.


@pytest.mark.parametrize("community_id", COMMUNITY_IDS)
def test_the_description_still_equals_the_v2_generator_topology(community_id: str, descriptions) -> None:
    generator = _load_module(TOOLS_DIR / EXPECTED[community_id]["generator"], f"_t1712_v2_{community_id}")

    participants = generator.build_participants()
    trustlines = generator.build_trustlines(participants)

    doc = descriptions[community_id]
    pid_by_ref = {p["ref"]: p["pid"] for p in doc["participants"]}

    described_roster = {(p["pid"], p["name"], p["type"], p["status"]) for p in doc["participants"]}
    generated_roster = {(p.pid, p.display_name, p.type, p.status) for p in participants}
    assert described_roster == generated_roster

    def _key(equivalent: str, from_pid: str, to_pid: str, limit: str, status: str, policy: dict) -> tuple:
        return (
            equivalent,
            from_pid,
            to_pid,
            limit,
            status,
            bool(policy.get("auto_clearing", False)),
            bool(policy.get("can_be_intermediate", False)),
        )

    described_lines = {
        _key(t["equivalent"], pid_by_ref[t["from"]], pid_by_ref[t["to"]], t["limit"], t["status"], t["policy"])
        for t in doc["trustlines"]
    }
    generated_lines = {
        _key(
            t["equivalent"],
            t["from"],
            t["to"],
            str(t["limit"]),
            str(t.get("status") or "active"),
            t.get("policy") or {},
        )
        for t in trustlines
    }
    assert described_lines == generated_lines


@pytest.mark.parametrize("community_id", COMMUNITY_IDS)
def test_the_extraction_bridge_still_reproduces_the_committed_description(community_id: str) -> None:
    """Re-running the bridge must rewrite the same bytes, or it has rotted.

    Dies with the generators, like the assertion above it.
    """
    extractor = _load_module(TOOLS_DIR / "extract_community_description.py", "_t1712_extractor")

    produced = extractor.build_description(community_id)
    committed = json.loads((COMMUNITIES_DIR / community_id / "community.json").read_text(encoding="utf-8"))
    assert produced == committed


# --- The simulator scenario is generated from the description -----------------


@pytest.mark.parametrize("community_id", COMMUNITY_IDS)
def test_the_committed_scenario_regenerates_from_the_description(community_id: str, tmp_path: Path) -> None:
    generator = _scenario_generator()
    spec = next(s for s in generator.SCENARIOS if s["community_id"] == community_id)

    produced = generator.generate(spec, out_root=tmp_path)
    committed = SCENARIOS_DIR / EXPECTED[community_id]["scenario_id"] / "scenario.json"

    assert produced.read_bytes() == committed.read_bytes(), (
        f"{committed} is no longer what the description produces; "
        "regenerate with ./.venv/Scripts/python.exe scripts/generate_simulator_seed_scenarios.py"
    )


@pytest.mark.parametrize("community_id", COMMUNITY_IDS)
def test_generation_is_deterministic(community_id: str, tmp_path: Path) -> None:
    generator = _scenario_generator()
    spec = next(s for s in generator.SCENARIOS if s["community_id"] == community_id)

    first = generator.generate(spec, out_root=tmp_path / "first")
    second = generator.generate(spec, out_root=tmp_path / "second")

    assert filecmp.cmp(first, second, shallow=False)


@pytest.mark.parametrize("community_id", COMMUNITY_IDS)
def test_the_scenario_keeps_no_second_roster(community_id: str, descriptions) -> None:
    """Whoever the description says exists is exactly who the scenario runs."""
    doc = descriptions[community_id]
    scenario = json.loads(
        (SCENARIOS_DIR / EXPECTED[community_id]["scenario_id"] / "scenario.json").read_text(encoding="utf-8")
    )

    described = {(p["pid"], p["name"], p["type"], p["status"], p["group"]) for p in doc["participants"]}
    in_scenario = {(p["id"], p["name"], p["type"], p["status"], p["groupId"]) for p in scenario["participants"]}
    assert described == in_scenario

    declared_groups = {g["id"]: g["label"] for g in doc["groups"]}
    assert {g["id"]: g["label"] for g in scenario["groups"]} == declared_groups


def test_the_scenario_generator_refuses_an_equivalent_the_community_does_not_have(tmp_path: Path) -> None:
    """Anti-vacuum for the scenario's own equivalent selection."""
    generator = _scenario_generator()
    spec = dict(generator.SCENARIOS[0])
    spec["equivalents"] = ["XTS"]

    with pytest.raises(RuntimeError, match="not active in community"):
        generator.generate(spec, out_root=tmp_path)


def test_the_scenario_generator_refuses_a_group_it_has_no_behaviour_for(descriptions) -> None:
    """A group the scenario cannot model must stop the run, not borrow a profile."""
    generator = _scenario_generator()
    doc = copy.deepcopy(descriptions["riverside-town-50"])
    doc["groups"].append({"id": "pirates", "label": "Pirates", "description": "unmodelled"})
    doc["participants"][0]["group"] = "pirates"

    with pytest.raises(RuntimeError, match="No realistic-v2 behaviour profile for group 'pirates'"):
        generator._scenario_from_community(
            scenario_id="unmodelled-group",
            community=doc,
            equivalents=["UAH"],
        )


def test_the_scenario_generator_refuses_a_selection_that_empties_the_graph(descriptions) -> None:
    """A scenario with no trustline is a dead run, and the schema would accept it.

    `scenario.schema.json` sets `trustlines.minItems: 0`, so nothing downstream
    would complain. The path is reachable: a description may declare an active
    equivalent that no line uses, and a scenario may then select exactly that one.
    """
    generator = _scenario_generator()
    doc = copy.deepcopy(descriptions["riverside-town-50"])
    doc["equivalents"].append(
        {"code": "XTS", "precision": 2, "description": "Test code with no lines", "is_active": True}
    )

    with pytest.raises(RuntimeError, match="no trustline survived"):
        generator._scenario_from_community(
            scenario_id="empty-selection",
            community=doc,
            equivalents=["XTS"],
        )


# --- The validator rejects descriptions that lie ------------------------------


def _mutate(doc: dict[str, Any], mutation) -> dict[str, Any]:
    clone = copy.deepcopy(doc)
    mutation(clone)
    return clone


def _drop_key(container: str, key: str):
    def apply(doc: dict[str, Any]) -> None:
        doc[container][0].pop(key)

    return apply


MUTATIONS: list[tuple[str, Any, str]] = [
    (
        "float limit instead of a decimal string",
        lambda d: d["trustlines"][0].__setitem__("limit", 1234.56),
        "must be a decimal string",
    ),
    (
        "integer limit instead of a decimal string",
        lambda d: d["trustlines"][0].__setitem__("limit", 1234),
        "must be a decimal string",
    ),
    (
        "limit with more digits than the equivalent's precision",
        lambda d: d["trustlines"][0].__setitem__("limit", "1234.5678"),
        "fractional digits",
    ),
    (
        "zero limit",
        lambda d: d["trustlines"][0].__setitem__("limit", "0"),
        "must be positive",
    ),
    (
        "trustline pointing at a participant that does not exist",
        lambda d: d["trustlines"][0].__setitem__("to", "nobody_at_all"),
        "is not a participant ref",
    ),
    (
        "self-loop trustline",
        lambda d: d["trustlines"][0].__setitem__("to", d["trustlines"][0]["from"]),
        "self-loop",
    ),
    (
        "duplicate (equivalent, from, to)",
        lambda d: d["trustlines"].append(copy.deepcopy(d["trustlines"][0])),
        "duplicates trustline",
    ),
    (
        "trustline in an equivalent that was never declared",
        lambda d: d["trustlines"][0].__setitem__("equivalent", "XTS"),
        "not a declared equivalent",
    ),
    (
        "trustline in an equivalent declared inactive",
        lambda d: (
            d["equivalents"][0].__setitem__("is_active", False),
            d["trustlines"].__setitem__(
                0, {**d["trustlines"][0], "equivalent": d["equivalents"][0]["code"]}
            ),
        ),
        "declared inactive",
    ),
    (
        "policy silently missing a decision",
        lambda d: d["trustlines"][0]["policy"].pop("can_be_intermediate"),
        "missing keys",
    ),
    (
        "policy carrying a non-boolean decision",
        lambda d: d["trustlines"][0]["policy"].__setitem__("auto_clearing", "yes"),
        "must be a boolean",
    ),
    (
        "participant in a group nobody declared",
        lambda d: d["participants"][0].__setitem__("group", "mystery"),
        "is not a declared group",
    ),
    (
        "group declared but left empty",
        lambda d: d["groups"].append({"id": "ghosts", "label": "Ghosts", "description": "nobody"}),
        "have no participants",
    ),
    (
        "two participants sharing one symbolic ref",
        lambda d: d["participants"][1].__setitem__("ref", d["participants"][0]["ref"]),
        "is used twice",
    ),
    (
        "two participants sharing one pid",
        lambda d: d["participants"][1].__setitem__("pid", d["participants"][0]["pid"]),
        "is used twice",
    ),
    (
        "a hole in the roster numbering",
        lambda d: d["participants"][1].__setitem__("index", len(d["participants"]) + 7),
        "without gaps",
    ),
    (
        "equivalent without a precision",
        _drop_key("equivalents", "precision"),
        "missing keys",
    ),
    (
        "equivalent with a precision outside the storage scale",
        lambda d: d["equivalents"][0].__setitem__("precision", 19),
        "outside 0..18",
    ),
    (
        "equivalent with a fractional precision",
        lambda d: d["equivalents"][0].__setitem__("precision", 2.5),
        "must be an integer",
    ),
    (
        "participant type the simulator cannot run",
        lambda d: d["participants"][0].__setitem__("type", "robot"),
        "not in",
    ),
    (
        "an extra top-level key nobody reads",
        lambda d: d.__setitem__("debts", []),
        "unknown top-level keys",
    ),
    (
        "an extra trustline key nobody reads",
        lambda d: d["trustlines"][0].__setitem__("used", "100.00"),
        "unknown keys",
    ),
    (
        "a description of a schema version we do not speak",
        lambda d: d.__setitem__("schema_version", "community/999"),
        "schema_version",
    ),
]


@pytest.mark.parametrize("what,mutation,message", MUTATIONS, ids=[m[0] for m in MUTATIONS])
def test_the_validator_rejects_a_description_that_lies(what: str, mutation, message: str, descriptions) -> None:
    doc = descriptions["riverside-town-50"]

    # Anti-vacuum: the unmutated document must pass, or the rejection below
    # would prove nothing about the rule under test.
    validate_community(copy.deepcopy(doc), source="control")

    with pytest.raises(CommunityError, match=re.escape(message)):
        validate_community(_mutate(doc, mutation), source=f"mutated:{what}")
