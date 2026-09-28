"""R-021-8 (programme 021, stage 4, `T2105`): architecture guard - the tick is one module, with no runner port.

WHAT IT PINS (spec, "Стадии", stage 4; Problem item 4). Before stage 4 the real-mode tick was spread over six
`real_tick_*` modules that reached back into the runner through a 19-attribute protocol `_RealRunnerPort`. After
stage 4, over every Python module under `app/`:

1. no module file `app/core/simulator/real_tick_*.py` exists - by shape, not by the list of six;
2. nothing imports a module whose dotted name has a `real_tick_*` component (`import ...`, `from ... import`, or the
   dotted name as a string, which is how `importlib` would reach it);
3. no class whose name contains `RunnerPort` is defined;
4. the money phase is entered through `run_money_phase_with_bounded_replay` in exactly ONE call, and that call is in
   `app/core/simulator/tick.py`.

WHAT IT DOES NOT SEE. It reads syntax. The same port rebuilt under another name, a module imported through a name
assembled at run time, or a second money phase that does not call the bounded replay are invisible to it. A green
run says the listed shapes are absent - not that the tick's behaviour is unchanged; that is what the tick suites
listed in the spec's Verification plan item 3 ("Тик") show. The counter-checks below plant each refused shape and
show it is seen, and show a benign sibling (`write_real_tick_artifact`, a method of the artifacts manager) is not.
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests.p021_support import require_target, target_xfail_021

REPO = Path(__file__).resolve().parents[2]
SCANNED_DIR = "app"
TICK_MODULE = "app/core/simulator/tick.py"
MONEY_ENTRY = "run_money_phase_with_bounded_replay"
REMOVED_PREFIX = "real_tick_"
PORT_SHAPE = "RunnerPort"


def _names_a_removed_module(dotted: str) -> bool:
    return any(part.startswith(REMOVED_PREFIX) for part in dotted.split("."))


def scan_source(source: str, module: str) -> tuple[list[str], list[str]]:
    """(refused shapes, money-entry call sites) in one module's source, each as `module:line: what`."""

    found: list[str] = []
    calls: list[str] = []
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
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            value = node.value
            # A dotted module path as a string (`importlib.import_module("app.core.simulator.real_tick_x")`), not
            # prose: prose has spaces, and a bare method name (`write_real_tick_artifact`) has no dot.
            if " " not in value and "." in value and _names_a_removed_module(value):
                found.append(f"{module}:{line}: names the module {value!r}")
        elif isinstance(node, ast.ClassDef) and PORT_SHAPE in node.name:
            found.append(f"{module}:{line}: class {node.name}")
        elif isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else None
            if name == MONEY_ENTRY:
                calls.append(f"{module}:{line}")
    return found, calls


def _modules(root: Path = REPO) -> list[Path]:
    return sorted((root / SCANNED_DIR).rglob("*.py"))


def _scan(root: Path = REPO) -> tuple[list[str], list[str]]:
    found: list[str] = []
    calls: list[str] = []
    for path in _modules(root):
        module = path.relative_to(root).as_posix()
        if path.name.startswith(REMOVED_PREFIX):
            found.append(f"{module}: a real_tick_* module exists")
        module_found, module_calls = scan_source(path.read_text(encoding="utf-8"), module)
        found.extend(module_found)
        calls.extend(module_calls)
    return found, calls


@target_xfail_021("T2105 (stage 4)", "six real_tick_* modules and the runner port `_RealRunnerPort`")
def test_the_tick_is_one_module_without_a_runner_port() -> None:
    found, calls = _scan()
    problems = list(found)
    if not (REPO / TICK_MODULE).exists():
        problems.append(f"{TICK_MODULE}: the tick module does not exist")
    if len(calls) != 1 or not calls[0].startswith(f"{TICK_MODULE}:"):
        problems.append(f"the money phase is entered at {calls or 'no call site'}, not once in {TICK_MODULE}")
    require_target(not problems, "the tick is not one module without a runner port:\n" + "\n".join(problems))


# --- counter-checks: the guard is not vacuous -------------------------------------------------------------


def test_the_scan_reads_the_runner_and_the_money_boundary() -> None:
    modules = {path.relative_to(REPO).as_posix() for path in _modules()}
    for carrier in ("app/core/simulator/real_runner_impl.py", "app/core/simulator/money_replay.py"):
        assert carrier in modules, f"the scan does not read {carrier}"


def test_each_refused_shape_is_seen() -> None:
    planted = {
        "from-import": "from app.core.simulator.real_tick_metrics import RealTickMetrics\n",
        "package from-import": "from app.core.simulator import real_tick_persistence\n",
        "plain import": "import app.core.simulator.real_tick_orchestrator as orchestrator\n",
        "dynamic import": 'import importlib\nimportlib.import_module("app.core.simulator.real_tick_payments_coordinator")\n',
        "runner port": "from typing import Protocol\nclass _RealRunnerPort(Protocol):\n    _lock: object\n",
        "renamed port": "class TickRunnerPort:\n    pass\n",
    }
    for label, source in planted.items():
        found, _ = scan_source(source, f"planted/{label}.py")
        assert found, f"the guard is blind to a planted {label}"


def test_every_money_entry_call_is_counted() -> None:
    source = (
        "from app.core.simulator import money_replay\n"
        "from app.core.simulator.money_replay import run_money_phase_with_bounded_replay\n"
        "async def a():\n    return await run_money_phase_with_bounded_replay(run_id='r')\n"
        "async def b():\n    return await money_replay.run_money_phase_with_bounded_replay(run_id='r')\n"
    )
    _, calls = scan_source(source, "planted/two_calls.py")
    assert len(calls) == 2, calls


def test_a_re_added_module_file_is_seen_by_its_shape(tmp_path) -> None:
    planted = tmp_path / "app" / "core" / "simulator" / "real_tick_anything.py"
    planted.parent.mkdir(parents=True)
    planted.write_text("x = 1\n", encoding="utf-8")
    found, _ = _scan(tmp_path)
    assert found == ["app/core/simulator/real_tick_anything.py: a real_tick_* module exists"], found


def test_benign_siblings_are_not_mistaken_for_the_removed_modules() -> None:
    benign = (
        "artifacts.write_real_tick_artifact(run, {'tick_index': 1})\n"
        "def write_real_tick_artifact(self, run, payload):\n    return None\n"
        "note = 'the real_tick_* modules were absorbed by tick.py'\n"
        "from app.core.simulator.tick import RealTick\n"
    )
    found, calls = scan_source(benign, "planted/benign.py")
    assert found == [] and calls == []
