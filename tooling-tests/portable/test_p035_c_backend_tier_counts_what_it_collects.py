"""The backend tier counts what it collects, and refuses a canonical run that lost or gained a case (035 F-035-14).

THE GAP THIS CLOSES. The tooling tier refuses a count that differs from `EXPECTED_CASES`, in either
direction, and refuses any deselection (`tooling-tests/conftest.py`). The backend tier has no such
count: `tests/conftest.py` only reorders the conformance aggregate, so deleting a module of tests, or a
`--deselect` / `--ignore` on the run, ends green. Measured 2026-10-08 on `75dafc82`, direct pytest (debug
path), `--collect-only -q -m "not slow"`: 3150 selected / 15 deselected; with one `--deselect` of one
case: 3149 selected / 16 deselected, exit 0. Nothing in the session notices.

THE RULE THIS FILE HOLDS (implemented 2026-10-08 in `tests/tier_count.py`, called from `tests/conftest.py`; the two
cases that were RED on `75dafc82` are the reproducer and stay as the regression test).
On the CANONICAL PROFILE - the whole tier, no positional selector, marker expression `not slow` (the
runner's default, `scripts/verify_local.ps1`) - a backend session whose number of selected cases differs
from a constant recorded in the repository ends with a usage error (exit 4) that names both numbers. The
rule is a count of the whole tier and applies to nothing else:

* a run with a positional selector (`-BackendSelector`) is a deliberate narrowing and is NOT counted;
* a run with no marker expression (`-IncludeExpensive`) is a different, wider profile and is NOT counted
  by this rule.

HOW THE CASES ASK. Each runs pytest in a subprocess, `--collect-only` only, against a PostgreSQL URL that
nothing listens on: collection opens no database (the same stand as
`tooling-tests/powershell/test_the_tier_refuses_a_database_that_is_not_postgres.py`), so the tooling tier
keeps needing no database. The count check must therefore fire at collection end, not at test run; a
mechanism that counted only at session finish would not be visible here and would have to be asked in a
run that executes cases (15 minutes) - that choice is the implementer's, recorded in the 035 changelog.
The deselected and ignored cases are taken from the baseline collection itself, so the file does not rot
when a test module is renamed.

COUNTER-CHECKS (AGENTS.md section 9, anti-vacuum): the refusal must not be a refusal of everything. The
unmodified canonical profile, a selector run (alone and with a `--deselect`) and the wide profile with a
`--deselect` are collected with exit 0 - green today and required to stay green. The two RED cases
confirm in their own message that the baseline collected a positive number of cases and that the
deselection or ignore really removed some, so a typo in the node id cannot make them pass or fail by
accident.

WHAT THIS DOES NOT SEE. The GROWTH direction (an unrecorded new case) cannot be reached by a subprocess run
without adding a test file under `tests/`, which this file does not do; it is held by calling the comparison
(`count_problem`) and the profile test (`is_canonical_profile`) directly, with a number above the constant. Which tests are present: the count says how many, not which - a case
swapped for a weaker one under the same number passes. A run started with `-o addopts=...` or
`PYTEST_ADDOPTS` that ends pytest before the count: the runner is not defended against reconfiguring
itself (see the limits in `tooling-tests/conftest.py`).
"""

from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]

_USAGE_ERROR = 4  # pytest.ExitCode.USAGE_ERROR
_NO_SERVER_URL = "postgresql+asyncpg://geo:geo@127.0.0.1:5432/geov0_test_p035c_count_probe"

# The canonical profile is exactly what `scripts/verify_local.ps1` passes by default: `-m "not slow"` and no
# positional selector. `-IncludeExpensive` drops the marker expression.
_CANONICAL = ("-m", "not slow")
_WIDE = ()

_SUMMARY = re.compile(r"(?P<selected>\d+)(?:/(?P<total>\d+))? tests? collected(?: \((?P<deselected>\d+) deselected\))?")


def _collect(*args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["ENV"] = "test"
    env["TEST_DATABASE_URL"] = _NO_SERVER_URL
    env["GEO_TEST_ALLOW_DB_RESET"] = "1"
    env.pop("GEO_TEST_USE_MIGRATED_SCHEMA", None)
    env.pop("PYTEST_ADDOPTS", None)
    return subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", *args],
        cwd=_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _selected(result: subprocess.CompletedProcess[str]) -> int:
    match = _SUMMARY.search(result.stdout)
    assert match is not None, f"no collection summary in pytest output:\n{result.stdout[-1500:]}"
    return int(match["selected"])


def _nodeids(result: subprocess.CompletedProcess[str]) -> list[str]:
    return [line for line in result.stdout.splitlines() if line.startswith("tests/") and "::" in line]


@pytest.fixture(scope="module")
def baseline() -> subprocess.CompletedProcess[str]:
    """The canonical profile, untouched: the tier as it is, which must collect and exit 0."""

    result = _collect(*_CANONICAL)
    assert result.returncode == 0, (
        f"the unmodified canonical profile did not collect (exit {result.returncode}):\n"
        f"{(result.stdout + result.stderr)[-1500:]}"
    )
    assert _selected(result) > 0, "the baseline collected nothing; the cases below would judge nothing"
    return result


def _a_unit_module(baseline: subprocess.CompletedProcess[str]) -> tuple[str, str]:
    """One case of `tests/unit` and its module, taken from the baseline so that a rename cannot rot this file."""

    for nodeid in _nodeids(baseline):
        if nodeid.startswith("tests/unit/"):
            return nodeid, nodeid.split("::", 1)[0]
    raise AssertionError("the baseline holds no case under tests/unit/")


def test_a_deselected_case_ends_the_canonical_backend_run(baseline: subprocess.CompletedProcess[str]) -> None:
    """Reproducer (red on 75dafc82): one `--deselect` on the whole tier must refuse the run, not pass it."""

    nodeid, _ = _a_unit_module(baseline)
    before = _selected(baseline)
    result = _collect(*_CANONICAL, "--deselect", nodeid)

    after = _selected(result)
    assert after < before, f"the --deselect of {nodeid!r} removed nothing ({before} -> {after}): a stale id, not a test"
    assert result.returncode == _USAGE_ERROR, (
        f"the canonical backend profile lost one case ({before} -> {after} selected) and pytest "
        f"exited {result.returncode}, not {_USAGE_ERROR}: the tier has no count of what it collects "
        f"(F-035-14)"
    )
    output = result.stdout + result.stderr
    assert f"selected {after} case(s), expected exactly {before}" in output, (
        "the refusal must name the actual and the expected number"
    )


def test_a_module_dropped_from_the_run_ends_the_canonical_backend_run(baseline: subprocess.CompletedProcess[str]) -> None:
    """Reproducer (red on 75dafc82): the loss of a whole module (`--ignore`, or a deleted file) must refuse the run."""

    _, module = _a_unit_module(baseline)
    before = _selected(baseline)
    result = _collect(*_CANONICAL, "--ignore", module)

    after = _selected(result)
    assert after < before, f"--ignore of {module!r} removed nothing ({before} -> {after}): a stale path, not a test"
    assert result.returncode == _USAGE_ERROR, (
        f"the canonical backend profile lost the module {module} ({before} -> {after} selected) and "
        f"pytest exited {result.returncode}, not {_USAGE_ERROR}: the tier has no count of what it "
        f"collects (F-035-14)"
    )
    assert f"selected {after} case(s), expected exactly {before}" in result.stdout + result.stderr


def test_the_unmodified_canonical_profile_is_not_refused(baseline: subprocess.CompletedProcess[str]) -> None:
    """Counter-check: the rule does not refuse the tier as it is (the `baseline` fixture asserts exit 0)."""

    assert _selected(baseline) > 0


def test_a_selector_run_is_not_counted_even_with_a_deselect(baseline: subprocess.CompletedProcess[str]) -> None:
    """Counter-check: `-BackendSelector` narrows on purpose, and a deselect inside it is the operator's business."""

    nodeid, module = _a_unit_module(baseline)
    plain = _collect(*_CANONICAL, "--", module)
    assert plain.returncode == 0, f"a selector run was refused (exit {plain.returncode}):\n{plain.stdout[-800:]}"
    with_deselect = _collect(*_CANONICAL, "--deselect", nodeid, "--", module)
    assert with_deselect.returncode == 0, (
        f"a selector run with a deselect was refused (exit {with_deselect.returncode}):\n"
        f"{with_deselect.stdout[-800:]}"
    )


def test_the_wide_profile_is_not_counted_by_this_rule(baseline: subprocess.CompletedProcess[str]) -> None:
    """Counter-check: `-IncludeExpensive` (no marker expression) is a different profile; a deselect in it is not refused."""

    nodeid, _ = _a_unit_module(baseline)
    wide = _collect(*_WIDE, "--deselect", nodeid)
    assert wide.returncode == 0, (
        f"the wide profile with a deselect was refused (exit {wide.returncode}); this rule counts the "
        f"canonical profile only:\n{wide.stdout[-800:]}"
    )


def _tier_count_module():
    path = _ROOT / "tests" / "tier_count.py"
    spec = importlib.util.spec_from_file_location("p035_c_tier_count_under_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_comparison_refuses_growth_and_loss_alike_and_accepts_the_recorded_number() -> None:
    """Unit check of the comparison, both directions (the subprocess cases above can reach only the loss)."""

    tier = _tier_count_module()
    expected = tier.EXPECTED_SELECTED_ITEMS
    assert expected > 0
    assert tier.count_problem(selected=expected) is None
    for selected, word in ((expected + 1, "MORE"), (expected - 1, "FEWER")):
        problem = tier.count_problem(selected=selected)
        assert problem is not None, f"{selected} selected against {expected} was accepted"
        assert f"selected {selected} case(s), expected exactly {expected}" in problem
        assert word in problem


def test_only_the_whole_tier_under_the_canonical_marker_expression_is_counted() -> None:
    """The profile decision in both outcomes: a rule that counted nothing, or everything, fails one row."""

    tier = _tier_count_module()
    root = _ROOT
    here = root

    def counted(markexpr, *args):
        return tier.is_canonical_profile(markexpr=markexpr, args=list(args), invocation_dir=here, root=root)

    assert counted("not slow")  # no argument: `testpaths = tests`
    assert counted("not slow", "tests")
    assert counted(" not slow ", "tests", "tests/")
    assert not counted("", "tests")  # -IncludeExpensive
    assert not counted(None)
    assert not counted("not slow and not x", "tests")
    assert not counted("not slow", "tests/unit")  # -BackendSelector
    assert not counted("not slow", "tests/unit/test_admin_audit_log_list.py")
    assert not counted("not slow", "tests/unit/test_admin_audit_log_list.py::test_x")
    assert not counted("not slow", "tests", "tests/unit")
    assert not counted("not slow", "docs")


def test_every_named_skip_is_still_declared_where_the_count_module_says_it_is() -> None:
    """The skipped cases of the canonical run are named in `tests/tier_count.py`; each name must still be true.

    Form check only: the file holds the decorator and the reason the list quotes. Whether the list is complete is
    read from `-rs` in the CI log, not from here.
    """

    named = (
        ("tests/unit/test_deployment_config.py", 'os.name == "nt" or shutil.which("bash") is None'),
        ("tests/unit/test_settings_guardrails.py", '@pytest.mark.skipif(os.name == "nt"'),
        ("tests/unit/test_p024_scenario_id_is_a_safe_path_segment.py", '@pytest.mark.skipif(os.name != "nt"'),
    )
    docstring = (_ROOT / "tests" / "tier_count.py").read_text(encoding="utf-8")
    for relative, declaration in named:
        assert relative in docstring, f"{relative} is not named in tests/tier_count.py"
        source = (_ROOT / relative).read_text(encoding="utf-8")
        assert declaration in source, f"{relative} no longer declares {declaration!r}; update the named list"
