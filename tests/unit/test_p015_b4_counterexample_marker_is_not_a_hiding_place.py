"""The `b4_counterexample` marker deselects exactly three modules, and never a fourth.

WHY THIS GUARD EXISTS. Programme 015 phase B step 2 put 46 counterexamples in the tree that are RED
ON PURPOSE: they are the recorded acceptance of step 4, written before the debt journal, and each
one fails because nothing in the tree has the property it names yet. They carry
`b4_counterexample` and the canonical runner deselects that marker, so a shared working tree's gate
stays a signal for the other sessions in it (`AGENTS.md` §7).

A marker that removes tests from the gate is exactly the shape of `AGENTS.md` §9's anti-vacuum rule
and §5's marker trap: the moment it can be applied to anything else, it stops being "the step-4
acceptance is pending" and becomes "this failure is inconvenient". So the allowlist below is
CLOSED. Adding a module to it is a deliberate, reviewable act, and it is the only way the marker
can spread.

WHAT THIS GUARD DOES NOT SEE, so its silence is not mistaken for more than it is:

* it reads `pytest.mark.b4_counterexample` written literally in the source. A marker added through
  `pytest_collection_modifyitems`, an `applymarker` call, a `parametrize` entry or an alias is
  invisible to it;
* it proves the runner's default expression EXCLUDES the marker by matching the string in
  `scripts/verify_local.ps1`. It does not run the runner, so it cannot prove the expression reaches
  pytest - the gate's own deselected count is what shows that;
* it says nothing about whether the counterexamples still assert what they were written to assert.
  Nothing can: that is what the per-test `MUTATION once step 4 exists` lines in their docstrings are
  for, and they have to be run by hand when step 4 lands.

THE CONTRACT THE MARKER ENCODES, repeated here because this is the file someone opens when they
want to know why a test is not running: STEP 4 REMOVES THE MARKER. It does not touch the
assertions. A counterexample made green by editing what it asserts has destroyed the only record of
what the journal was required to do.
"""

from __future__ import annotations

import ast
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_MARKER = "b4_counterexample"

#: The only modules allowed to carry the marker. Relative POSIX paths, so the failure message reads
#: the same on every platform.
_ALLOWED = {
    "tests/unit/test_p015_b4_transaction_contract.py",
    "tests/unit/test_p015_b4_write_guard.py",
    "tests/integration/test_p015_b4_transaction_contract_postgres.py",
}


def _modules_carrying_the_marker() -> set[str]:
    """Every module under `tests/` with a literal `pytest.mark.b4_counterexample` in its source."""
    carrying: set[str] = set()
    for path in sorted((_ROOT / "tests").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and node.attr == _MARKER
                and isinstance(node.value, ast.Attribute)
                and node.value.attr == "mark"
                and isinstance(node.value.value, ast.Name)
                and node.value.value.id == "pytest"
            ):
                carrying.add(path.relative_to(_ROOT).as_posix())
                break
    return carrying


def test_the_marker_is_carried_by_exactly_the_counterexample_modules() -> None:
    """The allowlist is closed in both directions."""
    carrying = _modules_carrying_the_marker()

    # ANTI-VACUUM, and it comes first. If the scan stops finding the marker - a rename, an ast
    # change, a module moved - `carrying` goes empty and the equality below would pass for a tree
    # where the marker is applied everywhere. An empty result is a broken scanner, not a clean tree.
    assert carrying, (
        f"no module under tests/ carries `pytest.mark.{_MARKER}`, so this guard measured nothing. "
        f"Either the counterexample modules lost their marker - in which case the canonical gate is "
        f"about to go permanently red - or this scan no longer recognises how it is written."
    )

    spread = sorted(carrying - _ALLOWED)
    assert not spread, (
        f"`{_MARKER}` appears on modules it does not belong to: {spread}. That marker means "
        f"'this test is the recorded acceptance of programme 015 phase B step 4 and is red until "
        f"step 4 exists'. It is not a way to take an ordinary failure out of the gate. If one of "
        f"these really is a step-2 counterexample, add it to `_ALLOWED` in this file deliberately."
    )
    missing = sorted(_ALLOWED - carrying)
    assert not missing, (
        f"these counterexample modules no longer carry `{_MARKER}`: {missing}. Either they were "
        f"deleted - which throws away the acceptance of step 4 - or they are about to run in the "
        f"canonical gate and redden it for every session sharing this tree."
    )


def test_the_marker_is_registered_so_strict_markers_can_see_it() -> None:
    """`--strict-markers` is on (`pytest.ini` addopts), so an unregistered marker is an error."""
    config = (_ROOT / "pytest.ini").read_text(encoding="utf-8")
    assert f"\n    {_MARKER}:" in config, (
        f"`{_MARKER}` is not registered in pytest.ini's `markers` list. With `--strict-markers` in "
        f"addopts an unregistered marker fails collection outright."
    )
    assert "--strict-markers" in config, (
        "pytest.ini no longer passes `--strict-markers`, so a typo in a marker name would silently "
        "select nothing instead of failing"
    )


def test_the_canonical_runner_excludes_the_marker_from_every_tier() -> None:
    """Every branch of the runner's marker expression must exclude it.

    Three branches, and all three matter: the default tier, `-IncludeExpensive`, and an explicitly
    requested `-BackendMarker`. The last one is why the PostgreSQL tier is not permanently red
    either. The escape hatch is naming the marker: an expression that mentions it is taken as
    deliberate and passed through untouched.
    """
    runner = (_ROOT / "scripts" / "verify_local.ps1").read_text(encoding="utf-8")

    assert f"'not slow and not postgres and not {_MARKER}'" in runner, (
        "the runner's DEFAULT marker expression no longer excludes the counterexamples; the SQLite "
        "tier is about to go permanently red"
    )
    assert f"'not postgres and not {_MARKER}'" in runner, (
        "the runner's -IncludeExpensive expression no longer excludes the counterexamples"
    )
    assert f'"$BackendMarker and not {_MARKER}"' in runner, (
        "an explicitly requested -BackendMarker no longer excludes the counterexamples, so the "
        "PostgreSQL tier goes permanently red"
    )
    assert f"'*{_MARKER}*'" in runner, (
        f"the runner no longer lets an expression that NAMES `{_MARKER}` through unchanged, so "
        f"there is no way to run the counterexamples deliberately and they have stopped being "
        f"runnable acceptance at all"
    )
