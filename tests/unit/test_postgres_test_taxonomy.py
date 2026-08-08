from __future__ import annotations

import ast
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_INTEGRATION_ROOT = _ROOT / "tests" / "integration"


def _parse_test_modules() -> dict[Path, ast.Module]:
    return {
        path: ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for path in sorted(_INTEGRATION_ROOT.glob("test_*.py"))
    }


def _is_postgres_marker(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "postgres"
        and isinstance(node.value, ast.Attribute)
        and node.value.attr == "mark"
        and isinstance(node.value.value, ast.Name)
        and node.value.value.id == "pytest"
    )


def _declares_module_postgres_marker(tree: ast.Module) -> bool:
    for statement in tree.body:
        value: ast.AST | None = None
        if isinstance(statement, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "pytestmark"
            for target in statement.targets
        ):
            value = statement.value
        elif (
            isinstance(statement, ast.AnnAssign)
            and isinstance(statement.target, ast.Name)
            and statement.target.id == "pytestmark"
        ):
            value = statement.value
        if value is not None and any(
            _is_postgres_marker(node) for node in ast.walk(value)
        ):
            return True
    return False


def _is_pytest_skip(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "skip"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "pytest"
    )


def _is_non_postgres_dialect_guard(node: ast.AST) -> bool:
    if not isinstance(node, ast.Compare) or not any(
        isinstance(operator, ast.NotIn) for operator in node.ops
    ):
        return False
    for comparator in node.comparators:
        if not isinstance(comparator, (ast.Set, ast.List, ast.Tuple)):
            continue
        values = {
            item.value.lower()
            for item in comparator.elts
            if isinstance(item, ast.Constant) and isinstance(item.value, str)
        }
        if {"postgresql", "postgres"} <= values:
            return True
    return False


def _has_postgres_only_dialect_skip(tree: ast.Module) -> bool:
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.If)
            and any(
                _is_non_postgres_dialect_guard(item) for item in ast.walk(node.test)
            )
            and any(
                _is_pytest_skip(item)
                for statement in node.body
                for item in ast.walk(statement)
            )
        ):
            return True
    return False


def _indented_blocks(text: str, *, key: str, indent: int) -> list[str]:
    lines = text.splitlines()
    header = re.compile(rf"^{' ' * indent}{re.escape(key)}:\s*(?:[|>][+-]?)?\s*$")
    starts = [index for index, line in enumerate(lines) if header.fullmatch(line)]
    blocks: list[str] = []
    for start in starts:
        end = len(lines)
        for index in range(start + 1, len(lines)):
            line = lines[index]
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            current_indent = len(line) - len(line.lstrip())
            if current_indent <= indent:
                end = index
                break
        blocks.append("\n".join(lines[start:end]))
    return blocks


def _executable_command(run_block: str) -> str:
    command_lines = [
        line.strip()
        for line in run_block.splitlines()[1:]
        if line.strip() and not line.lstrip().startswith("#")
    ]
    return re.sub(r"\s+", " ", " ".join(command_lines).replace("\\", " "))


def test_postgres_module_suffix_is_the_marker_owned_taxonomy() -> None:
    modules = _parse_test_modules()
    candidates = {
        path.relative_to(_ROOT)
        for path in modules
        if path.name.endswith("_postgres.py")
    }
    dialect_skip_modules = {
        path.relative_to(_ROOT)
        for path, tree in modules.items()
        if _has_postgres_only_dialect_skip(tree)
    }
    marked = {
        path.relative_to(_ROOT)
        for path, tree in modules.items()
        if _declares_module_postgres_marker(tree)
    }

    missing_markers = sorted(str(path) for path in candidates - marked)
    misnamed_markers = sorted(str(path) for path in marked - candidates)
    misnamed_dialect_skips = sorted(str(path) for path in dialect_skip_modules - candidates)
    assert not missing_markers, (
        f"PostgreSQL suffix modules without marker: {missing_markers}"
    )
    assert not misnamed_markers, (
        f"PostgreSQL-marked modules without _postgres.py suffix: {misnamed_markers}"
    )
    assert not misnamed_dialect_skips, (
        f"PostgreSQL dialect-skip modules without suffix: {misnamed_dialect_skips}"
    )


def test_postgres_ci_job_selects_marker_tier_without_file_allowlist() -> None:
    workflow = (_ROOT / ".github" / "workflows" / "quality.yml").read_text(
        encoding="utf-8"
    )
    postgres_jobs = _indented_blocks(workflow, key="postgres", indent=2)

    assert len(postgres_jobs) == 1
    run_blocks = _indented_blocks(postgres_jobs[0], key="run", indent=8)
    commands = [_executable_command(block) for block in run_blocks]
    canonical_commands = [
        command
        for command in commands
        if re.match(
            r"^(?:python(?:\.exe)? -m pytest|\./scripts/verify_local\.ps1)\b",
            command,
            flags=re.IGNORECASE,
        )
    ]

    assert len(canonical_commands) == 1
    command = canonical_commands[0]
    assert command.startswith("./scripts/verify_local.ps1 ")
    assert "-TaskSlug ci-postgres" in command
    assert "-BackendOnly" in command
    assert "-BackendMarker postgres" in command
    assert "-BackendSelector tests/integration" in command
    assert ".py" not in command
