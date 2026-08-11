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
_POWERSHELL_SYNTAX_ERROR = "__documentation_powershell_syntax_error__"


def _strip_powershell_block_comments(
    line: str,
    in_block_comment: bool,
) -> tuple[str, bool]:
    visible: list[str] = []
    quote: str | None = None
    index = 0
    while index < len(line):
        if in_block_comment:
            block_end = line.find("#>", index)
            if block_end < 0:
                return "".join(visible), True
            in_block_comment = False
            index = block_end + 2
            continue
        character = line[index]
        if character == "`" and index + 1 < len(line):
            visible.extend((character, line[index + 1]))
            index += 2
            continue
        if character in {'"', "'"}:
            quote = (
                None if quote == character else character if quote is None else quote
            )
            visible.append(character)
            index += 1
            continue
        if quote is None and line.startswith("<#", index):
            in_block_comment = True
            index += 2
            continue
        visible.append(character)
        index += 1
    return "".join(visible), in_block_comment


def _split_powershell_statements(line: str) -> list[str]:
    statements: list[str] = []
    current: list[str] = []
    quote: str | None = None
    index = 0
    while index < len(line):
        character = line[index]
        if character == "`" and index + 1 < len(line):
            current.extend((character, line[index + 1]))
            index += 2
            continue
        if character in {'"', "'"}:
            quote = (
                None if quote == character else character if quote is None else quote
            )
        if character == ";" and quote is None:
            statement = "".join(current).strip()
            if statement:
                statements.append(statement)
            current = []
        else:
            current.append(character)
        index += 1
    statement = "".join(current).strip()
    if statement:
        statements.append(statement)
    return statements


def _iter_documented_commands_from_content(content: str):
    fence_language: str | None = None
    in_powershell_block_comment = False
    powershell_block_comment_line: int | None = None
    powershell_here_string_end: str | None = None
    powershell_here_string_line: int | None = None
    pending_powershell_command: tuple[int, str] | None = None
    for line_number, line in enumerate(
        content.splitlines(),
        start=1,
    ):
        stripped = line.strip()
        fence = re.match(r"^```\s*([A-Za-z0-9_-]*)", stripped)
        if fence is not None:
            if in_powershell_block_comment:
                yield (
                    powershell_block_comment_line or line_number,
                    f"{_POWERSHELL_SYNTAX_ERROR}: unclosed block comment",
                    fence_language,
                )
            if powershell_here_string_end is not None:
                yield (
                    powershell_here_string_line or line_number,
                    f"{_POWERSHELL_SYNTAX_ERROR}: unclosed here-string",
                    fence_language,
                )
            if pending_powershell_command is not None:
                yield (
                    pending_powershell_command[0],
                    f"{_POWERSHELL_SYNTAX_ERROR}: dangling continuation",
                    fence_language,
                )
                pending_powershell_command = None
            fence_language = (
                None if fence_language is not None else fence.group(1).lower()
            )
            in_powershell_block_comment = False
            powershell_block_comment_line = None
            powershell_here_string_end = None
            powershell_here_string_line = None
            continue
        powershell_statements: list[str] | None = None
        powershell_line_continues = False
        here_string_start: re.Match[str] | None = None
        if fence_language in {"powershell", "pwsh"}:
            if powershell_here_string_end is not None:
                if not line.startswith(powershell_here_string_end):
                    continue
                here_string_suffix = line[len(powershell_here_string_end) :]
                if here_string_suffix.strip() and not re.match(
                    r"^\s*;",
                    here_string_suffix,
                ):
                    continue
                stripped = here_string_suffix.lstrip()
                if stripped.startswith(";"):
                    stripped = stripped[1:].lstrip()
                powershell_here_string_end = None
                powershell_here_string_line = None
                if not stripped:
                    continue
                line = stripped
            was_in_block_comment = in_powershell_block_comment
            line_for_block_comments = (
                line if in_powershell_block_comment else _strip_shell_comment(line)
            )
            visible, in_powershell_block_comment = _strip_powershell_block_comments(
                line_for_block_comments,
                in_powershell_block_comment,
            )
            if in_powershell_block_comment and not was_in_block_comment:
                powershell_block_comment_line = line_number
            elif not in_powershell_block_comment:
                powershell_block_comment_line = None
            visible = _strip_shell_comment(visible)
            here_string_start = re.search(r"@(['\"])\s*$", visible)
            if (
                here_string_start is not None
                and _tokenize_powershell_arguments(visible[: here_string_start.start()])
                is not None
            ):
                powershell_here_string_end = f"{here_string_start.group(1)}@"
                powershell_here_string_line = line_number
            else:
                here_string_start = None
            if pending_powershell_command is not None and (
                not visible.strip() or visible.lstrip().startswith("#")
            ):
                yield (
                    pending_powershell_command[0],
                    f"{_POWERSHELL_SYNTAX_ERROR}: interrupted continuation",
                    fence_language,
                )
                pending_powershell_command = None
                continue
            powershell_line_continues = visible.endswith("`")
            powershell_statements = _split_powershell_statements(visible)
            if not powershell_statements:
                if pending_powershell_command is not None:
                    yield (
                        pending_powershell_command[0],
                        f"{_POWERSHELL_SYNTAX_ERROR}: interrupted continuation",
                        fence_language,
                    )
                    pending_powershell_command = None
                continue
            stripped = powershell_statements[0]
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
        commands = powershell_statements or [stripped]
        for command_index, command in enumerate(commands):
            command_line = line_number
            if pending_powershell_command is not None:
                command_line, pending = pending_powershell_command
                command = f"{pending[:-1].rstrip()} {command.lstrip()}"
                pending_powershell_command = None
            if (
                powershell_line_continues
                and command_index == len(commands) - 1
                and fence_language in {"powershell", "pwsh"}
            ):
                pending_powershell_command = (command_line, command)
            elif command and not command.startswith("#"):
                if (
                    fence_language in {"powershell", "pwsh"}
                    and here_string_start is None
                    and not _powershell_inline_syntax_is_valid(command)
                ):
                    yield (
                        command_line,
                        f"{_POWERSHELL_SYNTAX_ERROR}: invalid quoting or escape",
                        fence_language,
                    )
                else:
                    yield command_line, command, fence_language
    if pending_powershell_command is not None:
        yield (
            pending_powershell_command[0],
            f"{_POWERSHELL_SYNTAX_ERROR}: dangling continuation",
            fence_language,
        )
    if powershell_here_string_end is not None:
        yield (
            powershell_here_string_line or 1,
            f"{_POWERSHELL_SYNTAX_ERROR}: unclosed here-string",
            fence_language,
        )
    if in_powershell_block_comment:
        yield (
            powershell_block_comment_line or 1,
            f"{_POWERSHELL_SYNTAX_ERROR}: unclosed block comment",
            fence_language,
        )


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
        "ruff.exe",
        "black",
        "black.exe",
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


def _is_venv_creation_command(command: str) -> bool:
    executable, arguments = _command_executable(command)
    return bool(
        executable in {"py", "py.exe"}
        and re.fullmatch(r"-m\s+venv\s+\.venv", arguments, re.IGNORECASE)
    )


def _is_venv_dependency_install_command(command: str) -> bool:
    candidate = command.strip()
    token = re.match(r"""^("[^"]+"|'[^']+'|\S+)(.*)$""", candidate)
    if token is None:
        return False
    executable_path = token.group(1).strip("\"'").replace("\\", "/").lower()
    arguments = token.group(2).lstrip()
    return bool(
        executable_path == "./.venv/scripts/python.exe"
        and re.fullmatch(
            r"-m\s+pip\s+install\s+-r\s+requirements\.txt\s+"
            r"-r\s+requirements-dev\.txt",
            arguments,
            re.IGNORECASE,
        )
    )


def _is_prepared_venv_tool_command(command: str, module: str) -> bool:
    return bool(
        re.match(
            rf"^\.\\\.venv\\Scripts\\python\.exe\s+-m\s+{module}(?:\s|$)",
            command,
            re.IGNORECASE,
        )
    )


def _contributor_venv_violations(content: str) -> list[str]:
    commands = [
        command for _, command, _ in _iter_documented_commands_from_content(content)
    ]
    syntax_errors = [
        command for command in commands if command.startswith(_POWERSHELL_SYNTAX_ERROR)
    ]
    creation_indexes = [
        index
        for index, command in enumerate(commands)
        if _is_venv_creation_command(command)
    ]
    install_indexes = [
        index
        for index, command in enumerate(commands)
        if _is_venv_dependency_install_command(command)
    ]
    tool_indexes: list[int] = []
    verifier_indexes: list[int] = []
    violations: list[str] = list(syntax_errors)
    for index, command in enumerate(commands):
        if _looks_like_verifier_command(command):
            if _is_canonical_verifier_command(command):
                verifier_indexes.append(index)
            else:
                violations.append(f"invalid canonical verifier invocation: {command}")
        module = _python_tool_module(command)
        if module is None:
            continue
        tool_indexes.append(index)
        if not _is_prepared_venv_tool_command(command, module):
            violations.append(f"tool does not use prepared venv: {command}")
    if not creation_indexes:
        violations.append("missing executable py -m venv .venv")
    if not install_indexes:
        violations.append("missing executable venv dependency install")
    consumer_indexes = tool_indexes + verifier_indexes
    if not consumer_indexes:
        violations.append("missing canonical verifier or Python-owned tool consumer")
    if (
        creation_indexes
        and install_indexes
        and consumer_indexes
        and not min(creation_indexes) < min(install_indexes) < min(consumer_indexes)
    ):
        violations.append("venv creation and install must precede Python-owned tools")
    return violations


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
        elif (
            character == "#"
            and quote is None
            and not (index > 0 and command[index - 1] == "<")
            and not (index + 1 < len(command) and command[index + 1] == ">")
        ):
            return command[:index].rstrip()
    return command.rstrip()


def _assignment_value(command: str, name: str) -> str | None:
    assignment = re.fullmatch(
        rf"(?:\$env:|export\s+)?{re.escape(name)}\s*=\s*"
        r"(?:(['\"])([^'\"]+)\1|([^\s'\"]+))\s*",
        command,
        re.IGNORECASE,
    )
    if assignment is None:
        return None
    return assignment.group(2) or assignment.group(3)


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


def _looks_like_verifier_command(command: str) -> bool:
    candidate = command.strip()
    if candidate.startswith("&"):
        candidate = candidate[1:].lstrip()
    token = re.match(r"""^("[^"]+"|'[^']+'|\S+)(.*)$""", candidate)
    if token is None:
        return False
    executable_path = token.group(1).strip("\"'").replace("\\", "/").lower()
    executable = executable_path.rsplit("/", 1)[-1]
    if executable == "verify_local.ps1":
        return True
    return bool(
        executable in {"powershell", "powershell.exe", "pwsh", "pwsh.exe"}
        and re.search(
            r"(?:^|[\s\"'])[^\s\"']*verify_local\.ps1(?:[\s\"']|$)",
            token.group(2),
            re.I,
        )
    )


def _tokenize_powershell_arguments(candidate: str) -> list[str] | None:
    tokens: list[str] = []
    current: list[str] = []
    quote: str | None = None
    index = 0
    while index < len(candidate):
        character = candidate[index]
        if character == "`":
            if index + 1 >= len(candidate):
                return None
            current.append(candidate[index + 1])
            index += 2
            continue
        if character in {'"', "'"}:
            if quote is None:
                quote = character
            elif quote == character:
                quote = None
            else:
                current.append(character)
            index += 1
            continue
        if character.isspace() and quote is None:
            if current:
                tokens.append("".join(current))
                current = []
            index += 1
            continue
        current.append(character)
        index += 1
    if quote is not None:
        return None
    if current:
        tokens.append("".join(current))
    return tokens


def _powershell_inline_syntax_is_valid(command: str) -> bool:
    command_without_comment = _strip_shell_comment(command)
    if _tokenize_powershell_arguments(command_without_comment) is None:
        return False
    return not command_without_comment.rstrip().endswith(("|", "&&", "||"))


def _parse_verifier_arguments(arguments: str) -> dict[str, str | bool] | None:
    tokens = _tokenize_powershell_arguments(arguments.strip())
    if tokens is None:
        return None
    parameter_arity = {
        "-taskslug": 1,
        "-staticdiagnostics": 0,
        "-backendselector": 1,
        "-backendmarker": 1,
        "-includeexpensive": 0,
        "-backendonly": 0,
        "-python": 1,
    }
    parsed: dict[str, str | bool] = {}
    index = 0
    while index < len(tokens):
        parameter = tokens[index].lower()
        arity = parameter_arity.get(parameter)
        if arity is None or parameter in parsed:
            return None
        if arity == 1:
            if index + 1 >= len(tokens) or tokens[index + 1].startswith("-"):
                return None
            index += 1
            parsed[parameter] = tokens[index]
        else:
            parsed[parameter] = True
        index += 1
    task_slug = parsed.get("-taskslug")
    if (
        isinstance(task_slug, str)
        and re.fullmatch(r"[A-Za-z0-9_-]+", task_slug) is None
    ):
        return None
    backend_marker = parsed.get("-backendmarker")
    if (
        isinstance(backend_marker, str)
        and re.fullmatch(
            r"[A-Za-z_][A-Za-z0-9_]*",
            backend_marker,
        )
        is None
    ):
        return None
    return parsed


def _canonical_verifier_arguments(command: str) -> dict[str, str | bool] | None:
    candidate = command.strip()
    uses_call_operator = False
    if candidate.startswith("&"):
        uses_call_operator = True
        candidate = candidate[1:].lstrip()
    token = re.match(r"""^("[^"]+"|'[^']+'|\S+)(.*)$""", candidate)
    if token is None:
        return None
    executable_was_quoted = token.group(1).startswith(('"', "'"))
    if executable_was_quoted and not uses_call_operator:
        return None
    executable_path = token.group(1).strip("\"'").replace("\\", "/").lower()
    arguments = token.group(2).lstrip()
    if executable_path in {"./scripts/verify_local.ps1", "scripts/verify_local.ps1"}:
        return _parse_verifier_arguments(arguments)
    executable = executable_path.rsplit("/", 1)[-1]
    if executable not in {"powershell", "powershell.exe", "pwsh", "pwsh.exe"}:
        return None
    file_invocation = re.fullmatch(
        r"(?:(?:-NoProfile|-NonInteractive)\s+|"
        r"-ExecutionPolicy\s+Bypass\s+)*"
        r"-File\s+(\"[^\"]+\"|'[^']+'|\S+)(.*)",
        arguments,
        re.IGNORECASE,
    )
    if file_invocation is None:
        return None
    verifier_path = file_invocation.group(1).strip("\"'").replace("\\", "/").lower()
    if verifier_path not in {"./scripts/verify_local.ps1", "scripts/verify_local.ps1"}:
        return None
    return _parse_verifier_arguments(file_invocation.group(2))


def _is_canonical_verifier_command(command: str) -> bool:
    return _canonical_verifier_arguments(command) is not None


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
        if command.startswith(_POWERSHELL_SYNTAX_ERROR):
            violations.append(f"{path}: {command}")
            continue
        variable_assignment = re.fullmatch(
            r"\$([A-Za-z_][A-Za-z0-9_]*)\s*=\s*" r"(?:(['\"])([^'\"]+)\2|([^\s'\"]+))",
            command,
        )
        if variable_assignment is not None:
            variables[variable_assignment.group(1).lower()] = variable_assignment.group(
                3
            ) or variable_assignment.group(4)
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
        url_value = _assignment_value(command, "TEST_DATABASE_URL")
        if url_value is not None:
            database_urls.append(url_value)
            url_indexes.append(command_index)
        reset_value = _assignment_value(command, "GEO_TEST_ALLOW_DB_RESET")
        if reset_value is not None:
            reset_values.append(reset_value)
            reset_indexes.append(command_index)
        if _looks_like_verifier_command(command):
            verifier_arguments = _canonical_verifier_arguments(command)
            if verifier_arguments is None:
                violations.append(f"{path}: invalid verifier invocation: {command}")
            elif verifier_arguments.get("-backendmarker") == "postgres":
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
            if command.startswith(_POWERSHELL_SYNTAX_ERROR):
                violations.append(f"{path.relative_to(_ROOT)}:{line_number}: {command}")
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
        assert "py -3.11 -m venv" not in content
        assert _contributor_venv_violations(content) == []


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
        ("ruff.exe check app", "ruff"),
        ("black.exe --check app", "black"),
        (r".\.venv\Scripts\python.exe -m ruff check app", "ruff"),
    ],
)
def test_python_tool_guard_recognizes_all_supported_launcher_shapes(
    command: str,
    module: str,
) -> None:
    assert _python_tool_module(command) == module


@pytest.mark.parametrize(
    "content",
    [
        r"""
```powershell
# py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-dev.txt
.\.venv\Scripts\python.exe -m ruff check app migrations
```
""",
        r"""
```powershell
py -m venv .venv
Write-Host ".\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-dev.txt"
.\.venv\Scripts\python.exe -m ruff check app migrations
```
""",
        r"""
```powershell
$help = @'
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-dev.txt
'@
.\.venv\Scripts\python.exe -m ruff check app migrations
```
""",
        r"""
```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-dev.txt
.\scripts\verify_local.ps1 -DefinitelyNotAParameter x -BackendOnly
.\.venv\Scripts\python.exe -m ruff check app migrations
```
""",
        r"""
```powershell
Write-Host "comment starts"; <#
py -m venv .venv
#>
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-dev.txt
.\.venv\Scripts\python.exe -m ruff check app migrations
```
""",
        r"""
```powershell
.\scripts\verify_local.ps1 -TaskSlug before_setup -BackendOnly
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-dev.txt
.\.venv\Scripts\python.exe -m ruff check app migrations
```
""",
        r"""
```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install --dry-run -r requirements.txt -r requirements-dev.txt
.\.venv\Scripts\python.exe -m ruff check app migrations
```
""",
        r"""
```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-dev.txt
```
""",
    ],
)
def test_contributor_venv_guard_rejects_missing_executable_setup_roles(
    content: str,
) -> None:
    assert _contributor_venv_violations(content)


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        (r".\scripts\verify_local.ps1 -BackendMarker postgres", True),
        (r'& ".\scripts\verify_local.ps1" -BackendMarker postgres', True),
        (
            "powershell.exe -NoProfile -File ./scripts/verify_local.ps1 "
            "-BackendMarker postgres",
            True,
        ),
        (r".\other\verify_local.ps1 -BackendMarker postgres", False),
        (r"C:\temp\verify_local.ps1 -BackendMarker postgres", False),
        ("verify_local.ps1 -BackendMarker postgres", False),
        ('"./scripts/verify_local.ps1" -BackendMarker postgres', False),
        (
            '"C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" '
            "-NoProfile -File ./scripts/verify_local.ps1 -BackendMarker postgres",
            False,
        ),
        (
            "powershell -Command Write-Host -File ./scripts/verify_local.ps1 "
            "-BackendMarker postgres",
            False,
        ),
        (
            "powershell.exe -ExecutionPolicy DefinitelyNotAPolicy "
            "-File ./scripts/verify_local.ps1 -BackendMarker postgres",
            False,
        ),
        (
            "./scripts/verify_local.ps1 -DefinitelyNotAParameter x "
            "-BackendMarker postgres",
            False,
        ),
        (
            "./scripts/verify_local.ps1 -TaskSlug bad.slug -BackendMarker postgres",
            False,
        ),
        (
            "./scripts/verify_local.ps1 -BackendMarker postgres "
            "-BackendMarker postgres",
            False,
        ),
        (
            './scripts/verify_local.ps1 -TaskSlug "safe -BackendMarker postgres "',
            False,
        ),
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


def test_powershell_parser_preserves_quoted_markers_and_post_comment_suffix() -> None:
    commands = [
        command
        for _, command, _ in _iter_documented_commands_from_content(
            """
```powershell
Write-Host '<#'
pytest.exe after_quoted_marker
Write-Host 'before'; <# ignored #>; pytest.exe after_comment
```
"""
        )
    ]

    assert "pytest.exe after_quoted_marker" in commands
    assert "pytest.exe after_comment" in commands


def test_powershell_line_comments_cannot_hide_following_pytest_commands() -> None:
    commands = [
        command
        for _, command, _ in _iter_documented_commands_from_content(
            """
```powershell
Write-Host "visible" # `
pytest.exe after_commented_backtick
# @'
pytest.exe after_commented_here_marker
'@
# <#
pytest.exe after_commented_block_marker
#>
```
"""
        )
    ]

    assert "pytest.exe after_commented_backtick" in commands
    assert "pytest.exe after_commented_here_marker" in commands
    assert "pytest.exe after_commented_block_marker" in commands
    assert any(command.startswith(_POWERSHELL_SYNTAX_ERROR) for command in commands)


def test_powershell_parser_preserves_here_string_terminator_suffix() -> None:
    commands = [
        command
        for _, command, _ in _iter_documented_commands_from_content(
            """
```powershell
$help = @'
not executable
'@; pytest.exe after_here_string
```
"""
        )
    ]

    assert "pytest.exe after_here_string" in commands


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
Write-Host "comment starts"; <#
createdb -U geo geov0_test_inline_block_comment
./scripts/verify_local.ps1 -BackendMarker postgres
#>
$env:TEST_DATABASE_URL = "postgresql+asyncpg://geo:geo@localhost:5432/geov0_test_inline_block_comment"
$env:GEO_TEST_ALLOW_DB_RESET = "1"
```
""",
        """
```powershell
createdb -U geo geov0_test_command_mode
$env:TEST_DATABASE_URL = "postgresql+asyncpg://geo:geo@localhost:5432/geov0_test_command_mode"
$env:GEO_TEST_ALLOW_DB_RESET = "1"
powershell -Command Write-Host -File ./scripts/verify_local.ps1 -BackendMarker postgres
```
""",
        """
```powershell
createdb -U geo geov0_test_bad_continuation
$env:TEST_DATABASE_URL = "postgresql+asyncpg://geo:geo@localhost:5432/geov0_test_bad_continuation"
$env:GEO_TEST_ALLOW_DB_RESET = "1"
./scripts/verify_local.ps1 -BackendMarker postgres `
  -DefinitelyNotAParameter x
```
""",
        """
```powershell
createdb -U geo geov0_test_dangling_continuation
$env:TEST_DATABASE_URL = "postgresql+asyncpg://geo:geo@localhost:5432/geov0_test_dangling_continuation"
$env:GEO_TEST_ALLOW_DB_RESET = "1"
./scripts/verify_local.ps1 -BackendMarker postgres `
```
""",
        """
```powershell
createdb -U geo geov0_test_invalid_sibling
$env:TEST_DATABASE_URL = "postgresql+asyncpg://geo:geo@localhost:5432/geov0_test_invalid_sibling"
$env:GEO_TEST_ALLOW_DB_RESET = "1"
./scripts/verify_local.ps1 -DefinitelyNotAParameter x -BackendMarker postgres
./scripts/verify_local.ps1 -BackendMarker postgres
```
""",
        """
```powershell
createdb -U geo geov0_test_unbalanced_quote
$env:TEST_DATABASE_URL = "postgresql+asyncpg://geo:geo@localhost:5432/geov0_test_unbalanced_quote"
$env:GEO_TEST_ALLOW_DB_RESET = "1"
./scripts/verify_local.ps1 -BackendMarker "postgres
```
""",
        """
```powershell
createdb -U geo geov0_test_unbalanced_url
$env:TEST_DATABASE_URL = "postgresql+asyncpg://geo:geo@localhost:5432/geov0_test_unbalanced_url
$env:GEO_TEST_ALLOW_DB_RESET = "1"
./scripts/verify_local.ps1 -BackendMarker postgres
```
""",
        (
            """
```powershell
createdb -U geo geov0_test_backtick_whitespace
$env:TEST_DATABASE_URL = "postgresql+asyncpg://geo:geo@localhost:5432/geov0_test_backtick_whitespace"
$env:GEO_TEST_ALLOW_DB_RESET = "1"
./scripts/verify_local.ps1 -BackendMarker postgres `"""
            + "   \n"
            + """
  -BackendOnly
```
"""
        ),
        """
```powershell
createdb -U geo geov0_test_indented_here_end
$env:TEST_DATABASE_URL = "postgresql+asyncpg://geo:geo@localhost:5432/geov0_test_indented_here_end"
$env:GEO_TEST_ALLOW_DB_RESET = "1"
./scripts/verify_local.ps1 -BackendMarker postgres
$ignored = @'
not executable
  '@
```
""",
        """
```powershell
createdb -U geo geov0_test_missing_here_end
$env:TEST_DATABASE_URL = "postgresql+asyncpg://geo:geo@localhost:5432/geov0_test_missing_here_end"
$env:GEO_TEST_ALLOW_DB_RESET = "1"
./scripts/verify_local.ps1 -BackendMarker postgres
$ignored = @'
not executable
```
""",
        """
```powershell
createdb -U geo geov0_test_missing_block_end
$env:TEST_DATABASE_URL = "postgresql+asyncpg://geo:geo@localhost:5432/geov0_test_missing_block_end"
$env:GEO_TEST_ALLOW_DB_RESET = "1"
./scripts/verify_local.ps1 -BackendMarker postgres
Write-Host "before"; <# unclosed comment
```
""",
        """
```powershell
createdb -U geo geov0_test_unbalanced_executable
$env:TEST_DATABASE_URL = "postgresql+asyncpg://geo:geo@localhost:5432/geov0_test_unbalanced_executable"
$env:GEO_TEST_ALLOW_DB_RESET = "1"
"./scripts/verify_local.ps1 -BackendMarker postgres
```
""",
        """
```powershell
createdb -U geo geov0_test_unbalanced_file
$env:TEST_DATABASE_URL = "postgresql+asyncpg://geo:geo@localhost:5432/geov0_test_unbalanced_file"
$env:GEO_TEST_ALLOW_DB_RESET = "1"
powershell -File "./scripts/verify_local.ps1 -BackendMarker postgres
```
""",
        """
```powershell
createdb -U geo geov0_test_unbalanced_reset
$env:TEST_DATABASE_URL = "postgresql+asyncpg://geo:geo@localhost:5432/geov0_test_unbalanced_reset"
$env:GEO_TEST_ALLOW_DB_RESET = "1
./scripts/verify_local.ps1 -BackendMarker postgres
```
""",
        """
```powershell
createdb -U geo "geov0_test_unbalanced_createdb
$env:TEST_DATABASE_URL = "postgresql+asyncpg://geo:geo@localhost:5432/geov0_test_unbalanced_createdb"
$env:GEO_TEST_ALLOW_DB_RESET = "1"
./scripts/verify_local.ps1 -BackendMarker postgres
```
""",
        """
```powershell
createdb -U geo geov0_test_invalid_here_suffix
$env:TEST_DATABASE_URL = "postgresql+asyncpg://geo:geo@localhost:5432/geov0_test_invalid_here_suffix"
$env:GEO_TEST_ALLOW_DB_RESET = "1"
./scripts/verify_local.ps1 -BackendMarker postgres
$ignored = @'
not executable
'@Write-Output "invalid suffix"
```
""",
        """
```powershell
createdb -U geo geov0_test_fake_here_header
$env:TEST_DATABASE_URL = "postgresql+asyncpg://geo:geo@localhost:5432/geov0_test_fake_here_header"
$env:GEO_TEST_ALLOW_DB_RESET = "1"
./scripts/verify_local.ps1 -BackendMarker postgres
$ignored = "unterminated @'
'@
```
""",
        """
```powershell
createdb -U geo geov0_test_dangling_non_verifier
$env:TEST_DATABASE_URL = "postgresql+asyncpg://geo:geo@localhost:5432/geov0_test_dangling_non_verifier"
$env:GEO_TEST_ALLOW_DB_RESET = "1"
./scripts/verify_local.ps1 -BackendMarker postgres
Write-Host "incomplete" `
```
""",
        """
```powershell
createdb -U geo geov0_test_continuation_blank
$env:TEST_DATABASE_URL = "postgresql+asyncpg://geo:geo@localhost:5432/geov0_test_continuation_blank"
$env:GEO_TEST_ALLOW_DB_RESET = "1"
./scripts/verify_local.ps1 -BackendMarker postgres `

  -BackendOnly
```
""",
        """
```powershell
createdb -U geo geov0_test_continuation_comment
$env:TEST_DATABASE_URL = "postgresql+asyncpg://geo:geo@localhost:5432/geov0_test_continuation_comment"
$env:GEO_TEST_ALLOW_DB_RESET = "1"
./scripts/verify_local.ps1 -BackendMarker postgres `
# continuation interrupted
  -BackendOnly
```
""",
        """
```powershell
createdb -U geo geov0_test_unbalanced_delimiter
$env:TEST_DATABASE_URL = "postgresql+asyncpg://geo:geo@localhost:5432/geov0_test_unbalanced_delimiter")
$env:GEO_TEST_ALLOW_DB_RESET = "1"
./scripts/verify_local.ps1 -BackendMarker postgres
```
""",
        """
```powershell
createdb -U geo geov0_test_incomplete_pipeline
$env:TEST_DATABASE_URL = "postgresql+asyncpg://geo:geo@localhost:5432/geov0_test_incomplete_pipeline"
$env:GEO_TEST_ALLOW_DB_RESET = "1"
./scripts/verify_local.ps1 -BackendMarker postgres
Write-Output "incomplete" |
```
""",
        """
```powershell
createdb -U geo geov0_test_pipeline_comment
$env:TEST_DATABASE_URL = "postgresql+asyncpg://geo:geo@localhost:5432/geov0_test_pipeline_comment"
$env:GEO_TEST_ALLOW_DB_RESET = "1"
./scripts/verify_local.ps1 -BackendMarker postgres
Write-Output "incomplete" | # no pipeline target
```
""",
        """
```powershell
createdb -U geo geov0_test_incomplete_chain
$env:TEST_DATABASE_URL = "postgresql+asyncpg://geo:geo@localhost:5432/geov0_test_incomplete_chain"
$env:GEO_TEST_ALLOW_DB_RESET = "1"
./scripts/verify_local.ps1 -BackendMarker postgres
Write-Output "incomplete" &&
```
""",
        """
```powershell
createdb -U geo geov0_test_url_suffix
$env:TEST_DATABASE_URL = "postgresql+asyncpg://geo:geo@localhost:5432/geov0_test_url_suffix" definitely_invalid
$env:GEO_TEST_ALLOW_DB_RESET = "1"
./scripts/verify_local.ps1 -BackendMarker postgres
```
""",
        """
```powershell
createdb -U geo geov0_test_reset_suffix
$env:TEST_DATABASE_URL = "postgresql+asyncpg://geo:geo@localhost:5432/geov0_test_reset_suffix"
$env:GEO_TEST_ALLOW_DB_RESET = "1" definitely_invalid
./scripts/verify_local.ps1 -BackendMarker postgres
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
