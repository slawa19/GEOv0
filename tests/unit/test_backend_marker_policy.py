import ast
import re
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[2]


def test_backend_e2e_is_not_a_registered_or_filtered_empty_tier() -> None:
    pytest_config = (_ROOT / "pytest.ini").read_text(encoding="utf-8")
    verifier = (_ROOT / "scripts" / "verify_local.ps1").read_text(encoding="utf-8")
    e2e_markers: list[Path] = []
    for path in sorted((_ROOT / "tests").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and node.attr == "e2e"
                and isinstance(node.value, ast.Attribute)
                and node.value.attr == "mark"
                and isinstance(node.value.value, ast.Name)
                and node.value.value.id == "pytest"
            ):
                e2e_markers.append(path.relative_to(_ROOT))

    assert "e2e:" not in pytest_config
    assert e2e_markers == []
    assert "not slow and not postgres" in verifier
    assert "not e2e" not in verifier


def test_backend_scenario_is_not_a_registered_empty_tier() -> None:
    pytest_config = (_ROOT / "pytest.ini").read_text(encoding="utf-8")
    active_testing_docs = [
        _ROOT / "docs" / "en" / "10-testing-framework.md",
        _ROOT / "docs" / "pl" / "10-testing-framework.md",
    ]
    scenario_markers: list[Path] = []
    for path in sorted((_ROOT / "tests").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and node.attr == "scenario"
                and isinstance(node.value, ast.Attribute)
                and node.value.attr == "mark"
                and isinstance(node.value.value, ast.Name)
                and node.value.value.id == "pytest"
            ):
                scenario_markers.append(path.relative_to(_ROOT))

    assert re.search(r"(?m)^\s*scenario(?:\([^)]*\))?\s*:", pytest_config) is None
    assert scenario_markers == []
    assert all(
        "pytest.mark.scenario" not in path.read_text(encoding="utf-8")
        for path in active_testing_docs
    )


def test_stable_contributor_guide_uses_canonical_backend_tiers() -> None:
    contributor_guide = (_ROOT / "docs" / "ru" / "06-contributing.md").read_text(
        encoding="utf-8"
    )
    vocabulary = (
        _ROOT / "specs" / "001-codebase-renovation" / "tasks.md"
    ).read_text(encoding="utf-8")

    bare_pytest_commands = [
        line.strip()
        for line in contributor_guide.splitlines()
        if re.match(r"^pytest(?:\s|$)", line.strip())
    ]
    assert bare_pytest_commands == []
    assert '-m "not slow"' not in contributor_guide
    assert "slow`/`postgres` tiers" in vocabulary
    assert "explicit `slow` selector" in vocabulary
