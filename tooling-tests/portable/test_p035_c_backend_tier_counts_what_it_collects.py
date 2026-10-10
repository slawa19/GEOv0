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


def _collect(
    *args: str, extra_pythonpath: Path | None = None, collect_only: bool = True
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    if extra_pythonpath is not None:
        env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(extra_pythonpath), env.get("PYTHONPATH")]))
    env["ENV"] = "test"
    env["TEST_DATABASE_URL"] = _NO_SERVER_URL
    env["GEO_TEST_ALLOW_DB_RESET"] = "1"
    env.pop("GEO_TEST_USE_MIGRATED_SCHEMA", None)
    env.pop("PYTEST_ADDOPTS", None)
    return subprocess.run(
        [sys.executable, "-m", "pytest", *(["--collect-only"] if collect_only else ["-rs"]), "-q", "-p", "no:cacheprovider", *args],
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


_COLLECTION_ERROR_PLUGIN = """
import pytest


class _CollectionError(pytest.File):
    def collect(self):
        raise ImportError("p035 simulated import error")


def pytest_collect_file(file_path, parent):
    if file_path.name == "{module_name}":
        return _CollectionError.from_parent(parent, path=file_path)
"""


def test_a_collection_error_stays_pytests_exit_2_and_is_not_reread_as_a_lost_test(
    baseline: subprocess.CompletedProcess[str], tmp_path: Path
) -> None:
    """Reproducer (red on 7edf4cb8, review P2): pytest 7.4 calls `pytest_collection_finish` in a `finally`, BEFORE it
    aborts on a collection error, so a module that fails to import reaches the count with its cases missing.

    The module's real cases are dropped with `--deselect` and a plugin makes the same file fail to collect, which is
    the shape of an import error in an existing module (done by hand once on `75dafc82`: exit 4, "a test was lost").
    Expected: pytest's own exit 2 and the collection error's text, and no claim that a test was lost.
    """

    nodeid, module = _a_unit_module(baseline)
    (tmp_path / "p035_collection_error_plugin.py").write_text(
        _COLLECTION_ERROR_PLUGIN.replace("{module_name}", Path(module).name), encoding="utf-8"
    )
    result = _collect(
        *_CANONICAL,
        "--deselect",
        module,
        "-p",
        "p035_collection_error_plugin",
        extra_pythonpath=tmp_path,
    )
    output = result.stdout + result.stderr
    assert "p035 simulated import error" in output, f"the collection error did not happen:\n{output[-1200:]}"
    assert result.returncode == 2, (
        f"a collection error ended the canonical backend run with exit {result.returncode}, not pytest's 2: "
        f"{[line for line in output.splitlines() if 'refused' in line][:1]}"
    )
    assert "backend tier refused" not in output, "the count judged a collection that pytest was about to abort"


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
    assert counted(" not  slow ", "tests", "tests/")  # whitespace is collapsed
    assert not counted("(not slow)", "tests")  # a differently spelled equivalent is NOT recognised (documented limit)
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


# ------------------------------------------------------------------------------------------------ the A10 exception
# 2026-10-10 (Q-C): exactly five real-pool-timeout cases of 035 A10 run in the required tier without `slow`. The list is
# `A10_REAL_POOL_TIMEOUT_CASES` in `tests/tier_count.py`; `tests/conftest.py` judges it at collection. These cases hold the
# list in place: the verdict function on planted collections (both outcomes), and the REAL collection, once as it is and
# twice with a plugin that plants the two violations an edit could make (a listed case turned `slow`; a sixth case marked).

_A10_FILE = "tests/integration/test_p035_a10_pool_wait_is_inside_the_payment_deadline_postgres.py"


def test_the_a10_verdict_holds_for_the_list_and_names_every_way_it_can_break() -> None:
    tier = _tier_count_module()
    listed = sorted(tier.A10_REAL_POOL_TIMEOUT_CASES)
    assert len(listed) == 5 and all(node_id.startswith(_A10_FILE + "::") for node_id in listed)
    rest = [(f"tests/unit/test_x.py::t{i}", False, False) for i in range(3)]
    whole = [(node_id, True, False) for node_id in listed] + rest
    assert tier.a10_exception_problem(whole) is None
    assert tier.a10_exception_problem(rest) is None  # the A10 module is not collected: nothing to judge but additions
    # a listed case missing from a collection that holds the module (renamed, re-parametrized or deleted)
    assert listed[0] in (tier.a10_exception_problem(whole[1:] + rest[:0]) or "")
    # reclassified: marker lost, or `slow` put back (which `-m 'not slow'` skips)
    lost = [(listed[0], False, False)] + [(n, True, False) for n in listed[1:]]
    assert "lost the a10_real_pool_timeout marker" in (tier.a10_exception_problem(lost) or "")
    slowed = [(listed[0], True, True)] + [(n, True, False) for n in listed[1:]]
    assert "carries `slow`" in (tier.a10_exception_problem(slowed) or "")
    # an addition: a sixth case with the marker, in this module or anywhere else
    for extra in (_A10_FILE + "::test_something_else", "tests/unit/test_x.py::t0"):
        added = whole + [(extra, True, False)]
        assert "without being on the closed list" in (tier.a10_exception_problem(added) or "")
        assert extra in (tier.a10_exception_problem(added) or "")


def test_the_real_collection_of_the_a10_module_selects_the_listed_five_without_slow() -> None:
    """The ids in the list are the ids pytest reports: marker selection finds exactly them, and `-m 'not slow'` keeps them."""

    listed = set(_tier_count_module().A10_REAL_POOL_TIMEOUT_CASES)
    by_marker = _collect("-m", "a10_real_pool_timeout", "--", _A10_FILE)
    assert by_marker.returncode == 0, (by_marker.stdout + by_marker.stderr)[-1500:]
    assert set(_nodeids(by_marker)) == listed
    canonical = _collect(*_CANONICAL, "--", _A10_FILE)
    assert canonical.returncode == 0, (canonical.stdout + canonical.stderr)[-1500:]
    assert listed <= set(_nodeids(canonical)), "a listed case is not selected by `-m 'not slow'`"


_A10_VIOLATION_PLUGIN = """
import pytest


def pytest_itemcollected(item):
    if item.nodeid == "{target}":
        item.add_marker({marker})
"""


@pytest.mark.parametrize(
    ("marker", "on_listed", "expected_text"),
    [
        ("pytest.mark.slow", True, "carries `slow`"),
        ("pytest.mark.a10_real_pool_timeout", False, "without being on the closed list"),
    ],
    ids=["a listed case turned slow", "a sixth case marked"],
)
def test_the_real_collection_ends_with_exit_4_when_the_a10_exception_is_broken(
    tmp_path: Path, marker: str, on_listed: bool, expected_text: str
) -> None:
    tier = _tier_count_module()
    target = sorted(tier.A10_REAL_POOL_TIMEOUT_CASES)[0]
    if not on_listed:
        baseline = _collect("--", _A10_FILE)
        target = next(node_id for node_id in _nodeids(baseline) if node_id not in tier.A10_REAL_POOL_TIMEOUT_CASES)
    (tmp_path / "p035_a10_violation_plugin.py").write_text(
        _A10_VIOLATION_PLUGIN.format(target=target.replace('"', '\\"'), marker=marker), encoding="utf-8"
    )
    result = _collect(*_CANONICAL, "-p", "p035_a10_violation_plugin", "--", _A10_FILE, extra_pythonpath=tmp_path)
    output = result.stdout + result.stderr
    assert result.returncode == _USAGE_ERROR, f"exit {result.returncode}, not {_USAGE_ERROR}:\n{output[-1500:]}"
    assert "the A10 exception" in output and expected_text in output, output[-1500:]


# --------------------------------------------------------------------- review of 5e9ba5fd: scope and execution (Q-C)
# Two P2 findings of the 2026-10-10 review: (1) a SINGLE A10 node was refused as "members missing"; (2) a listed member that
# is SKIPPED during execution left the run green. These cases are the reproducers (red on 5e9ba5fd) and stay as the guard.

_A10_NODE = _A10_FILE + "::"


@pytest.mark.parametrize(
    "selector",
    [
        _A10_NODE + "test_no_connection_around_routing_is_the_timeout_refusal_and_leaves_nothing",
        _A10_NODE + "test_a_cancellation_while_waiting_for_the_pool_leaves_nothing",
        _A10_NODE + "test_a_refusal_that_cannot_be_recorded_is_the_retryable_conflict_and_claims_nothing[the pool timeout-mode_b]",
    ],
    ids=["a function with two listed members", "a function with none", "one listed node by its full id"],
)
def test_a_single_a10_node_selector_is_not_judged_as_the_whole_module(selector: str) -> None:
    """A deliberate partial run (one function or one node) is complete for its scope: exit 0, the selection collected."""

    result = _collect("--", selector)
    output = result.stdout + result.stderr
    assert result.returncode == 0, f"a single-node selector was refused (exit {result.returncode}): {output[-900:]}"
    assert _nodeids(result), "the selector collected nothing: the case would pass by judging an empty set"


def test_the_whole_tier_with_the_a10_file_ignored_is_refused_by_the_membership_guard_itself() -> None:
    """`--ignore` of the A10 file on the whole tier: the COUNT also catches it, but the membership guard must name it."""

    result = _collect(*_CANONICAL, "--ignore", _A10_FILE)
    output = result.stdout + result.stderr
    assert result.returncode == _USAGE_ERROR, output[-900:]
    assert "the A10 exception" in output and "listed case is not collected" in output, output[-900:]


_A10_SKIP_PLUGIN = '''
import pytest

TARGET = {target!r}


def pytest_itemcollected(item):
    if item.nodeid == TARGET:
{collected}


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item):
    if item.nodeid == TARGET:
{setup}
'''


def _run_with_planted_skip(
    tmp_path: Path, *, target: str, collected: str, setup: str, k: str, m: str | None = None
) -> subprocess.CompletedProcess[str]:
    """Execute (not just collect) the A10 module narrowed by `-m`/`-k`, with a skip planted on `target` by a `-p` plugin.

    Only the planted case is selected, and it is skipped before any fixture is set up, so nothing here opens a database.
    """

    (tmp_path / "p035_a10_skip_plugin.py").write_text(
        _A10_SKIP_PLUGIN.format(
            target=target, collected="        " + collected, setup="        " + setup
        ),
        encoding="utf-8",
    )
    marker = ["-m", m] if m else []
    return _collect("-p", "p035_a10_skip_plugin", *marker, "-k", k, "--", _A10_FILE, extra_pythonpath=tmp_path, collect_only=False)


@pytest.mark.parametrize(
    ("collected", "setup"),
    [
        ("item.add_marker(pytest.mark.skip(reason='planted marker skip'))", "pass"),
        ("item.add_marker(pytest.mark.skipif(True, reason='planted skipif'))", "pass"),
        ("pass", "pytest.skip('planted runtime skip')"),
    ],
    ids=["pytest.mark.skip", "a true skipif", "pytest.skip() at runtime"],
)
def test_a_listed_member_skipped_during_execution_is_refused(tmp_path: Path, collected: str, setup: str) -> None:
    """The count and the membership stay right when a member is skipped; the run must still not be green."""

    target = _A10_NODE + "test_no_connection_for_the_attempt_is_the_timeout_refusal_and_leaves_nothing[the pool timeout-mode_b]"
    assert target in _tier_count_module().A10_REAL_POOL_TIMEOUT_CASES
    result = _run_with_planted_skip(
        tmp_path, target=target, collected=collected, setup=setup, k="test_no_connection_for_the_attempt", m="a10_real_pool_timeout"
    )
    output = result.stdout + result.stderr
    assert result.returncode != 0, f"a listed member was skipped and the run exited {result.returncode}:\n{output[-900:]}"
    assert "the A10 exception" in output and "skipped" in output, output[-900:]


def test_a_skipped_non_member_is_an_ordinary_skip(tmp_path: Path) -> None:
    """Counter-check: the refusal is about the five, not a blanket rule on skips."""

    other = _A10_NODE + "test_a_cancellation_while_waiting_for_the_pool_leaves_nothing[mode_b]"
    result = _run_with_planted_skip(
        tmp_path,
        target=other,
        collected="item.add_marker(pytest.mark.skip(reason='planted'))",
        setup="pass",
        k="test_a_cancellation_while_waiting",
    )
    assert result.returncode == 0 and "1 skipped" in result.stdout, (result.stdout + result.stderr)[-900:]
