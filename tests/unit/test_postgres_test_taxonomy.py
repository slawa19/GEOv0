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


def _is_skipif_mark(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "skipif"
        and isinstance(node.func.value, ast.Attribute)
        and node.func.value.attr == "mark"
    )


#: What makes a condition a question about WHICH DATABASE the tier runs on: the tier's URL, its environment variable, the
#: driver or backend name, or the fixture's dialect. Since 017 stage 2c `tests/conftest.py` refuses anything but a PostgreSQL
#: `TEST_DATABASE_URL` before collection, so such a condition can never be true and a skip behind it is dead code.
_DATABASE_QUESTION_NAMES = {"TEST_DATABASE_URL", "dialect"}
_DATABASE_QUESTION_ATTRIBUTES = {"dialect", "get_backend_name"}
_DATABASE_QUESTION_STRINGS = ("postgres", "TEST_DATABASE_URL", "sqlite")


def _asks_which_database(condition: ast.AST) -> bool:
    for node in ast.walk(condition):
        if isinstance(node, ast.Name) and node.id in _DATABASE_QUESTION_NAMES:
            return True
        if isinstance(node, ast.Attribute) and node.attr in _DATABASE_QUESTION_ATTRIBUTES:
            return True
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if any(token in node.value.lower() for token in (t.lower() for t in _DATABASE_QUESTION_STRINGS)):
                return True
    return False


def _skip_sites(tree: ast.Module) -> list[tuple[int, str, bool]]:
    """Every executable `pytest.skip(...)` call and every `pytest.mark.skipif(...)` mark of a module.

    Each is `(line, source of its condition, whether the condition asks which database the tier runs on)`. A `pytest.skip()`
    call's condition is the nearest enclosing `if` test; a `skipif`'s is its first argument. A skip with no enclosing `if`
    has the condition `<unconditional>`. Calls inside docstrings or comments are not nodes, so prose is never read.
    """

    sites: list[tuple[int, str, bool]] = []

    def visit(node: ast.AST, conditions: tuple[ast.AST, ...]) -> None:
        if isinstance(node, ast.If):
            inner = conditions + (node.test,)
            for child in node.body + node.orelse:  # the else branch answers the same question
                visit(child, inner)
            visit(node.test, conditions)
            return
        if _is_pytest_skip(node):
            asked = conditions[-1] if conditions else None
            sites.append(
                (
                    node.lineno,
                    ast.unparse(asked) if asked is not None else "<unconditional>",
                    asked is not None and any(_asks_which_database(c) for c in conditions),
                )
            )
        elif _is_skipif_mark(node) and node.args:
            sites.append((node.lineno, ast.unparse(node.args[0]), _asks_which_database(node.args[0])))
        for child in ast.iter_child_nodes(node):
            visit(child, conditions)

    visit(tree, ())
    return sites


#: THE ONE LIVE SKIP of `tests/integration` (2026-10-10, 035 Q-A): the simulator's storage can be switched off by
#: `SIMULATOR_DB_ENABLED` (`app/core/simulator/storage.py`, `app/config.py`), which no tier precondition covers. Every other
#: executable skip in this directory needs its own line here, with its reason, or it is refused.
_LIVE_SKIPS = {
    ("test_p1_tick_session_ownership_postgres.py", "not simulator_storage.db_enabled()"),
}


def _integration_sources() -> dict[str, ast.Module]:
    return {
        path.name: ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for path in sorted(_INTEGRATION_ROOT.glob("*.py"))
    }


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
# stays as history. The dialect skips it used to own were removed on 2026-10-10 (035 Q-A); what
# replaces that sub-check is `test_no_integration_test_skips_because_of_the_database_url_or_dialect`.
def test_no_module_takes_itself_out_of_the_postgres_tier() -> None:
    modules = _parse_test_modules()
    candidates = {
        path.relative_to(_ROOT)
        for path in modules
        if path.name.endswith("_postgres.py")
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


# 035 Q-A (2026-10-10): 24 skips behind "is this PostgreSQL?" were removed from `tests/integration`: the question has one
# answer on every supported path, so a skip behind it is dead code that reads as a safety net. The replacement invariant:
# an integration test is not skipped because of the database URL or dialect, and the only other skip is the one named live.
_PLANTED_DATABASE_SKIPS = {
    "a URL check": 'if "postgresql" not in TEST_DATABASE_URL:\n    pytest.skip("x")\n',
    "an environment URL check": 'url = os.environ.get("TEST_DATABASE_URL", "")\nif "postgresql" not in url:\n    pytest.skip("x")\n',
    "a dialect check": 'if db_session.get_bind().dialect.name not in {"postgresql"}:\n    pytest.skip("x")\n',
    "a dialect variable (the try/except form)": (
        'dialect = None\ntry:\n    dialect = db_session.get_bind().dialect.name\nexcept Exception:\n    dialect = None\n'
        'if dialect not in {"postgresql", "postgres"}:\n    pytest.skip("x")\n'
    ),
    "a helper's check": 'def _require_postgres(s):\n    if s.get_bind().dialect.name != "postgresql":\n        pytest.skip("x")\n',
    "a skipif mark": '@pytest.mark.skipif("postgresql" not in TEST_DATABASE_URL, reason="x")\ndef test_a(): pass\n',
    "a skipif mark with the keyword condition=": (
        '@pytest.mark.skipif(condition="postgresql" not in TEST_DATABASE_URL, reason="x")\ndef test_a(): pass\n'
    ),
    "an else branch": 'if "postgresql" in url:\n    pass\nelse:\n    pytest.skip("x")\n',
}
_PLANTED_OTHER_SKIPS = {
    "the live storage skip": 'if not simulator_storage.db_enabled():\n    pytest.skip("x")\n',
    "an unrelated optional-tool skip": 'if shutil.which("bash") is None:\n    pytest.skip("x")\n',
    "an unconditional skip": 'pytest.skip("x")\n',
    "a skipif on the platform": '@pytest.mark.skipif(os.name == "nt", reason="x")\ndef test_a(): pass\n',
    "a skipif on the platform with the keyword condition=": (
        '@pytest.mark.skipif(condition=os.name == "nt", reason="x")\ndef test_a(): pass\n'
    ),
}


def test_the_database_skip_detector_sees_every_form_and_only_those() -> None:
    """Anti-vacuum for the invariant below: planted positives are found, planted negatives are not flagged as database skips."""

    for label, source in _PLANTED_DATABASE_SKIPS.items():
        sites = _skip_sites(ast.parse(source))
        assert sites and all(asks for _, _, asks in sites), f"{label}: not recognised as a database skip: {sites}"
    for label, source in _PLANTED_OTHER_SKIPS.items():
        sites = _skip_sites(ast.parse(source))
        assert sites, f"{label}: the detector did not see the skip at all"
        assert not any(asks for _, _, asks in sites), f"{label}: wrongly flagged as a database skip: {sites}"
    assert _skip_sites(ast.parse('"""pytest.skip(x) in prose"""\n# pytest.skip(y)\nx = 1\n')) == []


def test_no_integration_test_skips_because_of_the_database_url_or_dialect() -> None:
    sources = _integration_sources()
    assert len(sources) >= 100, f"only {len(sources)} modules under tests/integration were read"
    database_skips: list[str] = []
    other_skips: set[tuple[str, str]] = set()
    for name, tree in sources.items():
        for line, condition, asks_which_database in _skip_sites(tree):
            if asks_which_database:
                database_skips.append(f"{name}:{line}: {condition}")
            else:
                other_skips.add((name, condition))
    assert database_skips == [], (
        "an integration test is skipped by a question about the database URL or dialect; "
        f"tests/conftest.py refuses a non-PostgreSQL URL before collection, so the skip is dead: {database_skips}"
    )
    assert other_skips == _LIVE_SKIPS, (
        f"executable skips under tests/integration other than the named live one: {sorted(other_skips - _LIVE_SKIPS)}; "
        f"named but gone: {sorted(_LIVE_SKIPS - other_skips)}. A new conditional skip needs its own line in _LIVE_SKIPS "
        "with the reason it can really fire."
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
