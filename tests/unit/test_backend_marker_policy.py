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
    assert all(
        not any(
            re.match(r"^(?:python\s+-m\s+)?pytest(?:\s|$)", line.strip())
            for line in path.read_text(encoding="utf-8").splitlines()
        )
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
        if re.match(r"^(?:python\s+-m\s+)?pytest(?:\s|$)", line.strip())
    ]
    assert bare_pytest_commands == []
    assert '-m "not slow"' not in contributor_guide
    assert "slow`/`postgres` tiers" in vocabulary
    assert "explicit `slow` selector" in vocabulary

    english_guide = (_ROOT / "docs" / "en" / "10-testing-framework.md").read_text(
        encoding="utf-8"
    )
    assert "geov0_test_docs_en" in english_guide
    assert 'localhost:5432/geov0"' not in english_guide
    assert "-BackendMarker postgres" in english_guide


def test_active_operational_docs_do_not_bypass_the_canonical_pytest_runner() -> None:
    operational_docs = [
        _ROOT / "docs" / "ru" / "06-contributing.md",
        _ROOT / "docs" / "ru" / "runbook-dev-wsl2-docker-no-desktop.md",
        _ROOT / "docs" / "en" / "06-contributing.md",
        _ROOT / "docs" / "en" / "10-testing-framework.md",
        _ROOT / "docs" / "pl" / "06-contributing.md",
        _ROOT / "docs" / "pl" / "10-testing-framework.md",
    ]
    direct_pytest = re.compile(
        r"^(?:pytest|(?:py|python(?:\.exe)?|.*[\\/]python(?:\.exe)?)\s+-m\s+pytest)(?:\s|$)",
        flags=re.IGNORECASE,
    )

    violations: list[str] = []
    for path in operational_docs:
        in_code_fence = False
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(),
            start=1,
        ):
            stripped = line.strip()
            if stripped.startswith("```"):
                in_code_fence = not in_code_fence
                continue
            inline_command = re.fullmatch(r"-\s*`([^`]+)`", stripped)
            if not in_code_fence and inline_command is None:
                continue
            command = (
                inline_command.group(1).strip()
                if inline_command is not None
                else stripped
            )
            if command.startswith("#"):
                continue
            if direct_pytest.match(command):
                violations.append(f"{path.relative_to(_ROOT)}:{line_number}: {command}")

    assert violations == []

    postgres_docs = [
        _ROOT / "docs" / "ru" / "runbook-dev-wsl2-docker-no-desktop.md",
        _ROOT / "docs" / "en" / "10-testing-framework.md",
    ]
    for path in postgres_docs:
        content = path.read_text(encoding="utf-8")
        database_names = re.findall(
            r"postgresql\+asyncpg://[^\s`\"']+/([A-Za-z0-9_]+)",
            content,
        )
        assert database_names
        assert all(name.startswith("geov0_test_") for name in database_names)
        assert "-BackendMarker postgres" in content
