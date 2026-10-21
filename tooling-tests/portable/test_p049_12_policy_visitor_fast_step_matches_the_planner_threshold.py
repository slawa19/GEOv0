"""POLICY GUARD (049-12): the visitor's «быстро» step and the planner's warm-up threshold are one number.

WHAT IS TWICE IN THE TREE. The visitor's three tempo steps (2 / 5 / 15 %) live in the UI only:
`simulator-ui/v2/src/components/TopBar.vue`, the `TEMPO_STEPS` literal. The backend's scenario warm-up ramps only
the tempo ABOVE the «быстро» step, and for that it holds the same 15 as `VISITOR_FAST_STEP_PERCENT`
(`app/core/simulator/real_payment_planner.py`). There is no API between them (the wire carries only the integer
`intensity_percent`; changing the API for one constant is out of proportion), so the number is kept in two places.

WHAT THIS GUARD CHECKS - AND ITS LIMITS. Only that the two LITERALS agree: the Python constant by an `ast` parse of
its module-level assignment (importing `app.core.simulator` pulls in `app.config`, which refuses to load without a
database URL, and this tier has none by design), the UI value by one narrow parse of the `TEMPO_STEPS` literal
(`{ id: 'fast', percent: N, ... }`). It does NOT check
behaviour: that the warm-up really leaves the three steps untouched is held by the warm-up tests of
`tests/unit/test_p049_s2a_fractional_action_budget.py`, which this guard does not replace. It reads no
documentation (documentation is outside the checks, AGENTS.md §11). If TopBar.vue is restructured so that the
literal is built another way, the parse finds nothing and the guard FAILS by name (it never passes on "not found") -
adjust `_ui_fast_percent` together with that change.
"""

import ast
import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_TOPBAR = _ROOT / "simulator-ui" / "v2" / "src" / "components" / "TopBar.vue"
_PLANNER = _ROOT / "app" / "core" / "simulator" / "real_payment_planner.py"

_WHAT_TO_DO = (
    "WHAT TO DO: the visitor's «быстро» step is one number kept in two places - change BOTH "
    "(`TEMPO_STEPS` in simulator-ui/v2/src/components/TopBar.vue and `VISITOR_FAST_STEP_PERCENT` in "
    "app/core/simulator/real_payment_planner.py), then re-check the warm-up tests "
    "(`.\\scripts\\verify_local.ps1 -TaskSlug <slug> -BackendOnly -BackendSelector "
    "tests/unit/test_p049_s2a_fractional_action_budget.py`) and the runbook "
    "(docs/ru/simulator/backend/real-mode-runbook.md). LIMITS: this guard compares the two literals; it does not "
    "check what the planner does with them."
)

_STEPS_LITERAL = re.compile(r"const\s+TEMPO_STEPS\s*=\s*\[(?P<body>.*?)\]\s*as\s+const", re.DOTALL)
_STEP = re.compile(r"\{\s*id:\s*'(?P<id>[a-z]+)'\s*,\s*percent:\s*(?P<percent>\d+)\s*,")


_COMMENTS = re.compile(r"/\*.*?\*/|//[^\n]*", re.DOTALL)


def _ui_steps(source: str) -> dict[str, int]:
    """`{id: percent}` of the one `TEMPO_STEPS` literal in the given TopBar.vue text, comments removed first (a commented-out
    literal or entry is not the UI value); empty when the literal is not found, ambiguous (two literals) or repeats an id."""
    code = _COMMENTS.sub("", source)
    literals = list(_STEPS_LITERAL.finditer(code))
    if len(literals) != 1:
        return {}
    entries = [(m["id"], int(m["percent"])) for m in _STEP.finditer(literals[0]["body"])]
    if len({i for i, _ in entries}) != len(entries):
        return {}
    return dict(entries)


def _ui_fast_percent(source: str) -> int:
    steps = _ui_steps(source)
    assert steps, (
        "the `TEMPO_STEPS` literal was not found in TopBar.vue (or has no `{ id: '..', percent: N, ...}` entries): "
        "the guard cannot read the UI value and refuses to pass on 'not found'. " + _WHAT_TO_DO
    )
    assert "fast" in steps, f"`TEMPO_STEPS` has no `fast` step (found {sorted(steps)}). " + _WHAT_TO_DO
    return steps["fast"]


def _planner_threshold(source: str) -> int:
    """Value of the module-level `VISITOR_FAST_STEP_PERCENT = <int literal>` in the given planner source."""
    found = [
        node.value.value
        for node in ast.parse(source).body
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "VISITOR_FAST_STEP_PERCENT" for t in node.targets)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, int)
        and not isinstance(node.value.value, bool)
    ]
    assert len(found) == 1, (
        f"expected exactly one module-level `VISITOR_FAST_STEP_PERCENT = <int literal>` in real_payment_planner.py, "
        f"found {len(found)}: the guard cannot read the backend value and refuses to pass on 'not found'. " + _WHAT_TO_DO
    )
    return found[0]


def test_the_parse_reads_a_literal_and_tells_values_apart():
    # Anti-vacuum for the parse itself: it must see all three steps of a literal in the real shape, and its answer must
    # follow the number written there - otherwise a parse that matches nothing or always answers 15 would pass the guard.
    shape = "const TEMPO_STEPS = [\n  { id: 'slow', percent: 2, key: 'a' },\n  { id: 'normal', percent: 5, key: 'b' },\n  { id: 'fast', percent: %d, key: 'c' },\n] as const"
    assert _ui_steps(shape % 15) == {"slow": 2, "normal": 5, "fast": 15}
    assert _ui_fast_percent(shape % 16) == 16
    assert _planner_threshold("A = 1\nVISITOR_FAST_STEP_PERCENT = 15  # c\n") == 15
    assert _planner_threshold("VISITOR_FAST_STEP_PERCENT = 14\n") == 14
    with pytest.raises(AssertionError):  # a bool is an int to Python; it is not a percent
        _planner_threshold("VISITOR_FAST_STEP_PERCENT = True\n")
    # Comments and ambiguity: a commented-out literal or entry is not the UI value, a repeated literal or id is refused.
    assert _ui_steps("// " + (shape % 15).replace("\n", "\n// ")) == {}
    assert _ui_steps("/* old: " + (shape % 99) + " */\n" + (shape % 15)) == {"slow": 2, "normal": 5, "fast": 15}
    assert _ui_steps((shape % 15).replace("{ id: 'fast', percent: 15, key: 'c' },", "// { id: 'fast', percent: 15, key: 'c' },")) == {"slow": 2, "normal": 5}
    assert _ui_steps(shape % 15 + "\n" + shape % 16) == {}
    assert _ui_steps((shape % 15).replace("] as const", "{ id: 'fast', percent: 30, key: 'd' },\n] as const")) == {}
    assert _ui_steps("const OTHER = [{ id: 'fast', percent: 15, key: 'c' }] as const") == {}


def test_the_ui_fast_step_percent_equals_the_planners_warmup_threshold():
    planner = _planner_threshold(_PLANNER.read_text(encoding="utf-8"))
    ui = _ui_fast_percent(_TOPBAR.read_text(encoding="utf-8"))
    assert ui == planner, (
        f"the UI «быстро» step is {ui} % (TopBar.vue `TEMPO_STEPS`) but the planner's warm-up threshold "
        f"`VISITOR_FAST_STEP_PERCENT` is {planner} %. " + _WHAT_TO_DO
    )
