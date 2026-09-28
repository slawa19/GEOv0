"""R-021-7 (programme 021, stage 3, `T2104`): architecture guard - the adaptive clearing mode is gone from `app/`.

WHAT IT PINS (spec, "Решения" item 1; F-021-13). The mode was switched on only by the raw environment variable
`SIMULATOR_CLEARING_POLICY=adaptive` and configured by 14 more raw reads - 15 in all, none of them in
`app/config.py`. After stage 3, in every Python module under `app/`:

1. nothing imports `adaptive_clearing_policy` (`import ...`, `from ... import`, or the dotted module name as a
   string, which is how `importlib` would reach it), and the module file itself does not exist;
2. none of the 15 variable names appears - neither as a string (an `os.getenv`/`safe_*_env` read) nor as an
   identifier (a settings field, which pydantic would map to the same variable case-insensitively). The rule is
   by shape, not by list: `SIMULATOR_CLEARING_POLICY` exactly, or anything that starts with
   `SIMULATOR_CLEARING_ADAPTIVE_`; the 15 listed names are the counter-check that the shape covers them all;
3. no name of the removed policy classes (`AdaptiveClearingPolicy`, `AdaptiveClearingPolicyConfig`,
   `AdaptiveClearingState`, `TickSignals`) is referenced.

WHAT IT DOES NOT SEE. It reads syntax: a variable name assembled at run time from fragments
(`"SIMULATOR_CLEARING_" + suffix`), a read outside `app/` (scripts, CI, a shell profile), or the same behaviour
reintroduced under new names is invisible to it. A green run says the listed shapes are absent from `app/`, not
that no adaptive behaviour exists - `tests/integration/test_simulator_static_clearing_cadence.py` is what shows
the static path still clears on the cadence tick only. The counter-checks at the bottom plant each refused shape
and show it is seen, and one benign sibling (`SIMULATOR_CLEARING_EVERY_N_TICKS`) is shown NOT to be.
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests.p021_support import require_target

REPO = Path(__file__).resolve().parents[2]
SCANNED_DIR = "app"

REMOVED_MODULE = "adaptive_clearing_policy"
REMOVED_MODULE_PATH = "app/core/simulator/adaptive_clearing_policy.py"
REMOVED_CLASSES = frozenset(
    {"AdaptiveClearingPolicy", "AdaptiveClearingPolicyConfig", "AdaptiveClearingState", "TickSignals"}
)

POLICY_VARIABLE = "SIMULATOR_CLEARING_POLICY"
ADAPTIVE_PREFIX = "SIMULATOR_CLEARING_ADAPTIVE_"

#: The 15 raw reads of F-021-13 (anchors on `4761c81`): the switch (`real_runner_impl.py:176`), twelve knobs
#: (`:182-196`) and two tick caps (`real_tick_clearing_coordinator.py:349-350`). Kept only to prove the shape
#: rule above covers every one of them - the scan itself does not depend on this list.
F_021_13_VARIABLES = (
    "SIMULATOR_CLEARING_POLICY",
    "SIMULATOR_CLEARING_ADAPTIVE_WARMUP_FALLBACK_CADENCE",
    "SIMULATOR_CLEARING_ADAPTIVE_WINDOW_TICKS",
    "SIMULATOR_CLEARING_ADAPTIVE_NO_CAPACITY_HIGH",
    "SIMULATOR_CLEARING_ADAPTIVE_NO_CAPACITY_LOW",
    "SIMULATOR_CLEARING_ADAPTIVE_MIN_INTERVAL_TICKS",
    "SIMULATOR_CLEARING_ADAPTIVE_BACKOFF_MAX_INTERVAL_TICKS",
    "SIMULATOR_CLEARING_ADAPTIVE_TIME_BUDGET_MS_MIN",
    "SIMULATOR_CLEARING_ADAPTIVE_TIME_BUDGET_MS_MAX",
    "SIMULATOR_CLEARING_ADAPTIVE_MAX_DEPTH_MIN",
    "SIMULATOR_CLEARING_ADAPTIVE_MAX_DEPTH_MAX",
    "SIMULATOR_CLEARING_ADAPTIVE_INFLIGHT_THRESHOLD",
    "SIMULATOR_CLEARING_ADAPTIVE_QUEUE_DEPTH_THRESHOLD",
    "SIMULATOR_CLEARING_ADAPTIVE_TICK_BUDGET_MS",
    "SIMULATOR_CLEARING_ADAPTIVE_MAX_EQ_PER_TICK",
)


def _is_adaptive_variable(text: str) -> bool:
    upper = text.upper()
    return upper == POLICY_VARIABLE or upper.startswith(ADAPTIVE_PREFIX)


def _identifiers(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, ast.Attribute):
        return [node.attr]
    if isinstance(node, ast.arg):
        return [node.arg]
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return [node.name]
    if isinstance(node, ast.keyword) and node.arg is not None:
        return [node.arg]
    if isinstance(node, ast.alias):
        return [node.name.rsplit(".", 1)[-1]] + ([node.asname] if node.asname else [])
    return []


def scan_source(source: str, module: str) -> list[str]:
    """Every adaptive-mode shape in one module's source, as `module:line: what`."""

    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        line = getattr(node, "lineno", 0)
        if isinstance(node, ast.Import):
            for alias in node.names:
                if REMOVED_MODULE in alias.name.split("."):
                    found.append(f"{module}:{line}: import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            parts = (node.module or "").split(".") + [alias.name for alias in node.names]
            if REMOVED_MODULE in parts:
                found.append(f"{module}:{line}: from {node.module} import ...")
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if _is_adaptive_variable(node.value):
                found.append(f"{module}:{line}: reads {node.value!r}")
            elif REMOVED_MODULE in node.value.split("."):
                found.append(f"{module}:{line}: names the module {node.value!r}")
        for name in _identifiers(node):
            if _is_adaptive_variable(name):
                found.append(f"{module}:{line}: identifier {name}")
            elif name in REMOVED_CLASSES:
                found.append(f"{module}:{line}: references {name}")
    return found


def _modules() -> list[Path]:
    return sorted((REPO / SCANNED_DIR).rglob("*.py"))


def _scan() -> list[str]:
    found: list[str] = []
    for path in _modules():
        module = path.relative_to(REPO).as_posix()
        found.extend(scan_source(path.read_text(encoding="utf-8"), module))
    if (REPO / REMOVED_MODULE_PATH).exists():
        found.append(f"{REMOVED_MODULE_PATH}: the module still exists")
    return found


def test_no_module_in_app_reaches_the_adaptive_clearing_mode() -> None:
    found = _scan()
    require_target(not found, "adaptive clearing mode still reachable from app/:\n" + "\n".join(found))


# --- counter-checks: the guard is not vacuous -------------------------------------------------------------


def test_the_scan_reads_the_simulator_modules_that_carried_the_mode() -> None:
    modules = {path.relative_to(REPO).as_posix() for path in _modules()}
    for carrier in (
        "app/config.py",
        "app/core/simulator/real_runner_impl.py",
        "app/core/simulator/real_tick_clearing_coordinator.py",
    ):
        assert carrier in modules, f"the scan does not read {carrier}"


def test_the_shape_rule_covers_every_one_of_the_15_reads() -> None:
    assert len(F_021_13_VARIABLES) == 15
    assert len(set(F_021_13_VARIABLES)) == 15
    for variable in F_021_13_VARIABLES:
        assert _is_adaptive_variable(variable), variable


def test_each_refused_shape_is_seen() -> None:
    planted = {
        "env read": 'import os\nos.getenv("SIMULATOR_CLEARING_POLICY", "static")\n',
        "helper read": '_safe_int_env("SIMULATOR_CLEARING_ADAPTIVE_TICK_BUDGET_MS", 0)\n',
        "settings field": "class Settings:\n    SIMULATOR_CLEARING_ADAPTIVE_WINDOW_TICKS: int = 30\n",
        "lowercase settings field": "class Settings:\n    simulator_clearing_policy: str = 'static'\n",
        "from-import": "from app.core.simulator.adaptive_clearing_policy import AdaptiveClearingPolicyConfig\n",
        "package from-import": "from app.core.simulator import adaptive_clearing_policy\n",
        "plain import": "import app.core.simulator.adaptive_clearing_policy as acp\n",
        "dynamic import": 'import importlib\nimportlib.import_module("app.core.simulator.adaptive_clearing_policy")\n',
        "class reference": "state = AdaptiveClearingState(cfg)\n",
    }
    for label, source in planted.items():
        assert scan_source(source, f"planted/{label}.py"), f"the guard is blind to a planted {label}"


def test_a_static_clearing_sibling_is_not_mistaken_for_the_mode() -> None:
    benign = (
        'import os\n'
        'os.getenv("SIMULATOR_CLEARING_EVERY_N_TICKS", "25")\n'
        '_safe_int_env("SIMULATOR_CLEARING_MAX_DEPTH", 6)\n'
        '_safe_int_env("SIMULATOR_REAL_CLEARING_TIME_BUDGET_MS", 250)\n'
        "clearing_policy = 'auto_clearing'\n"
    )
    assert scan_source(benign, "planted/benign.py") == []
