"""R-021-9 (programme 021, `T2109`): architecture guard - no clearing driver, no `real_runner.py` shim; the tick
calls the common clearing runner itself.

WHAT IT PINS (spec 021, "Стадии", row `T2109`; spec 023, slice (d), decisions 10 and R4). After 023 (d) the tick's
clearing went `RealTick._run_clearing` -> `RealClearingEngine.tick_real_mode_clearing` -> the runner, through a
`clearing_pass` seam that wrapped the runner's `on_committed`; `real_runner.py` was a subclass kept only as a
monkeypatch hook. After `T2109`, over every Python module under `app/`, `tests/` and `scripts/`:

1. neither module file `app/core/simulator/real_clearing_engine.py` nor `app/core/simulator/real_runner.py` exists;
2. nothing imports either module (`import ...`, `from ... import`, or the dotted name as a string, which is how
   `importlib` would reach it); `real_runner_impl` is a different module and is not refused;
3. none of the driver's dead names is defined or used as an identifier, attribute, parameter or keyword:
   `RealClearingEngine`, `tick_real_mode_clearing`, `_real_clearing_engine`, `clearing_service_cls`,
   `max_depth_override`, `time_budget_ms_override`, `clearing_max_depth_limit`;
4. under `app/core/simulator/`, the runner entry `run_clearing_pass` is called exactly ONCE, inside
   `RealTick._run_clearing` in `tick.py`, and that call passes the run perimeter (`allowed_participant_pids`), the
   caller deadline (`deadline`) and the tick's own progress callback (`on_committed`) by keyword - no wrapper that
   forwards `**kwargs` stands between the tick and the runner.

WHAT IT DOES NOT SEE. It reads syntax. A driver rebuilt under another name, a module imported through a name
assembled at run time, or a second call of the runner through `getattr` are invisible to it. A green run says the
listed shapes are absent - not that clearing behaves as before; that is what the tick clearing suites show
(`tests/unit/test_tick_clearing_publishes_progress.py`, `tests/integration/test_p023_d_tick_driver_through_runner_
postgres.py`, `tests/integration/test_simulator_static_clearing_cadence.py`). The counter-checks below plant each
refused shape and show it is seen, and show the benign siblings are not.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from tests.p021_support import require_target

REPO = Path(__file__).resolve().parents[2]
SCANNED_DIRS = ("app", "tests", "scripts")
REMOVED_FILES = ("app/core/simulator/real_clearing_engine.py", "app/core/simulator/real_runner.py")
REMOVED_COMPONENTS = frozenset({"real_clearing_engine", "real_runner"})
DEAD_NAMES = frozenset(
    {
        "RealClearingEngine",
        "tick_real_mode_clearing",
        "_real_clearing_engine",
        "clearing_service_cls",
        "max_depth_override",
        "time_budget_ms_override",
        "clearing_max_depth_limit",
    }
)
TICK_MODULE = "app/core/simulator/tick.py"
TICK_FUNCTION = "_run_clearing"
RUNNER_ENTRY = "run_clearing_pass"
REQUIRED_KEYWORDS = frozenset({"allowed_participant_pids", "deadline", "on_committed"})
_DOTTED_MODULE = re.compile(r"^app(\.\w+)+$")


def _names_a_removed_module(dotted: str) -> bool:
    return any(part in REMOVED_COMPONENTS for part in dotted.split("."))


def scan_source(source: str, module: str) -> list[str]:
    """The refused shapes (rules 2 and 3) in one module's source, each as `module:line: what`."""

    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        line = getattr(node, "lineno", 0)
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _names_a_removed_module(alias.name):
                    found.append(f"{module}:{line}: import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            dotted = ".".join([node.module or ""] + [alias.name for alias in node.names])
            if _names_a_removed_module(dotted):
                found.append(f"{module}:{line}: from {node.module} import ...")
            found.extend(f"{module}:{line}: imports {a.name}" for a in node.names if a.name in DEAD_NAMES)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            # A dotted module path as a string (`importlib.import_module("app.core.simulator.real_runner")`), not
            # prose or a file path: only `app.x.y` with nothing else in it.
            if _DOTTED_MODULE.match(node.value) and _names_a_removed_module(node.value):
                found.append(f"{module}:{line}: names the module {node.value!r}")
        elif isinstance(node, ast.Name) and node.id in DEAD_NAMES:
            found.append(f"{module}:{line}: name {node.id}")
        elif isinstance(node, ast.Attribute) and node.attr in DEAD_NAMES:
            found.append(f"{module}:{line}: attribute .{node.attr}")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name in DEAD_NAMES:
            found.append(f"{module}:{line}: defines {node.name}")
        elif isinstance(node, ast.arg) and node.arg in DEAD_NAMES:
            found.append(f"{module}:{line}: parameter {node.arg}")
        elif isinstance(node, ast.keyword) and node.arg in DEAD_NAMES:
            found.append(f"{module}:{line}: keyword {node.arg}=")
    return found


def runner_calls(source: str) -> list[tuple[str | None, int, frozenset[str]]]:
    """Every call of the runner entry: (enclosing top-level function or method, line, keywords passed by name).

    A call inside a nested function (a wrapper) is attributed to the method that encloses it; the wrapper shows up
    as the missing keywords, because it forwards them through `**kwargs`.
    """

    calls: list[tuple[str | None, int, frozenset[str]]] = []

    def visit(node: ast.AST, function: str | None) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.Call):
                func = child.func
                name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else None
                if name == RUNNER_ENTRY:
                    keywords = frozenset(k.arg for k in child.keywords if k.arg is not None)
                    calls.append((function, child.lineno, keywords))
            visit(child, function)

    for top in ast.parse(source).body:
        if isinstance(top, ast.ClassDef):
            for item in top.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    visit(item, item.name)
        elif isinstance(top, (ast.FunctionDef, ast.AsyncFunctionDef)):
            visit(top, top.name)
    return calls


def _modules(root: Path = REPO) -> list[Path]:
    paths: list[Path] = []
    for directory in SCANNED_DIRS:
        paths.extend(sorted((root / directory).rglob("*.py")))
    return paths


def _scan(root: Path = REPO) -> list[str]:
    found = [f"{relative}: the module file exists" for relative in REMOVED_FILES if (root / relative).exists()]
    for path in _modules(root):
        module = path.relative_to(root).as_posix()
        found.extend(scan_source(path.read_text(encoding="utf-8"), module))
    return found


def _runner_call_problems(root: Path = REPO) -> list[str]:
    problems: list[str] = []
    sites: list[str] = []
    for path in sorted((root / "app" / "core" / "simulator").rglob("*.py")):
        module = path.relative_to(root).as_posix()
        for function, line, keywords in runner_calls(path.read_text(encoding="utf-8")):
            sites.append(f"{module}:{line} in {function}")
            if module != TICK_MODULE or function != TICK_FUNCTION:
                problems.append(f"{module}:{line}: the runner is called from {function}, not {TICK_FUNCTION}")
            elif missing := sorted(REQUIRED_KEYWORDS - keywords):
                problems.append(f"{module}:{line}: the tick's runner call does not pass {missing} by keyword")
    if len(sites) != 1:
        problems.append(f"the runner entry is called at {sites or 'no site'} under app/core/simulator, not once")
    return problems


def test_no_driver_no_shim_and_the_tick_calls_the_runner_itself() -> None:
    problems = _scan() + _runner_call_problems()
    require_target(not problems, "the clearing driver or its shim is still here:\n" + "\n".join(problems))


# --- counter-checks: the guard is not vacuous -------------------------------------------------------------


def test_the_scan_reads_the_tick_the_runner_the_tests_and_the_scripts() -> None:
    modules = {path.relative_to(REPO).as_posix() for path in _modules()}
    for carrier in (
        TICK_MODULE,
        "app/core/simulator/real_runner_impl.py",
        "tests/simulator_tick_stand.py",
        "scripts/measure_p020_dfs_acceptance.py",
    ):
        assert carrier in modules, f"the scan does not read {carrier}"


def test_each_refused_shape_is_seen() -> None:
    planted = {
        "from-import": "from app.core.simulator.real_clearing_engine import RealClearingEngine\n",
        "package from-import": "from app.core.simulator import real_runner\n",
        "plain import": "import app.core.simulator.real_runner as real_runner_mod\n",
        "dynamic import": 'import importlib\nimportlib.import_module("app.core.simulator.real_clearing_engine")\n',
        "driver method call": "async def f(r):\n    return await r.tick_real_mode_clearing(None)\n",
        "driver attribute": "def f(r):\n    return r._real_clearing_engine\n",
        "dead parameter": "def f(*, max_depth_override=None):\n    return None\n",
        "dead keyword": "f(clearing_service_cls=object)\n",
        "dead constructor keyword": "f(clearing_max_depth_limit=6)\n",
        "budget override": "def f(time_budget_ms_override):\n    return None\n",
        "driver class": "class RealClearingEngine:\n    pass\n",
    }
    for label, source in planted.items():
        assert scan_source(source, f"planted/{label}.py"), f"the guard is blind to a planted {label}"


def test_a_re_added_module_file_is_seen(tmp_path) -> None:
    for relative in REMOVED_FILES:
        planted = tmp_path / relative
        planted.parent.mkdir(parents=True, exist_ok=True)
        planted.write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "scripts").mkdir()
    assert _scan(tmp_path) == [f"{relative}: the module file exists" for relative in REMOVED_FILES]


def test_the_runner_call_check_sees_a_wrapper_and_a_second_site() -> None:
    wrapped = (
        "class RealTick:\n"
        "    async def _run_clearing(self):\n"
        "        async def _recording_pass(f, eq, *, on_committed=None, **kwargs):\n"
        "            return await clearing_runner.run_clearing_pass(f, eq, on_committed=on_committed, **kwargs)\n"
        "        return await _recording_pass(None, 'X')\n"
    )
    [(function, _, keywords)] = runner_calls(wrapped)
    assert function == TICK_FUNCTION and REQUIRED_KEYWORDS - keywords == {"allowed_participant_pids", "deadline"}
    direct = (
        "class RealTick:\n"
        "    async def _run_clearing(self):\n"
        "        return await clearing_runner.run_clearing_pass(\n"
        "            f, eq, allowed_participant_pids=p, on_committed=cb, deadline=d)\n"
        "    async def other(self):\n"
        "        return await run_clearing_pass(f, eq)\n"
    )
    calls = runner_calls(direct)
    assert [(c[0], c[2] >= REQUIRED_KEYWORDS) for c in calls] == [(TICK_FUNCTION, True), ("other", False)]


def test_benign_siblings_are_not_mistaken_for_the_removed_driver() -> None:
    benign = (
        "from app.core.simulator.real_runner_impl import RealRunnerImpl\n"
        "import app.core.simulator.real_runner_impl as real_runner_impl\n"
        "runtime._real_runner.fail_run\n"
        "note = 'real_runner.py and real_clearing_engine.py were removed by T2109'\n"
        "path = 'app/core/simulator/real_clearing_engine.py'\n"
        "from app.core.clearing.runner import run_clearing_pass\n"
        "async def maybe_run_clearing(self, *, max_depth=None):\n    return None\n"
    )
    assert scan_source(benign, "planted/benign.py") == []
