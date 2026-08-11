import ast
import re
from pathlib import Path

import pytest

from scripts.validate_test_database_url import (
    UnsafeTestDatabaseError,
    assert_safe_test_database_url,
)


_ROOT = Path(__file__).resolve().parents[2]
_ACTIVE_OPERATIONAL_DOCS = (
    _ROOT / "README.md",
    _ROOT / "AGENTS.md",
    _ROOT / "docs" / "ru" / "06-contributing.md",
    _ROOT / "docs" / "ru" / "10-testing-framework.md",
    _ROOT / "docs" / "ru" / "runbook-dev-wsl2-docker-no-desktop.md",
    _ROOT / "docs" / "en" / "06-contributing.md",
    _ROOT / "docs" / "en" / "10-testing-framework.md",
    _ROOT / "docs" / "pl" / "06-contributing.md",
    _ROOT / "docs" / "pl" / "10-testing-framework.md",
)
_POSTGRES_EXAMPLE_DOCS = (
    _ROOT / "docs" / "ru" / "runbook-dev-wsl2-docker-no-desktop.md",
    _ROOT / "docs" / "en" / "10-testing-framework.md",
)


def _iter_documented_commands(path: Path):
    fence_language: str | None = None
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        stripped = line.strip()
        fence = re.match(r"^```\s*([A-Za-z0-9_-]*)", stripped)
        if fence is not None:
            fence_language = (
                None if fence_language is not None else fence.group(1).lower()
            )
            continue
        inline_command = re.fullmatch(r"-\s*`([^`]+)`", stripped)
        if fence_language is None and inline_command is None:
            continue
        command = inline_command.group(1).strip() if inline_command else stripped
        if command and not command.startswith("#"):
            yield line_number, command, fence_language


def _command_executable(command: str) -> tuple[str, str]:
    candidate = command.strip()
    if candidate.startswith("&"):
        candidate = candidate[1:].lstrip()
    token_match = re.match(r"""^("[^"]+"|'[^']+'|\S+)(.*)$""", candidate)
    if token_match is None:
        return "", ""
    executable = token_match.group(1).strip("\"'").replace("\\", "/")
    return executable.rsplit("/", 1)[-1].lower(), token_match.group(2).lstrip()


def _is_direct_pytest_command(command: str) -> bool:
    executable, arguments = _command_executable(command)
    if executable in {"pytest", "pytest.exe"}:
        return True
    if executable in {"py", "py.exe"} or re.fullmatch(
        r"python(?:\d+(?:\.\d+)*)?(?:\.exe)?",
        executable,
    ):
        return re.match(r"^-m\s+pytest(?:\s|$)", arguments, re.IGNORECASE) is not None
    return False


def _shell_contract_violation(command: str, fence_language: str | None) -> bool:
    if re.match(
        r"^(?:TEST_DATABASE_URL|GEO_TEST_ALLOW_DB_RESET)\s*=",
        command,
        re.IGNORECASE,
    ):
        return True
    return bool(
        fence_language in {"bash", "sh", "shell"}
        and re.match(r"^(?:&\s*)?[.\\/]+scripts[\\/]verify_local\.ps1", command)
    )


def _postgres_example_violations(path: Path, content: str) -> list[str]:
    violations: list[str] = []
    created_names = set(
        re.findall(
            r"\bcreatedb\b[^\r\n`]*\b(geov0_test_[A-Za-z0-9_-]*)\b",
            content,
        )
    )
    urls = re.findall(
        r"postgresql\+asyncpg://[^\s`\"']+/([A-Za-z0-9_-]+)",
        content,
    )
    if not urls:
        violations.append(f"{path}: no PostgreSQL test URL")
    for database_name in urls:
        database_url = f"postgresql+asyncpg://geo:geo@localhost:5432/{database_name}"
        try:
            assert_safe_test_database_url(
                database_url,
                allow_destructive_reset="1",
                repo_root=_ROOT,
                required_backend="postgresql",
            )
        except UnsafeTestDatabaseError as exc:
            violations.append(f"{path}: {database_name}: {exc}")
    if created_names != set(urls):
        violations.append(
            f"{path}: created databases {sorted(created_names)} != URL databases "
            f"{sorted(set(urls))}"
        )
    if "-BackendMarker postgres" not in content:
        violations.append(f"{path}: missing -BackendMarker postgres")
    return violations


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
        for path in _ACTIVE_OPERATIONAL_DOCS
    )


def test_stable_contributor_guide_uses_canonical_backend_tiers() -> None:
    contributor_guide = (_ROOT / "docs" / "ru" / "06-contributing.md").read_text(
        encoding="utf-8"
    )
    vocabulary = (_ROOT / "specs" / "001-codebase-renovation" / "tasks.md").read_text(
        encoding="utf-8"
    )

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
    violations: list[str] = []
    for path in _ACTIVE_OPERATIONAL_DOCS:
        for line_number, command, fence_language in _iter_documented_commands(path):
            if _is_direct_pytest_command(command):
                violations.append(f"{path.relative_to(_ROOT)}:{line_number}: {command}")
            if _shell_contract_violation(command, fence_language):
                violations.append(
                    f"{path.relative_to(_ROOT)}:{line_number}: shell mismatch: {command}"
                )

    assert violations == []

    postgres_violations: list[str] = []
    for path in _POSTGRES_EXAMPLE_DOCS:
        content = path.read_text(encoding="utf-8")
        postgres_violations.extend(_postgres_example_violations(path, content))
    assert postgres_violations == []


@pytest.mark.parametrize(
    "command",
    [
        "pytest.exe -q",
        r".\.venv\Scripts\pytest.exe -q",
        r'& ".\.venv\Scripts\python.exe" -m pytest -q',
        r"'C:\Python311\python.exe' -m pytest tests/unit",
        "py -m pytest -q",
        "python3.11 -m pytest -q",
    ],
)
def test_direct_pytest_guard_recognizes_supported_launcher_shapes(command: str) -> None:
    assert _is_direct_pytest_command(command)


@pytest.mark.parametrize(
    ("command", "fence_language"),
    [
        (r".\scripts\verify_local.ps1 -TaskSlug invalid_bash", "bash"),
        ("TEST_DATABASE_URL=postgresql://localhost/geov0_test_shell", "bash"),
        ("GEO_TEST_ALLOW_DB_RESET=1", "powershell"),
    ],
)
def test_shell_guard_rejects_non_executable_documented_commands(
    command: str,
    fence_language: str,
) -> None:
    assert _shell_contract_violation(command, fence_language)


@pytest.mark.parametrize(
    "content",
    [
        """
createdb -U geo geov0_test_
TEST_DATABASE_URL=postgresql+asyncpg://geo:geo@localhost:5432/geov0_test_
verify_local.ps1 -BackendMarker postgres
""",
        """
createdb -U geo geov0_test_created
TEST_DATABASE_URL=postgresql+asyncpg://geo:geo@localhost:5432/geov0_test_other
verify_local.ps1 -BackendMarker postgres
""",
    ],
)
def test_postgres_doc_guard_rejects_invalid_or_mismatched_database_names(
    content: str,
) -> None:
    assert _postgres_example_violations(Path("synthetic.md"), content)
