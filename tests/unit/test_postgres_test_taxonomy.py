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


# THE TAXONOMY INVERTED ON 2026-09-23 (017 stage 2c, T1702), AND WHAT IT PROTECTS DID NOT CHANGE.
# Until then a module named `*_postgres.py` had to carry `pytest.mark.postgres`, and a marked module
# had to carry the suffix: the marker moved it out of the SQLite default tier into the PostgreSQL
# one, where its PostgreSQL-only premise held. What that protected was "a PostgreSQL module runs
# where PostgreSQL is". Since 2c the one tier IS PostgreSQL (`tests/conftest.py` refuses anything
# else) and the marker is gone, so the same protection now reads: no module may take itself out of
# that tier by a `postgres` marker. Under `--strict-markers` an unregistered marker already fails
# collection; this names the module instead of leaving a collection error to be read. The suffix
# stays as history and still owns the PostgreSQL-only dialect skips, which are now unreachable but
# name the module's premise.
def test_no_module_takes_itself_out_of_the_postgres_tier() -> None:
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
    marked = sorted(
        str(path.relative_to(_ROOT))
        for path, tree in modules.items()
        if _declares_module_postgres_marker(tree)
    )
    config = (_ROOT / "pytest.ini").read_text(encoding="utf-8")

    # Anti-vacuum: the scan must still see the modules the marker used to cover (56 on 2026-09-23).
    assert len(candidates) >= 50, f"only {len(candidates)} *_postgres.py modules found"
    assert not marked, f"modules marked `postgres` again - there is no second tier to go to: {marked}"
    assert "\n    postgres:" not in config, "pytest.ini registers the `postgres` marker again"
    misnamed_dialect_skips = sorted(str(path) for path in dialect_skip_modules - candidates)
    assert not misnamed_dialect_skips, (
        f"PostgreSQL dialect-skip modules without suffix: {misnamed_dialect_skips}"
    )


def test_the_marker_scan_still_sees_a_marker() -> None:
    """Counter-check for the scan above: a planted module marker is found in each spelling."""

    for source in (
        "import pytest\npytestmark = pytest.mark.postgres\n",
        "import pytest\npytestmark = [pytest.mark.postgres, pytest.mark.asyncio]\n",
    ):
        assert _declares_module_postgres_marker(ast.parse(source)), source
    assert not _declares_module_postgres_marker(
        ast.parse("import pytest\npytestmark = pytest.mark.asyncio\n")
    )


# THE JOB THESE TWO GUARDS WATCH WAS RENAMED, NOT WEAKENED (2026-09-21, T1701). They used to read
# the scheduled `postgres` job; programme 017 stage 1 deleted it and moved both of its PostgreSQL
# pytest sessions into `required-backend`, which runs on every pull request. The assertions below
# are the same contract on the new owner, with one addition forced by the move: the required job
# also runs the default tier, so "exactly one canonical command" became "exactly one command that
# asks for the marker, and no raw pytest anywhere in the job".
_POSTGRES_CI_JOB = "required-backend"


def test_postgres_ci_job_runs_the_whole_tier_without_file_allowlist() -> None:
    """One canonical backend-tier command, no raw pytest, and no file selected.

    Until 017 stage 2c this asserted exactly one `-BackendMarker postgres -BackendSelector
    tests/integration` command. The job now runs ONE session of the whole tier, so "no file
    allowlist" became "no selector at all": the matrix and the former marker tier are collected by
    the tier itself.
    """
    workflow = (_ROOT / ".github" / "workflows" / "quality.yml").read_text(
        encoding="utf-8"
    )
    postgres_jobs = _indented_blocks(workflow, key=_POSTGRES_CI_JOB, indent=2)

    assert len(postgres_jobs) == 1
    run_blocks = _indented_blocks(postgres_jobs[0], key="run", indent=8)
    commands = [_executable_command(block) for block in run_blocks]
    raw_pytest_commands = [
        command
        for command in commands
        if re.match(r"^python(?:\.exe)? -m pytest\b", command, flags=re.IGNORECASE)
    ]
    tier_commands = [
        command
        for command in commands
        if command.startswith("./scripts/verify_local.ps1 ")
    ]

    assert raw_pytest_commands == []
    assert len(tier_commands) == 1, tier_commands
    command = tier_commands[0]
    assert "-TaskSlug ci-required-backend" in command
    assert "-BackendOnly" in command
    assert "-BackendSelector" not in command
    assert "-BackendMarker" not in command
    assert ".py" not in command


def test_postgres_ci_job_uses_production_migration_entrypoint() -> None:
    """Architecture guard: keep the long-revision preflight on the CI path."""
    workflow = (_ROOT / ".github" / "workflows" / "quality.yml").read_text(
        encoding="utf-8"
    )
    postgres_jobs = _indented_blocks(workflow, key=_POSTGRES_CI_JOB, indent=2)

    assert len(postgres_jobs) == 1
    run_blocks = _indented_blocks(postgres_jobs[0], key="run", indent=8)
    commands = [_executable_command(block) for block in run_blocks]
    migration_blocks = [
        block for block in run_blocks if "scripts/check_alembic_heads.py" in block
    ]

    assert len(migration_blocks) == 1
    migration_lines = [
        line.strip()
        for line in migration_blocks[0].splitlines()[1:]
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert migration_lines == [
        "python scripts/check_alembic_heads.py",
        "bash docker/docker-entrypoint.sh true",
    ]
    direct_upgrade = re.compile(r"(?:^|\s)(?:python\s+-m\s+)?alembic\b.*\bupgrade\b")
    assert not any(direct_upgrade.search(command) for command in commands)
