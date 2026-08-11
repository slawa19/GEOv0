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
_POSTGRES_CREATE_EXAMPLE_DOCS = (
    _ROOT / "README.md",
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
    in_powershell_block_comment = False
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
            in_powershell_block_comment = False
            continue
        if fence_language in {"powershell", "pwsh"}:
            if in_powershell_block_comment:
                if "#>" in stripped:
                    in_powershell_block_comment = False
                continue
            if stripped.startswith("<#"):
                in_powershell_block_comment = "#>" not in stripped[2:]
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


def _python_tool_module(command: str) -> str | None:
    executable, arguments = _command_executable(command)
    if executable in {
        "uvicorn",
        "uvicorn.exe",
        "alembic",
        "alembic.exe",
        "ruff",
        "black",
    }:
        return executable.removesuffix(".exe")
    if executable in {"py", "py.exe"} or re.fullmatch(
        r"python(?:\d+(?:\.\d+)*)?(?:\.exe)?",
        executable,
    ):
        module = re.match(
            r"^-m\s+(uvicorn|alembic|ruff|black)(?:\s|$)", arguments, re.I
        )
        return module.group(1).lower() if module is not None else None
    return None


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
    expanded: list[str] = []
    quote: str | None = None
    index = 0
    while index < len(command):
        character = command[index]
        if character in {'"', "'"}:
            quote = (
                None if quote == character else character if quote is None else quote
            )
            expanded.append(character)
            index += 1
            continue
        if character == "$" and quote != "'":
            variable = re.match(r"\$([A-Za-z_][A-Za-z0-9_]*)", command[index:])
            if variable is not None:
                token = variable.group(0)
                expanded.append(variables.get(variable.group(1).lower(), token))
                index += len(token)
                continue
        expanded.append(character)
        index += 1
    return "".join(expanded)


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
    candidate = command.strip()
    if candidate.startswith("&"):
        candidate = candidate[1:].lstrip()
    token = re.match(r"""^("[^"]+"|'[^']+'|\S+)(.*)$""", candidate)
    if token is None:
        return False
    executable_path = token.group(1).strip("\"'").replace("\\", "/").lower()
    arguments = token.group(2).lstrip()
    if executable_path in {"./scripts/verify_local.ps1", "scripts/verify_local.ps1"}:
        return True
    executable = executable_path.rsplit("/", 1)[-1]
    if executable not in {"powershell", "powershell.exe", "pwsh", "pwsh.exe"}:
        return False
    file_argument = re.search(
        r"""(?:^|\s)-File\s+("[^"]+"|'[^']+'|\S+)""",
        arguments,
        re.IGNORECASE,
    )
    if file_argument is None:
        return False
    verifier_path = file_argument.group(1).strip("\"'").replace("\\", "/").lower()
    return verifier_path in {"./scripts/verify_local.ps1", "scripts/verify_local.ps1"}


def _postgres_example_violations(
    path: Path,
    content: str,
    *,
    require_create: bool | None = None,
) -> list[str]:
    violations: list[str] = []
    if require_create is None:
        require_create = path in _POSTGRES_CREATE_EXAMPLE_DOCS
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
    created_indexes: list[int] = []
    url_indexes: list[int] = []
    reset_indexes: list[int] = []
    verifier_indexes: list[int] = []
    for command_index, command in enumerate(commands):
        created_name = _created_database_name(command)
        if created_name is not None:
            created_names.add(created_name)
            created_indexes.append(command_index)
        url_assignment = re.match(
            r"^(?:\$env:|export\s+)?TEST_DATABASE_URL\s*=\s*[\"']?" r"([^\s\"']+)",
            command,
            re.IGNORECASE,
        )
        if url_assignment is not None:
            database_urls.append(url_assignment.group(1))
            url_indexes.append(command_index)
        reset_assignment = re.match(
            r"^(?:\$env:|export\s+)?GEO_TEST_ALLOW_DB_RESET\s*=\s*[\"']?"
            r"([^\s\"']+)",
            command,
            re.IGNORECASE,
        )
        if reset_assignment is not None:
            reset_values.append(reset_assignment.group(1))
            reset_indexes.append(command_index)
        if _is_canonical_verifier_command(command) and re.search(
            r"-BackendMarker\s+postgres(?:\s|$)", command
        ):
            postgres_verifiers.append(command)
            verifier_indexes.append(command_index)

    if not database_urls:
        violations.append(f"{path}: no PostgreSQL test URL")
    if require_create and not created_names:
        violations.append(f"{path}: missing executable createdb command")
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
    if verifier_indexes and (
        not url_indexes
        or not reset_indexes
        or min(url_indexes) > min(verifier_indexes)
        or min(reset_indexes) > min(verifier_indexes)
        or (
            require_create
            and (not created_indexes or min(created_indexes) > min(verifier_indexes))
        )
    ):
        violations.append(f"{path}: PostgreSQL setup must precede the verifier")
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
    for path in _CONTRIBUTOR_GUIDES:
        content = path.read_text(encoding="utf-8")
        commands = [command for _, command, _ in _iter_documented_commands(path)]

        assert "py -m venv .venv" in content
        assert "py -3.11 -m venv" not in content
        assert r".\.venv\Scripts\python.exe -m pip" in content
        assert r".\.venv\Scripts\python.exe -m ruff" in content
        for command in commands:
            module = _python_tool_module(command)
            if module is not None:
                assert re.match(
                    rf"^\.\\\.venv\\Scripts\\python\.exe\s+-m\s+{module}(?:\s|$)",
                    command,
                    re.IGNORECASE,
                )


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
    ("command", "module"),
    [
        ("py -m uvicorn app.main:app", "uvicorn"),
        ("python3.11 -m alembic upgrade head", "alembic"),
        ("black --check app", "black"),
        (r".\.venv\Scripts\python.exe -m ruff check app", "ruff"),
    ],
)
def test_python_tool_guard_recognizes_all_supported_launcher_shapes(
    command: str,
    module: str,
) -> None:
    assert _python_tool_module(command) == module


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        (r".\scripts\verify_local.ps1 -BackendMarker postgres", True),
        (
            "powershell.exe -NoProfile -File ./scripts/verify_local.ps1 "
            "-BackendMarker postgres",
            True,
        ),
        (r".\other\verify_local.ps1 -BackendMarker postgres", False),
        (r"C:\temp\verify_local.ps1 -BackendMarker postgres", False),
        ("verify_local.ps1 -BackendMarker postgres", False),
    ],
)
def test_canonical_verifier_guard_requires_repository_script_path(
    command: str,
    expected: bool,
) -> None:
    assert _is_canonical_verifier_command(command) is expected


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
./scripts/verify_local.ps1 -BackendMarker postgres
```
""",
        """
```powershell
createdb -U geo geov0_test_created
$env:TEST_DATABASE_URL = "postgresql+asyncpg://geo:geo@localhost:5432/geov0_test_other"
$env:GEO_TEST_ALLOW_DB_RESET = "1"
./scripts/verify_local.ps1 -BackendMarker postgres
```
""",
        """
```powershell
# createdb -U geo geov0_test_comment
$env:TEST_DATABASE_URL = "postgresql+asyncpg://geo:geo@localhost:5432/geov0_test_comment"
$env:GEO_TEST_ALLOW_DB_RESET = "1"
# ./scripts/verify_local.ps1 -BackendMarker postgres
```
""",
        """
```powershell
createdb -U geo geov0_test_missing_reset
$env:TEST_DATABASE_URL = "postgresql+asyncpg://geo:geo@localhost:5432/geov0_test_missing_reset"
./scripts/verify_local.ps1 -BackendMarker postgres
```
""",
        """
```powershell
Write-Host "createdb -U geo geov0_test_echo"
$env:TEST_DATABASE_URL = "postgresql+asyncpg://geo:geo@localhost:5432/geov0_test_echo"
$env:GEO_TEST_ALLOW_DB_RESET = "1"
Write-Host "./scripts/verify_local.ps1 -BackendMarker postgres"
```
""",
        """
```powershell
$env:TEST_DATABASE_URL = "postgresql+asyncpg://geo:geo@localhost:5432/geov0_test_missing_create"
$env:GEO_TEST_ALLOW_DB_RESET = "1"
./scripts/verify_local.ps1 -BackendMarker postgres
```
""",
        """
```powershell
createdb -U geo geov0_test_trailing_comment
$env:TEST_DATABASE_URL = "postgresql+asyncpg://geo:geo@localhost:5432/geov0_test_trailing_comment"
$env:GEO_TEST_ALLOW_DB_RESET = "1"
Write-Host "not a verifier" # ./scripts/verify_local.ps1 -BackendMarker postgres
```
""",
        """
```powershell
<#
createdb -U geo geov0_test_block_comment
./scripts/verify_local.ps1 -BackendMarker postgres
#>
$env:TEST_DATABASE_URL = "postgresql+asyncpg://geo:geo@localhost:5432/geov0_test_block_comment"
$env:GEO_TEST_ALLOW_DB_RESET = "1"
```
""",
        """
```powershell
createdb -U geo geov0_test_wrong_order
$env:TEST_DATABASE_URL = "postgresql+asyncpg://geo:geo@localhost:5432/geov0_test_wrong_order"
./scripts/verify_local.ps1 -BackendMarker postgres
$env:GEO_TEST_ALLOW_DB_RESET = "1"
```
""",
        """
```powershell
$taskSlug = "single_quote"
createdb -U geo geov0_test_single_quote
$env:TEST_DATABASE_URL = 'postgresql+asyncpg://geo:geo@localhost:5432/geov0_test_$taskSlug'
$env:GEO_TEST_ALLOW_DB_RESET = "1"
./scripts/verify_local.ps1 -BackendMarker postgres
```
""",
    ],
)
def test_postgres_doc_guard_rejects_invalid_or_mismatched_database_names(
    content: str,
) -> None:
    assert _postgres_example_violations(
        Path("synthetic.md"),
        content,
        require_create=True,
    )
