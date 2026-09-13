"""The `b4_counterexample` marker is gone, and nothing may bring it back.

WHAT THIS GUARD USED TO DO, because the change is the point. Programme 015 phase B step 2 put 107
counterexamples in the tree that were RED ON PURPOSE: the recorded acceptance of step 4, written
before the debt journal, each failing because nothing in the tree had the property it named. They
carried `pytest.mark.b4_counterexample` and the canonical runner deselected that marker from every
tier, so a shared working tree's gate stayed a signal for the other sessions in it (`AGENTS.md`
§7). This file held the marker to a CLOSED allowlist, in both directions: it failed if the marker
appeared on a module that was not a counterexample, and it failed if a counterexample lost it.

WHAT IT DOES NOW. Step 4 slice C built `app/core/ledger/journal.py` into the four production
writers and armed it, the counterexamples went green THROUGH THE JOURNAL with their assertions
untouched, and the marker was removed from `pytest.ini`, from `scripts/verify_local.ps1` and from
all seven modules. So the allowlist is empty, and an empty allowlist is not a guard - it is a
sentence that cannot fail. The direction is therefore inverted: the marker must not exist. No
module may carry it, `pytest.ini` may not register it, and the runner may not subtract it from any
tier.

WHY THAT IS WORTH A TEST AND NOT JUST A DELETION. The counterexamples now run in the two canonical
gates. The cheapest way for a future session to make an inconvenient one stop failing is to put the
marker back - it already has a documented history, a runner branch that once honoured it and a file
full of prose explaining why those tests are allowed not to run. `AGENTS.md` §5 names exactly that
shape as a source of false green, and §9's anti-vacuum rule says a mechanism that excludes things
must carry a counter-check. This file is the counter-check, pointed the other way.

WHAT THIS GUARD DOES NOT SEE, so its silence is not mistaken for more than it is:

* it reads `pytest.mark.b4_counterexample` written literally in the source. A marker applied through
  `pytest_collection_modifyitems`, an `applymarker` call, a `parametrize` entry or an alias is
  invisible to it - as is a DIFFERENT marker name invented for the same purpose, which no textual
  guard can recognise;
* it matches strings in `scripts/verify_local.ps1`. It does not run the runner, so it cannot prove
  the marker expression it reads is the one that reaches pytest - the gate's own selected and
  deselected counts are what show that;
* it says nothing about whether the counterexamples still assert what they were written to assert.
  Nothing can: that is what the per-test `MUTATION once step 4 exists` lines in their docstrings are
  for, and they have to be run by hand.
"""

from __future__ import annotations

import ast
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_MARKER = "b4_counterexample"


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


def test_no_module_carries_the_marker_any_more() -> None:
    """The step-4 counterexamples run in the gate. Nothing may take them back out of it."""

    # ANTI-VACUUM, FIRST, and it is not the same anti-vacuum this file used to carry. The old one
    # checked that the scan still FOUND the marker; there is nothing left to find, so what has to
    # be proven instead is that the scan still WORKS - that it recognises the shape it is looking
    # for. A scanner that silently stopped recognising `pytest.mark.<name>` would report an empty
    # result for a tree where the marker had been reapplied everywhere.
    probe = ast.parse("import pytest\npytestmark = pytest.mark." + _MARKER + "\n")
    found_in_probe = [
        node
        for node in ast.walk(probe)
        if isinstance(node, ast.Attribute)
        and node.attr == _MARKER
        and isinstance(node.value, ast.Attribute)
        and node.value.attr == "mark"
        and isinstance(node.value.value, ast.Name)
        and node.value.value.id == "pytest"
    ]
    assert found_in_probe, (
        "this guard no longer recognises `pytest.mark.<marker>` in an AST, so its verdict below "
        "measured nothing at all"
    )

    carrying = sorted(_modules_carrying_the_marker())
    assert not carrying, (
        f"`{_MARKER}` is back, on {carrying}. That marker meant 'this test is the recorded "
        f"acceptance of programme 015 phase B step 4 and is red until step 4 exists'. Step 4 "
        f"exists: `app/core/ledger/journal.py` is armed and the counterexamples pass through it. "
        f"Re-applying the marker can now only mean taking a REAL failure out of the gate, which is "
        f"the false green `AGENTS.md` §5 names. Fix the failure, or - if a genuinely new red-first "
        f"acceptance is being written - give it a marker of its own with its own contract and its "
        f"own closed allowlist, so nobody inherits this one's permission."
    )


def test_pytest_no_longer_registers_the_marker() -> None:
    """With `--strict-markers`, an unregistered marker fails collection outright.

    That is the enforcement the assertion above leans on: as long as the marker is not in the
    `markers` list, a module that applies it cannot even be collected, so the textual scan is a
    second line rather than the only one.
    """

    config = (_ROOT / "pytest.ini").read_text(encoding="utf-8")
    assert "--strict-markers" in config, (
        "pytest.ini no longer passes `--strict-markers`, so an unregistered marker would be a "
        "silent no-op instead of a collection error - and the marker this file exists to keep out "
        "could be reapplied without anything objecting"
    )
    assert f"\n    {_MARKER}:" not in config, (
        f"`{_MARKER}` is registered in pytest.ini's `markers` list again. Registration is what "
        f"makes the marker usable under `--strict-markers`; the marker was retired with step 4 "
        f"and the counterexamples it covered now run in both canonical tiers."
    )


def test_the_canonical_runner_subtracts_the_marker_from_no_tier() -> None:
    """No branch of the runner's marker expression may exclude it.

    Three branches, and all three mattered: the default tier, `-IncludeExpensive`, and an explicitly
    requested `-BackendMarker`. Each carried `and not b4_counterexample` and each has stopped. A
    runner that put the exclusion back would take the counterexamples out of the gate without any
    module in `tests/` changing, so the assertion above could not see it.
    """

    runner = (_ROOT / "scripts" / "verify_local.ps1").read_text(encoding="utf-8")

    assert "'not slow and not postgres'" in runner, (
        "the runner's DEFAULT marker expression is not `not slow and not postgres` any more; if it "
        "has grown another exclusion, say which tests it removes and why they may not run"
    )
    assert "'not postgres'" in runner, (
        "the runner's -IncludeExpensive expression is not `not postgres` any more"
    )
    offending = [
        line.strip()
        for line in runner.splitlines()
        if _MARKER in line and not line.strip().startswith("#")
    ]
    assert not offending, (
        f"the canonical runner subtracts `{_MARKER}` again: {offending}. Every tier - default, "
        f"-IncludeExpensive and an explicitly requested -BackendMarker - must select the step-4 "
        f"counterexamples like any other test."
    )
