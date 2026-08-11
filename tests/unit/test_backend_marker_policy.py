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
    _ROOT / "docs" / "ru" / "testing" / "quick-start-and-debugging.md",
    _ROOT / "docs" / "en" / "06-contributing.md",
    _ROOT / "docs" / "en" / "10-testing-framework.md",
    _ROOT / "docs" / "pl" / "06-contributing.md",
    _ROOT / "docs" / "pl" / "10-testing-framework.md",
)
_POSTGRES_EXAMPLE_DOCS = (
    _ROOT / "README.md",
    _ROOT / "AGENTS.md",
    _ROOT / "docs" / "ru" / "10-testing-framework.md",
    _ROOT / "docs" / "ru" / "runbook-dev-wsl2-docker-no-desktop.md",
    _ROOT / "docs" / "en" / "10-testing-framework.md",
)
_CONTRIBUTOR_GUIDES = (
    _ROOT / "docs" / "ru" / "06-contributing.md",
    _ROOT / "docs" / "en" / "06-contributing.md",
    _ROOT / "docs" / "pl" / "06-contributing.md",
)


def _iter_documented_commands_from_content(content: str):
    fence_language: str | None = None
    for line_number, line in enumerate(
        content.splitlines(),
        start=1,
    ):
        stripped = line.strip()
        fence = re.match(r"^```\s*([A-Za-z0-9_-]*)", stripped)
        if fence is not None:
            fence_language = (
                None if fence_language is not None else fence.group(1).lower()
            )
            continue
        inline_commands = re.findall(r"`([^`]+)`", stripped)
        has_command_role = bool(
            re.match(r"^(?:[-*+]|\d+[.)])\s*`", stripped)
            or re.search(
                r"(?<![-./])\b(?:run|execute|command|запуст\w*|выполн\w*|команд\w*|"
                r"uruchom\w*|polecen\w*)\b",
                stripped,
                re.IGNORECASE,
            )
        )
        explicitly_debug_only = bool(
            re.search(r"debug[- ]path|отладочн\w*|диагностическ\w*", stripped, re.I)
        )
        indented_command = line.startswith(("    ", "\t"))
        if fence_language is None and not indented_command:
            if not has_command_role or explicitly_debug_only:
                continue
            for command in inline_commands:
                if command.strip():
                    yield line_number, command.strip(), None
            continue
        command = stripped
        if command and not command.startswith("#"):
            yield line_number, command, fence_language


def _iter_documented_commands(path: Path):
    yield from _iter_documented_commands_from_content(path.read_text(encoding="utf-8"))


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
    if fence_language in {"bash", "sh", "shell"} and re.match(
        r"^\$env:(?:TEST_DATABASE_URL|GEO_TEST_ALLOW_DB_RESET)\s*=",
        command,
        re.IGNORECASE,
    ):
        return True
    if fence_language in {"bash", "sh", "shell"} and re.match(
        r"^(?:&\s*)?[\"']?\.\\",
        command,
    ):
        return True
    if fence_language in {"powershell", "pwsh"} and re.match(
        r"^export\s+(?:TEST_DATABASE_URL|GEO_TEST_ALLOW_DB_RESET)\s*=",
        command,
        re.IGNORECASE,
    ):
        return True
    return bool(
        fence_language in {"bash", "sh", "shell"}
        and re.match(r"^(?:&\s*)?[.\\/]+scripts[\\/]verify_local\.ps1", command)
    )


def _strip_shell_comment(command: str) -> str:
    quote: str | None = None
    for index, character in enumerate(command):
        if character in {'"', "'"}:
            quote = (
                None if quote == character else character if quote is None else quote
            )
        elif character == "#" and quote is None:
            return command[:index].rstrip()
    return command.rstrip()


def _expand_powershell_variables(command: str, variables: dict[str, str]) -> str:
    return re.sub(
        r"\$([A-Za-z_][A-Za-z0-9_]*)",
        lambda match: variables.get(match.group(1).lower(), match.group(0)),
        command,
    )


def _created_database_name(command: str) -> str | None:
    createdb = re.match(
        r"^(?:(?:wsl(?:\.exe)?\s+)?docker(?:\.exe)?\s+exec\s+\S+\s+)?"
        r"createdb(?:\.exe)?\b(.*)$",
        command,
        re.IGNORECASE,
    )
    if createdb is None:
        return None
    database_names = re.findall(r"\b(geov0_test_[A-Za-z0-9_-]*)\b", createdb.group(1))
    return database_names[-1] if database_names else None


def _is_canonical_verifier_command(command: str) -> bool:
    executable, arguments = _command_executable(command)
    if executable == "verify_local.ps1":
        return True
    return bool(
        executable in {"powershell", "powershell.exe", "pwsh", "pwsh.exe"}
        and re.search(r"(?:^|\s)-File\s+\S*verify_local\.ps1(?:\s|$)", arguments, re.I)
    )


def _postgres_example_violations(path: Path, content: str) -> list[str]:
    violations: list[str] = []
    variables: dict[str, str] = {}
    commands: list[str] = []
    for _, raw_command, _ in _iter_documented_commands_from_content(content):
        command = _strip_shell_comment(raw_command)
        variable_assignment = re.match(
            r"^\$([A-Za-z_][A-Za-z0-9_]*)\s*=\s*[\"']([^\"']+)[\"']$",
            command,
        )
        if variable_assignment is not None:
            variables[variable_assignment.group(1).lower()] = variable_assignment.group(
                2
            )
        commands.append(_expand_powershell_variables(command, variables))

    created_names: set[str] = set()
    database_urls: list[str] = []
    reset_values: list[str] = []
    postgres_verifiers: list[str] = []
    for command in commands:
        created_name = _created_database_name(command)
        if created_name is not None:
            created_names.add(created_name)
        url_assignment = re.match(
            r"^(?:\$env:|export\s+)?TEST_DATABASE_URL\s*=\s*[\"']?" r"([^\s\"']+)",
            command,
            re.IGNORECASE,
        )
        if url_assignment is not None:
            database_urls.append(url_assignment.group(1))
        reset_assignment = re.match(
            r"^(?:\$env:|export\s+)?GEO_TEST_ALLOW_DB_RESET\s*=\s*[\"']?"
            r"([^\s\"']+)",
            command,
            re.IGNORECASE,
        )
        if reset_assignment is not None:
            reset_values.append(reset_assignment.group(1))
        if _is_canonical_verifier_command(command) and re.search(
            r"-BackendMarker\s+postgres(?:\s|$)", command
        ):
            postgres_verifiers.append(command)

    if not database_urls:
        violations.append(f"{path}: no PostgreSQL test URL")
    database_names: set[str] = set()
    reset_value = reset_values[0] if len(set(reset_values)) == 1 else None
    for database_url in database_urls:
        try:
            parsed_url = assert_safe_test_database_url(
                database_url,
                allow_destructive_reset=reset_value,
                repo_root=_ROOT,
                required_backend="postgresql",
            )
        except UnsafeTestDatabaseError as exc:
            violations.append(f"{path}: {database_url}: {exc}")
        else:
            database_names.add(parsed_url.database or "")
    if created_names and created_names != database_names:
        violations.append(
            f"{path}: created databases {sorted(created_names)} != URL databases "
            f"{sorted(database_names)}"
        )
    if not reset_values or any(value != "1" for value in reset_values):
        violations.append(f"{path}: missing executable GEO_TEST_ALLOW_DB_RESET=1")
    if not postgres_verifiers:
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


def test_contributor_guides_keep_python_tools_on_the_prepared_venv() -> None:
    bare_python_tool = re.compile(
        r"^(?:uvicorn|alembic|python(?:\.exe)?\s+-m\s+(?:uvicorn|alembic|ruff|black))"
        r"(?:\s|$)",
        re.IGNORECASE,
    )
    for path in _CONTRIBUTOR_GUIDES:
        content = path.read_text(encoding="utf-8")
        commands = [command for _, command, _ in _iter_documented_commands(path)]

        assert "py -m venv .venv" in content
        assert "py -3.11 -m venv" not in content
        assert r".\.venv\Scripts\python.exe -m pip" in content
        assert r".\.venv\Scripts\python.exe -m ruff" in content
        assert not any(bare_python_tool.match(command) for command in commands)


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
        (
            '$env:TEST_DATABASE_URL = "postgresql://localhost/geov0_test_shell"',
            "bash",
        ),
        ("export GEO_TEST_ALLOW_DB_RESET=1", "powershell"),
        (r".\.venv\Scripts\python.exe -m ruff check app", "bash"),
    ],
)
def test_shell_guard_rejects_non_executable_documented_commands(
    command: str,
    fence_language: str,
) -> None:
    assert _shell_contract_violation(command, fence_language)


def test_document_command_parser_covers_markdown_command_roles() -> None:
    commands = {
        command
        for _, command, _ in _iter_documented_commands_from_content(
            """
```powershell
pytest.exe fenced
```
- `pytest.exe bullet`
1. `pytest.exe numbered`
Run this command: `pytest.exe prose`
Run `pytest.exe prose trailing` now.
1. Run `pytest.exe numbered prose`
    pytest.exe indented
"""
        )
    }

    assert commands == {
        "pytest.exe fenced",
        "pytest.exe bullet",
        "pytest.exe numbered",
        "pytest.exe prose",
        "pytest.exe prose trailing",
        "pytest.exe numbered prose",
        "pytest.exe indented",
    }


@pytest.mark.parametrize(
    "content",
    [
        """
```powershell
createdb -U geo geov0_test_
$env:TEST_DATABASE_URL = "postgresql+asyncpg://geo:geo@localhost:5432/geov0_test_"
$env:GEO_TEST_ALLOW_DB_RESET = "1"
verify_local.ps1 -BackendMarker postgres
```
""",
        """
```powershell
createdb -U geo geov0_test_created
$env:TEST_DATABASE_URL = "postgresql+asyncpg://geo:geo@localhost:5432/geov0_test_other"
$env:GEO_TEST_ALLOW_DB_RESET = "1"
verify_local.ps1 -BackendMarker postgres
```
""",
        """
```powershell
# createdb -U geo geov0_test_comment
$env:TEST_DATABASE_URL = "postgresql+asyncpg://geo:geo@localhost:5432/geov0_test_comment"
$env:GEO_TEST_ALLOW_DB_RESET = "1"
# verify_local.ps1 -BackendMarker postgres
```
""",
        """
```powershell
createdb -U geo geov0_test_missing_reset
$env:TEST_DATABASE_URL = "postgresql+asyncpg://geo:geo@localhost:5432/geov0_test_missing_reset"
verify_local.ps1 -BackendMarker postgres
```
""",
        """
```powershell
Write-Host "createdb -U geo geov0_test_echo"
$env:TEST_DATABASE_URL = "postgresql+asyncpg://geo:geo@localhost:5432/geov0_test_echo"
$env:GEO_TEST_ALLOW_DB_RESET = "1"
Write-Host "verify_local.ps1 -BackendMarker postgres"
```
""",
        """
```powershell
createdb -U geo geov0_test_trailing_comment
$env:TEST_DATABASE_URL = "postgresql+asyncpg://geo:geo@localhost:5432/geov0_test_trailing_comment"
$env:GEO_TEST_ALLOW_DB_RESET = "1"
Write-Host "not a verifier" # verify_local.ps1 -BackendMarker postgres
```
""",
    ],
)
def test_postgres_doc_guard_rejects_invalid_or_mismatched_database_names(
    content: str,
) -> None:
    assert _postgres_example_violations(Path("synthetic.md"), content)
